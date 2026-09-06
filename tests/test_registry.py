"""Pairing lookups -- the code that decides whose transmitter a command reaches.

These get more attention than their size suggests. In a shared deployment the
Lambda is authorised to publish to every bridge, so this lookup is the only
thing keeping one operator's voice off another operator's radio.
"""

import pytest

from k4echo import registry
from k4echo.registry import Registry, RegistryError, UnknownCode, UnknownSpeaker


class FakeDynamo:
    """Stands in for boto3's low-level ``dynamodb`` client."""

    def __init__(self, tables=None, error=None):
        self.tables = tables if tables is not None else {}
        self.error = error
        self.reads = []
        self.puts = []

    def get_item(self, TableName, Key, ConsistentRead=False):  # noqa: N803 - boto3 names
        if self.error:
            raise self.error
        self.reads.append(TableName)
        (_, typed), = Key.items()
        item = self.tables.get(TableName, {}).get(typed["S"])
        return {"Item": item} if item else {}

    def put_item(self, TableName, Item):  # noqa: N803 - boto3 names
        if self.error:
            raise self.error
        self.puts.append((TableName, Item))
        self.tables.setdefault(TableName, {})[Item["user_id"]["S"]] = Item


def make_registry(pairings=None, codes=None, error=None):
    tables = {
        "pairings": {
            user: {"user_id": {"S": user}, "thing_name": {"S": thing}}
            for user, thing in (pairings or {}).items()
        },
        "codes": {
            code: {"code": {"S": code}, "thing_name": {"S": thing}}
            for code, thing in (codes or {}).items()
        },
    }
    client = FakeDynamo(tables, error=error)
    return Registry(pairings_table="pairings", codes_table="codes", client=client), client


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_paired_speaker_resolves_to_their_own_bridge():
    reg, _ = make_registry(pairings={"amzn1.user.alice": "k4-alice-bridge"})
    assert reg.resolve_thing("amzn1.user.alice") == "k4-alice-bridge"


def test_two_speakers_resolve_to_different_bridges():
    reg, _ = make_registry(
        pairings={"amzn1.user.alice": "k4-alice-bridge", "amzn1.user.bob": "k4-bob-bridge"}
    )
    assert reg.resolve_thing("amzn1.user.alice") == "k4-alice-bridge"
    assert reg.resolve_thing("amzn1.user.bob") == "k4-bob-bridge"


def test_an_unpaired_speaker_is_refused_rather_than_defaulted():
    reg, _ = make_registry(pairings={"amzn1.user.alice": "k4-alice-bridge"})
    with pytest.raises(UnknownSpeaker):
        reg.resolve_thing("amzn1.user.stranger")


def test_a_request_with_no_user_id_is_refused():
    reg, client = make_registry(pairings={"amzn1.user.alice": "k4-alice-bridge"})
    with pytest.raises(UnknownSpeaker):
        reg.resolve_thing(None)
    assert client.reads == [], "an empty user id should not even be looked up"


def test_a_malformed_row_is_a_fault_not_an_unpaired_user():
    """Distinguishing these matters: one is 'go and pair', the other is a bug."""
    reg, client = make_registry()
    client.tables["pairings"] = {"amzn1.user.alice": {"user_id": {"S": "amzn1.user.alice"}}}
    with pytest.raises(RegistryError) as exc:
        reg.resolve_thing("amzn1.user.alice")
    assert not isinstance(exc.value, UnknownSpeaker)


def test_a_dynamo_outage_surfaces_as_a_registry_error():
    reg, _ = make_registry(error=RuntimeError("throttled"))
    with pytest.raises(RegistryError):
        reg.resolve_thing("amzn1.user.alice")


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def test_binding_records_the_pairing_and_returns_the_bridge():
    reg, client = make_registry(codes={"482913": "k4-alice-bridge"})
    assert reg.bind("482913", "amzn1.user.alice") == "k4-alice-bridge"

    table, item = client.puts[0]
    assert table == "pairings"
    assert item["user_id"]["S"] == "amzn1.user.alice"
    assert item["thing_name"]["S"] == "k4-alice-bridge"
    assert int(item["paired_at"]["N"]) > 0


def test_a_bound_speaker_then_resolves():
    reg, _ = make_registry(codes={"482913": "k4-alice-bridge"})
    reg.bind("482913", "amzn1.user.alice")
    assert reg.resolve_thing("amzn1.user.alice") == "k4-alice-bridge"


def test_an_unknown_code_is_refused():
    reg, client = make_registry(codes={"482913": "k4-alice-bridge"})
    with pytest.raises(UnknownCode):
        reg.bind("111111", "amzn1.user.alice")
    assert client.puts == []


@pytest.mark.parametrize("spoken", ["4829", "48291337", "", None, "abcdef"])
def test_a_code_of_the_wrong_length_never_reaches_the_table(spoken):
    reg, client = make_registry(codes={"482913": "k4-alice-bridge"})
    with pytest.raises(UnknownCode):
        reg.bind(spoken, "amzn1.user.alice")
    assert client.reads == []


def test_a_code_that_lost_its_leading_zero_is_refused_not_guessed():
    """AMAZON.NUMBER drops a leading zero, so 048291 arrives as 48291.

    Padding it back would mean guessing, and a wrong guess binds a speaker to
    someone else's bridge. enroll_operator.sh never issues such a code.
    """
    reg, _ = make_registry(codes={"048291": "k4-alice-bridge"})
    with pytest.raises(UnknownCode):
        reg.bind("48291", "amzn1.user.alice")


def test_digits_spoken_with_pauses_are_the_same_code():
    """Alexa may hand back '4 8 2 9 1 3' depending on how it was said."""
    reg, _ = make_registry(codes={"482913": "k4-alice-bridge"})
    assert reg.bind("4 8 2 9 1 3", "amzn1.user.alice") == "k4-alice-bridge"


def test_re_pairing_overwrites_so_a_re_enabled_skill_recovers():
    """Alexa mints a new user id when a skill is disabled and re-enabled."""
    reg, _ = make_registry(codes={"482913": "k4-alice-bridge"})
    reg.bind("482913", "amzn1.user.alice-before")
    reg.bind("482913", "amzn1.user.alice-after")
    assert reg.resolve_thing("amzn1.user.alice-after") == "k4-alice-bridge"


def test_pairing_with_no_user_id_is_refused():
    reg, client = make_registry(codes={"482913": "k4-alice-bridge"})
    with pytest.raises(UnknownSpeaker):
        reg.bind("482913", None)
    assert client.puts == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_and_hides_the_raw_identifier():
    user = "amzn1.ask.account.SOMETHINGVERYLONG"
    assert registry.fingerprint(user) == registry.fingerprint(user)
    assert user not in registry.fingerprint(user)
    assert registry.fingerprint(user) != registry.fingerprint(user + "x")


@pytest.mark.parametrize(
    "raw,expected",
    [("482913", "482913"), ("4 8 2 9 1 3", "482913"), ("48-29-13", "482913"), (482913, "482913")],
)
def test_normalise_code_reduces_to_digits(raw, expected):
    assert registry.normalise_code(raw) == expected


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("on", True),
                                            ("0", False), ("", False), ("no", False)])
def test_multi_tenant_is_read_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv("K4_MULTI_TENANT", value)
    assert registry.multi_tenant_enabled() is expected


def test_multi_tenant_is_off_when_unset(monkeypatch):
    monkeypatch.delenv("K4_MULTI_TENANT", raising=False)
    assert registry.multi_tenant_enabled() is False
