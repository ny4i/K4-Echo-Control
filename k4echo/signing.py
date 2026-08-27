"""HMAC-SHA256 request signing for the Lambda -> home-bridge hop.

The command payload is not secret (it says "power_off"), so the goal here is
not confidentiality -- it is *authenticity* and *freshness*.  Anyone who finds
the bridge's open port must not be able to forge a command, and must not be
able to replay one they captured earlier.

Signed string:  ``v1:{timestamp}:{nonce}:{body}``
Header form:    ``X-K4-Signature: v1=<hex digest>``

The bridge rejects a request whose timestamp is outside ``max_skew`` seconds of
its own clock, and rejects a nonce it has already seen inside that window.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Dict, Tuple

SIGNATURE_VERSION = "v1"
HEADER_SIGNATURE = "X-K4-Signature"
HEADER_TIMESTAMP = "X-K4-Timestamp"
HEADER_NONCE = "X-K4-Nonce"

DEFAULT_MAX_SKEW_SECONDS = 300


class SignatureError(Exception):
    """Raised when a request cannot be authenticated."""


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def signing_string(timestamp: int, nonce: str, body: bytes) -> bytes:
    """Build the exact byte string that gets HMAC'd."""
    prefix = "{}:{}:{}:".format(SIGNATURE_VERSION, int(timestamp), nonce)
    return prefix.encode("utf-8") + _as_bytes(body)


def compute_signature(secret: str, timestamp: int, nonce: str, body: bytes) -> str:
    """Return the ``v1=<hex>`` signature header value for a request."""
    digest = hmac.new(
        _as_bytes(secret),
        signing_string(timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()
    return "{}={}".format(SIGNATURE_VERSION, digest)


def sign_request(secret: str, body: bytes, timestamp: int = None, nonce: str = None) -> Dict[str, str]:
    """Return the full set of auth headers for ``body``."""
    ts = int(time.time()) if timestamp is None else int(timestamp)
    nnc = uuid.uuid4().hex if nonce is None else nonce
    return {
        HEADER_TIMESTAMP: str(ts),
        HEADER_NONCE: nnc,
        HEADER_SIGNATURE: compute_signature(secret, ts, nnc, body),
    }


def verify_request(
    secret: str,
    headers: Dict[str, str],
    body: bytes,
    max_skew: int = DEFAULT_MAX_SKEW_SECONDS,
    now: float = None,
) -> Tuple[int, str]:
    """Validate signature and freshness.

    Returns ``(timestamp, nonce)`` so the caller can record the nonce against
    replay.  Raises :class:`SignatureError` on any failure.
    """
    lookup = {k.lower(): v for k, v in (headers or {}).items()}

    raw_ts = lookup.get(HEADER_TIMESTAMP.lower())
    nonce = lookup.get(HEADER_NONCE.lower())
    presented = lookup.get(HEADER_SIGNATURE.lower())

    if not raw_ts or not nonce or not presented:
        raise SignatureError("missing signature headers")

    try:
        timestamp = int(raw_ts)
    except (TypeError, ValueError):
        raise SignatureError("malformed timestamp")

    current = time.time() if now is None else now
    if abs(current - timestamp) > max_skew:
        raise SignatureError("timestamp outside accepted window")

    expected = compute_signature(secret, timestamp, nonce, body)
    if not hmac.compare_digest(expected, presented):
        raise SignatureError("signature mismatch")

    return timestamp, nonce


class ReplayGuard:
    """Remembers recently accepted nonces so a captured request is single-use."""

    def __init__(self, window_seconds: int = DEFAULT_MAX_SKEW_SECONDS * 2):
        self._window = window_seconds
        self._seen: Dict[str, float] = {}

    def check_and_record(self, nonce: str, now: float = None) -> None:
        current = time.time() if now is None else now
        self._prune(current)
        if nonce in self._seen:
            raise SignatureError("nonce already used")
        self._seen[nonce] = current

    def _prune(self, current: float) -> None:
        cutoff = current - self._window
        for key in [k for k, seen_at in self._seen.items() if seen_at < cutoff]:
            del self._seen[key]
