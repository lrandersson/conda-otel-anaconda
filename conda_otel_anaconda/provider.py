"""Send conda-otel install events to Anaconda in the existing signal format."""

from __future__ import annotations

import json
import platform
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import requests
from anaconda_anon_usage import tokens
from conda_otel.capture import EventName
from conda_otel.core import Event, Provider
from conda_otel_anaconda import __version__
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

if TYPE_CHECKING:
    from conda.base.context import Context

ENDPOINT = "https://public.telemetry.anaconda.com/v1/logs"

#: Bump whenever the content or shape of the signals changes.
SIGNAL_VERSION = "1"

#: Version of the resource attributes this provider sends.
#: Not the same as SIGNAL_VERSION.
RESOURCE_SCHEMA_VERSION = "0.1.0"

#: Limits for list-valued attributes.
LIST_ITEM_LIMIT = 50
LIST_BYTE_LIMIT = 500


class Channel(StrEnum):
    """Channel names that may be sent.

    Any other channel is sent as OTHER, so a channel URL or internal name
    cannot reach the payload.
    """

    DEFAULTS = "defaults"
    MAIN = "main"
    MAIN_X = "main-x"
    CONDA_FORGE = "conda-forge"
    OTHER = "other"


KNOWN_CHANNELS = frozenset(Channel) - {Channel.OTHER}


#: Resource attribute name and the function that gives its value.
TOKEN_FUNCS = {
    "aau.version": tokens.version_token,
    "aau.client.token": tokens.client_token,
    "aau.session.token": tokens.session_token,
    "aau.environment.token": tokens.environment_token,
    "aau.organization.tokens": tokens.organization_tokens,
    "aau.installer.tokens": tokens.installer_tokens,
    "aau.machine.tokens": tokens.machine_tokens,
    "aau.anaconda_auth.token": tokens.anaconda_auth_token,
}


def get_aau_attributes() -> dict[str, str]:
    """Return the anonymous usage token attributes.

    Skip a token that is None. Lists become JSON strings.
    """
    attributes = {}
    for name, func in TOKEN_FUNCS.items():
        value = func()
        if value is None:
            continue
        attributes[name] = (
            json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        )
    return attributes


def truncate(items: list[Any]) -> tuple[list[Any], bool]:
    """Cut a list to the item limit and the JSON-encoded UTF-8 byte limit.

    Return the kept items and whether anything was dropped.
    """
    truncated = len(items) > LIST_ITEM_LIMIT
    kept: list[Any] = []
    for item in items[:LIST_ITEM_LIMIT]:
        candidate = [*kept, item]
        if len(json.dumps(candidate).encode("utf-8")) > LIST_BYTE_LIMIT:
            truncated = True
            break
        kept = candidate
    return kept, truncated


def shared_attributes(attrs: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Build the attributes that all install signals have.

    A channel that is not in KNOWN_CHANNELS is sent as "other". Return the
    attributes and whether a list was cut.
    """
    channels, channels_truncated = truncate(
        [c if c in KNOWN_CHANNELS else Channel.OTHER.value for c in attrs["channels"]]
    )
    requested, requested_truncated = truncate(list(attrs["requested_packages"]))
    return (
        {
            "command": attrs["command"],
            "event.schema_version": SIGNAL_VERSION,
            "install.channels": channels,
            "install.channel_priority": attrs["channel_priority"],
            "requested.packages": requested,
        },
        channels_truncated or requested_truncated,
    )


def install_pnfe(event: Event) -> Event:
    """Build the signal for packages that were not found."""
    attrs = event.attributes
    shared, truncated = shared_attributes(attrs)
    missing, missing_truncated = truncate(list(attrs["missing_packages"]))
    return Event(
        "install.pnfe",
        {
            **shared,
            "exception.name": attrs["exception_name"],
            "exception.missing_specs": missing,
            "truncated": truncated or missing_truncated,
        },
    )


def install_success(event: Event) -> Event:
    """Build the signal for a finished install."""
    attrs = event.attributes
    shared, truncated = shared_attributes(attrs)
    resolved, resolved_truncated = truncate(list(attrs["resolved_packages"]))
    return Event(
        "install.success",
        {
            **shared,
            "resolved.packages": resolved,
            "truncated": truncated or resolved_truncated,
        },
    )


#: The install events this provider sends. All other events are dropped.
INSTALL_EVENTS = {
    EventName.PACKAGES_NOT_FOUND: install_pnfe,
    EventName.SUCCESS: install_success,
}


def transform(event: Event) -> Event | None:
    """Turn a neutral install event into an Anaconda signal. Drop other events."""
    handler = INSTALL_EVENTS.get(event.name)
    if handler is None:
        return None
    signal = handler(event)
    # Same attribute as the conda-anaconda-telemetry signals have.
    signal.attributes["log.event.name"] = signal.name
    return signal


def anaconda_provider(context: Context, endpoint: str = ENDPOINT) -> Provider:
    """Build the provider. This is the entry point conda-otel calls.

    Use the proxy from the conda settings. Only tests change the endpoint.
    """
    session = requests.Session()
    proxy = requests.utils.select_proxy(endpoint, context.proxy_servers)
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return Provider(
        name="anaconda",
        exporter=OTLPLogExporter(endpoint=endpoint, timeout=2, session=session),
        resource_attributes={
            "service.name": "conda-otel-anaconda",
            "service.version": __version__,
            "platform": "conda",
            "os.type": platform.system(),
            "python.version": platform.python_version(),
            "client.sdk.version": __version__,
            "schema.version": RESOURCE_SCHEMA_VERSION,
            "parameters": "{}",
            **get_aau_attributes(),
        },
        transform=transform,
    )
