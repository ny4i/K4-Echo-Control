"""TCP client for the Elecraft K4's CAT-over-TCP interface (default port 9200).

The K4 speaks the same ASCII CAT protocol it uses over serial: commands and
responses are ';'-terminated tokens.  The radio may also emit unsolicited
tokens (auto-info), so reading a reply means scanning the stream for a token
with the prefix we asked about rather than assuming the next token is ours.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import List, Optional

LOG = logging.getLogger(__name__)

DEFAULT_PORT = 9200
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_REPLY_TIMEOUT = 3.0
# Give the radio a moment to consume a fire-and-forget command before we close
# the socket, so the close never races the write.
DEFAULT_SETTLE_SECONDS = 0.25


class RadioError(Exception):
    """Raised when the radio cannot be reached or does not answer."""


def split_tokens(buffer: str) -> List[str]:
    """Split a raw CAT stream into complete ';'-terminated tokens."""
    parts = buffer.split(";")
    # A trailing fragment with no ';' is an incomplete token; drop it.
    return [p + ";" for p in parts[:-1] if p]


class K4Client:
    """One-shot connection to a K4.

    A fresh connection per command keeps the bridge stateless and avoids
    holding the radio's single control socket open between voice requests.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        reply_timeout: float = DEFAULT_REPLY_TIMEOUT,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    ):
        self.host = host
        self.port = int(port)
        self.connect_timeout = float(connect_timeout)
        self.reply_timeout = float(reply_timeout)
        self.settle_seconds = float(settle_seconds)

    def send(self, cat: str, expect_prefix: Optional[str] = None) -> Optional[str]:
        """Send one CAT command.

        If ``expect_prefix`` is given, wait for and return the first response
        token starting with that prefix.  Otherwise send and return ``None``.
        """
        LOG.info("sending %r to %s:%s", cat, self.host, self.port)
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout
            ) as sock:
                sock.settimeout(self.reply_timeout)
                sock.sendall(cat.encode("ascii"))

                if expect_prefix is None:
                    time.sleep(self.settle_seconds)
                    return None

                return self._read_reply(sock, expect_prefix)
        except socket.timeout as exc:
            raise RadioError(
                "timed out talking to the radio at {}:{}".format(self.host, self.port)
            ) from exc
        except OSError as exc:
            raise RadioError(
                "cannot reach the radio at {}:{} ({})".format(self.host, self.port, exc)
            ) from exc

    def _read_reply(self, sock: socket.socket, expect_prefix: str) -> str:
        prefix = expect_prefix.upper()
        deadline = time.monotonic() + self.reply_timeout
        buffer = ""

        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                break

            buffer += chunk.decode("ascii", errors="replace")
            for token in split_tokens(buffer):
                if token.upper().startswith(prefix):
                    return token
            # Keep only the trailing incomplete fragment.
            buffer = buffer.rpartition(";")[2]

        raise RadioError(
            "radio did not answer {!r} within {:.0f}s".format(expect_prefix, self.reply_timeout)
        )

    def ping(self) -> str:
        """Query power state -- doubles as a reachability check."""
        return self.send("PS;", expect_prefix="PS")
