from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conda_otel_anaconda import provider


class RecordingServer(HTTPServer):
    """HTTP server that keeps each POST it receives."""

    endpoint: str
    received: list[tuple[str, str | None, bytes]]


@pytest.fixture
def otlp_server() -> Iterator[RecordingServer]:
    """Local HTTP server that records POST requests."""
    received: list[tuple[str, str | None, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        """Request handler that records each POST."""

        def do_POST(self) -> None:
            """Save the request and answer with 200."""
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append((self.path, self.headers.get("Content-Type"), body))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            """Keep the test output quiet."""

    server = RecordingServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.endpoint = f"http://127.0.0.1:{server.server_port}"
    server.received = received
    yield server
    server.shutdown()


@pytest.fixture(autouse=True)
def fake_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the token functions, so no test reads or writes real token files."""
    monkeypatch.setattr(
        provider,
        "TOKEN_FUNCS",
        {
            "aau.client.token": lambda: "test-client-token",
            "aau.installer.tokens": lambda: ["test-installer-token"],
            "aau.anaconda_auth.token": lambda: None,
        },
    )
