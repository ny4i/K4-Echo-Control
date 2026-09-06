"""Maps an Alexa speaker to the bridge that belongs to them.

Single-tenant deployments never touch this module: the Lambda addresses the one
thing named by ``K4_IOT_THING_NAME`` and there is nothing to resolve.  When one
skill serves several operators, every request has to be steered to *that
speaker's own* radio, and this is where that happens.

Treat the lookup as a security boundary rather than a convenience.  A shared
deployment's Lambda necessarily holds an IAM policy covering every bridge's
topic, so the only thing standing between one operator's voice and another
operator's transmitter is the answer this module returns.  Two rules follow,
and both are load-bearing:

* **No default and no fallback.**  An unknown speaker is refused outright.  A
  resolution failure must never degrade into "use the configured thing", which
  in a shared deployment means "key whichever radio happens to be first".
* **Every resolution is logged**, so a misroute is discoverable afterwards
  instead of theoretical.  The speaker is recorded as a stable fingerprint
  rather than the raw Alexa user id, which keeps the logs correlatable without
  writing an account identifier into them.

Identity comes from ``session.user.userId``, which Alexa scopes to this skill.
It is not permanent -- disabling and re-enabling the skill mints a new one --
so pairing codes stay valid for re-use rather than burning on first contact.
That way re-pairing costs the operator one sentence instead of an email.

Lambda-side only.  It must not import :mod:`k4echo.radio`, :mod:`k4echo.config`
or :mod:`k4echo.bridge`, or the deployment zip stops importing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Any, Dict, Optional

LOG = logging.getLogger(__name__)

DEFAULT_PAIRINGS_TABLE = "k4echo-pairings"
DEFAULT_CODES_TABLE = "k4echo-codes"

CODE_LENGTH = 6


class RegistryError(Exception):
    """The registry could not be consulted at all."""


class UnknownSpeaker(RegistryError):
    """No radio is paired with this Alexa user."""


class UnknownCode(RegistryError):
    """The spoken pairing code matches no bridge."""


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def fingerprint(user_id: str) -> str:
    """A short, stable, non-reversible handle for one Alexa user.

    Logs need to be correlatable -- "which speaker did this command come
    from" -- without carrying the account identifier itself.
    """
    return hashlib.sha256((user_id or "").encode("utf-8")).hexdigest()[:12]


def normalise_code(spoken: Any) -> str:
    """Reduce whatever Alexa heard to bare digits.

    A number slot may arrive as ``"482913"`` or as ``"4 8 2 9 1 3"`` depending
    on how the speaker paced it, and either is the same code.

    Note what this deliberately does *not* do: recover a lost leading zero.
    ``AMAZON.NUMBER`` yields a number, so a code of ``048291`` reaches us as
    ``"48291"`` and is rejected as the wrong length. Padding it back would mean
    guessing, and guessing here binds someone to a bridge. ``enroll_operator.sh``
    therefore never issues a code starting with zero.
    """
    return re.sub(r"\D", "", str(spoken or ""))


class Registry:
    """DynamoDB-backed pairing table.

    Two tables, both keyed by a single string attribute:

    ``k4echo-pairings``   ``user_id``  -> ``thing_name``, ``paired_at``
    ``k4echo-codes``      ``code``     -> ``thing_name``, ``owner_note``

    The client is injectable so the resolution logic can be tested without
    standing up DynamoDB -- worth doing, since a bug here has consequences at
    the far end of somebody's coax.
    """

    def __init__(
        self,
        pairings_table: Optional[str] = None,
        codes_table: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self.pairings_table = pairings_table or _env("K4_PAIRINGS_TABLE", DEFAULT_PAIRINGS_TABLE)
        self.codes_table = codes_table or _env("K4_CODES_TABLE", DEFAULT_CODES_TABLE)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # provided by the Lambda runtime

            self._client = boto3.client("dynamodb")
        return self._client

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve_thing(self, user_id: Optional[str]) -> str:
        """Return the thing name paired with ``user_id``.

        Raises :class:`UnknownSpeaker` when there is no pairing.  It never
        returns a default -- see the module docstring.
        """
        if not user_id:
            raise UnknownSpeaker("the request carried no Alexa user id")

        item = self._get(self.pairings_table, {"user_id": {"S": user_id}})
        if not item:
            LOG.info("no pairing for speaker %s", fingerprint(user_id))
            raise UnknownSpeaker("no radio is paired with this Alexa account")

        thing = (item.get("thing_name") or {}).get("S")
        if not thing:
            # A malformed row is a bug, not an unpaired user; do not guess.
            raise RegistryError(
                "pairing row for speaker {} has no thing_name".format(fingerprint(user_id))
            )

        LOG.info("resolved speaker %s -> thing %s", fingerprint(user_id), thing)
        return thing

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def bind(self, spoken_code: Any, user_id: Optional[str]) -> str:
        """Point ``user_id`` at whichever bridge owns ``spoken_code``.

        Returns the thing name now paired.  Re-binding is allowed and simply
        overwrites, which is what makes recovery from a disable/re-enable a
        single spoken sentence.
        """
        if not user_id:
            raise UnknownSpeaker("the request carried no Alexa user id")

        code = normalise_code(spoken_code)
        if len(code) != CODE_LENGTH:
            raise UnknownCode(
                "a pairing code is {} digits, heard {}".format(CODE_LENGTH, len(code) or "none")
            )

        item = self._get(self.codes_table, {"code": {"S": code}})
        if not item:
            LOG.info("speaker %s offered an unknown pairing code", fingerprint(user_id))
            raise UnknownCode("that pairing code is not registered")

        thing = (item.get("thing_name") or {}).get("S")
        if not thing:
            raise RegistryError("pairing code row has no thing_name")

        try:
            self.client.put_item(
                TableName=self.pairings_table,
                Item={
                    "user_id": {"S": user_id},
                    "thing_name": {"S": thing},
                    "paired_at": {"N": str(int(time.time()))},
                },
            )
        except Exception as exc:  # boto3 raises service-specific errors
            raise RegistryError("could not record the pairing ({})".format(exc)) from exc

        LOG.info("paired speaker %s -> thing %s", fingerprint(user_id), thing)
        return thing

    # ------------------------------------------------------------------

    def _get(self, table: str, key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.get_item(TableName=table, Key=key, ConsistentRead=True)
        except Exception as exc:  # boto3 raises service-specific errors
            raise RegistryError("could not read {} ({})".format(table, exc)) from exc
        return response.get("Item") or None


def multi_tenant_enabled() -> bool:
    """Whether this deployment serves more than one operator.

    Read per call rather than at import, so a single Lambda image can be
    flipped by configuration and so tests can toggle it.
    """
    return (_env("K4_MULTI_TENANT", "") or "").strip().lower() in ("1", "true", "yes", "on")
