# conda-otel-anaconda

The Anaconda provider for [conda-otel](https://github.com/lrandersson/conda-otel). It sends install events to Anaconda.

> **Status:** proof of concept. Do not use in production.

## Data this provider sends

The provider is off by default. Users opt in with `conda_otel_anaconda: true` under `plugins` in `.condarc`. The core setting `conda_otel` is on by default but sends nothing without an enabled provider.

The provider sends logs to `https://public.telemetry.anaconda.com/v1/logs`. The tables list exported log and resource attributes. The code does not state field purposes or justification; these remain drafts pending sign-off.

### Install success

| Attribute | Source | Processing | Purpose |
| --- | --- | --- | --- |
| `command` | Captured command | Core allows only `install`. | Draft: pending sign-off. To be confirmed. |
| `event.schema_version` | Provider `SIGNAL_VERSION` | Sends `1`. | Draft: pending sign-off. To be confirmed. |
| `log.event.name` | Provider signal name | Sends `install.success`, also used as the log body and event name. | Draft: pending sign-off. To be confirmed. |
| `install.channels` | Configured channels | Core redacts credentials and local paths. Provider keeps only `defaults`, `main`, `main-x` and `conda-forge`; all others become `other`. Cuts the list to 50 items and 500 JSON-encoded UTF-8 bytes. | Draft: pending sign-off. To be confirmed. |
| `install.channel_priority` | Conda context | Core allows only `strict`, `flexible` and `disabled`. | Draft: pending sign-off. To be confirmed. |
| `requested.packages` | Requested specs | Core extracts validated exact package names. Provider cuts the list to 50 items and 500 bytes. Private package names remain. | Draft: pending sign-off. To be confirmed. |
| `resolved.packages` | Package metadata | Sends `name=version=build`; cuts the list to 50 items and 500 bytes. | Draft: pending sign-off. To be confirmed. |
| `truncated` | Provider list processing | True if any list was cut. | Draft: pending sign-off. To be confirmed. |

### Packages not found

| Attribute | Source | Processing | Purpose |
| --- | --- | --- | --- |
| `command` | Captured command | Core allows only `install`. | Draft: pending sign-off. To be confirmed. |
| `event.schema_version` | Provider `SIGNAL_VERSION` | Sends `1`. | Draft: pending sign-off. To be confirmed. |
| `log.event.name` | Provider signal name | Sends `install.pnfe`, also used as the log body and event name. | Draft: pending sign-off. To be confirmed. |
| `install.channels` | Configured channels | Core redacts credentials and local paths. Provider keeps only `defaults`, `main`, `main-x` and `conda-forge`; all others become `other`. Cuts the list to 50 items and 500 JSON-encoded UTF-8 bytes. | Draft: pending sign-off. To be confirmed. |
| `install.channel_priority` | Conda context | Core allows only `strict`, `flexible` and `disabled`. | Draft: pending sign-off. To be confirmed. |
| `requested.packages` | Requested specs | Core extracts validated exact package names. Provider cuts the list to 50 items and 500 bytes. Private package names remain. | Draft: pending sign-off. To be confirmed. |
| `exception.name` | Raised exception class | Core maps subclasses to `PackagesNotFoundInChannelsError` and allows only that name. | Draft: pending sign-off. To be confirmed. |
| `exception.missing_specs` | Exception package specs | Core extracts validated exact names; provider cuts the list to 50 items and 500 bytes. Private package names remain. | Draft: pending sign-off. To be confirmed. |
| `truncated` | Provider list processing | True if any list was cut. | Draft: pending sign-off. To be confirmed. |

The byte limit for every list is measured after JSON encoding in UTF-8.

### Resource attributes

Resource attributes are attached to both signals. AAU values come from `anaconda-anon-usage`; `None` values are omitted, lists and dictionaries become JSON strings, and other values become strings.

| Attribute | Source | Processing | Purpose |
| --- | --- | --- | --- |
| `service.name` | Provider constant | Sends `conda-otel-anaconda`. | Draft: pending sign-off. To be confirmed. |
| `service.version` | Provider package version | Unchanged. | Draft: pending sign-off. To be confirmed. |
| `platform` | Provider constant | Sends `conda`. | Draft: pending sign-off. To be confirmed. |
| `os.type` | `platform.system()` | Unchanged. | Draft: pending sign-off. To be confirmed. |
| `python.version` | `platform.python_version()` | Unchanged. | Draft: pending sign-off. To be confirmed. |
| `client.sdk.version` | Provider package version | Unchanged. | Draft: pending sign-off. To be confirmed. |
| `schema.version` | Provider `RESOURCE_SCHEMA_VERSION` | Sends `0.1.0`. | Draft: pending sign-off. To be confirmed. |
| `parameters` | Provider constant | Sends `{}` as a string. | Draft: pending sign-off. To be confirmed. |
| `aau.version` | AAU `version_token()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.client.token` | AAU `client_token()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.session.token` | AAU `session_token()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.environment.token` | AAU `environment_token()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.organization.tokens` | AAU `organization_tokens()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.installer.tokens` | AAU `installer_tokens()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.machine.tokens` | AAU `machine_tokens()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `aau.anaconda_auth.token` | AAU `anaconda_auth_token()` | AAU string conversion above. | Draft: pending sign-off. To be confirmed. |
| `telemetry.sdk.language` | OpenTelemetry SDK | Added by the SDK. | Draft: pending sign-off. To be confirmed. |
| `telemetry.sdk.name` | OpenTelemetry SDK | Added by the SDK. | Draft: pending sign-off. To be confirmed. |
| `telemetry.sdk.version` | OpenTelemetry SDK | Added by the SDK. | Draft: pending sign-off. To be confirmed. |
| `service.instance.id` | OpenTelemetry SDK | Added by the SDK. | Draft: pending sign-off. To be confirmed. |

### Package data justification

- Package names: Draft: pending sign-off. To be confirmed; exact names can identify private packages.
- Package versions: Draft: pending sign-off. To be confirmed; resolved versions are sent in addition to names.
- Package builds: Draft: pending sign-off. To be confirmed; resolved build strings are sent in addition to names and versions.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
