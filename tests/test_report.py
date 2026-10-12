"""Tests for driftcheck.report renderers and the exit-code helper."""

from __future__ import annotations

import json

from driftcheck.models import EndpointResult, Mismatch, RunReport
from driftcheck.report import exit_code, render_console, render_html, render_json, render_markdown


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
