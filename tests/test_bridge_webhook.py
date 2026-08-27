"""Exercises the real bridge HTTP server against a simulated radio."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from fake_k4 import FakeK4
from k4echo import signing
from k4echo.bridge import CommandExecutor, make_webhook_handler
from k4echo.config import BridgeConfig, RadioConfig, WebhookConfig
from http.server import ThreadingHTTPServer

SECRET = "s" * 48


@pytest.fixture
def stack():
    """A running bridge plus the fake radio behind it."""
    radio = FakeK4(power_on=True).start()
    config = BridgeConfig(
        transport="webhook",
        radio=RadioConfig(host="127.0.0.1", port=radio.port, reply_timeout=1.0),
        webhook=WebhookConfig(bind="127.0.0.1", port=0, secret=SECRET),
    )
    executor = CommandExecutor(config)
    executor.client.settle_seconds = 0.05

    handler = make_webhook_handler(config, executor, signing.ReplayGuard())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = "http://127.0.0.1:{}/command".format(server.server_address[1])
    try:
        yield radio, url, server
    finally:
        server.shutdown()
        server.server_close()
        radio.stop()


def post(url, payload, secret=SECRET, headers=None):
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or signing.sign_request(secret, body))
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def test_signed_power_off_reaches_the_radio(stack):
    radio, url, _ = stack
    status, payload = post(url, {"command": "power_off"})

    assert status == 200
    assert payload["ok"] is True
    assert payload["cat"] == "PS0;"
    assert "PS0;" in radio.received
    assert radio.power_on is False


def test_signed_query_returns_the_radio_reply(stack):
    _, url, _ = stack
    status, payload = post(url, {"command": "power_query"})

    assert status == 200
    assert payload["reply"] == "PS1;"
    assert payload["radio_reachable"] is True


def test_unsigned_request_is_rejected(stack):
    _, url, _ = stack
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url, {"command": "power_off"}, headers={"Content-Type": "application/json"})
    assert exc.value.code == 401


def test_request_signed_with_the_wrong_secret_is_rejected(stack):
    radio, url, _ = stack
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url, {"command": "power_off"}, secret="x" * 48)
    assert exc.value.code == 401
    assert radio.received == []


def test_a_captured_request_cannot_be_replayed(stack):
    _, url, _ = stack
    body = json.dumps({"command": "power_off"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers.update(signing.sign_request(SECRET, body))

    assert post(url, {"command": "power_off"}, headers=headers)[0] == 200

    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url, {"command": "power_off"}, headers=headers)
    assert exc.value.code == 401


def test_unknown_command_is_refused(stack):
    radio, url, _ = stack
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url, {"command": "launch_the_missiles"})
    assert exc.value.code == 400
    assert radio.received == []


def test_raw_cat_is_refused_by_default(stack):
    """A leaked secret must not become arbitrary control of the radio."""
    radio, url, _ = stack
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url, {"cat": "MN073;"})
    assert exc.value.code == 400
    assert radio.received == []


def test_wrong_path_is_not_found(stack):
    _, url, _ = stack
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url.replace("/command", "/somewhere-else"), {"command": "power_off"})
    assert exc.value.code == 404


def test_health_endpoint_needs_no_signature(stack):
    _, url, _ = stack
    with urllib.request.urlopen(url.replace("/command", "/health"), timeout=5) as response:
        assert json.loads(response.read().decode())["ok"] is True


def test_query_reports_an_unreachable_radio_without_failing(stack):
    """A K4 in standby drops its network link; that is an answer, not an error."""
    radio, url, _ = stack
    radio.stop()

    status, payload = post(url, {"command": "power_query"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["radio_reachable"] is False
    assert payload["reply"] is None


def test_power_off_to_an_unreachable_radio_is_an_error(stack):
    radio, url, _ = stack
    radio.stop()

    with pytest.raises(urllib.error.HTTPError) as exc:
        post(url, {"command": "power_off"})
    assert exc.value.code == 502
