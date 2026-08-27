"""Lambda-side transport behaviour, with AWS stood in for by fakes."""

import io
import json
import time

import pytest

from k4echo import commands
from k4echo.transports import IotTransport, TransportError, WebhookTransport, build_transport


class FakeIotClient:
    """Stands in for boto3's ``iot-data`` client."""

    def __init__(self, shadow=None, publish_error=None):
        self.published = []
        self._shadow = shadow
        self._publish_error = publish_error

    def publish(self, topic, qos, payload):
        if self._publish_error:
            raise self._publish_error
        self.published.append({"topic": topic, "qos": qos, "payload": json.loads(payload)})

    def get_thing_shadow(self, thingName):  # noqa: N803 - boto3's parameter name
        if self._shadow is None:
            raise RuntimeError("no shadow for thing {}".format(thingName))
        return {"payload": io.BytesIO(json.dumps(self._shadow).encode())}


def shadow(power="on", reply="PS1;", reachable=True, age=0):
    return {
        "state": {
            "reported": {
                "power": power,
                "power_reply": reply,
                "radio_reachable": reachable,
                "updated_at": int(time.time()) - age,
            }
        }
    }


def test_power_off_is_published_to_the_command_topic():
    client = FakeIotClient()
    transport = IotTransport(thing_name="k4-shack", client=client)

    result = transport.execute(commands.POWER_OFF, request_id="req-1")

    assert len(client.published) == 1
    message = client.published[0]
    assert message["topic"] == "k4echo/k4-shack/cmd"
    assert message["qos"] == 1
    assert message["payload"]["command"] == "power_off"
    assert message["payload"]["request_id"] == "req-1"
    assert result.synchronous is False


def test_the_raw_cat_string_is_never_put_on_the_wire():
    """The bridge resolves names itself, so a topic subscriber learns nothing."""
    client = FakeIotClient()
    IotTransport(thing_name="k4-shack", client=client).execute(commands.POWER_OFF)

    assert "PS0;" not in json.dumps(client.published)


def test_a_publish_failure_becomes_a_transport_error():
    client = FakeIotClient(publish_error=RuntimeError("throttled"))
    with pytest.raises(TransportError, match="could not publish"):
        IotTransport(thing_name="k4-shack", client=client).execute(commands.POWER_OFF)


def test_status_is_read_from_the_device_shadow():
    client = FakeIotClient(shadow=shadow(reply="PS1;"))
    result = IotTransport(thing_name="k4-shack", client=client).execute(commands.POWER_QUERY)

    assert client.published == []  # a query must not command the radio
    assert result.reply == "PS1;"
    assert result.radio_reachable is True


def test_a_stale_shadow_is_reported_rather_than_believed():
    client = FakeIotClient(shadow=shadow(age=99999))
    transport = IotTransport(thing_name="k4-shack", client=client)

    with pytest.raises(TransportError, match="has not reported in"):
        transport.execute(commands.POWER_QUERY)


def test_shadow_marks_an_unreachable_radio():
    client = FakeIotClient(shadow=shadow(power="unknown", reply=None, reachable=False))
    result = IotTransport(thing_name="k4-shack", client=client).execute(commands.POWER_QUERY)

    assert result.radio_reachable is False


def test_a_missing_shadow_becomes_a_transport_error():
    transport = IotTransport(thing_name="k4-shack", client=FakeIotClient(shadow=None))
    with pytest.raises(TransportError, match="could not read"):
        transport.execute(commands.POWER_QUERY)


def test_transport_is_selected_by_environment(monkeypatch):
    monkeypatch.setenv("K4_IOT_THING_NAME", "k4-shack")
    monkeypatch.setenv("K4_TRANSPORT", "iot")
    assert isinstance(build_transport(), IotTransport)

    monkeypatch.setenv("K4_TRANSPORT", "webhook")
    monkeypatch.setenv("K4_BRIDGE_URL", "http://192.168.1.9:8443/command")
    monkeypatch.setenv("K4_BRIDGE_SECRET", "z" * 48)
    assert isinstance(build_transport(), WebhookTransport)


def test_iot_is_the_default_transport(monkeypatch):
    monkeypatch.delenv("K4_TRANSPORT", raising=False)
    monkeypatch.setenv("K4_IOT_THING_NAME", "k4-shack")
    assert isinstance(build_transport(), IotTransport)


def test_an_unknown_transport_is_refused(monkeypatch):
    monkeypatch.setenv("K4_TRANSPORT", "carrier-pigeon")
    with pytest.raises(TransportError, match="unknown transport"):
        build_transport()


def test_webhook_transport_needs_its_url(monkeypatch):
    monkeypatch.setenv("K4_TRANSPORT", "webhook")
    monkeypatch.delenv("K4_BRIDGE_URL", raising=False)
    with pytest.raises(TransportError, match="K4_BRIDGE_URL"):
        build_transport()
