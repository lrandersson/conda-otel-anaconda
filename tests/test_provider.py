from __future__ import annotations

import json
import platform
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import conda.plugins.manager as conda_manager
import pytest
from conda.base.context import Context
from conda.exceptions import PackagesNotFoundInChannelsError
from conda.plugins.hookspec import CondaSpecs
from conda.plugins.manager import CondaPluginManager
from conda_otel import capture, core, sources
from conda_otel import hooks as core_hooks
from conda_otel.core import Dispatcher, Event
from conda_otel.events import EventName, matched_events
from conftest import decode_request

from conda_otel_anaconda import __version__, hooks
from conda_otel_anaconda import provider as anaconda

if TYPE_CHECKING:
    from typing import Any

    from conftest import RecordingServer

# Synthetic test data. Every secret contains SECRET.
SECRET = "SECRET"
#: A private URL with credentials, a local path and a public channel name.
RAW_CHANNELS = (
    "https://user:SECRET@host.example/t/SECRET_TO.KEN/chan?token=SECRET",
    "file:///home/SECRET/chan",
    "conda-forge",
)
REDACTED_CHANNELS = ["https://host.example/chan", "<local>", "conda-forge"]
PRIVATE_PACKAGE = (
    "x-internal-tool"  # private names reach the export by design (see README)
)
PRIVATE_RECORD = "x-internal-tool=1.0=0"
MISSING_PACKAGE = "x-unreleased-tool"
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


class FooPluginError(PackagesNotFoundInChannelsError):
    """A plugin's own subclass of the not-found error."""


def test_synthetic_secrets_do_not_reach_the_export(
    monkeypatch: pytest.MonkeyPatch, otlp_server: RecordingServer
) -> None:
    """Input and export contain no secrets; private channels export as "other"."""
    context = SimpleNamespace(
        plugins=SimpleNamespace(conda_otel=True),
        channels=RAW_CHANNELS,
        channel_priority="strict",
        dry_run=False,
        download_only=False,
    )
    monkeypatch.setattr(capture, "context", context)
    monkeypatch.setattr(sources, "context", context)
    provider = anaconda.anaconda_provider(
        SimpleNamespace(proxy_servers={}), f"{otlp_server.endpoint}/v1/logs"
    )
    seen = []
    original = provider.transform

    def transform(event: Event) -> Event | None:
        """Record the input before the provider changes it."""
        seen.append(event)
        return original(event)

    provider.transform = transform
    dispatcher = Dispatcher([provider])
    monkeypatch.setattr(capture, "get_dispatcher", lambda: dispatcher)
    capture.capture_command("install")
    capture.capture_requested_packages(frozenset({PRIVATE_PACKAGE}), frozenset())
    capture.capture_resolved_packages(
        "repodata.json",
        (),
        (SimpleNamespace(name=PRIVATE_PACKAGE, version="1.0", build="0"),),
    )
    capture.report_success("install")
    capture.capture_command("install")
    capture.capture_requested_packages(frozenset({MISSING_PACKAGE}), frozenset())
    exc = FooPluginError([MISSING_PACKAGE], ["conda-forge"])
    capture.report_error(SimpleNamespace(exc_value=exc, exc_type=type(exc)))

    assert [event.attributes for event in seen] == [
        {
            "command": "install",
            "channels": REDACTED_CHANNELS,
            "channel_priority": "strict",
            "requested_packages": [PRIVATE_PACKAGE],
            "resolved_packages": [PRIVATE_RECORD],
        },
        {
            "command": "install",
            "channels": REDACTED_CHANNELS,
            "channel_priority": "strict",
            "requested_packages": [MISSING_PACKAGE],
            "missing_packages": [MISSING_PACKAGE],
            "exception_name": "PackagesNotFoundInChannelsError",
        },
    ]
    records = []
    for _path, _type, body in otlp_server.received:
        batch, _resource = decode_request(body)
        records.extend(batch)
        for value in (SECRET, "host.example", "FooPluginError"):
            assert value.encode("utf-8") not in body
    assert len(records) == 2
    by_name = {record["body"]: record for record in records}
    success = by_name["install.success"]
    assert success["install.channels"] == ["other", "other", "conda-forge"]
    assert success["requested.packages"] == [PRIVATE_PACKAGE]
    assert success["resolved.packages"] == [PRIVATE_RECORD]
    not_found = by_name["install.pnfe"]
    assert not_found["exception.name"] == "PackagesNotFoundInChannelsError"
    assert not_found["exception.missing_specs"] == [MISSING_PACKAGE]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            SUCCESS,
            {
                "command": "install",
                "event.schema_version": "1",
                "install.channels": ["conda-forge", "other"],
                "log.event.name": "install.success",
                "install.channel_priority": "flexible",
                "requested.packages": ["numpy"],
                "resolved.packages": ["numpy=1.0=0", "python=3.12=0"],
                "truncated": False,
            },
            id="success",
        ),
        pytest.param(
            PNFE,
            {
                "command": "install",
                "event.schema_version": "1",
                "install.channels": ["defaults"],
                "log.event.name": "install.pnfe",
                "install.channel_priority": "strict",
                "requested.packages": ["nope"],
                "exception.name": "PackagesNotFoundInChannelsError",
                "exception.missing_specs": ["nope"],
                "truncated": False,
            },
            id="packages-not-found",
        ),
    ],
)
def test_install_signal(source: Event, expected: dict[str, Any]) -> None:
    """Check the signal name and attributes for each install outcome."""
    event = anaconda.transform(source)
    assert event is not None
    assert event.name == expected["log.event.name"]
    assert event.attributes == expected


