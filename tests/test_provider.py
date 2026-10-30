from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import tomllib
from conda_otel.capture import EventName
from conda_otel.core import Dispatcher, Event
from conda_otel_anaconda import __version__
from conda_otel_anaconda import provider as anaconda
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)

if TYPE_CHECKING:
    from conftest import RecordingServer

SUCCESS = Event(
    EventName.SUCCESS,
    {
        "command": "install",
        "channels": ("conda-forge", "https://internal.example.com/secret"),
        "channel_priority": "flexible",
        "requested_packages": ("numpy",),
        "resolved_packages": ("numpy=1.0=0", "python=3.12=0"),
    },
)
PNFE = Event(
    EventName.PACKAGES_NOT_FOUND,
    {
        "command": "install",
        "channels": ("defaults",),
        "channel_priority": "strict",
        "requested_packages": ("nope",),
        "missing_packages": ("nope",),
        "exception_name": "PackagesNotFoundInChannelsError",
    },
)


def test_install_success_signal() -> None:
    """A success event becomes an install.success signal.

    Unknown channels become 'other'.
    """
    event = anaconda.transform(SUCCESS)
    assert event.name == "install.success"
    assert event.attributes == {
        "command": "install",
        "event.schema_version": "1",
        "install.channels": ["conda-forge", "other"],
        "log.event.name": "install.success",
        "install.channel_priority": "flexible",
        "requested.packages": ["numpy"],
        "resolved.packages": ["numpy=1.0=0", "python=3.12=0"],
        "truncated": False,
    }


def test_install_pnfe_signal() -> None:
    """A packages-not-found event becomes an install.pnfe signal."""
    event = anaconda.transform(PNFE)
    assert event.name == "install.pnfe"
    assert event.attributes == {
        "command": "install",
        "event.schema_version": "1",
        "install.channels": ["defaults"],
        "log.event.name": "install.pnfe",
        "install.channel_priority": "strict",
        "requested.packages": ["nope"],
        "exception.name": "PackagesNotFoundInChannelsError",
        "exception.missing_specs": ["nope"],
        "truncated": False,
    }


def test_long_lists_are_truncated() -> None:
    """A list over the limit is cut, and the signal says so."""
    many = Event(
        EventName.SUCCESS,
        {
            **SUCCESS.attributes,
            "resolved_packages": [f"pkg{i}=1.0=0" for i in range(100)],
        },
    )
    attrs = anaconda.transform(many).attributes
    assert len(attrs["resolved.packages"]) <= anaconda.LIST_ITEM_LIMIT
    assert attrs["truncated"] is True


def test_byte_limit_cuts_a_short_list() -> None:
    """A list under the item limit is still cut when it is over the byte limit."""
    items = ["x" * 30] * 20
    assert len(items) < anaconda.LIST_ITEM_LIMIT
    kept, truncated = anaconda.truncate(items)
    assert len(json.dumps(kept).encode("utf-8")) <= anaconda.LIST_BYTE_LIMIT
    assert 0 < len(kept) < len(items)
    assert truncated is True


def test_aau_attributes() -> None:
    """Lists become JSON strings and tokens that are None are left out."""
    assert anaconda.get_aau_attributes() == {
        "aau.client.token": "test-client-token",
        "aau.installer.tokens": '["test-installer-token"]',
    }


def test_other_events_are_dropped() -> None:
    """Events that are not install events are dropped."""
    assert anaconda.transform(Event("create.success", {})) is None


def test_signals_reach_the_endpoint(otlp_server: RecordingServer) -> None:
    """Install signals reach the endpoint. Other events do not."""
    dispatcher = Dispatcher(
        [
            anaconda.anaconda_provider(
                SimpleNamespace(proxy_servers={}), f"{otlp_server.endpoint}/v1/logs"
            )
        ]
    )
    dispatcher.emit(SUCCESS)
    dispatcher.emit(PNFE)
    dispatcher.emit(Event("other", {}))
    assert dispatcher.flush() == {"anaconda": True}

    names: list[str] = []
    resource: dict[str, Any] = {}
    for _path, _type, body in otlp_server.received:
        request = ExportLogsServiceRequest()
        request.ParseFromString(body)
        resource = {
            kv.key: kv.value.string_value
            for kv in request.resource_logs[0].resource.attributes
        }
        for record in request.resource_logs[0].scope_logs[0].log_records:
            names.append(record.body.string_value)
    assert sorted(names) == ["install.pnfe", "install.success"]
    assert resource["platform"] == "conda"
    assert resource["client.sdk.version"] == __version__
    assert resource["schema.version"] == anaconda.RESOURCE_SCHEMA_VERSION == "0.1.0"
    assert resource["parameters"] == "{}"
    assert resource["aau.client.token"] == "test-client-token"


def test_conda_proxy_is_used(
    monkeypatch: pytest.MonkeyPatch, otlp_server: RecordingServer
) -> None:
    """The provider sends through the proxy from the conda settings."""
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    context = SimpleNamespace(proxy_servers={"http": otlp_server.endpoint})
    dispatcher = Dispatcher(
        [anaconda.anaconda_provider(context, "http://telemetry.invalid/v1/logs")]
    )
    dispatcher.emit(SUCCESS)
    assert dispatcher.flush() == {"anaconda": True}
    ((path, _, _),) = otlp_server.received
    assert path == "http://telemetry.invalid/v1/logs"


def test_provider_is_registered_for_conda_otel() -> None:
    """The entry point in pyproject.toml must point to the provider function."""
    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    target = data["project"]["entry-points"]["conda_otel.providers"]["anaconda"]
    module, _, attr = target.partition(":")
    assert (
        getattr(__import__(module, fromlist=[attr]), attr) is anaconda.anaconda_provider
    )
