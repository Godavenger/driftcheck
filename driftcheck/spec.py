"""Load and query OpenAPI specifications for driftcheck.

Parses an OpenAPI 3.0/3.1 document from a local path or an http(s) URL (JSON or
YAML), validates it, resolves every ``$ref`` so stored schemas are
self-contained, and exposes the small query surface the drift engine relies on.
"""

from __future__ import annotations

import pathlib

import httpx
import jsonref
import openapi_spec_validator
import yaml

from driftcheck.errors import SpecError
from driftcheck.models import ExpectedResponse

_HTTP_TIMEOUT_SECONDS = 10.0
_MAX_MATERIALIZE_DEPTH = 200


def _read_raw(location: str) -> str:
    """Return raw spec text from an http(s) URL or a local file path."""
    if location.startswith(("http://", "https://")):
        try:
            response = httpx.get(location, timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SpecError(f"Could not fetch spec from {location!r}: {exc}") from exc
        return response.text
    try:
        return pathlib.Path(location).read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"Could not read spec file {location!r}: {exc}") from exc


def _validate_openapi(doc: dict) -> None:
    """Validate a parsed OpenAPI document, raising SpecError on any failure."""
    validator = getattr(openapi_spec_validator, "validate", None) or getattr(
        openapi_spec_validator, "validate_spec", None
    )
    if validator is None:  # pragma: no cover - defensive
        raise SpecError("openapi_spec_validator exposes no validate function.")
    try:
        validator(doc)
    except Exception as exc:  # noqa: BLE001 - normalize everything to SpecError
        raise SpecError(f"OpenAPI validation failed: {exc}") from exc


def _materialize(value, _depth: int = 0, _seen=None):
    """Deep-copy a resolved node into plain dict/list/scalars (no jsonref proxies).

    jsonref returns lazy proxies in place of ``$ref`` nodes; walking them here
    produces self-contained plain containers. A path-local ``_seen`` set and a
    depth cap guarantee termination even on circular specs.
    """
    if _seen is None:
        _seen = frozenset()
    if _depth > _MAX_MATERIALIZE_DEPTH:
        if isinstance(value, dict):
            return {}
        if isinstance(value, list):
            return []
        return value
    if isinstance(value, dict):
        marker = id(value)
        if marker in _seen:
            return {}
        nxt = _seen | {marker}
        return {k: _materialize(v, _depth + 1, nxt) for k, v in value.items()}
    if isinstance(value, list):
        marker = id(value)
        if marker in _seen:
            return []
        nxt = _seen | {marker}
        return [_materialize(v, _depth + 1, nxt) for v in value]
    return value


class SpecModel:
    """Queryable wrapper around a validated, $ref-resolved OpenAPI document."""

    def __init__(self, doc: dict) -> None:
        self._doc = doc

    @property
    def doc(self) -> dict:
        """The resolved OpenAPI document (all schemas are self-contained)."""
        return self._doc

    def _operation(self, path: str, method: str):
        """Return the operation object for path+method, or None if absent."""
        try:
            return self._doc["paths"][path][method.lower()]
        except (KeyError, TypeError):
            return None

    def expected_response(self, path: str, method: str, status: int) -> ExpectedResponse | None:
        """Return the documented JSON Schema for path+method+status, else None.

        Yields None whenever any link in the responses -> status -> content ->
        ``application/json`` -> schema chain is absent (including no JSON body).
        """
        operation = self._operation(path, method)
        if operation is None:
            return None
        try:
            responses = operation["responses"]
        except (KeyError, TypeError):
            return None
        response = None
        for key in (str(status), status):
            try:
                if key in responses:
                    response = responses[key]
                    break
            except TypeError:
                continue
        if response is None:
            return None
        try:
            schema = response["content"]["application/json"]["schema"]
        except (KeyError, TypeError):
            return None
        if schema is None:
            return None
        return ExpectedResponse(status=status, schema=_materialize(schema))

    def documented_statuses(self, path: str, method: str) -> list[int]:
        """Return the integer status codes documented for path+method, ascending.

        Non-integer response keys (e.g. ``"default"``) are skipped; method is
        case-insensitive.
        """
        operation = self._operation(path, method)
        if operation is None:
            return []
        try:
            responses = operation["responses"]
        except (KeyError, TypeError):
            return []
        statuses: list[int] = []
        for key in responses:
            if isinstance(key, bool):
                continue
            if isinstance(key, int):
                statuses.append(key)
            elif isinstance(key, str) and key.isdigit():
                statuses.append(int(key))
        return sorted(statuses)


def load_spec(location: str) -> SpecModel:
    """Load, validate, and $ref-resolve an OpenAPI spec into a SpecModel.

    ``location`` may be a local file path or an http(s) URL; JSON and YAML are
    both accepted. Any load, parse, or validation failure raises SpecError.
    """
    raw = _read_raw(location)
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SpecError(f"Could not parse spec at {location!r}: {exc}") from exc
    if not isinstance(doc, dict):
        raise SpecError(f"Spec at {location!r} did not parse to a mapping.")
    _validate_openapi(doc)
    try:
        resolved = jsonref.replace_refs(doc)
    except Exception as exc:  # noqa: BLE001 - normalize everything to SpecError
        raise SpecError(f"Could not resolve $refs in spec at {location!r}: {exc}") from exc
    return SpecModel(resolved)
