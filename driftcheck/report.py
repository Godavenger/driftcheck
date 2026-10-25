"""Render a :class:`RunReport` to console, JSON, Markdown, or a standalone HTML page.

All renderers are pure: they take a report and return a string (or an int for
:func:`exit_code`). The bundle writer saves those renderings from a single run.
"""

from __future__ import annotations

import dataclasses
import html
import json
import re
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.table import Table

from driftcheck.models import RunReport

_STYLE = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;
--muted:#656d76;--border:#d8dee4;--ok:#1a7f37;--bad:#cf222e;--okbg:#eafbea;--badbg:#ffebe9}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;
--muted:#9198a1;--border:#30363d;--ok:#3fb950;--bad:#f85149;--okbg:#12261e;--badbg:#2d1416}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 64px}
header{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:24px;margin:0;letter-spacing:-.02em}
.status{font-weight:700;padding:4px 12px;border-radius:999px;font-size:13px}
.status.pass{background:var(--okbg);color:var(--ok)}
.status.drift{background:var(--badbg);color:var(--bad)}
.sub{color:var(--muted);margin:6px 0 24px;font-size:14px}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--border);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--border);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
tr:last-child td{border-bottom:none}
.name{font-weight:600}
.path{color:var(--muted);font-size:13px;font-family:ui-monospace,Menlo,monospace}
.pill{font-size:12px;font-weight:700;padding:2px 10px;border-radius:999px}
.pill.ok{background:var(--okbg);color:var(--ok)}
.pill.bad{background:var(--badbg);color:var(--bad)}
.mismatches{margin:0;padding-left:18px}
.mismatches li{margin-bottom:8px}
.mismatches code{background:var(--badbg);color:var(--bad);
padding:1px 6px;border-radius:6px;font-size:12px}
.loc{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--muted)}
.msg{font-size:13px;color:var(--muted);margin-top:2px}
.none{color:var(--muted)}
footer{margin-top:24px;color:var(--muted);font-size:13px}
a{color:inherit}
"""


def render_console(report: RunReport) -> str:
    """Render ``report`` as a plain-text table, drifted rows styled red."""
    console = Console(record=True, width=120, file=StringIO(), markup=False, highlight=False)

    summary = (
        f"total: {report.total}  "
        f"conformant: {report.conformant}  "
        f"nonconformant: {report.nonconformant}"
    )
    console.print(summary)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Endpoint")
    table.add_column("Status")
    table.add_column("Drift")

    for result in report.results:
        status = "OK" if result.conformant else "DRIFT"
        brief = "; ".join(f"{m.kind} {m.location}" for m in result.mismatches) or "-"
        table.add_row(
            result.name,
            status,
            brief,
            style=None if result.conformant else "red",
        )

    console.print(table)
    return console.export_text()


def render_json(report: RunReport) -> str:
    """Render ``report`` as indented JSON with stable key ordering."""
    return json.dumps(dataclasses.asdict(report), indent=2, sort_keys=True)


def render_markdown(report: RunReport) -> str:
    """Render ``report`` as a Markdown summary plus a per-endpoint list."""
    mark = "✅" if report.nonconformant == 0 else "❌"
    lines = [f"{mark} {report.conformant}/{report.total} endpoints conformant", ""]

    for result in report.results:
        status = "OK" if result.conformant else "DRIFT"
        lines.append(
            f"- **{_markdown_text(result.name)}** "
            f"({_markdown_text(result.method)} {_markdown_text(result.path)}): {status}"
        )
        if not result.conformant:
            for m in result.mismatches:
                lines.append(
                    f"  - {_markdown_text(m.kind)} - {_markdown_text(m.location)} "
                    f"- {_markdown_text(m.message)}"
                )

    return "\n".join(lines)


def _markdown_text(value: str) -> str:
    """Keep endpoint and response data literal in GitHub-rendered Markdown."""
    text = html.escape(" ".join(value.splitlines()))
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", text)


def render_html(report: RunReport) -> str:
    """Render ``report`` as a self-contained HTML page for the live demo site."""
    passed = report.nonconformant == 0
    status_label = "PASS" if passed else "DRIFT"
    status_class = "pass" if passed else "drift"
    generated = html.escape(report.generated_at)

    rows = []
    for result in report.results:
        row_class = "ok" if result.conformant else "bad"
        badge = "OK" if result.conformant else "DRIFT"
        if result.mismatches:
            items = "".join(
                f"<li><code>{html.escape(m.kind)}</code> "
                f"<span class='loc'>{html.escape(m.location or '-')}</span>"
                f"<div class='msg'>{html.escape(m.message)}</div></li>"
                for m in result.mismatches
            )
            drift_cell = f"<ul class='mismatches'>{items}</ul>"
        else:
            drift_cell = "<span class='none'>—</span>"
        rows.append(
            f"<tr><td><div class='name'>{html.escape(result.name)}</div>"
            f"<div class='path'>{html.escape(result.method)} {html.escape(result.path)}</div></td>"
            f"<td><span class='pill {row_class}'>{badge}</span></td>"
            f"<td>{drift_cell}</td></tr>"
        )
    rows_html = "\n".join(rows)

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>driftcheck — API conformance</title>\n"
        f'<style>{_STYLE}</style>\n</head>\n<body>\n<div class="wrap">\n'
        f"<header><h1>driftcheck</h1>"
        f'<span class="status {status_class}">{status_label}</span></header>\n'
        f'<p class="sub">{report.conformant}/{report.total} endpoints conformant · '
        f"generated {generated}</p>\n"
        "<table>\n<thead><tr><th>Endpoint</th><th>Status</th><th>Drift</th></tr></thead>\n<tbody>\n"
        f"{rows_html}\n</tbody>\n</table>\n"
        '<footer>Generated by <a href="https://github.com/Godavenger/driftcheck">driftcheck</a>'
        " — API contract drift detector.</footer>\n</div>\n</body>\n</html>\n"
    )


def exit_code(report: RunReport) -> int:
    """Return 0 when nothing drifted, else 1 (process exit convention)."""
    return 0 if report.nonconformant == 0 else 1


def render_badge(report: RunReport) -> str:
    """Render a shields.io endpoint badge using the report's own counts."""
    if report.total == 0:
        message, color = "no checks", "lightgrey"
    elif report.nonconformant == 0:
        message, color = f"{report.conformant}/{report.total} passing", "brightgreen"
    else:
        message, color = f"{report.nonconformant}/{report.total} drifted", "red"
    return json.dumps(
        {
            "schemaVersion": 1,
            "label": "API conformance",
            "message": message,
            "color": color,
        },
        indent=2,
        sort_keys=True,
    )


def write_report_bundle(report: RunReport, directory: Path) -> None:
    """Write an HTML report, JSON report, and badge from exactly one API run.

    File-system errors are deliberately passed to the CLI so a failed export
    returns an operational error instead of publishing incomplete results.
    """
    rendered = {
        "index.html": render_html(report),
        "report.json": render_json(report),
        "badge.json": render_badge(report),
    }
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        (directory / name).write_text(content + "\n", encoding="utf-8")
