"""The home-network bridge.

Runs on a Raspberry Pi, a Windows box, or anything else on the same LAN as the
radio.  It accepts a command from the Lambda -- over AWS IoT Core (outbound
only) or over a signed HTTP webhook -- resolves it against the command
allow-list, and sends the corresponding CAT string to the K4 on TCP 9200.

    python -m k4echo.bridge --config /etc/k4echo/bridge.ini
    python -m k4echo.bridge --selftest          # just talk to the radio
    python -m k4echo.bridge --send power_off    # run one command locally
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from . import commands, signing
from .config import BridgeConfig, ConfigError, load
from .radio import K4Client, RadioError

LOG = logging.getLogger("k4echo.bridge")

MAX_BODY_BYTES = 4096


class CommandExecutor:
    """Turns a command name into an actual CAT exchange with the radio."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.client = K4Client(
            host=config.radio.host,
            port=config.radio.port,
            connect_timeout=config.radio.connect_timeout,
            reply_timeout=config.radio.reply_timeout,
        )

    def resolve(self, payload: Dict[str, Any]):
        """Map a request payload onto a command, honouring the allow-list."""
        name = payload.get("command")
        if name:
            command = commands.lookup(name)
            if command is None:
                raise ValueError("unknown command {!r}".format(name))
            return command

        raw = payload.get("cat")
        if raw and self.config.allow_raw_cat:
            LOG.warning("executing raw CAT string %r (allow_raw_cat is on)", raw)
            return commands.K4Command(name="raw", cat=raw, expects_reply=False, speech="")

        if raw:
            raise ValueError("raw CAT strings are disabled on this bridge")

        raise ValueError("payload has no 'command' field")

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run one command and return a JSON-serialisable result."""
        command = self.resolve(payload)
        prefix = command.cat.rstrip(";")[:2] if command.expects_reply else None

        try:
            reply = self.client.send(command.cat, expect_prefix=prefix)
        except RadioError as exc:
            if command.expects_reply:
                # For a status query, "can't connect" is itself the answer:
                # the K4 drops its network interface in standby.
                LOG.info("radio unreachable during query: %s", exc)
                return {
                    "ok": True,
                    "command": command.name,
                    "cat": command.cat,
                    "reply": None,
                    "radio_reachable": False,
                    "detail": str(exc),
                }
            LOG.error("radio error running %s: %s", command.name, exc)
            return {"ok": False, "command": command.name, "cat": command.cat, "error": str(exc)}

        LOG.info("command %s (%s) ok, reply=%r", command.name, command.cat, reply)
        return {
            "ok": True,
            "command": command.name,
            "cat": command.cat,
            "reply": reply,
            "radio_reachable": True,
            "detail": "",
        }

    def power_state(self) -> Dict[str, Any]:
        """Snapshot of the radio's power state, for the IoT device shadow."""
        result = self.execute({"command": commands.POWER_QUERY.name})
        reply = result.get("reply") or ""
        return {
            "power": "on" if reply.upper().startswith("PS1") else ("standby" if reply.upper().startswith("PS0") else "unknown"),
            "power_reply": reply or None,
            "radio_reachable": bool(result.get("radio_reachable")),
            "radio_host": "{}:{}".format(self.config.radio.host, self.config.radio.port),
            "updated_at": int(time.time()),
        }


# --------------------------------------------------------------------------
# Signed HTTP webhook server
# --------------------------------------------------------------------------


