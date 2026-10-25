"""Load OpenAPI 3.0/3.1 contracts and select documented response schemas."""

from __future__ import annotations

import pathlib
from urllib.parse import urlsplit
from urllib.request import url2pathname

import httpx
import jsonref
import openapi_spec_validator
import yaml

from driftcheck.errors import SpecError
from driftcheck.models import ExpectedResponse

_HTTP_TIMEOUT_SECONDS = 10.0


def _read_raw(location: str) -> tuple[str, str]:
    if location.startswith(("http://", "https://")):
        try:
            response = httpx.get(location, timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True)
            response.raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise SpecError(f"Could not fetch spec from {location!r}: {exc}") from exc
        return response.text, str(response.url)
    if location.startswith("file:"):
        location = url2pathname(urlsplit(location).path)
    try:
        path = pathlib.Path(location)
        return path.read_text(encoding="utf-8-sig"), path.resolve().as_uri()
    except (OSError, UnicodeError, ValueError) as exc:
        raise SpecError(f"Could not read spec file {location!r}: {exc}") from exc


def _load_document(location: str):
    raw, _ = _read_raw(location)
    return _parse_document(raw, location)


def _parse_document(raw: str, location: str):
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SpecError(f"Could not parse spec at {location!r}: {exc}") from exc


class _OpenAPI30Validator(openapi_spec_validator.OpenAPIV30SpecValidator):
    resolver_handlers = {"file": _load_document, "http": _load_document, "https": _load_document}


class _OpenAPI31Validator(openapi_spec_validator.OpenAPIV31SpecValidator):
    resolver_handlers = {"file": _load_document, "http": _load_document, "https": _load_document}


def _materialize(value, *, siblings: bool = False, _memo=None):
    """Remove lazy proxies while retaining recursive schema constraints.

    A memo preserves recursive containers instead of replacing repeated schemas
    with an unconstrained ``{}``. The validator then follows the finite response
    body through the recursive schema. OpenAPI 3.1 schema $ref siblings apply in
    addition to the referenced constraints; OpenAPI 3.0 ignores those siblings.
    """
    if _memo is None:
        _memo = {}
    if isinstance(value, jsonref.JsonRef):
        reference = value.__reference__
        if siblings and len(reference) > 1:
            marker = id(value)
            if marker not in _memo:
                result = {}
                _memo[marker] = result
                result.update(
                    {
                        key: _materialize(item, siblings=siblings, _memo=_memo)
                        for key, item in reference.items()
                        if key != "$ref"
                    }
                )
                # Keep siblings at the containing schema scope so keywords such
                # as unevaluatedProperties can see properties evaluated by $ref.
                # Existing allOf branches still apply alongside the reference.
                result["allOf"] = [
                    _materialize(value.__subject__, siblings=siblings, _memo=_memo),
                    *result.get("allOf", []),
                ]
            return _memo[marker]
        value = value.__subject__
    if isinstance(value, (dict, list)):
        marker = id(value)
        if marker in _memo:
            return _memo[marker]
        result = {} if isinstance(value, dict) else []
        _memo[marker] = result
        if isinstance(value, dict):
            result.update(
                (key, _materialize(item, siblings=siblings, _memo=_memo))
                for key, item in value.items()
            )
        else:
            result.extend(_materialize(item, siblings=siblings, _memo=_memo) for item in value)
        return result
    return value


class SpecModel:
    """Queryable OpenAPI document with resolved references."""

    def __init__(self, doc: dict) -> None:
        self._doc = doc

    @property
    def doc(self) -> dict:
        return self._doc

    def _operation(self, path: str, method: str):
        try:
            return self._doc["paths"][path][method.lower()]
        except (KeyError, TypeError):
            return None

    def has_operation(self, path: str, method: str) -> bool:
        return self._operation(path, method) is not None

    def _responses(self, path: str, method: str) -> dict:
        operation = self._operation(path, method)
        return operation.get("responses", {}) if isinstance(operation, dict) else {}

    def _response(self, path: str, method: str, status: int):
        responses = self._responses(path, method)
        for key in (str(status), status, f"{status // 100}XX", "default"):
            if key in responses:
                return responses[key]
        return None

    def is_documented_status(self, path: str, method: str, status: int | None) -> bool:
        return status is not None and self._response(path, method, status) is not None

    def documented_content_types(self, path: str, method: str, status: int) -> tuple[str, ...]:
        response = self._response(path, method, status)
        if response is None:
            return ()
        return tuple(response.get("content", {}))

    def matching_content_type(
        self, path: str, method: str, status: int, content_type: str | None
    ) -> str | None:
        if not content_type:
            return None
        media = content_type.split(";", 1)[0].strip().lower()
        declared = self.documented_content_types(path, method, status)
        for candidate in (media, f"{media.split('/')[0]}/*", "*/*"):
            for value in declared:
                if value.split(";", 1)[0].strip().lower() == candidate:
                    return value
        return None

    def expected_response(
        self, path: str, method: str, status: int, content_type: str | None = None
    ) -> ExpectedResponse | None:
        """Select the exact status, then its range, then ``default`` response.

        With a Content-Type, select its most specific declared media range.
        Without one, prefer JSON (including vendor +json) for callers querying
        a schema without a live response.
        """
        try:
            response = self._response(path, method, status)
            if response is None:
                return None
            content = response.get("content", {})
            if content_type is None:
                media_types = sorted(
                    content,
                    key=lambda value: (
                        value != "application/json",
                        not value.endswith("+json"),
                        value,
                    ),
                )
            else:
                matched = self.matching_content_type(path, method, status, content_type)
                media_types = (matched,) if matched else ()
            for media_type in media_types:
                if media_type not in content:
                    continue
                schema = content[media_type].get("schema")
                if schema is None:
                    return None
                version = self._doc.get("openapi", "3.1.0")
                return ExpectedResponse(
                    status=status,
                    schema=_materialize(schema, siblings=version.startswith("3.1.")),
                    openapi_version=version,
                    media_type=media_type,
                )
            return None
        except Exception as exc:
            raise SpecError(
                f"Could not resolve response schema for {method} {path}: {exc}"
            ) from exc

    def documented_statuses(self, path: str, method: str) -> list[int]:
        """Return explicit numeric status codes (ranges/default are not integers)."""
        return sorted(
            int(key)
            for key in self._responses(path, method)
            if not isinstance(key, bool) and str(key).isdigit()
        )


def load_spec(location: str) -> SpecModel:
    """Read and validate a local/remote JSON or YAML OpenAPI 3.0/3.1 document."""
    raw, base_uri = _read_raw(location)
    doc = _parse_document(raw, location)
    if not isinstance(doc, dict):
        raise SpecError(f"Spec at {location!r} did not parse to a mapping.")
    version = doc.get("openapi")
    if not isinstance(version, str) or not version.startswith(("3.0.", "3.1.")):
        raise SpecError("Only OpenAPI 3.0 and 3.1 specifications are supported.")
    try:
        validator = _OpenAPI30Validator if version.startswith("3.0.") else _OpenAPI31Validator
        validator(doc, base_uri=base_uri).validate()
        resolved = jsonref.replace_refs(
            doc, base_uri=base_uri, loader=_load_document, lazy_load=False
        )
    except Exception as exc:
        raise SpecError(f"Could not validate or resolve spec at {location!r}: {exc}") from exc
    return SpecModel(resolved)
