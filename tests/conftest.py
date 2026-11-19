from __future__ import annotations

import contextlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

import pytest
import urllib3.util.connection
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)

from conda_otel_anaconda import provider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any


class RecordingServer(HTTPServer):
    """HTTP server that keeps each POST it receives."""

    endpoint: str
    received: list[tuple[str, str | None, bytes]]


@contextlib.contextmanager
def recording_server() -> Iterator[RecordingServer]:
    """Run a local HTTP server that records POST requests.

    The server is shut down on exit, so the caller can read every recorded request.
    """
    received: list[tuple[str, str | None, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        """Request handler that records each POST."""

        def do_POST(self) -> None:
            """Record the POST and return HTTP 200."""
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
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def otlp_server() -> Iterator[RecordingServer]:
    """Local HTTP server that records POST requests."""
    with recording_server() as server:
        yield server


def decode_value(value: Any) -> Any:
    """Return the Python value of an OTLP AnyValue."""
    kind = value.WhichOneof("value")
    if kind == "array_value":
        return [item.string_value for item in value.array_value.values]
    return getattr(value, kind)


def decode_request(body: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return one dict per record (body and attributes) and the resource dict."""
    request = ExportLogsServiceRequest()
    request.ParseFromString(body)
    # Test exports contain one resource and one scope, both at index 0.
    resource_logs = request.resource_logs[0]
    resource = {
        kv.key: decode_value(kv.value) for kv in resource_logs.resource.attributes
    }
    records = [
        {
            "body": decode_value(record.body),
            **{kv.key: decode_value(kv.value) for kv in record.attributes},
        }
        for record in resource_logs.scope_logs[0].log_records
    ]
    return records, resource


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Let tests check where telemetry would go without contacting outside servers.

    Record the hosts urllib3 tries to reach and block nonlocal connections.
    Clear proxy settings so requests go straight to the local test server.
    """
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
    blocked: list[str] = []
    original = urllib3.util.connection.create_connection
    loopback = {"127.0.0.1", "localhost"}

    def guarded(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        host = address[0]
        if host in loopback:
            return original(address, *args, **kwargs)
        blocked.append(host)
        # Report the block as a connection failure to urllib3.
        raise OSError("network blocked in tests")

    monkeypatch.setattr(urllib3.util.connection, "create_connection", guarded)
    return blocked


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
