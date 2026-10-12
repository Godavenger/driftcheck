# driftcheck

**Detect when a live API drifts from its OpenAPI contract.** A small Python CLI, plus a
GitHub Action that publishes a live conformance badge and an HTML report to GitHub Pages.

[![CI](https://github.com/Godavenger/driftcheck/actions/workflows/ci.yml/badge.svg)](https://github.com/Godavenger/driftcheck/actions/workflows/ci.yml)
[![API conformance](https://img.shields.io/endpoint?url=https://godavenger.github.io/driftcheck/badge.json)](https://godavenger.github.io/driftcheck/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

---

## The problem

An OpenAPI spec is a promise: *call this endpoint and you'll get a body shaped like this.*
Over time a live API drifts from that promise — a field changes type, a required field
goes missing, an undocumented status code appears — and nothing fails loudly until a
client breaks in production. driftcheck calls the real endpoints and checks each response
against the documented schema, so drift shows up as a diff, not an incident.

## Live demo

driftcheck runs against the public [Swagger Petstore] every day and publishes the result:

- 📊 **[Live report](https://godavenger.github.io/driftcheck/)** — per-endpoint status
- the conformance badge above is generated from that same run

At the time of writing it finds *real* drift in Petstore — which is rather the point.

## What it checks

- **Status codes** the spec never documents → `undocumented_status`
- **Response bodies** against the documented JSON Schema — flagged by kind:
  `type`, `required`, `enum`, `extra` (unexpected property), `nullable`
- **Transport** failures (timeout / connection) are reported per-endpoint, so one dead
  endpoint never crashes the whole run

## Install

```bash
# from source (not yet on PyPI)
git clone https://github.com/Godavenger/driftcheck
cd driftcheck
uv sync
```

## Quickstart

Write a `config.yaml` (see [`config.example.yaml`](config.example.yaml)):

```yaml
spec: https://petstore3.swagger.io/api/v3/openapi.json
base_url: https://petstore3.swagger.io/api/v3
endpoints:
  - name: find-pets-by-status
    path: /pet/findByStatus
    query_params: { status: available }
  - name: inventory
    path: /store/inventory
```

Then run:

```bash
driftcheck check --config config.yaml
```

It prints a table and **exits non-zero if any endpoint drifted** — handy as a CI gate.

## Output formats

`--format console` (default) · `json` · `markdown` · `html`

```bash
# build the HTML dashboard the live demo publishes
driftcheck check --config config.yaml --format html --output site/index.html
```

## How it works

| Module | Responsibility |
|---|---|
| `config.py` | Parse & validate `config.yaml` |
| `spec.py` | Load the OpenAPI spec, resolve `$ref`s, index responses |
| `client.py` | Perform each request (httpx), capture the response |
| `validate.py` | Response body vs JSON Schema → typed mismatches |
| `engine.py` | Status match + orchestrate the whole run |
| `report.py` | Render console / JSON / Markdown / HTML + exit code |
| `cli.py` | Typer entrypoint |

The core logic is pure and clock-free (timestamps are injected), so every module is
unit-tested with mocked HTTP — the test suite never touches the network.

## Authentication

For APIs that need a token, point `auth_header_env` at the **name** of an environment
variable (never the token itself):

```yaml
auth_header_env: API_TOKEN
auth_header_name: Authorization
```

In CI, store the token as a GitHub Secret exposed under that variable name.

## How it compares (an honest note)

driftcheck is a focused, zero-infrastructure take on contract conformance — it is **not**
trying to replace mature tools like [Schemathesis], [Dredd], or [oasdiff], which are more
powerful and battle-tested. Where it leans is simplicity: no database, no services, one
config file, and a scheduled CI job that turns *"does the live API still match its spec?"*
into a badge you can glance at. I built it to learn API contract testing and CI/CD
end-to-end, and to be genuinely useful for small projects.

## Development

```bash
uv sync
uv run pytest          # HTTP fully mocked — no network
uv run ruff check .
uv run ruff format .
```

`.github/workflows/ci.yml` runs lint + tests on every push and PR. The scheduled
`.github/workflows/drift.yml` runs the live check and deploys the report to GitHub Pages.

## License

MIT © Sharanya Mondal — see [LICENSE](LICENSE).

[Swagger Petstore]: https://petstore3.swagger.io/
[Schemathesis]: https://github.com/schemathesis/schemathesis
[Dredd]: https://github.com/apiaryio/dredd
[oasdiff]: https://github.com/oasdiff/oasdiff


