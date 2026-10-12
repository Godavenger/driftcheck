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
from driftcheck.config import load_config
from driftcheck.engine import run
from driftcheck.errors import ConfigError, SpecError
from driftcheck.report import (
    exit_code,
    render_console,
    render_html,
    render_json,
    render_markdown,
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
) -> None:
    """Run the configured checks against the live API and report any drift."""
    try:
        cfg = load_config(config)
        spec = load_spec(cfg.spec)
    except (ConfigError, SpecError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    report = run(cfg, spec, generated_at=datetime.now(UTC).isoformat())

    renderers = {
        OutputFormat.console: render_console,
        OutputFormat.json: render_json,
        OutputFormat.markdown: render_markdown,
        OutputFormat.html: render_html,
    }
    text = renderers[fmt](report)

    if output:
        Path(output).write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text)

    raise typer.Exit(exit_code(report))


if __name__ == "__main__":
    app()
