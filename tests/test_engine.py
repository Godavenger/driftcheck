"""Unit tests for :func:`driftcheck.engine.check_endpoint`.

All HTTP is mocked with respx against a throwaway base URL; the real network is
never touched. Each test is self-contained: it loads the committed OpenAPI
fixture and stands up its own respx router.
"""

from __future__ import annotations

import pathlib

import httpx
import respx

from driftcheck.engine import check_endpoint
from driftcheck.models import Config, EndpointCheck
from driftcheck.spec import load_spec

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
