"""Alexa custom-skill request parsing and response building.

Kept free of any radio or transport knowledge so the speech layer can be
tested on its own.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SKILL_NAME = "K4 Control"


class SkillIdMismatch(Exception):
    """Raised when a request does not come from our own skill."""


def verify_application_id(event: Dict[str, Any], expected: Optional[str]) -> None:
    """Reject requests from any skill other than ours.

    Alexa-hosted Lambdas are invoked over a public-ish trigger, so a Lambda
    that skips this check can be driven by anyone who learns its ARN.  When
    ``expected`` is unset we skip the check but the caller is expected to warn.
    """
    if not expected:
        return

    presented = (
        event.get("session", {}).get("application", {}).get("applicationId")
        or event.get("context", {}).get("System", {}).get("application", {}).get("applicationId")
    )
    if presented != expected:
        raise SkillIdMismatch(
            "request application id {!r} does not match the configured skill id".format(presented)
        )


def request_type(event: Dict[str, Any]) -> str:
    return event.get("request", {}).get("type", "")


def intent_name(event: Dict[str, Any]) -> str:
    return event.get("request", {}).get("intent", {}).get("name", "")


def slot_value(event: Dict[str, Any], slot: str) -> Optional[str]:
    slots = event.get("request", {}).get("intent", {}).get("slots", {}) or {}
    entry = slots.get(slot) or {}
    return entry.get("value")


def respond(
    speech: str,
    end_session: bool = True,
    reprompt: Optional[str] = None,
    card_title: Optional[str] = None,
    card_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a well-formed Alexa response envelope."""
    response: Dict[str, Any] = {
        "outputSpeech": {"type": "PlainText", "text": speech},
        "shouldEndSession": end_session,
    }

    if reprompt:
        response["reprompt"] = {
            "outputSpeech": {"type": "PlainText", "text": reprompt}
        }

    if card_title:
        response["card"] = {
            "type": "Simple",
            "title": card_title,
            "content": card_text or speech,
        }

    return {"version": "1.0", "response": response}
