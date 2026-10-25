"""Command-line entrypoint for driftcheck.

Wires up the Typer ``app`` with a top-level ``--version`` option and the
``check`` command, which loads a config and spec, runs the conformance checks,
renders the report in the requested format, and exits with a drift-aware code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import typer

from driftcheck import __version__
from driftcheck.client import auth_headers
from driftcheck.config import load_config
from driftcheck.engine import run
from driftcheck.errors import ConfigError, SpecError
from driftcheck.report import (
    exit_code,
    render_console,
    render_html,
    render_json,
    render_markdown,
    write_report_bundle,
)
from driftcheck.spec import load_spec

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Detect when a live API drifts from its OpenAPI contract.",
)


class OutputFormat(StrEnum):
    """Supported ``--format`` values for the ``check`` command."""

    console = "console"
    json = "json"
    markdown = "markdown"
    html = "html"


def _version_callback(value: bool) -> None:
    """Print the version and exit when ``--version`` is supplied."""
    if value:
        typer.echo(f"driftcheck {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """driftcheck — API contract drift detector."""


@app.command()
def check(
    config: str = typer.Option(..., "--config", help="Path to the driftcheck config file."),
    fmt: OutputFormat = typer.Option(
        OutputFormat.console,
        "--format",
        help="Output format for the drift report.",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        help="Write the report to this path instead of stdout.",
    ),
    report_dir: str | None = typer.Option(
        None,
        "--report-dir",
        help="Write index.html, report.json, and badge.json from this same run into a directory.",
    ),
) -> None:
    """Run the configured checks against the live API and report any drift."""
    if output and report_dir:
        raise typer.BadParameter("--output and --report-dir cannot be used together")
    try:
        cfg = load_config(config)
        auth_headers(cfg)
        spec = load_spec(cfg.spec)
        report = run(cfg, spec, generated_at=datetime.now(UTC).isoformat())
    except (ConfigError, SpecError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    renderers = {
        OutputFormat.console: render_console,
        OutputFormat.json: render_json,
        OutputFormat.markdown: render_markdown,
        OutputFormat.html: render_html,
    }
    text = renderers[fmt](report)

    try:
        if report_dir:
            write_report_bundle(report, Path(report_dir))
            typer.echo(text)
            typer.echo(f"wrote report bundle to {report_dir}", err=True)
        elif output:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            typer.echo(f"wrote {output}", err=True)
        else:
            typer.echo(text)
    except OSError as exc:
        typer.secho(f"could not write report: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    raise typer.Exit(exit_code(report))


if __name__ == "__main__":
    app()
