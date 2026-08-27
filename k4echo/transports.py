"""How the Lambda reaches the bridge running on the home network.

Two transports are supported, and they trade off differently:

``iot`` (recommended, default)
    The bridge holds an *outbound* MQTT connection to AWS IoT Core and the
    Lambda publishes a command to it.  Nothing listens for inbound connections
    at home, so no port forward and no firewall hole are required.  Power
    commands are fire-and-forget; power *status* is read from the device
    shadow that the bridge keeps updated.

``webhook``
    The Lambda makes a signed HTTP POST straight to the bridge, which means a
    forwarded port on the home router.  In exchange the call is synchronous,
    so the radio's actual reply comes back in the same request.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from . import signing
from .commands import K4Command

LOG = logging.getLogger(__name__)

# Alexa gives a skill about 8 seconds to answer, so the call to the bridge has
# to finish comfortably inside that or the user hears a generic failure.
DEFAULT_HTTP_TIMEOUT = 6.0
DEFAULT_SHADOW_MAX_AGE = 300


class TransportError(Exception):
    """Raised when the bridge could not be reached or refused the command."""


@dataclass
class BridgeResult:
    """Outcome of handing one command to the bridge."""

    ok: bool
    detail: str
    reply: Optional[str] = None
    synchronous: bool = True
    radio_reachable: bool = True


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _require(name: str) -> str:
    value = _env(name)
    if not value:
        raise TransportError("environment variable {} is not set".format(name))
    return value


# --------------------------------------------------------------------------
# Signed HTTP webhook
# --------------------------------------------------------------------------

_secret_cache: Dict[str, str] = {}


def _bridge_secret() -> str:
    """Fetch the shared signing secret, preferring Secrets Manager."""
    arn = _env("K4_BRIDGE_SECRET_ARN")
    if arn:
        if arn not in _secret_cache:
            import boto3  # provided by the Lambda runtime

            payload = boto3.client("secretsmanager").get_secret_value(SecretId=arn)
            raw = payload.get("SecretString") or ""
            try:
                # Accept either a bare string or {"secret": "..."} JSON.
                raw = json.loads(raw).get("secret", raw)
            except (ValueError, AttributeError):
                pass
            _secret_cache[arn] = raw
        return _secret_cache[arn]

    return _require("K4_BRIDGE_SECRET")


class WebhookTransport:
    """Signed HTTP POST to a bridge reachable through the home router."""

    name = "webhook"

    def __init__(self, url: Optional[str] = None, secret: Optional[str] = None, timeout: Optional[float] = None):
        self.url = url or _require("K4_BRIDGE_URL")
        self._secret = secret
        self.timeout = timeout if timeout is not None else float(_env("K4_BRIDGE_TIMEOUT", DEFAULT_HTTP_TIMEOUT))

    @property
    def secret(self) -> str:
        if self._secret is None:
            self._secret = _bridge_secret()
        return self._secret

    def execute(self, command: K4Command, request_id: Optional[str] = None) -> BridgeResult:
        body = json.dumps(
            {
                "command": command.name,
                "request_id": request_id or uuid.uuid4().hex,
                "issued_at": int(time.time()),
            },
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        headers.update(signing.sign_request(self.secret, body))

        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise TransportError("bridge returned HTTP {}: {}".format(exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise TransportError("cannot reach the bridge ({})".format(exc.reason)) from exc
        except (TimeoutError, OSError) as exc:
            raise TransportError("cannot reach the bridge ({})".format(exc)) from exc

        if not payload.get("ok"):
            raise TransportError(payload.get("error") or "the bridge rejected the command")

        return BridgeResult(
            ok=True,
            detail=payload.get("detail") or "",
            reply=payload.get("reply"),
            synchronous=True,
            radio_reachable=bool(payload.get("radio_reachable", True)),
        )


# --------------------------------------------------------------------------
# AWS IoT Core (no inbound port at home)
# --------------------------------------------------------------------------


class IotTransport:
    """Publish the command to AWS IoT Core; the bridge is subscribed to it."""

    name = "iot"

    def __init__(self, thing_name: Optional[str] = None, topic: Optional[str] = None, client: Any = None):
        self.thing_name = thing_name or _require("K4_IOT_THING_NAME")
        self.topic = topic or _env("K4_IOT_TOPIC", "k4echo/{}/cmd".format(self.thing_name))
        self.shadow_max_age = int(_env("K4_SHADOW_MAX_AGE", DEFAULT_SHADOW_MAX_AGE))
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # provided by the Lambda runtime

            endpoint = _env("K4_IOT_ENDPOINT")
            kwargs = {"endpoint_url": "https://" + endpoint} if endpoint else {}
            self._client = boto3.client("iot-data", **kwargs)
        return self._client

    def execute(self, command: K4Command, request_id: Optional[str] = None) -> BridgeResult:
        if command.expects_reply:
            return self._read_shadow(command)

        payload = json.dumps(
            {
                "command": command.name,
                "request_id": request_id or uuid.uuid4().hex,
                "issued_at": int(time.time()),
            },
            separators=(",", ":"),
        )

        try:
            self.client.publish(topic=self.topic, qos=1, payload=payload.encode("utf-8"))
        except Exception as exc:  # boto3 raises service-specific errors
            raise TransportError("could not publish to AWS IoT ({})".format(exc)) from exc

        return BridgeResult(ok=True, detail="", reply=None, synchronous=False)

    def _read_shadow(self, command: K4Command) -> BridgeResult:
        """Answer a status query from the shadow the bridge keeps updated."""
        try:
            document = self.client.get_thing_shadow(thingName=self.thing_name)
            state = json.loads(document["payload"].read().decode("utf-8"))
        except Exception as exc:
            raise TransportError("could not read the radio's status ({})".format(exc)) from exc

        reported = state.get("state", {}).get("reported", {}) or {}
        updated_at = reported.get("updated_at")

        if updated_at and (time.time() - float(updated_at)) > self.shadow_max_age:
            raise TransportError(
                "the home bridge has not reported in for a while, so I can't tell you the radio's state"
            )

        return BridgeResult(
            ok=True,
            detail="",
            reply=reported.get("power_reply"),
            synchronous=True,
            radio_reachable=bool(reported.get("radio_reachable", True)),
        )


def build_transport(kind: Optional[str] = None):
    """Instantiate the transport named by ``K4_TRANSPORT`` (default ``iot``)."""
    selected = (kind or _env("K4_TRANSPORT", "iot")).strip().lower()

    if selected == "webhook":
        return WebhookTransport()
    if selected == "iot":
        return IotTransport()

    raise TransportError("unknown transport {!r} (expected 'iot' or 'webhook')".format(selected))
