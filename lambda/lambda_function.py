"""AWS Lambda entry point for the K4 Control Alexa skill.

Alexa invokes this function; it validates the request, maps the spoken intent
to a command from the allow-list, and hands that command to the bridge running
on the home network.  It never opens a socket to the radio itself.

Configuration is entirely by environment variable -- see docs/SETUP.md.
"""

from __future__ import annotations

import logging
import os

from k4echo import alexa, commands, registry
from k4echo.transports import BridgeResult, TransportError, build_transport

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("K4_LOG_LEVEL", "INFO").upper())

HELP_TEXT = (
    "You can say: turn off the radio, turn on the radio, or is the radio on. "
    "What would you like to do?"
)
LAUNCH_TEXT = "K four control ready. You can say turn off the radio."
GOODBYE_TEXT = "Seventy three."

INTENT_TO_COMMAND = {
    "PowerOffIntent": commands.POWER_OFF,
    "PowerOnIntent": commands.POWER_ON,
    "PowerStatusIntent": commands.POWER_QUERY,
}

EXIT_INTENTS = {"AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"}

PAIR_INTENT = "PairIntent"

UNPAIRED_TEXT = (
    "This skill isn't paired with a radio yet. "
    "Say: pair, followed by your six digit code."
)
PAIRED_TEXT = "Paired. You can now say, turn off the radio."
BAD_CODE_TEXT = (
    "I didn't recognise that pairing code. It's six digits. "
    "Say pair, followed by the code."
)
PAIRING_DISABLED_TEXT = "This skill is set up for a single radio, so there is nothing to pair."
REGISTRY_FAULT_TEXT = "I couldn't look up which radio is yours. Please try again shortly."

# Built lazily and cached across invocations, like the boto3 clients.
_REGISTRY = None


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = registry.Registry()
    return _REGISTRY


def lambda_handler(event, context):
    """Alexa Skills Kit handler."""
    try:
        alexa.verify_application_id(event, os.environ.get("K4_SKILL_ID"))
    except alexa.SkillIdMismatch as exc:
        LOG.warning("rejecting request: %s", exc)
        return alexa.respond("This request did not come from the K four control skill.")

    if not os.environ.get("K4_SKILL_ID"):
        LOG.warning("K4_SKILL_ID is not set -- any skill can invoke this function")

    kind = alexa.request_type(event)
    LOG.info("request type=%s intent=%s", kind, alexa.intent_name(event))

    if kind == "LaunchRequest":
        return alexa.respond(LAUNCH_TEXT, end_session=False, reprompt=HELP_TEXT)

    if kind == "SessionEndedRequest":
        return alexa.respond("")

    if kind != "IntentRequest":
        return alexa.respond(HELP_TEXT, end_session=False, reprompt=HELP_TEXT)

    return _handle_intent(event)


def _handle_intent(event):
    name = alexa.intent_name(event)

    if name == "AMAZON.HelpIntent":
        return alexa.respond(HELP_TEXT, end_session=False, reprompt=HELP_TEXT)

    if name in EXIT_INTENTS:
        return alexa.respond(GOODBYE_TEXT)

    if name == PAIR_INTENT:
        return _handle_pair(event)

    command = INTENT_TO_COMMAND.get(name)
    if command is None:
        # Covers AMAZON.FallbackIntent and anything unmapped.
        return alexa.respond(
            "Sorry, I didn't catch that. " + HELP_TEXT, end_session=False, reprompt=HELP_TEXT
        )

    try:
        thing_name = _target_thing(event)
    except registry.UnknownSpeaker:
        return alexa.respond(UNPAIRED_TEXT, end_session=False, reprompt=UNPAIRED_TEXT)
    except registry.RegistryError as exc:
        LOG.error("registry lookup failed: %s", exc)
        return alexa.respond(REGISTRY_FAULT_TEXT)

    request_id = event.get("request", {}).get("requestId")
    return _run_command(command, request_id, thing_name=thing_name)


def _target_thing(event):
    """Which operator's bridge this request is for.

    ``None`` in a private deployment, where the transport falls back to the one
    thing named in the environment.  In a shared deployment an unresolved
    speaker raises -- there is deliberately no default, because defaulting here
    would aim somebody's voice at a stranger's transmitter.
    """
    if not registry.multi_tenant_enabled():
        return None
    return _registry().resolve_thing(alexa.user_id(event))


def _handle_pair(event):
    """Bind this Alexa user to the bridge holding the spoken code."""
    if not registry.multi_tenant_enabled():
        return alexa.respond(PAIRING_DISABLED_TEXT)

    spoken = alexa.slot_value(event, "code")

    try:
        thing_name = _registry().bind(spoken, alexa.user_id(event))
    except registry.UnknownCode as exc:
        LOG.info("pairing refused: %s", exc)
        return alexa.respond(BAD_CODE_TEXT, end_session=False, reprompt=BAD_CODE_TEXT)
    except registry.RegistryError as exc:
        LOG.error("pairing failed: %s", exc)
        return alexa.respond(REGISTRY_FAULT_TEXT)

    return alexa.respond(
        PAIRED_TEXT,
        card_title=alexa.SKILL_NAME,
        card_text="Paired with bridge {}.".format(thing_name),
    )


def _run_command(command, request_id=None, thing_name=None):
    try:
        transport = build_transport(thing_name=thing_name)
        result = transport.execute(command, request_id=request_id)
    except TransportError as exc:
        LOG.error("command %s failed: %s", command.name, exc)
        return alexa.respond(
            "I couldn't reach the radio. {}".format(_friendly(str(exc))),
            card_title=alexa.SKILL_NAME,
            card_text="Command {} failed: {}".format(command.cat, exc),
        )
    except Exception as exc:  # noqa: BLE001 - never hand Alexa a raw stack trace
        LOG.exception("unexpected failure running %s", command.name)
        return alexa.respond(
            "Something went wrong sending that to the radio.",
            card_title=alexa.SKILL_NAME,
            card_text="Command {} failed: {}".format(command.cat, exc),
        )

    return alexa.respond(
        _speech_for(command, result),
        card_title=alexa.SKILL_NAME,
        card_text="Sent {} to the K4.".format(command.cat),
    )


def _speech_for(command, result: BridgeResult) -> str:
    if command.expects_reply:
        if not result.radio_reachable:
            # A K4 in standby takes its network interface down with it, so an
            # unreachable radio is the normal answer for "is it on?".
            return "I can't reach the K four, so it's most likely in standby."
        return commands.describe_power_reply(result.reply or "")

    if result.synchronous:
        return command.speech

    # Fire-and-forget transports cannot confirm the radio acted on it.
    return command.speech.rstrip(".") + ", command sent."


def _friendly(message: str) -> str:
    """Make a transport error safe and pleasant to speak aloud."""
    cleaned = message.strip().rstrip(".")
    if not cleaned:
        return "Please check the home bridge."
    return cleaned[0].upper() + cleaned[1:] + "."
