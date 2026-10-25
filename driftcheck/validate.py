"""Validate a live response body against its documented JSON Schema.

This is the heart of drift detection: given the schema the spec promises for a
particular status code (:class:`ExpectedResponse`) and what the server actually
returned (:class:`ActualResponse`), produce a deterministic list of
:class:`Mismatch` records -- one per schema violation. Status-code, transport and
"undocumented status" drift are the engine's concern; here we only compare the
response *body* against the *schema*.
"""

from __future__ import annotations

import re
from typing import Any

from jsonschema.exceptions import ValidationError
from openapi_schema_validator import OAS30ReadValidator, OAS31Validator

from driftcheck.errors import SpecError
from driftcheck.models import ActualResponse, ExpectedResponse, Mismatch

# Map a jsonschema validator keyword to one of our Mismatch.kind values.
# "type" is refined to "nullable" at runtime when the offending value is null;
# any keyword not listed here falls back to "type" (see _kind_for).
_KEYWORD_KINDS = {
    "type": "type",
    "required": "required",
    "enum": "enum",
    "additionalProperties": "extra",
}


def validate_response(expected: ExpectedResponse, actual: ActualResponse) -> list[Mismatch]:
    """Return one Mismatch per schema violation found in ``actual.body``.

    The body is validated using its OpenAPI version's response rules. Errors
    are emitted in a deterministic order (by JSON location, then validator keyword,
    then message). Returns ``[]`` when the body fully conforms to the schema.
    """
    validator_class = (
        OAS30ReadValidator if expected.openapi_version.startswith("3.0.") else OAS31Validator
    )
    validator = validator_class(expected.schema)
    try:
        errors = sorted(
            validator.iter_errors(actual.body),
            key=lambda error: (_location(error), str(error.validator), error.message),
        )
    except Exception as exc:
        raise SpecError(f"Could not validate response schema: {exc}") from exc
    return [_to_mismatch(error) for error in errors]


def _to_mismatch(error: ValidationError) -> Mismatch:
    """Convert a single jsonschema validation error into a Mismatch."""
    kind = _kind_for(error)
    return Mismatch(
        kind=kind,
        location=_location(error),
        expected=_expected_for(error),
        actual=_actual_for(error, kind),
        message=error.message,
    )


def _kind_for(error: ValidationError) -> str:
    """Classify a validation error into a Mismatch.kind."""
    if error.validator == "type":
        return "nullable" if error.instance is None else "type"
    return _KEYWORD_KINDS.get(str(error.validator), "type")


def _location(error: ValidationError) -> str:
    """Return the JSON path of the offending value, e.g. ``$.tags[0].id``."""
    path = getattr(error, "json_path", None)
    if path:
        return path
    # Fallback for jsonschema builds without ``json_path``.
    location = "$"
    for segment in error.absolute_path:
        if isinstance(segment, int):
            location += f"[{segment}]"
        else:
            location += f".{segment}"
    return location


def _expected_for(error: ValidationError) -> str:
    """A short human string describing the constraint that was violated."""
    keyword = error.validator
    value = error.validator_value
    if keyword == "type":
        return f"type={_join(value)}"
    if keyword == "required":
        return "required"
    if keyword == "enum":
        return f"one of [{_join(value, sep=', ')}]"
    if keyword == "additionalProperties":
        return "no additional properties"
    return f"{keyword}={value}"


def _actual_for(error: ValidationError, kind: str) -> str:
    """A short string of the offending value, its type, or why it is wrong."""
    if kind == "nullable":
        return "None"
    if kind == "required":
        return "missing"
    if kind == "extra":
        return _extra_keys(error)
    if error.validator == "type":
        return type(error.instance).__name__
    return _short(error.instance)


def _extra_keys(error: ValidationError) -> str:
    """List the undocumented keys that triggered an additionalProperties error."""
    schema = error.schema if isinstance(error.schema, dict) else {}
    instance = error.instance
    if isinstance(instance, dict):
        allowed = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        extras = sorted(
            key
            for key in instance
            if key not in allowed and not any(re.search(pattern, key) for pattern in patterns)
        )
        if extras:
            return ", ".join(extras)
    return "unexpected key"


def _join(value: Any, sep: str = ",") -> str:
    """Join a scalar or an iterable of schema values into a short string."""
    if isinstance(value, (list, tuple)):
        return sep.join(str(item) for item in value)
    return str(value)


def _short(value: Any, limit: int = 60) -> str:
    """A compact representation of an instance value for display."""
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        return type(value).__name__
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
