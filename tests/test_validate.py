"""Unit tests for ``driftcheck.validate.validate_response``.

One test per Mismatch kind the validator derives from a schema violation
(type, required, nullable, enum, extra). Each builds a tiny inline schema and
body and asserts both the classified ``kind`` and the JSON-path ``location``.
"""

from __future__ import annotations

import pytest

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


def test_openapi_30_nullable_accepts_null():
    expected = ExpectedResponse(200, {"type": "string", "nullable": True}, "3.0.3")
    assert validate_response(expected, _actual(None)) == []
    assert validate_response(expected, _actual(42))


def test_openapi_31_uses_null_union_instead_of_nullable_annotation():
    expected = ExpectedResponse(200, {"type": ["string", "null"]}, "3.1.0")
    assert validate_response(expected, _actual(None)) == []
    expected = ExpectedResponse(200, {"type": "string", "nullable": True}, "3.1.0")
    assert validate_response(expected, _actual(None))


@pytest.mark.parametrize(
    "exclusive,body,conformant", [(True, 5, False), (False, 5, True), (True, 6, True)]
)
def test_openapi_30_boolean_exclusive_minimum(exclusive, body, conformant):
    expected = ExpectedResponse(
        200, {"type": "number", "minimum": 5, "exclusiveMinimum": exclusive}, "3.0.3"
    )
    assert (validate_response(expected, _actual(body)) == []) is conformant


def test_openapi_30_write_only_required_property_is_not_required_in_response():
    expected = ExpectedResponse(
        200,
        {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "readOnly": True},
                "password": {"type": "string", "writeOnly": True},
            },
            "required": ["id", "password"],
        },
        "3.0.3",
    )
    assert validate_response(expected, _actual({"id": 1})) == []
    assert validate_response(expected, _actual({}))[0].kind == "required"
    assert validate_response(expected, _actual({"id": 1, "password": "secret"}))


def test_extra_keys_excludes_properties_allowed_by_a_pattern():
    expected = ExpectedResponse(
        200,
        {
            "type": "object",
            "patternProperties": {"^x-": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    mismatches = validate_response(expected, _actual({"x-good": "yes", "bad": "no"}))
    assert len(mismatches) == 1
    assert mismatches[0].actual == "bad"


def test_boolean_false_schema_rejects_every_value():
    assert validate_response(ExpectedResponse(200, False), _actual(None))
