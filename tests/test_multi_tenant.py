"""One skill serving several operators.

The behaviour worth protecting here is negative: when the Lambda cannot work
out whose radio a request belongs to, it must send *nothing*. A shared
deployment's IAM policy covers every bridge, so a fallback would not fail
safe -- it would key a stranger's transmitter.
"""

import pytest

import lambda_function
from k4echo import commands, registry
from k4echo.transports import BridgeResult

SKILL_ID = "amzn1.ask.skill.11111111-2222-3333-4444-555555555555"


class RecordingTransport:
    """Captures what would have gone to a bridge."""

    def __init__(self, thing_name):
        self.thing_name = thing_name
        self.executed = []

    def execute(self, command, request_id=None):
        self.executed.append(command.name)
        return BridgeResult(ok=True, detail="", reply="PS1;", synchronous=False)


class TransportSpy:
    """Stands in for ``build_transport`` and remembers who was addressed."""

    def __init__(self):
        self.built = []

    def __call__(self, kind=None, thing_name=None):
        transport = RecordingTransport(thing_name)
        self.built.append(transport)
        return transport

    @property
    def things(self):
        return [t.thing_name for t in self.built]

    @property
    def commands_sent(self):
        return [name for t in self.built for name in t.executed]


class FakeRegistry:
    def __init__(self, pairings=None, codes=None, fault=None):
        self.pairings = dict(pairings or {})
        self.codes = dict(codes or {})
        self.fault = fault

    def resolve_thing(self, user_id):
        if self.fault:
            raise self.fault
        if not user_id or user_id not in self.pairings:
            raise registry.UnknownSpeaker("not paired")
        return self.pairings[user_id]

    def bind(self, spoken_code, user_id):
        if self.fault:
            raise self.fault
        code = registry.normalise_code(spoken_code)
        if code not in self.codes:
            raise registry.UnknownCode("no such code")
        self.pairings[user_id] = self.codes[code]
        return self.codes[code]


def intent_event(name, user="amzn1.user.alice", slots=None):
    intent = {"name": name}
    if slots:
        intent["slots"] = {k: {"name": k, "value": v} for k, v in slots.items()}
    return {
        "version": "1.0",
        "session": {
            "application": {"applicationId": SKILL_ID},
            "user": {"userId": user},
        },
        "context": {"System": {"application": {"applicationId": SKILL_ID}}},
        "request": {"type": "IntentRequest", "requestId": "amzn1.echo-api.request.abc", "intent": intent},
    }


def speech(response):
    return response["response"]["outputSpeech"]["text"]


@pytest.fixture
def shared(monkeypatch):
    """A multi-tenant deployment with two operators paired."""
    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    monkeypatch.setenv("K4_MULTI_TENANT", "1")

    spy = TransportSpy()
    monkeypatch.setattr(lambda_function, "build_transport", spy)

    reg = FakeRegistry(
        pairings={"amzn1.user.alice": "k4-alice-bridge", "amzn1.user.bob": "k4-bob-bridge"},
        codes={"482913": "k4-carol-bridge"},
    )
    monkeypatch.setattr(lambda_function, "_REGISTRY", reg)
    return spy, reg


@pytest.fixture
def private(monkeypatch):
    """A single-operator deployment, i.e. how it shipped before."""
    monkeypatch.setenv("K4_SKILL_ID", SKILL_ID)
    monkeypatch.delenv("K4_MULTI_TENANT", raising=False)

    spy = TransportSpy()
    monkeypatch.setattr(lambda_function, "build_transport", spy)
    monkeypatch.setattr(lambda_function, "_REGISTRY", None)
    return spy


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_a_command_goes_to_the_speakers_own_bridge(shared):
    spy, _ = shared
    lambda_function.lambda_handler(intent_event("PowerOffIntent", user="amzn1.user.alice"), None)
    assert spy.things == ["k4-alice-bridge"]
    assert spy.commands_sent == ["power_off"]


