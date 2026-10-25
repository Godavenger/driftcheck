# driftcheck

**Detect when a live API drifts from its OpenAPI contract.** A small Python CLI, plus a
scheduled GitHub Actions workflow that publishes a conformance badge and an HTML report.

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

The report and badge come from the same set of requests. A red badge means that run
found drift or an endpoint could not be reached; the details are in the report.

## What it checks

- **Status codes** the spec never documents → `undocumented_status`. Exact codes,
  ranges such as `2XX`, and `default` responses are matched in that order.
- **Response bodies** against the documented JSON Schema — flagged by kind:
  `type`, `required`, `enum`, `extra` (unexpected property), `nullable`
- **OpenAPI 3.0 and 3.1** response semantics, including 3.0 `nullable` and numeric
  bounds, local and relative `$ref`s, and recursive schemas
- **Media types and JSON decoding**, so an HTML error page or malformed JSON cannot
  accidentally pass as a documented JSON string (`content_type`, `invalid_json`,
  or `body` for an empty required response)
- **Transport** failures (timeout / connection) are reported per-endpoint, so one dead
  endpoint never crashes the whole run

## Install

```bash
# from source (not yet on PyPI)
git clone https://github.com/Godavenger/driftcheck
cd driftcheck
uv sync --locked
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
uv run driftcheck check --config config.yaml
```

It prints a table and **exits non-zero if any endpoint drifted** — handy as a CI gate.

`uv run` uses the project's virtual environment. For an isolated command available
outside the checkout, install with `uv tool install .` or `pipx install .`.

| Exit code | Meaning |
|---|---|
| `0` | All configured endpoints conform |
| `1` | Drift or an endpoint transport failure; a report is still produced |
| `2` | Invalid configuration/specification, missing configured credentials, or export failure |

## Output formats

`--format console` (default) · `json` · `markdown` · `html`

```bash
# build the HTML dashboard the live demo publishes
uv run driftcheck check --config config.yaml --format html --output site/index.html
```

Output directories are created automatically. To publish matching HTML, JSON, and
badge results, run the endpoints **once**:

```bash
uv run driftcheck check --config config.yaml --report-dir site
```

This writes `site/index.html`, `site/report.json`, and `site/badge.json`. It also
prints the selected `--format` to stdout; file notices go to stderr. `--output` and
`--report-dir` are mutually exclusive. The scheduled demo accepts exit code `1` so
drift stays visible, and stops on operational errors instead of deploying partial files.

## Configuration

Local `spec` paths are relative to the **configuration file**, so the same config
works from any working directory. URLs must use HTTP or HTTPS. Endpoint `path`
must exactly match a template in the specification:

```yaml
spec: ./openapi.yaml
base_url: https://api.example.com/v1
timeout_seconds: 10
endpoints:
  - name: get-pet
    method: GET
    path: /pets/{petId}
    path_params: { petId: 42 }
    query_params: { includeDetails: true }
    expected_status: 200
```

Names must be unique, and unknown configuration keys are rejected to catch typos.
`method` defaults to `GET`; `expected_status` is optional and
pins an exact status even when the spec also permits error responses. Path values
are URL-encoded. Path/query parameters accept strings, finite numbers, and booleans;
arrays and objects need serialization that this CLI does not currently implement.
Timeouts must be finite and greater than zero.

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
Set the complete header value, such as `Bearer your-token`; driftcheck does not add
a prefix. An explicitly configured variable that is missing or empty stops the run
with exit code `2`. API authentication headers are not forwarded to specification
or external-reference URLs; use a local spec for contracts requiring authentication.

## Scope

The CLI sends the requests you configure and validates their responses. It does
not generate test inputs, upload request bodies, exercise authentication flows, or
measure performance. Only OpenAPI 3.0/3.1 are supported. Response headers beyond
Content-Type, binary/multipart bodies, and OpenAPI links/callbacks are outside its
scope. JSON Schema `format` annotations are not treated as extra validation rules.

A conformant result covers only the configured examples and constraints written
in the spec. Extra object properties are allowed unless the schema forbids them.
Reports can include values from API responses; publish reports for public data only.

## How it compares (an honest note)

driftcheck is a focused, zero-infrastructure take on contract conformance — it is **not**
trying to replace mature tools like [Schemathesis], [Dredd], or [oasdiff], which are more
powerful and battle-tested. Where it leans is simplicity: no database, no services, one
config file, and a scheduled CI job that turns *"does the live API still match its spec?"*
into a badge you can glance at. I built it to learn API contract testing and CI/CD
end-to-end, and to be genuinely useful for small projects.

## Development

```bash
uv sync --locked
uv run pytest --cov=driftcheck --cov-report=term-missing --cov-fail-under=85
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests mock HTTP and do not contact live APIs. `.github/workflows/ci.yml` runs lint,
format checks, tests, coverage, and package builds on Python 3.11–3.13 on Linux,
plus Python 3.12 on Windows. The scheduled
`.github/workflows/drift.yml` runs the live check and deploys the report to GitHub Pages.

For versioned releases, see [the TestPyPI/PyPI publishing guide](docs/releasing.md).
The publishing workflow uses trusted publishing; registry account setup is required
before the first upload. Source installation works independently of registry publication.

## License

MIT © Sharanya Mondal — see [LICENSE](LICENSE).

[Swagger Petstore]: https://petstore3.swagger.io/
[Schemathesis]: https://github.com/schemathesis/schemathesis
[Dredd]: https://github.com/apiaryio/dredd
[oasdiff]: https://github.com/oasdiff/oasdiff


