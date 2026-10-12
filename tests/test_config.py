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
