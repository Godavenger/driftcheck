"""Tests for driftcheck.config.load_config."""

import textwrap

import pytest

from driftcheck.config import load_config
from driftcheck.errors import ConfigError
from driftcheck.models import Config, EndpointCheck

VALID_YAML = textwrap.dedent(
    """\
    spec: https://example.com/openapi.json
    base_url: https://example.com/api
    auth_header_env: API_TOKEN
    timeout_seconds: 5.0
    endpoints:
      - name: list-pets
        path: /pets
        query_params: { status: available, limit: 2 }
      - name: get-pet
        path: /pets/{petId}
        method: get
        path_params: { petId: 42 }
        expected_status: 200
    """
)


def _write(tmp_path, text: str) -> str:
    """Write YAML text to a temp config file and return its path as a str."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    return str(cfg_path)


def test_valid_config_loads_endpoints(tmp_path):
    """A valid YAML file loads into a Config of parsed EndpointCheck objects."""
    config = load_config(_write(tmp_path, VALID_YAML))

    assert isinstance(config, Config)
    assert config.spec == "https://example.com/openapi.json"
    assert config.base_url == "https://example.com/api"
    assert config.auth_header_env == "API_TOKEN"
    assert config.auth_header_name == "Authorization"  # default
    assert config.timeout_seconds == 5.0

    assert len(config.endpoints) == 2
    assert all(isinstance(e, EndpointCheck) for e in config.endpoints)

    first = config.endpoints[0]
    assert first.name == "list-pets"
    assert first.path == "/pets"
    assert first.method == "GET"  # defaulted
    # path_params/query_params default to {} and VALUES are coerced to str.
    assert first.path_params == {}
    assert first.query_params == {"status": "available", "limit": "2"}
    assert first.expected_status is None

    second = config.endpoints[1]
    assert second.method == "GET"  # "get" normalized to upper
    assert second.path_params == {"petId": "42"}
    assert second.expected_status == 200


def test_missing_base_url_raises(tmp_path):
    """A config missing base_url raises ConfigError naming the offending key."""
    text = textwrap.dedent(
        """\
        spec: https://example.com/openapi.json
        endpoints:
          - name: list-pets
            path: /pets
        """
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, text))

    assert "base_url" in str(excinfo.value)


def test_missing_file_raises(tmp_path):
    """A nonexistent path raises ConfigError with the not-found message."""
    missing = str(tmp_path / "does-not-exist.yaml")

    with pytest.raises(ConfigError) as excinfo:
        load_config(missing)

    assert "config file not found" in str(excinfo.value)


def test_empty_endpoints_raises(tmp_path):
    """An empty endpoints list is rejected and names the endpoints key."""
    text = textwrap.dedent(
        """\
        spec: https://example.com/openapi.json
        base_url: https://example.com/api
        endpoints: []
        """
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, text))

    assert "endpoints" in str(excinfo.value)


_BASE_TOP = "spec: https://x\nbase_url: https://x\n"
_END = "endpoints:\n  - {name: a, path: /a}\n"


@pytest.mark.parametrize(
    ("yaml_text", "needle"),
    [
        ("spec: [bad", "valid YAML"),
        ("# just a comment\n", "empty"),
        ("- a\n- b\n", "mapping"),
        ("base_url: https://x\n" + _END, "spec"),
        ("spec: 1\nbase_url: https://x\n" + _END, "spec"),
        (_BASE_TOP + "auth_header_env: 123\n" + _END, "auth_header_env"),
        (_BASE_TOP + 'auth_header_name: ""\n' + _END, "auth_header_name"),
        (_BASE_TOP + "timeout_seconds: soon\n" + _END, "timeout_seconds"),
        (_BASE_TOP + "timeout_seconds: true\n" + _END, "timeout_seconds"),
        (_BASE_TOP + "endpoints:\n  - just-a-string\n", "mapping"),
        (_BASE_TOP + "endpoints:\n  - {path: /a}\n", "name"),
        (_BASE_TOP + "endpoints:\n  - {name: a}\n", "path"),
        (_BASE_TOP + "endpoints:\n  - {name: a, path: /a, method: 1}\n", "method"),
        (_BASE_TOP + "endpoints:\n  - {name: a, path: /a, expected_status: x}\n", "non-integer"),
        (_BASE_TOP + "endpoints:\n  - {name: a, path: /a, path_params: [1, 2]}\n", "path_params"),
    ],
)
def test_invalid_configs_raise_configerror(tmp_path, yaml_text, needle):
    """Each malformed config raises ConfigError whose message names the problem."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, yaml_text))
    assert needle in str(excinfo.value)


