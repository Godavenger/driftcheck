"""Load and validate a driftcheck YAML config file into a :class:`Config`.

This module owns the config boundary: it turns an on-disk YAML file into a
validated ``Config`` of ``EndpointCheck`` objects, raising ``ConfigError`` with a
readable, key-naming message for anything missing, empty, or mistyped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from driftcheck.errors import ConfigError
from driftcheck.models import Config, EndpointCheck


def load_config(path: str) -> Config:
    """Parse and validate the YAML config at ``path`` into a ``Config``.

    Raises ``ConfigError`` if the file is missing, is not valid YAML, is not a
    top-level mapping, or is missing/mistyped on a required key.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"config file could not be read: {path} ({exc})") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc

    if raw is None:
        raise ConfigError("config file is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"config must be a YAML mapping, got {type(raw).__name__}")

    spec = _require_str(raw, "spec")
    base_url = _require_str(raw, "base_url")
    endpoints = _parse_endpoints(raw)

    auth_header_env = raw.get("auth_header_env")
    if auth_header_env is not None and not isinstance(auth_header_env, str):
        raise ConfigError("config key 'auth_header_env' must be a string or null")

    auth_header_name = raw.get("auth_header_name")
    if auth_header_name is None:
        auth_header_name = "Authorization"
    elif not isinstance(auth_header_name, str) or auth_header_name.strip() == "":
        raise ConfigError("config key 'auth_header_name' must be a non-empty string")

    raw_timeout = raw.get("timeout_seconds")
    if raw_timeout is None:
        timeout_seconds = 10.0
    elif isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int | float):
        raise ConfigError("config key 'timeout_seconds' must be a number")
    else:
        timeout_seconds = float(raw_timeout)

    return Config(
        spec=spec,
        base_url=base_url,
        endpoints=endpoints,
        auth_header_env=auth_header_env,
        auth_header_name=auth_header_name,
        timeout_seconds=timeout_seconds,
    )


def _require_str(raw: dict[str, Any], key: str) -> str:
    """Return a present, non-empty string value for a required top-level key."""
    if key not in raw or raw[key] is None:
        raise ConfigError(f"missing required config key: {key}")
    value = raw[key]
    if not isinstance(value, str):
        raise ConfigError(f"config key '{key}' must be a string, got {type(value).__name__}")
    if value.strip() == "":
        raise ConfigError(f"config key '{key}' must not be empty")
    return value


def _parse_endpoints(raw: dict[str, Any]) -> list[EndpointCheck]:
    """Validate the ``endpoints`` key and parse each entry into an EndpointCheck."""
    if "endpoints" not in raw or raw["endpoints"] is None:
        raise ConfigError("missing required config key: endpoints")
    endpoints = raw["endpoints"]
    if not isinstance(endpoints, list):
        raise ConfigError(f"config key 'endpoints' must be a list, got {type(endpoints).__name__}")
    if not endpoints:
        raise ConfigError("config key 'endpoints' must not be empty")
    return [_parse_endpoint(item, index) for index, item in enumerate(endpoints)]


def _parse_endpoint(item: Any, index: int) -> EndpointCheck:
    """Parse one endpoint mapping into an EndpointCheck, naming bad fields."""
    if not isinstance(item, dict):
        raise ConfigError(f"endpoint #{index} must be a mapping, got {type(item).__name__}")

    name = item.get("name")
    if not isinstance(name, str) or name.strip() == "":
        raise ConfigError(f"endpoint #{index} is missing a non-empty 'name'")

    path = item.get("path")
    if not isinstance(path, str) or path.strip() == "":
        raise ConfigError(f"endpoint '{name}' is missing a non-empty 'path'")

    method = item.get("method", "GET")
    if not isinstance(method, str) or method.strip() == "":
        raise ConfigError(f"endpoint '{name}' has an invalid 'method'")

    expected_status = item.get("expected_status")
    if expected_status is not None and (
        isinstance(expected_status, bool) or not isinstance(expected_status, int)
    ):
        raise ConfigError(f"endpoint '{name}' has a non-integer 'expected_status'")

    return EndpointCheck(
        name=name,
        path=path,
        method=method.upper(),
        path_params=_coerce_str_map(item.get("path_params"), name, "path_params"),
        query_params=_coerce_str_map(item.get("query_params"), name, "query_params"),
        expected_status=expected_status,
    )


def _coerce_str_map(value: Any, endpoint: str, key: str) -> dict[str, str]:
    """Coerce an optional mapping's keys and values to str (default empty dict)."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"endpoint '{endpoint}' key '{key}' must be a mapping, got {type(value).__name__}"
        )
    return {str(k): str(v) for k, v in value.items()}
