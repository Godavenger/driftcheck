"""Unit tests for ``driftcheck.validate.validate_response``.

One test per Mismatch kind the validator derives from a schema violation
(type, required, nullable, enum, extra). Each builds a tiny inline schema and
body and asserts both the classified ``kind`` and the JSON-path ``location``.
"""

from __future__ import annotations

from driftcheck.models import ActualResponse, ExpectedResponse
from driftcheck.validate import validate_response


def _actual(body: object) -> ActualResponse:
    """Wrap a decoded body in an ActualResponse; status/timing are irrelevant here."""
    return ActualResponse(status=200, body=body, elapsed_ms=1.0)


def test_type_mismatch_is_classified_as_type() -> None:
    """A wrong scalar type at a property yields kind 'type' at that property's path."""
    expected = ExpectedResponse(
        status=200,
        schema={"type": "object", "properties": {"id": {"type": "string"}}},
    )

    mismatches = validate_response(expected, _actual({"id": 123}))

    assert len(mismatches) == 1
    assert mismatches[0].kind == "type"
    assert mismatches[0].location == "$.id"


def test_missing_required_property_is_classified_as_required() -> None:
    """A missing required property yields kind 'required' at the object's path."""
    expected = ExpectedResponse(
        status=200,
        schema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    )

    mismatches = validate_response(expected, _actual({}))

    assert len(mismatches) == 1
    assert mismatches[0].kind == "required"
    assert mismatches[0].location == "$"


def test_null_for_typed_field_is_classified_as_nullable() -> None:
    """A null where a concrete type is required yields kind 'nullable'."""
    expected = ExpectedResponse(status=200, schema={"type": "string"})

    mismatches = validate_response(expected, _actual(None))

    assert len(mismatches) == 1
    assert mismatches[0].kind == "nullable"
    assert mismatches[0].location == "$"


def test_value_outside_enum_is_classified_as_enum() -> None:
    """A value not in the enum yields kind 'enum' at that property's path."""
    expected = ExpectedResponse(
        status=200,
        schema={"type": "object", "properties": {"status": {"enum": ["a", "b", "c"]}}},
    )

    mismatches = validate_response(expected, _actual({"status": "x"}))

    assert len(mismatches) == 1
    assert mismatches[0].kind == "enum"
    assert mismatches[0].location == "$.status"


def test_undocumented_property_is_classified_as_extra() -> None:
    """An unexpected key under additionalProperties:false yields kind 'extra'."""
    expected = ExpectedResponse(
        status=200,
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string"}},
        },
    )

    mismatches = validate_response(expected, _actual({"id": "ok", "extra": "nope"}))

    assert len(mismatches) == 1
    assert mismatches[0].kind == "extra"
    assert mismatches[0].location == "$"
