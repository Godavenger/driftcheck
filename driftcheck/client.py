"""HTTP client: perform the single live call for one endpoint check.

Transport and timeout failures are captured into an :class:`ActualResponse`
(``status=None`` with a non-empty ``error``) instead of being raised, so the engine
can report drift for a single endpoint rather than aborting the whole run.
"""

from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

import httpx

from driftcheck.errors import ConfigError
from driftcheck.models import ActualResponse, Config, EndpointCheck

_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")


def auth_headers(config: Config) -> dict[str, str]:
    """Resolve configured credentials without ever including their value in errors."""
    if not config.auth_header_env:
        return {}
    token = os.environ.get(config.auth_header_env)
    if not token or not token.strip():
        raise ConfigError(
            f"authentication environment variable '{config.auth_header_env}' is not set or is empty"
        )
    if not token.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise ConfigError(
            f"authentication environment variable '{config.auth_header_env}' "
            "is not a valid HTTP header value"
        )
    return {config.auth_header_name: token}


def _endpoint_path(check: EndpointCheck) -> str:
    """Substitute literal OpenAPI parameter names and keep each value in its segment."""
    if any(char in _PATH_PARAMETER.sub("", check.path) for char in "{}"):
        raise ValueError(f"malformed path placeholders in {check.path!r}")

    def replace(match: re.Match) -> str:
        value = str(check.path_params[match.group(1)])
        # Dot-only segments would otherwise be normalized as navigation by HTTP clients.
        if value in {".", ".."}:
            return value.replace(".", "%2E")
        return quote(value, safe="")

    return _PATH_PARAMETER.sub(replace, check.path)


def _reject_json_constant(value: str) -> None:
    """JSON does not permit the NaN/Infinity extensions Python accepts by default."""
    raise ValueError(f"invalid JSON constant: {value}")


def call_endpoint(config: Config, check: EndpointCheck) -> ActualResponse:
    """Call one endpoint and return the observed response.

    Builds the URL from ``config.base_url`` + ``check.path`` with ``{name}``
    placeholders filled from ``check.path_params``, attaches query params and an
    optional auth header, times the request, and parses the body as JSON when
    possible (falling back to raw text). Timeout/transport errors are returned as
    an ``ActualResponse`` with ``status=None`` and ``error`` set, never raised.
    """
    try:
        url = config.base_url.rstrip("/") + _endpoint_path(check)
        headers = auth_headers(config)
    except (KeyError, ValueError, ConfigError) as exc:
        return ActualResponse(
            status=None,
            body=None,
            elapsed_ms=0.0,
            error=f"missing path parameter {exc}" if isinstance(exc, KeyError) else str(exc),
        )

    start = time.perf_counter()
    try:
        resp = httpx.request(
            check.method,
            url,
            params=check.query_params or None,
            headers=headers or None,
            timeout=config.timeout_seconds,
        )
    except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        message = str(exc) or type(exc).__name__
        for token in headers.values():
            message = message.replace(token, "[redacted]")
        return ActualResponse(status=None, body=None, elapsed_ms=elapsed_ms, error=message)

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower() or None
    body = resp.text
    json_error = None
    if content_type is None or content_type == "application/json" or content_type.endswith("+json"):
        try:
            body = resp.json(parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            json_error = "response body is not valid JSON"

    return ActualResponse(
        status=resp.status_code,
        body=body,
        elapsed_ms=elapsed_ms,
        error=None,
        content_type=content_type,
        body_present=bool(resp.content),
        json_error=json_error,
    )
