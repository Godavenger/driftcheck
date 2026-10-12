"""HTTP client: perform the single live call for one endpoint check.

Transport and timeout failures are captured into an :class:`ActualResponse`
(``status=None`` with a non-empty ``error``) instead of being raised, so the engine
can report drift for a single endpoint rather than aborting the whole run.
"""

from __future__ import annotations

import os
import time

import httpx

from driftcheck.models import ActualResponse, Config, EndpointCheck


def call_endpoint(config: Config, check: EndpointCheck) -> ActualResponse:
    """Call one endpoint and return the observed response.

    Builds the URL from ``config.base_url`` + ``check.path`` with ``{name}``
    placeholders filled from ``check.path_params``, attaches query params and an
    optional auth header, times the request, and parses the body as JSON when
    possible (falling back to raw text). Timeout/transport errors are returned as
    an ``ActualResponse`` with ``status=None`` and ``error`` set, never raised.
    """
    try:
        url = config.base_url.rstrip("/") + check.path.format(**check.path_params)
    except (KeyError, IndexError) as exc:
        return ActualResponse(
            status=None,
            body=None,
            elapsed_ms=0.0,
            error=f"missing path parameter {exc} for path {check.path!r}",
        )

    headers: dict[str, str] = {}
    if config.auth_header_env:
        token = os.environ.get(config.auth_header_env)
        if token:
            headers[config.auth_header_name] = token

    start = time.perf_counter()
    try:
        resp = httpx.request(
            check.method,
            url,
            params=check.query_params or None,
            headers=headers or None,
            timeout=config.timeout_seconds,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return ActualResponse(status=None, body=None, elapsed_ms=elapsed_ms, error=str(exc))

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    try:
        body = resp.json()
    except Exception:  # any JSON/decoding failure -> fall back to raw text
        body = resp.text

    return ActualResponse(
        status=resp.status_code,
        body=body,
        elapsed_ms=elapsed_ms,
        error=None,
    )
