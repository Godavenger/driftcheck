"""Tests for driftcheck.client.call_endpoint (all HTTP mocked with respx)."""

import httpx
import pytest
import respx

from driftcheck.client import call_endpoint
from driftcheck.models import Config, EndpointCheck

BASE = "https://api.example.com"


def _config(**overrides) -> Config:
    """Build a Config with client-test defaults, overridable per test."""
    params = {"spec": "unused.yaml", "base_url": BASE, "endpoints": []}
    params.update(overrides)
    return Config(**params)


def test_success_populates_status_body_and_clears_error():
    """A 200 JSON response yields status + parsed body and no error."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/pet/123").mock(
            return_value=httpx.Response(200, json={"id": 123, "name": "Rex"})
        )
        check = EndpointCheck(name="get-pet", path="/pet/{petId}", path_params={"petId": "123"})
        actual = call_endpoint(_config(), check)

        assert route.called
        assert actual.status == 200
        assert actual.body == {"id": 123, "name": "Rex"}
        assert actual.error is None
        assert isinstance(actual.elapsed_ms, float)
        assert actual.elapsed_ms >= 0.0


def test_connect_timeout_returns_error_without_raising():
    """A ConnectTimeout is captured into ActualResponse.error, never raised."""
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/pet/1").mock(side_effect=httpx.ConnectTimeout("boom"))
        check = EndpointCheck(name="get-pet", path="/pet/{petId}", path_params={"petId": "1"})

        actual = call_endpoint(_config(), check)  # must not raise

        assert actual.status is None
        assert actual.body is None
        assert isinstance(actual.error, str)
        assert actual.error != ""
        assert actual.elapsed_ms >= 0.0


def test_query_params_are_attached():
    """query_params are sent on the wire."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json=[]))
        check = EndpointCheck(
            name="search",
            path="/search",
            query_params={"q": "cat", "limit": "5"},
        )
        actual = call_endpoint(_config(), check)

        assert route.called
        assert actual.status == 200
        sent = route.calls.last.request
        assert sent.url.params["q"] == "cat"
        assert sent.url.params["limit"] == "5"


def test_auth_header_added_when_env_var_has_value(monkeypatch):
    """A set auth env var is sent as the configured auth header."""
    monkeypatch.setenv("DRIFT_TOKEN", "secret-abc")
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/pet/1").mock(return_value=httpx.Response(200, json={}))
        config = _config(auth_header_env="DRIFT_TOKEN", auth_header_name="X-API-Key")
        check = EndpointCheck(name="get-pet", path="/pet/{petId}", path_params={"petId": "1"})

        call_endpoint(config, check)

        assert route.called
        assert route.calls.last.request.headers["X-API-Key"] == "secret-abc"


def test_missing_auth_environment_does_not_send_unauthenticated_request(monkeypatch):
    """A configured missing credential is an error before a request is sent."""
    monkeypatch.delenv("DRIFT_TOKEN", raising=False)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/pet/1").mock(return_value=httpx.Response(200, json={}))
        config = _config(auth_header_env="DRIFT_TOKEN", auth_header_name="X-API-Key")
        check = EndpointCheck(name="get-pet", path="/pet/{petId}", path_params={"petId": "1"})

        actual = call_endpoint(config, check)

        assert not route.called
        assert actual.status is None
        assert "DRIFT_TOKEN" in actual.error


