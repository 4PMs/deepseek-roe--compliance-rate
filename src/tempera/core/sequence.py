"""Run-scoped sequence allocation shared by observer processes.

The service binds to ``0.0.0.0`` because the Sequelize observer runs inside a
Docker container and must reach the host via ``host.docker.internal``.
Binding to ``127.0.0.1`` blocks container access. Security relies on
per-run token authentication, not interface restriction. The agent runs as a
host process and could reach ``127.0.0.1`` anyway, so interface restriction
provides no isolation benefit against the agent.
"""

import json
from socketserver import StreamRequestHandler, ThreadingTCPServer
from threading import Lock, Thread


class SequenceAllocator:
    def __init__(self, start: int = 0) -> None:
        self._next_value = start
        self._lock = Lock()

    def next(self) -> int:
        with self._lock:
            value = self._next_value
            self._next_value += 1
            return value


class SequenceService:
    """Small run-local TCP allocator; requests are linearized at the source.

    Lifecycle::

        service = SequenceService("token")
        with service:          # __enter__ starts the background thread
            ...                # allocate sequences
        # __exit__ shuts down cleanly even if __enter__ was never called
    """

    _SHUTDOWN_TIMEOUT = 5.0  # seconds to wait for serve_forever to stop

    def __init__(self, token: str, host: str = "0.0.0.0", port: int = 0):
        self.allocator = SequenceAllocator()
        service = self

        class Handler(StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.loads(self.rfile.readline())
                    if request.get("token") != token:
                        return
                    self.wfile.write(
                        (json.dumps({"seq": service.allocator.next()}) + "\n").encode()
                    )
                    self.wfile.flush()
                except (ValueError, TypeError, AttributeError):
                    return

        self._server = ThreadingTCPServer((host, port), Handler)
        self._server.daemon_threads = True
        self.address = self._server.server_address
        self.token = token
        self._thread = Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def __enter__(self) -> "SequenceService":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        # Guard: shutdown() blocks forever if serve_forever() was never started,
        # and join() raises RuntimeError if the thread was never started at all.
        # Only call either when the background thread is actually running.
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=self._SHUTDOWN_TIMEOUT)
        self._server.server_close()


def request_sequence(host: str, port: int, token: str, timeout: float = 5) -> int:
    import socket

    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.sendall((json.dumps({"token": token}) + "\n").encode())
        # Apply the same timeout to the read phase so we never block indefinitely
        # if the server fails to respond.  socket.makefile() does not accept a
        # 'timeout' keyword; set it on the socket before calling makefile().
        connection.settimeout(timeout)
        response = json.loads(connection.makefile().readline())
    seq = response["seq"]
    if not isinstance(seq, int) or seq < 0:
        raise ValueError("sequence service returned an invalid seq")
    return seq
