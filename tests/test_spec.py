"""Tests for driftcheck.spec against the committed petstore fixture.

Every test loads the spec fresh so the cases stay independent and deterministic.
No network access is needed: the fixture is read from a local file.
"""

import pathlib

import httpx
import pytest
import respx
import yaml

from driftcheck.errors import SpecError
from driftcheck.models import ActualResponse, ExpectedResponse
from driftcheck.spec import SpecModel, load_spec
from driftcheck.validate import validate_response

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


def _write_spec(tmp_path, schema, *, version="3.1.0", components=None):
    document = {
        "openapi": version,
        "info": {"title": "Test", "version": "1.0"},
        "paths": {
            "/item": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Item",
                            "content": {"application/json": {"schema": schema}},
                        }
                    }
                }
            }
        },
    }
    if components is not None:
        document["components"] = {"schemas": components}
    path = tmp_path / "openapi.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_recursive_schema_checks_nested_values(tmp_path):
    node = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "integer"},
            "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
        },
    }
    path = _write_spec(tmp_path, {"$ref": "#/components/schemas/Node"}, components={"Node": node})
    expected = load_spec(str(path)).expected_response("/item", "GET", 200)
    mismatches = validate_response(
        expected,
        ActualResponse(200, {"id": 1, "children": [{"id": 2, "children": [{"id": "bad"}]}]}, 0),
    )
    assert [(item.kind, item.location) for item in mismatches] == [
        ("type", "$.children[0].children[0].id")
    ]


def test_relative_yaml_schema_reference_resolves(tmp_path):
    (tmp_path / "schemas.yaml").write_text("Item:\n  type: integer\n", encoding="utf-8")
    path = _write_spec(tmp_path, {"$ref": "./schemas.yaml#/Item"})
    expected = load_spec(str(path)).expected_response("/item", "GET", 200)
    assert expected.schema == {"type": "integer"}


def test_broken_ref_fails_while_loading(tmp_path):
    path = _write_spec(tmp_path, {"$ref": "#/components/schemas/Missing"})
    with pytest.raises(SpecError):
        load_spec(str(path))


@pytest.mark.parametrize("version", ["2.0", "3.2.0"])
def test_unsupported_openapi_version_is_rejected(tmp_path, version):
    path = _write_spec(tmp_path, {}, version=version)
    with pytest.raises(SpecError, match="Only OpenAPI 3.0 and 3.1"):
        load_spec(str(path))


def test_openapi_31_ref_siblings_add_constraints(tmp_path):
    path = _write_spec(
        tmp_path,
        {"$ref": "#/components/schemas/Item", "maximum": 3},
        components={"Item": {"type": "integer", "minimum": 2}},
    )
    expected = load_spec(str(path)).expected_response("/item", "GET", 200)
    assert validate_response(expected, ActualResponse(200, 2, 0)) == []
    assert validate_response(expected, ActualResponse(200, 1, 0))
    assert validate_response(expected, ActualResponse(200, 4, 0))


def test_openapi_31_unevaluated_properties_sees_ref_and_sibling_allof(tmp_path):
    path = _write_spec(
        tmp_path,
        {
            "$ref": "#/components/schemas/Item",
            "allOf": [{"properties": {"name": {"type": "string"}}}],
            "unevaluatedProperties": False,
        },
        components={"Item": {"type": "object", "properties": {"id": {"type": "integer"}}}},
    )
    expected = load_spec(str(path)).expected_response("/item", "GET", 200)
    assert validate_response(expected, ActualResponse(200, {"id": 1}, 0)) == []
    assert validate_response(expected, ActualResponse(200, {"id": 1, "name": "item"}, 0)) == []
    assert validate_response(expected, ActualResponse(200, {"id": "bad"}, 0))
    assert validate_response(expected, ActualResponse(200, {"name": 123}, 0))
    assert validate_response(expected, ActualResponse(200, {"id": 1, "extra": True}, 0))


def test_openapi_30_ref_siblings_are_ignored(tmp_path):
    path = _write_spec(
        tmp_path,
        {"$ref": "#/components/schemas/Item", "maximum": 3},
        version="3.0.3",
        components={"Item": {"type": "integer"}},
    )
    expected = load_spec(str(path)).expected_response("/item", "GET", 200)
    assert validate_response(expected, ActualResponse(200, 4, 0)) == []


def test_false_boolean_response_schema_is_preserved(tmp_path):
    path = _write_spec(tmp_path, False)
    expected = load_spec(str(path)).expected_response("/item", "GET", 200)
    assert expected.schema is False
    assert validate_response(expected, ActualResponse(200, None, 0))


def test_status_selection_prefers_exact_then_range_then_default():
    responses = {
        status: {"description": "Response", "content": {"application/json": {"schema": schema}}}
        for status, schema in (
            ("200", {"const": "exact"}),
            ("2XX", {"const": "range"}),
            ("default", {"const": "fallback"}),
        )
    }
    spec = SpecModel({"paths": {"/item": {"get": {"responses": responses}}}})
    assert spec.expected_response("/item", "GET", 200).schema == {"const": "exact"}
    assert spec.expected_response("/item", "GET", 201).schema == {"const": "range"}
    assert spec.expected_response("/item", "GET", 503).schema == {"const": "fallback"}
    assert spec.is_documented_status("/item", "GET", 503)


def test_vendor_json_and_media_range_select_most_specific_schema():
    content = {
        "application/problem+json": {"schema": {"const": "specific"}},
        "application/*": {"schema": {"const": "type"}},
        "*/*": {"schema": {"const": "any"}},
    }
    spec = SpecModel({"paths": {"/item": {"get": {"responses": {"200": {"content": content}}}}}})
    assert spec.expected_response(
        "/item", "GET", 200, "application/problem+json; charset=utf-8"
    ).schema == {"const": "specific"}
    assert spec.expected_response("/item", "GET", 200, "application/xml").schema == {
        "const": "type"
    }
    assert spec.expected_response("/item", "GET", 200, "text/plain").schema == {"const": "any"}


def test_redirected_remote_spec_resolves_relative_yaml_ref_from_final_url(tmp_path):
    path = _write_spec(tmp_path, {"$ref": "schemas.yaml#/Item"})
    with respx.mock as router:
        router.get("https://spec.test/openapi.yaml").respond(
            302, headers={"Location": "https://spec.test/v2/openapi.yaml"}
        )
        router.get("https://spec.test/v2/openapi.yaml").respond(200, text=path.read_text())
        external = router.get("https://spec.test/v2/schemas.yaml").respond(
            200, text="Item:\n  type: integer\n"
        )
        model = load_spec("https://spec.test/openapi.yaml")
        expected = model.expected_response("/item", "GET", 200)
    assert external.called
    assert expected.schema == {"type": "integer"}


@pytest.mark.parametrize("error", [httpx.InvalidURL("bad URL"), ValueError("bad URL")])
def test_invalid_remote_spec_url_is_a_spec_error(monkeypatch, error):
    def invalid_url(*args, **kwargs):
        raise error

    monkeypatch.setattr(httpx, "get", invalid_url)
    with pytest.raises(SpecError, match="Could not fetch spec"):
        load_spec("https://invalid.test/spec.yaml")


def test_missing_spec_file_is_a_spec_error(tmp_path):
    with pytest.raises(SpecError, match="Could not read spec"):
        load_spec(str(tmp_path / "missing.yaml"))


@pytest.mark.parametrize("raw", ["[", "not a mapping", "openapi: 3.1.0"])
def test_malformed_specs_are_spec_errors(tmp_path, raw):
    path = tmp_path / "invalid.yaml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SpecError):
        load_spec(str(path))