def test_subscriptions_match_handler_keys() -> None:
    """The provider subscribes to exactly the events it handles."""
    provider = anaconda.anaconda_provider(
        SimpleNamespace(proxy_servers={}), "http://127.0.0.1:9/v1/logs"
    )
    expected = {"install.success", "install.packages_not_found"}
    assert matched_events(provider.events) == set(anaconda.INSTALL_EVENTS) == expected


def test_long_lists_are_truncated() -> None:
    """Trim lists over the item limit and set the truncated flag."""
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
    """The byte limit can trim a list even when it fits the item limit."""
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

    records: list[dict[str, Any]] = []
    resource: dict[str, Any] = {}
    for _path, _type, body in otlp_server.received:
        batch, resource = decode_request(body)
        records.extend(batch)
    assert len(records) == 2
    by_name = {record["body"]: record for record in records}
    assert by_name == {
        "install.success": {
            "body": "install.success",
            "command": "install",
            "event.schema_version": "1",
            "install.channels": ["conda-forge", "other"],
            "log.event.name": "install.success",
            "install.channel_priority": "flexible",
            "requested.packages": ["numpy"],
            "resolved.packages": ["numpy=1.0=0", "python=3.12=0"],
            "truncated": False,
        },
        "install.pnfe": {
            "body": "install.pnfe",
            "command": "install",
            "event.schema_version": "1",
            "install.channels": ["defaults"],
            "log.event.name": "install.pnfe",
            "install.channel_priority": "strict",
            "requested.packages": ["nope"],
            "exception.name": "PackagesNotFoundInChannelsError",
            "exception.missing_specs": ["nope"],
            "truncated": False,
        },
    }
    # Skip keys that change per process or per SDK version.
    stable = {
        key: value
        for key, value in resource.items()
        if not key.startswith("telemetry.sdk.")
        and key not in {"service.version", "service.instance.id"}
    }
    assert stable == {
        "service.name": "conda-otel-anaconda",
        "platform": "conda",
        "os.type": platform.system(),
        "python.version": platform.python_version(),
        "client.sdk.version": __version__,
        "schema.version": anaconda.RESOURCE_SCHEMA_VERSION,
        "parameters": "{}",
        "aau.client.token": "test-client-token",
        "aau.installer.tokens": '["test-installer-token"]',
    }


def test_anaconda_endpoint_is_blocked(loopback_only: list[str]) -> None:
    """The test fixture blocks connections to the production endpoint."""
    dispatcher = Dispatcher(
        [anaconda.anaconda_provider(SimpleNamespace(proxy_servers={}))]
    )
    dispatcher.emit(SUCCESS)
    assert dispatcher.flush() == {"anaconda": False}
    assert "public.telemetry.anaconda.com" in loopback_only


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


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("true", ["anaconda"]), ("false", []), (None, [])],
)
def test_provider_is_enabled_only_by_setting(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: list[str]
) -> None:
    """The provider loads only when the anaconda setting is true."""
    # Use a private manager. The global one loads every installed conda plugin.
    # It is also cached and shared between tests.
    manager = CondaPluginManager()
    manager.add_hookspecs(CondaSpecs)
    manager.register(core_hooks)
    manager.register(hooks)
    monkeypatch.setattr(conda_manager, "get_plugin_manager", lambda: manager)
    if env_value is None:
        monkeypatch.delenv("CONDA_PLUGINS_CONDA_OTEL_ANACONDA", raising=False)
    else:
        monkeypatch.setenv("CONDA_PLUGINS_CONDA_OTEL_ANACONDA", env_value)
    context = Context(search_path=())
    providers = core.load_providers(context)
    assert [p.name for p in providers] == expected


def test_conda_entry_point_targets_hooks() -> None:
    """The package registers its hooks module as a conda plugin."""
    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    target = data["project"]["entry-points"]["conda"]["conda-otel-anaconda"]
    assert target == "conda_otel_anaconda.hooks"
