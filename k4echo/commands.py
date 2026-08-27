"""Catalog of Elecraft K4 CAT commands exposed by the skill.

The Lambda never sends a raw CAT string to the bridge by default; it sends a
symbolic command *name* and the bridge resolves it against its own copy of this
catalog.  That way a leaked signing secret cannot be used to drive arbitrary
CAT commands into the radio -- an attacker is limited to the verbs below.

Elecraft CAT framing: ASCII, each command terminated with ';'.  ``PS`` is the
power-state command -- ``PS0;`` standby, ``PS1;`` on, ``PS;`` query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class K4Command:
    """One voice-addressable radio command."""

    name: str
    cat: str
    expects_reply: bool
    speech: str


POWER_OFF = K4Command(
    name="power_off",
    cat="PS0;",
    expects_reply=False,
    speech="Turning the K four off.",
)

POWER_ON = K4Command(
    name="power_on",
    cat="PS1;",
    expects_reply=False,
    speech="Turning the K four on.",
)

POWER_QUERY = K4Command(
    name="power_query",
    cat="PS;",
    expects_reply=True,
    speech="Checking the K four.",
)

CATALOG: Dict[str, K4Command] = {
    cmd.name: cmd for cmd in (POWER_OFF, POWER_ON, POWER_QUERY)
}


def lookup(name: str) -> Optional[K4Command]:
    """Return the command registered under ``name``, or None if unknown."""
    return CATALOG.get((name or "").strip().lower())


def describe_power_reply(reply: str) -> str:
    """Turn a ``PS;`` response into something Alexa can say.

    The radio answers ``PS1;`` (on) or ``PS0;`` (standby).  Anything else is
    reported verbatim so an odd reply is visible rather than silently mapped.
    """
    token = (reply or "").strip().upper()
    if token.startswith("PS1"):
        return "The K four is on."
    if token.startswith("PS0"):
        return "The K four is in standby."
    if not token:
        return "The radio did not answer the power query."
    return "The radio answered {}.".format(token.rstrip(";"))
