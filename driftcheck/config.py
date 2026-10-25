"""Load and validate a driftcheck YAML config file into a :class:`Config`.

This module owns the config boundary: it turns an on-disk YAML file into a
validated ``Config`` of ``EndpointCheck`` objects, raising ``ConfigError`` with a
readable, key-naming message for anything missing, empty, or mistyped.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from driftcheck.errors import ConfigError
from driftcheck.models import Config, EndpointCheck

_METHODS = {"GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE"}
_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def load_config(path: str) -> Config:
    """Parse and validate the YAML config at ``path`` into a ``Config``.

    Raises ``ConfigError`` if the file is missing, is not valid YAML, is not a
    top-level mapping, or is missing/mistyped on a required key.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"config file could not be read: {path} ({exc})") from exc
    except UnicodeError as exc:
        raise ConfigError(f"config file must be UTF-8: {path}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc

    if raw is None:
        raise ConfigError("config file is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"config must be a YAML mapping, got {type(raw).__name__}")
    _reject_unknown(
        raw,
        {"spec", "base_url", "endpoints", "auth_header_env", "auth_header_name", "timeout_seconds"},
        "config",
    )

    spec = _require_str(raw, "spec")
    base_url = _require_str(raw, "base_url")
    _validate_base_url(base_url)
    if not spec.startswith(("http://", "https://")):
        if "://" in spec:
            raise ConfigError("config key 'spec' must be a file path or an HTTP(S) URL")
        spec = str((Path(path).resolve().parent / spec).resolve())
    endpoints = _parse_endpoints(raw)

    auth_header_env = raw.get("auth_header_env")
    if auth_header_env is not None and (
        not isinstance(auth_header_env, str)
        or not auth_header_env.strip()
        or any(char in auth_header_env for char in "=\x00\r\n")
    ):
        raise ConfigError("config key 'auth_header_env' must be a non-empty variable name or null")

    auth_header_name = raw.get("auth_header_name")
    if auth_header_name is None:
        auth_header_name = "Authorization"
    elif not isinstance(auth_header_name, str) or not _HEADER_NAME.fullmatch(auth_header_name):
        raise ConfigError("config key 'auth_header_name' must be a valid HTTP header name")

    raw_timeout = raw.get("timeout_seconds")
    if raw_timeout is None:
        timeout_seconds = 10.0
    elif isinstance(raw_timeout, bool) or not isinstance(raw_timeout, int | float):
        raise ConfigError("config key 'timeout_seconds' must be a number")
    else:
        try:
            timeout_seconds = float(raw_timeout)
        except OverflowError as exc:
            raise ConfigError("config key 'timeout_seconds' is too large") from exc
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ConfigError("config key 'timeout_seconds' must be a finite number greater than zero")

    return Config(
        spec=spec,
        base_url=base_url,
        endpoints=endpoints,
        auth_header_env=auth_header_env,
        auth_header_name=auth_header_name,
        timeout_seconds=timeout_seconds,
    )


def _validate_base_url(value: str) -> None:
    """Require an absolute HTTP base URL with no query or fragment."""
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and not parts.query
            and not parts.fragment
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
        # Accessing port also validates invalid/out-of-range ports.
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError(
            "config key 'base_url' must be an absolute HTTP(S) URL without a query or fragment"
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


def _reject_unknown(raw: dict, allowed: set[str], context: str) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise ConfigError(f"{context} has unknown keys: {', '.join(unknown)}")


def _parse_endpoints(raw: dict[str, Any]) -> list[EndpointCheck]:
    """Validate the ``endpoints`` key and parse each entry into an EndpointCheck."""
    if "endpoints" not in raw or raw["endpoints"] is None:
        raise ConfigError("missing required config key: endpoints")
    endpoints = raw["endpoints"]
    if not isinstance(endpoints, list):
        raise ConfigError(f"config key 'endpoints' must be a list, got {type(endpoints).__name__}")
    if not endpoints:
        raise ConfigError("config key 'endpoints' must not be empty")
    parsed = [_parse_endpoint(item, index) for index, item in enumerate(endpoints)]
    names = [endpoint.name for endpoint in parsed]
    if len(names) != len(set(names)):
        raise ConfigError("endpoint 'name' values must be unique")
    return parsed


def _parse_endpoint(item: Any, index: int) -> EndpointCheck:
    """Parse one endpoint mapping into an EndpointCheck, naming bad fields."""
    if not isinstance(item, dict):
        raise ConfigError(f"endpoint #{index} must be a mapping, got {type(item).__name__}")

    name = item.get("name")
    if not isinstance(name, str) or name.strip() == "":
        raise ConfigError(f"endpoint #{index} is missing a non-empty 'name'")
    _reject_unknown(
        item,
        {"name", "path", "method", "path_params", "query_params", "expected_status"},
        f"endpoint '{name}'",
    )

    path = item.get("path")
    if not isinstance(path, str) or path.strip() == "":
        raise ConfigError(f"endpoint '{name}' is missing a non-empty 'path'")
    if not path.startswith("/") or "?" in path or "#" in path or any(c.isspace() for c in path):
        raise ConfigError(
            f"endpoint '{name}' path must start with '/' and have no query, fragment, or whitespace"
        )
    if any(char in _PATH_PARAMETER.sub("", path) for char in "{}"):
        raise ConfigError(f"endpoint '{name}' has malformed path placeholders")

    method = item.get("method", "GET")
    if not isinstance(method, str) or method.strip() == "":
        raise ConfigError(f"endpoint '{name}' has an invalid 'method'")
    method = method.upper()
    if method not in _METHODS:
        raise ConfigError(f"endpoint '{name}' has an unsupported HTTP 'method': {method}")

    expected_status = item.get("expected_status")
    if expected_status is not None and (
        isinstance(expected_status, bool) or not isinstance(expected_status, int)
    ):
        raise ConfigError(f"endpoint '{name}' has a non-integer 'expected_status'")
    if expected_status is not None and not 100 <= expected_status <= 599:
        raise ConfigError(f"endpoint '{name}' 'expected_status' must be between 100 and 599")

    path_params = _coerce_str_map(item.get("path_params"), name, "path_params")
    placeholders = set(_PATH_PARAMETER.findall(path))
    missing = placeholders - path_params.keys()
    if missing:
        raise ConfigError(f"endpoint '{name}' is missing path_params: {', '.join(sorted(missing))}")
    unused = path_params.keys() - placeholders
    if unused:
        raise ConfigError(f"endpoint '{name}' has unused path_params: {', '.join(sorted(unused))}")

    return EndpointCheck(
        name=name,
        path=path,
        method=method,
        path_params=path_params,
        query_params=_coerce_str_map(item.get("query_params"), name, "query_params"),
        expected_status=expected_status,
    )


def _coerce_str_map(value: Any, endpoint: str, key: str) -> dict[str, str]:
    """Encode scalar parameters; nested values need explicit OpenAPI serialization."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"endpoint '{endpoint}' key '{key}' must be a mapping, got {type(value).__name__}"
        )
    result = {}
    for param, item in value.items():
        if not isinstance(param, str) or not param:
            raise ConfigError(f"endpoint '{endpoint}' key '{key}' must have non-empty string keys")
        if not isinstance(item, str | int | float | bool) or (
            isinstance(item, float) and not math.isfinite(item)
        ):
            raise ConfigError(
                f"endpoint '{endpoint}' key '{key}.{param}' must be a string, number, or boolean"
            )
        result[param] = str(item).lower() if isinstance(item, bool) else str(item)
    return result
