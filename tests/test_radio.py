import pytest

from fake_k4 import FakeK4
from k4echo.radio import K4Client, RadioError, split_tokens


def test_split_tokens_keeps_only_complete_tokens():
    assert split_tokens("PS1;FA00014050000;PS") == ["PS1;", "FA00014050000;"]
    assert split_tokens("no terminator") == []
    assert split_tokens("") == []


def test_power_off_reaches_the_radio():
    with FakeK4(power_on=True) as radio:
        K4Client("127.0.0.1", radio.port, settle_seconds=0.05).send("PS0;")
        assert "PS0;" in radio.received
        assert radio.power_on is False


def test_query_returns_the_power_state():
    with FakeK4(power_on=True) as radio:
        assert K4Client("127.0.0.1", radio.port).ping() == "PS1;"

    with FakeK4(power_on=False) as radio:
        assert K4Client("127.0.0.1", radio.port).ping() == "PS0;"


def test_reply_is_found_past_unsolicited_traffic():
    """Auto-info chatter before our answer must not be mistaken for it."""

    class ChattyK4(FakeK4):
        def _dispatch(self, conn, token):
            self.received.append(token + ";")
            if token == "PS":
                conn.sendall(b"FA00014050000;AI2;PS1;")

    with ChattyK4() as radio:
        assert K4Client("127.0.0.1", radio.port).ping() == "PS1;"


def test_unreachable_radio_raises_a_clear_error():
    client = K4Client("127.0.0.1", 9, connect_timeout=0.5)
    with pytest.raises(RadioError, match="cannot reach the radio"):
        client.send("PS0;")


def test_silent_radio_times_out_rather_than_hanging():
    class SilentK4(FakeK4):
        def _dispatch(self, conn, token):
            self.received.append(token + ";")

    with SilentK4() as radio:
        client = K4Client("127.0.0.1", radio.port, reply_timeout=0.5)
        with pytest.raises(RadioError, match="did not answer"):
            client.ping()
