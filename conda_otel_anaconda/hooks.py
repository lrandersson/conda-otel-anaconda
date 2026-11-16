"""Conda settings and the optional conda-otel provider hook."""

from __future__ import annotations

from conda.common.configuration import PrimitiveParameter
from conda.plugins import hookimpl
from conda.plugins.types import CondaSetting


@hookimpl
def conda_settings():
    """Require explicit consent before sending Anaconda telemetry."""
    yield CondaSetting(
        name="conda_otel_anaconda",
        description="Whether to send conda-otel events to Anaconda",
        parameter=PrimitiveParameter(False, element_type=bool),
    )


@hookimpl(optionalhook=True)
def conda_otel_providers(context):
    """Load the provider only after the user enables it."""
    if context.plugins.conda_otel_anaconda:
        from conda_otel_anaconda.provider import anaconda_provider

        yield anaconda_provider(context)
