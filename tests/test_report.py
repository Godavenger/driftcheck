"""Tests for driftcheck.report renderers and the exit-code helper."""

from __future__ import annotations

import json

import pytest

from driftcheck.models import EndpointResult, Mismatch, RunReport
from driftcheck.report import (
    exit_code,
    render_badge,
    render_console,
    render_html,
    render_json,
    render_markdown,
    write_report_bundle,
)


def _mixed_report() -> RunReport:
    """One conformant endpoint and one drifted endpoint (with a mismatch)."""
    ok = EndpointResult(
        name="list-pets",
        path="/pets",
        method="GET",
        conformant=True,
        mismatches=[],
    )
    drift = EndpointResult(
        name="get-pet",
        path="/pets/{petId}",
        method="GET",
        conformant=False,
        mismatches=[
            Mismatch(
                kind="type",
                location="$.name",
                expected="string",
                actual="integer",
                message="expected string, got integer",
            )
        ],
    )
    return RunReport(
        results=[ok, drift],
        total=2,
        conformant=1,
        nonconformant=1,
        generated_at="2026-10-12T00:00:00Z",
    )


def _clean_report() -> RunReport:
    """A report where every endpoint is conformant."""
    return RunReport(
        results=[
            EndpointResult(
                name="list-pets",
                path="/pets",
                method="GET",
                conformant=True,
                mismatches=[],
            )
        ],
        total=1,
        conformant=1,
        nonconformant=0,
        generated_at="2026-10-12T00:00:00Z",
    )


def test_render_json_has_counts_and_timestamp() -> None:
    data = json.loads(render_json(_mixed_report()))
    assert data["total"] == 2
    assert data["conformant"] == 1
    assert data["nonconformant"] == 1
    assert data["generated_at"] == "2026-10-12T00:00:00Z"


def test_render_json_includes_nested_mismatch() -> None:
    data = json.loads(render_json(_mixed_report()))
    drift = next(r for r in data["results"] if r["name"] == "get-pet")
    assert drift["mismatches"][0]["kind"] == "type"
    assert drift["mismatches"][0]["location"] == "$.name"


def test_render_markdown_includes_both_endpoint_names() -> None:
    md = render_markdown(_mixed_report())
    assert "list-pets" in md
    assert "get-pet" in md


def test_render_console_lists_endpoints_and_status() -> None:
    text = render_console(_mixed_report())
    assert "list-pets" in text
    assert "get-pet" in text
    assert "DRIFT" in text
    assert "nonconformant: 1" in text


def test_exit_code_nonzero_when_drift_present() -> None:
    assert exit_code(_mixed_report()) == 1


def test_exit_code_zero_when_all_conformant() -> None:
    assert exit_code(_clean_report()) == 0


def test_render_html_is_a_full_page_with_endpoint_names() -> None:
    page = render_html(_mixed_report())
    assert page.lstrip().startswith("<!doctype html>")
    assert "list-pets" in page
    assert "get-pet" in page
    assert "DRIFT" in page
    assert "$.name" in page  # the mismatch location is rendered


def test_render_html_marks_clean_report_as_pass() -> None:
    assert ">PASS<" in render_html(_clean_report())


def test_console_renderer_does_not_write_to_stdout(capsys) -> None:
    report = _mixed_report()
    report.results[0].name = "[red]literal[/red]"
    rendered = render_console(report)
    assert capsys.readouterr().out == ""
    assert "[red]literal[/red]" in rendered


def test_report_formats_escape_response_and_endpoint_markup() -> None:
    report = _mixed_report()
    report.results[0].name = '<script>alert("endpoint")</script>'
    report.results[1].mismatches[0].message = "[click](https://example.com)\n# injected"
    page = render_html(report)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    markdown = render_markdown(report)
    assert "<script>" not in markdown
    assert r"\[click\]\(https://example.com\) \# injected" in markdown


@pytest.mark.parametrize(
    ("report", "message", "color"),
    [
        (_clean_report(), "1/1 passing", "brightgreen"),
        (_mixed_report(), "1/2 drifted", "red"),
        (RunReport([], 0, 0, 0, "2026-10-25T00:00:00Z"), "no checks", "lightgrey"),
    ],
)
def test_badge_reflects_report_counts(report, message, color) -> None:
    badge = json.loads(render_badge(report))
    assert badge["schemaVersion"] == 1
    assert badge["message"] == message
    assert badge["color"] == color


def test_bundle_preserves_same_report_and_creates_directory(tmp_path) -> None:
    report = _mixed_report()
    directory = tmp_path / "nested" / "site"
    write_report_bundle(report, directory)
    data = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    badge = json.loads((directory / "badge.json").read_text(encoding="utf-8"))
    page = (directory / "index.html").read_text(encoding="utf-8")
    assert data["generated_at"] == report.generated_at
    assert data["nonconformant"] == 1
    assert report.generated_at in page
    assert "1/2 endpoints conformant" in page
    assert badge["message"] == "1/2 drifted"


def test_bundle_propagates_export_failures(tmp_path) -> None:
    destination = tmp_path / "file"
    destination.write_text("occupied", encoding="utf-8")
    with pytest.raises(OSError):
        write_report_bundle(_clean_report(), destination)