def test_local_spec_is_relative_to_config_directory(tmp_path, monkeypatch):
    configs = tmp_path / "configs"
    configs.mkdir()
    monkeypatch.chdir(tmp_path)
    path = _write(configs, "spec: ../specs/api.yaml\nbase_url: https://x\n" + _END)
    config = load_config(path)
    assert config.spec == str(tmp_path / "specs" / "api.yaml")


def test_invalid_utf8_is_a_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(str(path))


@pytest.mark.parametrize("timeout", ["0", "-1", ".inf", "-.inf", ".nan"])
def test_timeout_must_be_positive_and_finite(tmp_path, timeout):
    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config(_write(tmp_path, _BASE_TOP + f"timeout_seconds: {timeout}\n" + _END))


@pytest.mark.parametrize(
    "url",
    [
        "api.example.com",
        "ftp://x",
        "https://",
        "https://x:99999",
        "https://x/api?key=1",
        "https://x/#fragment",
        "https://bad host/api",
    ],
)
def test_base_url_must_be_an_http_base(tmp_path, url):
    with pytest.raises(ConfigError, match="base_url"):
        load_config(_write(tmp_path, f"spec: spec.yaml\nbase_url: {url}\n" + _END))


@pytest.mark.parametrize(
    ("endpoint", "needle"),
    [
        ("{name: a, path: pets}", "path"),
        ("{name: a, path: '/pets?status=sold'}", "path"),
        ("{name: a, path: '/pets/{id'}", "placeholder"),
        ("{name: a, path: '/pets/{id}'}", "missing path_params"),
        ("{name: a, path: /pets, path_params: {id: 1}}", "unused path_params"),
        ("{name: a, path: /pets, method: FETCH}", "method"),
        ("{name: a, path: /pets, expected_status: 99}", "expected_status"),
        ("{name: a, path: /pets, expected_status: 600}", "expected_status"),
        ("{name: a, path: /pets, query_params: {filter: [a, b]}}", "query_params"),
        ("{name: a, path: /pets, query_params: {filter: null}}", "query_params"),
    ],
)
def test_invalid_endpoint_settings_fail_before_requests(tmp_path, endpoint, needle):
    with pytest.raises(ConfigError, match=needle):
        load_config(_write(tmp_path, _BASE_TOP + f"endpoints:\n  - {endpoint}\n"))


def test_duplicate_endpoint_names_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unique"):
        load_config(_write(tmp_path, _BASE_TOP + _END + "  - {name: a, path: /b}\n"))


def test_boolean_query_values_use_json_spelling(tmp_path):
    config = load_config(
        _write(
            tmp_path,
            _BASE_TOP + "endpoints:\n  - {name: a, path: /a, query_params: {active: true}}\n",
        )
    )
    assert config.endpoints[0].query_params == {"active": "true"}


@pytest.mark.parametrize(
    "text",
    [
        _BASE_TOP + "timeot_seconds: 2\n" + _END,
        _BASE_TOP + "endpoints:\n  - {name: a, path: /a, expected_stats: 201}\n",
    ],
)
def test_unknown_keys_do_not_silently_disable_settings(tmp_path, text):
    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(_write(tmp_path, text))