def make_webhook_handler(config: BridgeConfig, executor: CommandExecutor, guard: signing.ReplayGuard):
    """Build a request handler class bound to this bridge's configuration."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "k4echo-bridge"
        sys_version = ""

        def log_message(self, fmt, *args):  # route access logs through logging
            LOG.debug("%s - %s", self.address_string(), fmt % args)

        def _reply(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler naming
            if self.path.rstrip("/") == "/health":
                self._reply(200, {"ok": True, "service": "k4echo-bridge"})
            else:
                self._reply(404, {"ok": False, "error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != config.webhook.path.rstrip("/"):
                self._reply(404, {"ok": False, "error": "not found"})
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._reply(400, {"ok": False, "error": "bad content length"})
                return

            if length <= 0 or length > MAX_BODY_BYTES:
                self._reply(413, {"ok": False, "error": "bad request size"})
                return

            body = self.rfile.read(length)

            try:
                _, nonce = signing.verify_request(
                    config.webhook.secret,
                    dict(self.headers.items()),
                    body,
                    max_skew=config.webhook.max_skew,
                )
                guard.check_and_record(nonce)
            except signing.SignatureError as exc:
                LOG.warning("rejected request from %s: %s", self.address_string(), exc)
                self._reply(401, {"ok": False, "error": "unauthorized"})
                return

            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._reply(400, {"ok": False, "error": "malformed json"})
                return

            try:
                result = executor.execute(payload)
            except ValueError as exc:
                self._reply(400, {"ok": False, "error": str(exc)})
                return

            self._reply(200 if result.get("ok") else 502, result)

    return Handler


def run_webhook(config: BridgeConfig, executor: CommandExecutor) -> None:
    handler = make_webhook_handler(config, executor, signing.ReplayGuard(config.webhook.max_skew * 2))
    server = ThreadingHTTPServer((config.webhook.bind, config.webhook.port), handler)

    scheme = "http"
    if config.webhook.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config.webhook.tls_cert, config.webhook.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    LOG.info(
        "webhook bridge listening on %s://%s:%s%s -> radio %s:%s",
        scheme, config.webhook.bind, config.webhook.port, config.webhook.path,
        config.radio.host, config.radio.port,
    )

    _install_signal_handlers(server.shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        LOG.info("webhook bridge stopped")


# --------------------------------------------------------------------------
# AWS IoT Core client
# --------------------------------------------------------------------------


def run_iot(config: BridgeConfig, executor: CommandExecutor) -> None:
    try:
        from awscrt import mqtt
        from awsiot import mqtt_connection_builder
    except ImportError as exc:  # pragma: no cover - depends on host install
        raise ConfigError(
            "the iot transport needs the AWS IoT SDK: pip install awsiotsdk"
        ) from exc

    client_id = config.iot.client_id or "{}-{}".format(config.iot.thing_name, os.getpid())

    connection = mqtt_connection_builder.mtls_from_path(
        endpoint=config.iot.endpoint,
        cert_filepath=config.iot.cert,
        pri_key_filepath=config.iot.key,
        ca_filepath=config.iot.root_ca,
        client_id=client_id,
        clean_session=False,
        keep_alive_secs=30,
    )

    LOG.info("connecting to AWS IoT at %s as %s", config.iot.endpoint, client_id)
    connection.connect().result()
    LOG.info("connected; subscribing to %s", config.command_topic)

    shadow_topic = "$aws/things/{}/shadow/update".format(config.iot.thing_name)

    def publish(topic: str, payload: Dict[str, Any]) -> None:
        connection.publish(topic=topic, payload=json.dumps(payload), qos=mqtt.QoS.AT_LEAST_ONCE)

    def report_state() -> None:
        try:
            publish(shadow_topic, {"state": {"reported": executor.power_state()}})
        except Exception:  # noqa: BLE001 - a shadow update must never kill the bridge
            LOG.exception("failed to update device shadow")

    def on_message(topic, payload, **_kwargs):
        try:
            request = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            LOG.warning("ignoring malformed message on %s", topic)
            return

        LOG.info("received %s on %s", request.get("command"), topic)
        try:
            result = executor.execute(request)
        except ValueError as exc:
            result = {"ok": False, "error": str(exc)}

        result["request_id"] = request.get("request_id")
        publish(config.result_topic, result)
        report_state()

    connection.subscribe(
        topic=config.command_topic, qos=mqtt.QoS.AT_LEAST_ONCE, callback=on_message
    )[0].result()

    stop = threading.Event()
    _install_signal_handlers(stop.set)

    LOG.info("iot bridge ready -> radio %s:%s", config.radio.host, config.radio.port)
    report_state()

    interval = max(30, config.iot.shadow_interval)
    while not stop.wait(interval):
        report_state()

    LOG.info("disconnecting from AWS IoT")
    connection.disconnect().result()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _install_signal_handlers(callback) -> None:
    def handler(signum, _frame):
        LOG.info("received signal %s, shutting down", signum)
        threading.Thread(target=callback, daemon=True).start()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="k4echo.bridge", description="K4 Echo Control home bridge")
    parser.add_argument("-c", "--config", help="path to bridge.ini")
    parser.add_argument("--selftest", action="store_true", help="query the radio and exit")
    parser.add_argument("--send", metavar="COMMAND", help="run one command locally and exit")
    parser.add_argument("--list-commands", action="store_true", help="show the command allow-list")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_commands:
        for command in commands.CATALOG.values():
            print("{:<14} {}".format(command.name, command.cat))
        return 0

    try:
        config = load(args.config)
    except ConfigError as exc:
        print("configuration error: {}".format(exc), file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    source = getattr(config, "source_path", None)
    LOG.info("loaded configuration from %s", source or "defaults and environment")

    executor = CommandExecutor(config)

    if args.selftest:
        state = executor.power_state()
        print(json.dumps(state, indent=2))
        return 0 if state["radio_reachable"] else 1

    if args.send:
        result = executor.execute({"command": args.send})
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    try:
        config.validate()
    except ConfigError as exc:
        print("configuration error: {}".format(exc), file=sys.stderr)
        return 2

    if config.transport == "webhook":
        run_webhook(config, executor)
    else:
        run_iot(config, executor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
