"""Tests for driftcheck.spec against the committed petstore fixture.

Every test loads the spec fresh so the cases stay independent and deterministic.
No network access is needed: the fixture is read from a local file.
"""

import pathlib

from driftcheck.models import ExpectedResponse
from driftcheck.spec import SpecModel, load_spec

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "petstore.json"


def _has_no_ref(node) -> bool:
    """Recursively report whether a resolved schema is free of any ``$ref`` key."""
    if isinstance(node, dict):
        if "$ref" in node:
            return False
        return all(_has_no_ref(value) for value in node.values())
    if isinstance(node, list):
        return all(_has_no_ref(item) for item in node)
    return True


def test_load_spec_returns_spec_model():
    model = load_spec(str(FIXTURE))
    assert isinstance(model, SpecModel)


def test_documented_statuses_pet_by_id():
    model = load_spec(str(FIXTURE))
    assert model.documented_statuses("/pet/{petId}", "GET") == [200, 400, 404]


def test_documented_statuses_is_case_insensitive():
    model = load_spec(str(FIXTURE))
    assert model.documented_statuses("/pet/{petId}", "get") == [200, 400, 404]


def test_documented_statuses_single_status():
    model = load_spec(str(FIXTURE))
    assert model.documented_statuses("/store/inventory", "GET") == [200]


def test_documented_statuses_unknown_path_is_empty():
    model = load_spec(str(FIXTURE))
    assert model.documented_statuses("/does/not/exist", "GET") == []


def test_expected_response_is_present_and_resolved():
    model = load_spec(str(FIXTURE))
    expected = model.expected_response("/pet/{petId}", "GET", 200)
    assert expected is not None
    assert isinstance(expected, ExpectedResponse)
    assert expected.status == 200
    assert "id" in expected.schema["properties"]


def test_expected_response_has_no_unresolved_refs():
    model = load_spec(str(FIXTURE))
    expected = model.expected_response("/pet/{petId}", "GET", 200)
    assert expected is not None
    # The Pet schema pulls in Category and Tag via $ref; all must be inlined.
    assert _has_no_ref(expected.schema)


def test_expected_response_resolves_nested_refs():
    model = load_spec(str(FIXTURE))
    expected = model.expected_response("/pet/{petId}", "GET", 200)
    assert expected is not None
    category = expected.schema["properties"]["category"]
    assert "name" in category["properties"]


def test_expected_response_array_schema_is_resolved():
    model = load_spec(str(FIXTURE))
    expected = model.expected_response("/pet/findByStatus", "GET", 200)
    assert expected is not None
    assert expected.schema["type"] == "array"
    assert _has_no_ref(expected.schema)
    assert "id" in expected.schema["items"]["properties"]


def test_expected_response_without_json_content_is_none():
    model = load_spec(str(FIXTURE))
    # 400 is documented but carries only a description, no response body.
    assert model.expected_response("/pet/{petId}", "GET", 400) is None


def test_expected_response_undocumented_status_is_none():
    model = load_spec(str(FIXTURE))
    assert model.expected_response("/pet/{petId}", "GET", 500) is None


def test_expected_response_unknown_path_is_none():
    model = load_spec(str(FIXTURE))
    assert model.expected_response("/does/not/exist", "GET", 200) is None
