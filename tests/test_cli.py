"""Tests for the driftcheck command-line interface."""

from __future__ import annotations

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
