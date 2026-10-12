"""Tests for driftcheck.client.call_endpoint (all HTTP mocked with respx)."""

import httpx
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


def test_no_auth_header_when_env_var_unset(monkeypatch):
    """When the named env var is unset, no auth header is sent."""
    monkeypatch.delenv("DRIFT_TOKEN", raising=False)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{BASE}/pet/1").mock(return_value=httpx.Response(200, json={}))
        config = _config(auth_header_env="DRIFT_TOKEN", auth_header_name="X-API-Key")
        check = EndpointCheck(name="get-pet", path="/pet/{petId}", path_params={"petId": "1"})

        call_endpoint(config, check)

        assert route.called
        assert "X-API-Key" not in route.calls.last.request.headers


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
