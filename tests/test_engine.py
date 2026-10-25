"""Unit tests for :func:`driftcheck.engine.check_endpoint`.

All HTTP is mocked with respx against a throwaway base URL; the real network is
never touched. Each test is self-contained: it loads the committed OpenAPI
fixture and stands up its own respx router.
"""

from __future__ import annotations

import pathlib

import httpx
import pytest
import respx

from driftcheck.engine import check_endpoint, run
from driftcheck.errors import SpecError
from driftcheck.models import Config, EndpointCheck
from driftcheck.spec import SpecModel, load_spec

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "petstore.json"
BASE_URL = "https://api.test"


def _config(check: EndpointCheck) -> Config:
    """Build a Config pointed at the mocked base URL with a single endpoint."""
    return Config(spec=str(FIXTURE), base_url=BASE_URL, endpoints=[check])


def test_undocumented_status_is_flagged() -> None:
    """A 500 on an operation that only documents 200 is undocumented drift."""
    spec = load_spec(str(FIXTURE))
    check = EndpointCheck(name="inventory", path="/store/inventory", method="GET")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE_URL}/store/inventory").mock(
            return_value=httpx.Response(500, json={"message": "boom"})
        )
        result = check_endpoint(_config(check), spec, check)

    assert route.called
    assert result.conformant is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].kind == "undocumented_status"


def test_transport_error_is_flagged() -> None:
    """A connection timeout surfaces as a single transport mismatch."""
    spec = load_spec(str(FIXTURE))
    check = EndpointCheck(name="inventory", path="/store/inventory", method="GET")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE_URL}/store/inventory").mock(side_effect=httpx.ConnectTimeout)
        result = check_endpoint(_config(check), spec, check)

    assert route.called
    assert result.conformant is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].kind == "transport"


def test_conformant_response_has_no_mismatches() -> None:
    """A documented 200 whose body satisfies the schema is fully conformant."""
    spec = load_spec(str(FIXTURE))
    check = EndpointCheck(name="inventory", path="/store/inventory", method="GET")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE_URL}/store/inventory").mock(
            return_value=httpx.Response(200, json={"available": 10, "pending": 3, "sold": 7})
        )
        result = check_endpoint(_config(check), spec, check)

    assert route.called
    assert result.conformant is True
    assert result.mismatches == []


def test_expected_status_mismatch_is_flagged() -> None:
    """A documented status that differs from the pinned expected_status is drift."""
    spec = load_spec(str(FIXTURE))
    check = EndpointCheck(
        name="get-pet",
        path="/pet/{petId}",
        method="GET",
        path_params={"petId": "1"},
        expected_status=200,
    )

    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE_URL}/pet/1").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        result = check_endpoint(_config(check), spec, check)

    assert route.called
    assert result.conformant is False
    assert len(result.mismatches) == 1
    assert result.mismatches[0].kind == "status"


def _inline_spec(*, status="200", media_type="application/json", schema=None):
    return SpecModel(
        {
            "openapi": "3.1.0",
            "paths": {
                "/item": {
                    "get": {
                        "responses": {
                            status: {
                                "content": {media_type: {"schema": schema or {"type": "string"}}}
                            }
                        }
                    }
                }
            },
        }
    )


@pytest.mark.parametrize("documented", ["2XX", "default"])
def test_range_and_default_responses_validate_body(documented):
    spec = _inline_spec(status=documented)
    check = EndpointCheck("item", "/item")
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(201, json=123)
        result = check_endpoint(_config(check), spec, check)
    assert result.mismatches[0].kind == "type"


def test_invalid_json_cannot_pass_a_string_schema():
    check = EndpointCheck("item", "/item")
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(
            200, content=b"not json", headers={"Content-Type": "application/json"}
        )
        result = check_endpoint(_config(check), _inline_spec(), check)
    assert result.mismatches[0].kind == "invalid_json"


@pytest.mark.parametrize("content_type", ["text/plain", None])
def test_wrong_or_missing_content_type_is_drift(content_type):
    check = EndpointCheck("item", "/item")
    headers = {"Content-Type": content_type} if content_type else {}
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(200, content=b'"valid json"', headers=headers)
        result = check_endpoint(_config(check), _inline_spec(), check)
    assert result.mismatches[0].kind == "content_type"


def test_vendor_json_schema_is_checked():
    check = EndpointCheck("item", "/item")
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(
            200, content=b"123", headers={"Content-Type": "application/problem+json"}
        )
        result = check_endpoint(
            _config(check), _inline_spec(media_type="application/problem+json"), check
        )
    assert result.mismatches[0].kind == "type"


def test_text_response_uses_string_schema():
    check = EndpointCheck("item", "/item")
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(200, text="123")
        result = check_endpoint(_config(check), _inline_spec(media_type="text/plain"), check)
    assert result.conformant


def test_empty_text_response_can_conform_to_a_string_schema():
    check = EndpointCheck("item", "/item")
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(200, text="")
        result = check_endpoint(_config(check), _inline_spec(media_type="text/plain"), check)
    assert result.conformant


def test_empty_body_cannot_pass_a_nullable_schema():
    check = EndpointCheck("item", "/item")
    with respx.mock as router:
        router.get(f"{BASE_URL}/item").respond(200, headers={"Content-Type": "application/json"})
        result = check_endpoint(
            _config(check), _inline_spec(schema={"type": ["string", "null"]}), check
        )
    assert result.mismatches[0].kind == "body"


def test_head_response_does_not_require_body_schema():
    check = EndpointCheck("item", "/item", "HEAD")
    spec = _inline_spec()
    spec.doc["paths"]["/item"]["head"] = spec.doc["paths"]["/item"]["get"]
    with respx.mock as router:
        router.head(f"{BASE_URL}/item").respond(200, headers={"Content-Type": "application/json"})
        result = check_endpoint(_config(check), spec, check)
    assert result.conformant


def test_run_rejects_unknown_operation_before_making_any_requests():
    config = _config(EndpointCheck("item", "/item"))
    config.endpoints.append(EndpointCheck("typo", "/missing"))
    with respx.mock as router:
        with pytest.raises(SpecError, match="GET /missing"):
            run(config, _inline_spec(), "2026-01-01T00:00:00Z")
    assert len(router.calls) == 0
