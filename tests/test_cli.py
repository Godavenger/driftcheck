"""Tests for the driftcheck command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
import yaml
from typer.testing import CliRunner

from driftcheck import __version__, cli
from driftcheck.errors import ConfigError
from driftcheck.models import Config, EndpointResult, Mismatch, RunReport

runner = CliRunner()


def _nonconformant_report() -> RunReport:
    """Build a well-formed report that contains drift (nonconformant > 0)."""
    mismatch = Mismatch(
        kind="status",
        location="$.status",
        expected="200",
        actual="404",
        message="expected 200 but got 404",
    )
    result = EndpointResult(
        name="get pet",
        path="/pet/{petId}",
        method="GET",
        conformant=False,
        mismatches=[mismatch],
    )
    return RunReport(
        results=[result],
        total=1,
        conformant=0,
        nonconformant=1,
        generated_at="2026-10-12T00:00:00+00:00",
    )


def _patch_pipeline(monkeypatch, report: RunReport) -> None:
    """Stub out config/spec loading and the run so ``check`` returns ``report``."""
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path: Config(spec="dummy-spec", base_url="https://api.example.com", endpoints=[]),
    )
    monkeypatch.setattr(cli, "load_spec", lambda location: object())
    monkeypatch.setattr(cli, "run", lambda *args, **kwargs: report)


def test_check_reports_drift_exits_1(monkeypatch):
    """``check`` exits 1 when the run finds nonconformant endpoints."""
    _patch_pipeline(monkeypatch, _nonconformant_report())

    result = runner.invoke(cli.app, ["check", "--config", "ignored.yaml"])

    assert result.exit_code == 1


def test_version_exits_zero_and_prints_version():
    """``--version`` prints the version string and exits 0."""
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_check_config_error_exits_2(monkeypatch):
    """A ConfigError during loading is reported and exits 2."""

    def boom(path):
        raise ConfigError("broken config")

    monkeypatch.setattr(cli, "load_config", boom)

    result = runner.invoke(cli.app, ["check", "--config", "ignored.yaml"])

    assert result.exit_code == 2


def test_check_output_writes_file(monkeypatch, tmp_path):
    """``--output`` writes the rendered report to disk and echoes the path."""
    _patch_pipeline(monkeypatch, _nonconformant_report())
    monkeypatch.setattr(cli, "render_console", lambda report: "RENDERED-REPORT")

    out_path = tmp_path / "report.txt"
    result = runner.invoke(
        cli.app,
        ["check", "--config", "ignored.yaml", "--output", str(out_path)],
    )

    assert out_path.read_text(encoding="utf-8") == "RENDERED-REPORT"
    assert f"wrote {out_path}" in result.output


def test_check_output_creates_parent_directories(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, _nonconformant_report())
    path = tmp_path / "site" / "reports" / "index.html"
    result = runner.invoke(
        cli.app, ["check", "--config", "ignored.yaml", "--format", "html", "--output", str(path)]
    )
    assert result.exit_code == 1
    assert "<!doctype html>" in path.read_text(encoding="utf-8").lower()


def test_output_error_has_actionable_exit_code(monkeypatch, tmp_path):
    _patch_pipeline(monkeypatch, _nonconformant_report())
    path = tmp_path / "file"
    path.write_text("not a directory", encoding="utf-8")
    result = runner.invoke(
        cli.app, ["check", "--config", "ignored.yaml", "--output", str(path / "report.json")]
    )
    assert result.exit_code == 2
    assert "could not write report" in result.output


def test_bundle_and_output_are_mutually_exclusive(monkeypatch):
    def unexpected_load(path):
        raise AssertionError("must reject options before loading or running")

    monkeypatch.setattr(cli, "load_config", unexpected_load)
    result = runner.invoke(
        cli.app,
        ["check", "--config", "ignored.yaml", "--output", "report.json", "--report-dir", "site"],
    )
    assert result.exit_code == 2
    assert "cannot be used together" in result.output


def test_report_bundle_uses_exactly_one_run(monkeypatch, tmp_path):
    report = _nonconformant_report()
    _patch_pipeline(monkeypatch, report)
    calls = []

    def record_run(*args, **kwargs):
        calls.append(kwargs)
        return report

    monkeypatch.setattr(cli, "run", record_run)
    directory = tmp_path / "site"
    result = runner.invoke(
        cli.app,
        ["check", "--config", "ignored.yaml", "--format", "json", "--report-dir", str(directory)],
    )
    assert result.exit_code == 1
    assert len(calls) == 1
    payload = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert payload["generated_at"] == report.generated_at
    assert payload["nonconformant"] == 1
    assert (directory / "badge.json").is_file()
    assert (directory / "index.html").is_file()
    assert json.loads(result.stdout) == payload


def test_missing_auth_environment_exits_before_spec_loading(monkeypatch):
    monkeypatch.delenv("MISSING_DRIFT_TOKEN", raising=False)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path: Config(
            spec="unused", base_url="https://x", endpoints=[], auth_header_env="MISSING_DRIFT_TOKEN"
        ),
    )

    def unexpected_load(location):
        raise AssertionError("credential must be checked before loading spec")

    monkeypatch.setattr(cli, "load_spec", unexpected_load)
    result = runner.invoke(cli.app, ["check", "--config", "ignored.yaml"])
    assert result.exit_code == 2
    assert "MISSING_DRIFT_TOKEN" in result.output


def test_full_cli_loads_relative_spec_and_emits_json(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "petstore.json"
    (config_dir / "openapi.json").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "spec": "openapi.json",
                "base_url": "https://api.example.com/v3",
                "endpoints": [{"name": "inventory", "path": "/store/inventory"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with respx.mock as router:
        route = router.get("https://api.example.com/v3/store/inventory").mock(
            return_value=httpx.Response(200, json={"available": 5})
        )
        result = runner.invoke(
            cli.app, ["check", "--config", "config/config.yaml", "--format", "json"]
        )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    report = json.loads(result.stdout)
    assert report["conformant"] == 1
    assert report["nonconformant"] == 0