def test_two_speakers_reach_two_different_bridges(shared):
    spy, _ = shared
    lambda_function.lambda_handler(intent_event("PowerOffIntent", user="amzn1.user.alice"), None)
    lambda_function.lambda_handler(intent_event("PowerOffIntent", user="amzn1.user.bob"), None)
    assert spy.things == ["k4-alice-bridge", "k4-bob-bridge"]


def test_an_unpaired_speaker_is_told_to_pair_and_nothing_is_sent(shared):
    spy, _ = shared
    response = lambda_function.lambda_handler(
        intent_event("PowerOffIntent", user="amzn1.user.stranger"), None
    )
    assert "pair" in speech(response).lower()
    assert spy.built == [], "an unresolved speaker must not reach any bridge"


def test_a_registry_outage_is_spoken_not_crashed(monkeypatch, shared):
    spy, reg = shared
    reg.fault = registry.RegistryError("dynamo is having a day")
    response = lambda_function.lambda_handler(intent_event("PowerOffIntent"), None)
    assert response["response"]["outputSpeech"]["text"]
    assert spy.built == []


def test_a_request_with_no_user_id_sends_nothing(shared):
    spy, _ = shared
    event = intent_event("PowerOffIntent")
    del event["session"]["user"]
    response = lambda_function.lambda_handler(event, None)
    assert "pair" in speech(response).lower()
    assert spy.built == []


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def test_pairing_binds_the_speaker_and_confirms(shared):
    spy, reg = shared
    response = lambda_function.lambda_handler(
        intent_event("PairIntent", user="amzn1.user.carol", slots={"code": "482913"}), None
    )
    assert "paired" in speech(response).lower()
    assert reg.pairings["amzn1.user.carol"] == "k4-carol-bridge"
    assert spy.built == [], "pairing should not touch a radio"


def test_a_paired_speaker_can_then_drive_their_radio(shared):
    spy, _ = shared
    lambda_function.lambda_handler(
        intent_event("PairIntent", user="amzn1.user.carol", slots={"code": "482913"}), None
    )
    lambda_function.lambda_handler(
        intent_event("PowerOffIntent", user="amzn1.user.carol"), None
    )
    assert spy.things == ["k4-carol-bridge"]


def test_a_bad_pairing_code_is_refused_and_binds_nothing(shared):
    spy, reg = shared
    response = lambda_function.lambda_handler(
        intent_event("PairIntent", user="amzn1.user.carol", slots={"code": "999999"}), None
    )
    assert "code" in speech(response).lower()
    assert "amzn1.user.carol" not in reg.pairings
    assert response["response"]["shouldEndSession"] is False


def test_a_bad_code_leaves_an_existing_pairing_alone(shared):
    _, reg = shared
    lambda_function.lambda_handler(
        intent_event("PairIntent", user="amzn1.user.alice", slots={"code": "999999"}), None
    )
    assert reg.pairings["amzn1.user.alice"] == "k4-alice-bridge"


# ---------------------------------------------------------------------------
# The private deployment must keep working exactly as before
# ---------------------------------------------------------------------------


def test_single_tenant_still_addresses_the_configured_radio(private):
    private_spy = private
    lambda_function.lambda_handler(intent_event("PowerOffIntent"), None)
    assert private_spy.things == [None], "the transport should fall back to K4_IOT_THING_NAME"
    assert private_spy.commands_sent == ["power_off"]


def test_single_tenant_ignores_the_user_id_entirely(private):
    lambda_function.lambda_handler(intent_event("PowerOffIntent", user="amzn1.user.whoever"), None)
    assert private.things == [None]


def test_pairing_is_meaningless_in_a_single_tenant_deployment(private):
    response = lambda_function.lambda_handler(
        intent_event("PairIntent", slots={"code": "482913"}), None
    )
    assert "single radio" in speech(response).lower()
    assert private.built == []


def test_the_command_catalog_is_unchanged_by_any_of_this():
    assert sorted(commands.CATALOG) == ["power_off", "power_on", "power_query"]
