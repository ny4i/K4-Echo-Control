"""Alexa-facing behaviour of the Lambda, including a full end-to-end path."""

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import lambda_function
from fake_k4 import FakeK4
from k4echo import signing
from k4echo.bridge import CommandExecutor, make_webhook_handler
from k4echo.config import BridgeConfig, RadioConfig, WebhookConfig

SKILL_ID = "amzn1.ask.skill.11111111-2222-3333-4444-555555555555"
SECRET = "e" * 48


def alexa_event(request, skill_id=SKILL_ID):
    return {
        "version": "1.0",
        "session": {"application": {"applicationId": skill_id}},
        "context": {"System": {"application": {"applicationId": skill_id}}},
        "request": request,
    }


def intent_event(name, skill_id=SKILL_ID):
    return alexa_event(
        {"type": "IntentRequest", "requestId": "amzn1.echo-api.request.abc", "intent": {"name": name}},
        skill_id=skill_id,
    )


def speech(response):
    return response["response"]["outputSpeech"]["text"]


@pytest.fixture
def live_stack(monkeypatch):
    """Lambda -> signed webhook -> bridge -> radio, all really running."""
    radio = FakeK4(power_on=True).start()
    config = BridgeConfig(
        transport="webhook",
        radio=RadioConfig(host="127.0.0.1", port=radio.port, reply_timeout=1.0),
        webhook=WebhookConfig(bind="127.0.0.1", port=0, secret=SECRET),
    )
    executor = CommandExecutor(config)
    executor.client.settle_seconds = 0.05

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_webhook_handler(config, executor, signing.ReplayGuard())
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()

    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    monkeypatch.setenv("K4_TRANSPORT", "webhook")
    monkeypatch.setenv("K4_BRIDGE_SECRET", SECRET)
    monkeypatch.setenv(
        "K4_BRIDGE_URL", "http://127.0.0.1:{}/command".format(server.server_address[1])
    )

    try:
        yield radio
    finally:
        server.shutdown()
        server.server_close()
        radio.stop()


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------


def test_saying_turn_off_the_radio_sends_ps0_to_the_k4(live_stack):
    radio = live_stack
    response = lambda_function.lambda_handler(intent_event("PowerOffIntent"), None)

    assert "PS0;" in radio.received
    assert radio.power_on is False
    assert speech(response) == "Turning the K four off."
    assert response["response"]["shouldEndSession"] is True


def test_turn_on_sends_ps1(live_stack):
    radio = live_stack
    radio.power_on = False
    lambda_function.lambda_handler(intent_event("PowerOnIntent"), None)

    assert "PS1;" in radio.received
    assert radio.power_on is True


def test_status_intent_reports_the_real_radio_state(live_stack):
    radio = live_stack
    assert speech(lambda_function.lambda_handler(intent_event("PowerStatusIntent"), None)) == (
        "The K four is on."
    )

    radio.power_on = False
    assert speech(lambda_function.lambda_handler(intent_event("PowerStatusIntent"), None)) == (
        "The K four is in standby."
    )


def test_status_explains_an_unreachable_radio_rather_than_erroring(live_stack):
    live_stack.stop()
    assert "standby" in speech(lambda_function.lambda_handler(intent_event("PowerStatusIntent"), None))


def test_a_down_bridge_produces_a_spoken_apology_not_a_crash(live_stack, monkeypatch):
    monkeypatch.setenv("K4_BRIDGE_URL", "http://127.0.0.1:9/command")
    response = lambda_function.lambda_handler(intent_event("PowerOffIntent"), None)

    assert "couldn't reach the radio" in speech(response)
    assert response["version"] == "1.0"


def test_a_dead_radio_is_reported_when_powering_off(live_stack):
    live_stack.stop()
    assert "couldn't reach the radio" in speech(
        lambda_function.lambda_handler(intent_event("PowerOffIntent"), None)
    )


# --------------------------------------------------------------------------
# Skill plumbing
# --------------------------------------------------------------------------


def test_a_request_from_another_skill_is_refused(live_stack):
    radio = live_stack
    response = lambda_function.lambda_handler(
        intent_event("PowerOffIntent", skill_id="amzn1.ask.skill.someone-else"), None
    )

    assert radio.received == []
    assert "did not come from" in speech(response)


def test_launch_request_keeps_the_session_open(monkeypatch):
    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    response = lambda_function.lambda_handler(alexa_event({"type": "LaunchRequest"}), None)

    assert response["response"]["shouldEndSession"] is False
    assert "reprompt" in response["response"]


def test_help_intent_lists_what_you_can_say(monkeypatch):
    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    text = speech(lambda_function.lambda_handler(intent_event("AMAZON.HelpIntent"), None))

    assert "turn off the radio" in text


@pytest.mark.parametrize(
    "intent", ["AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"]
)
def test_exit_intents_end_the_session(monkeypatch, intent):
    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    response = lambda_function.lambda_handler(intent_event(intent), None)

    assert response["response"]["shouldEndSession"] is True


def test_fallback_intent_reprompts_without_touching_the_radio(live_stack):
    radio = live_stack
    response = lambda_function.lambda_handler(intent_event("AMAZON.FallbackIntent"), None)

    assert radio.received == []
    assert response["response"]["shouldEndSession"] is False


def test_session_ended_request_is_accepted(monkeypatch):
    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    response = lambda_function.lambda_handler(alexa_event({"type": "SessionEndedRequest"}), None)

    assert response["version"] == "1.0"


def test_every_response_is_a_valid_alexa_envelope(live_stack):
    for intent in ("PowerOffIntent", "AMAZON.HelpIntent", "AMAZON.FallbackIntent"):
        response = lambda_function.lambda_handler(intent_event(intent), None)
        json.dumps(response)  # must be serialisable
        assert response["version"] == "1.0"
        assert response["response"]["outputSpeech"]["type"] == "PlainText"
        assert isinstance(response["response"]["shouldEndSession"], bool)
