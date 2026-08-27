"""AWS Lambda entry point for the K4 Control Alexa skill.

Alexa invokes this function; it validates the request, maps the spoken intent
to a command from the allow-list, and hands that command to the bridge running
on the home network.  It never opens a socket to the radio itself.

Configuration is entirely by environment variable -- see docs/SETUP.md.
"""

from __future__ import annotations

import logging
import os

from k4echo import alexa, commands
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

    command = INTENT_TO_COMMAND.get(name)
    if command is None:
        # Covers AMAZON.FallbackIntent and anything unmapped.
        return alexa.respond(
            "Sorry, I didn't catch that. " + HELP_TEXT, end_session=False, reprompt=HELP_TEXT
        )

    request_id = event.get("request", {}).get("requestId")
    return _run_command(command, request_id)


def _run_command(command, request_id=None):
    try:
        transport = build_transport()
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
