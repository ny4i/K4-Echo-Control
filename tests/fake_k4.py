"""A stand-in for a real K4, used by the tests and by tools/fake_k4.py.

Speaks just enough Elecraft CAT to exercise the bridge: it tracks power state,
answers ``PS;``, and accepts ``PS0;`` / ``PS1;``.
"""

from __future__ import annotations

import socket
import threading


class FakeK4:
    """Minimal CAT-over-TCP server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, power_on: bool = True):
        self.power_on = power_on
        self.received = []
        # (cat token, power state immediately after handling it)
        self.history = []
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(5)
        self.host, self.port = self._server.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "FakeK4":
        self._thread.start()
        return self

    def stop(self) -> None:
        """Shut down for real.

        Closing a listening socket does not wake a thread already blocked in
        ``accept()`` -- the kernel keeps the listener alive until that call
        returns -- so the next connection would still be served.  Wake it with
        a throwaway connection first, then close.
        """
        self._stop.set()
        try:
            self._server.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as waker:
                waker.settimeout(0.5)
                waker.connect((self.host, self.port))
        except OSError:
            pass
        try:
            self._server.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buffer = ""
        conn.settimeout(2.0)
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(256)
                except (socket.timeout, OSError):
                    return
                if not chunk:
                    return
                buffer += chunk.decode("ascii", errors="replace")
                while ";" in buffer:
                    token, _, buffer = buffer.partition(";")
                    self._dispatch(conn, token.upper())

    def _dispatch(self, conn: socket.socket, token: str) -> None:
        self.received.append(token + ";")
        if token == "PS":
            conn.sendall(b"PS1;" if self.power_on else b"PS0;")
        elif token == "PS0":
            self.power_on = False
        elif token == "PS1":
            self.power_on = True
        self.history.append((token + ";", self.power_on))

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
