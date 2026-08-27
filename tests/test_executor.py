"""The bridge's command executor, independent of any transport."""

import pytest

from fake_k4 import FakeK4
from k4echo.bridge import CommandExecutor
from k4echo.config import BridgeConfig, RadioConfig


def executor_for(radio, **kwargs):
    config = BridgeConfig(
        radio=RadioConfig(host="127.0.0.1", port=radio.port, reply_timeout=1.0), **kwargs
    )
    executor = CommandExecutor(config)
    executor.client.settle_seconds = 0.05
    return executor


def test_a_name_is_resolved_to_its_cat_string():
    with FakeK4() as radio:
        result = executor_for(radio).execute({"command": "power_off"})

    assert result["cat"] == "PS0;"
    assert result["ok"] is True


def test_an_unknown_name_is_refused_before_touching_the_radio():
    with FakeK4() as radio:
        with pytest.raises(ValueError, match="unknown command"):
            executor_for(radio).execute({"command": "self_destruct"})
        assert radio.received == []


def test_an_empty_payload_is_refused():
    with FakeK4() as radio:
        with pytest.raises(ValueError, match="no 'command' field"):
            executor_for(radio).execute({})


def test_raw_cat_is_refused_unless_explicitly_enabled():
    with FakeK4() as radio:
        with pytest.raises(ValueError, match="raw CAT strings are disabled"):
            executor_for(radio).execute({"cat": "FA00014050000;"})
        assert radio.received == []


def test_raw_cat_works_when_the_operator_opts_in():
    with FakeK4() as radio:
        executor_for(radio, allow_raw_cat=True).execute({"cat": "FA00014050000;"})
        assert "FA00014050000;" in radio.received


def test_power_state_snapshot_for_a_live_radio():
    with FakeK4(power_on=True) as radio:
        state = executor_for(radio).power_state()

    assert state["power"] == "on"
    assert state["power_reply"] == "PS1;"
    assert state["radio_reachable"] is True
    assert state["radio_host"].endswith(str(radio.port))
    assert isinstance(state["updated_at"], int)


def test_power_state_snapshot_for_a_radio_in_standby():
    with FakeK4(power_on=False) as radio:
        assert executor_for(radio).power_state()["power"] == "standby"


def test_power_state_snapshot_when_the_radio_is_gone():
    radio = FakeK4().start()
    executor = executor_for(radio)
    radio.stop()

    state = executor.power_state()
    assert state["power"] == "unknown"
    assert state["radio_reachable"] is False
    assert state["power_reply"] is None