def test_non_json_body_falls_back_to_text():
    """A non-JSON body is returned as raw text, with no error."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/ping").mock(
            return_value=httpx.Response(200, text="pong", headers={"content-type": "text/plain"})
        )
        check = EndpointCheck(name="ping", path="/ping")
        actual = call_endpoint(_config(), check)

        assert route.called
        assert actual.status == 200
        assert actual.body == "pong"
        assert actual.error is None


def test_http_method_is_honored():
    """call_endpoint issues the request using check.method."""
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/pet").mock(return_value=httpx.Response(201, json={"id": 1}))
        check = EndpointCheck(name="create-pet", path="/pet", method="POST")
        actual = call_endpoint(_config(), check)

        assert route.called
        assert actual.status == 201
        assert route.calls.last.request.method == "POST"


def test_base_url_trailing_slash_is_normalized():
    """A trailing slash on base_url does not produce a doubled path separator."""
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/pet/1").mock(return_value=httpx.Response(200, json={}))
        config = _config(base_url=f"{BASE}/")
        check = EndpointCheck(name="get-pet", path="/pet/{petId}", path_params={"petId": "1"})
        actual = call_endpoint(config, check)

        assert route.called
        assert actual.status == 200


def test_missing_path_param_is_reported_not_raised():
    """A path placeholder with no matching path_param is a per-call error, not a crash."""
    check = EndpointCheck(name="get-pet", path="/pet/{petId}")  # no path_params provided
    actual = call_endpoint(_config(), check)  # must not raise

    assert actual.status is None
    assert actual.error is not None
    assert "petId" in actual.error


def test_path_parameter_names_are_literal_and_values_are_encoded():
    check = EndpointCheck(name="lookup", path="/pets/{pet.id}", path_params={"pet.id": "a/b ?#%"})
    with respx.mock as router:
        route = router.get(f"{BASE}/pets/a%2Fb%20%3F%23%25").respond(200, json={})
        actual = call_endpoint(_config(), check)
    assert actual.status == 200
    assert route.calls.last.request.url.raw_path == b"/pets/a%2Fb%20%3F%23%25"


def test_dot_only_path_parameter_does_not_navigate_to_parent():
    check = EndpointCheck(name="lookup", path="/pets/{id}", path_params={"id": ".."})
    with respx.mock as router:
        route = router.get(f"{BASE}/pets/%2E%2E").respond(200, json={})
        actual = call_endpoint(_config(), check)
    assert actual.status == 200
    assert route.calls.last.request.url.raw_path == b"/pets/%2E%2E"


@pytest.mark.parametrize("path", ["/pet/{id", "/pet/id}", "/pet/{}"])
def test_malformed_path_templates_return_errors(path):
    actual = call_endpoint(_config(), EndpointCheck(name="lookup", path=path))
    assert actual.status is None
    assert "malformed" in actual.error


def test_malformed_json_retains_response_metadata():
    with respx.mock as router:
        router.get(f"{BASE}/pets").respond(
            200, text="not JSON", headers={"Content-Type": "Application/JSON; charset=utf-8"}
        )
        actual = call_endpoint(_config(), EndpointCheck(name="pets", path="/pets"))
    assert actual.status == 200
    assert actual.body == "not JSON"
    assert actual.body_present
    assert actual.content_type == "application/json"
    assert actual.json_error
    assert actual.error is None


def test_plain_text_that_looks_like_json_stays_text():
    with respx.mock as router:
        router.get(f"{BASE}/count").respond(200, text="123")
        actual = call_endpoint(_config(), EndpointCheck(name="count", path="/count"))
    assert actual.body == "123"
    assert actual.json_error is None


def test_empty_body_is_distinct_from_json_null():
    with respx.mock as router:
        router.get(f"{BASE}/empty").respond(204)
        router.get(f"{BASE}/null").respond(
            200, content="null", headers={"Content-Type": "application/json"}
        )
        empty = call_endpoint(_config(), EndpointCheck(name="empty", path="/empty"))
        null = call_endpoint(_config(), EndpointCheck(name="null", path="/null"))
    assert empty.body_present is False
    assert null.body_present is True
    assert null.body is None
    assert null.json_error is None


def test_empty_transport_message_is_still_an_error():
    with respx.mock as router:
        router.get(f"{BASE}/pets").mock(side_effect=httpx.ConnectError(""))
        actual = call_endpoint(_config(), EndpointCheck(name="pets", path="/pets"))
    assert actual.error == "ConnectError"


def test_transport_error_does_not_disclose_auth_token(monkeypatch):
    monkeypatch.setenv("DRIFT_TOKEN", "secret-value")
    with respx.mock as router:
        router.get(f"{BASE}/pets").mock(
            side_effect=httpx.LocalProtocolError("bad header secret-value")
        )
        actual = call_endpoint(
            _config(auth_header_env="DRIFT_TOKEN"), EndpointCheck(name="pets", path="/pets")
        )
    assert "secret-value" not in actual.error
    assert "[redacted]" in actual.error


@pytest.mark.parametrize("body", ["NaN", "Infinity", "-Infinity", '{"value":NaN}'])
def test_nonfinite_json_extensions_are_not_valid_json(body):
    with respx.mock as router:
        router.get(f"{BASE}/value").respond(
            200, content=body, headers={"Content-Type": "application/json"}
        )
        actual = call_endpoint(_config(), EndpointCheck(name="value", path="/value"))
    assert actual.json_error
    assert actual.error is None
