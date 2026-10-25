# Releasing driftcheck

The repository builds wheels and source distributions. Publishing is configured
in [publish.yml](../.github/workflows/publish.yml), but the package-index accounts
must be connected before the first release. Committing this workflow does not
publish a package.

## One-time setup

Create accounts on both [TestPyPI](https://test.pypi.org/) and
[PyPI](https://pypi.org/), and confirm the `driftcheck` project name is available to
you. The two services have separate accounts and project ownership. If another
owner has the name, choose a distribution name you control and update
`pyproject.toml`, the lockfile, and installation examples before publishing.

Create GitHub repository environments named `testpypi` and `pypi`. Restrict them
to release tags (`v*`); configure a required reviewer on `pypi` if you want a
final human release gate.

On each package index, add a pending trusted publisher (or a publisher for an
existing project) with these values:

| Setting | TestPyPI | PyPI |
| --- | --- | --- |
| Project name | `driftcheck` | `driftcheck` |
| GitHub owner | `Godavenger` | `Godavenger` |
| Repository | `driftcheck` | `driftcheck` |
| Workflow filename | `publish.yml` | `publish.yml` |
| Environment | `testpypi` | `pypi` |

The workflow authenticates with GitHub OIDC. No PyPI API token or repository
secret is needed. See the
[trusted-publishing documentation](https://docs.pypi.org/trusted-publishers/).

## Build and verify

Update the version in both `pyproject.toml` and `driftcheck/__init__.py`, then
refresh the lockfile and run:

```bash
uv lock
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
uvx --from twine twine check --strict dist/*
```

Use a fresh virtual environment to install the wheel from `dist/`, then run
`driftcheck --version` and `driftcheck check --help` from outside the checkout.
This ensures the command is loading the packaged code. Source distributions
include the package, tests, public documentation, and example configuration;
local configuration, caches, and reports are excluded.

Commit the release changes and create a tag matching the version, for example
`v0.1.0`. Push the commit and tag. The publishing workflow rejects branch runs
and tags that disagree with the package and CLI versions.

## TestPyPI, then PyPI

1. In GitHub Actions, select **publish → Run workflow**, choose the version tag,
   and leave **Package index** set to `testpypi`. The workflow tests the code,
   builds both distributions, validates their metadata, smoke-tests the installed
   wheel, and publishes the artifacts to TestPyPI.
2. Verify the TestPyPI installation in a fresh environment. Install dependencies
   from PyPI first, then install only driftcheck from TestPyPI. For `0.1.0`:

   ```bash
   uv export --locked --no-dev --no-emit-project --output-file /tmp/driftcheck-deps.txt
   uv venv /tmp/driftcheck-testpypi
   uv pip install --python /tmp/driftcheck-testpypi/bin/python -r /tmp/driftcheck-deps.txt
   uv pip install --python /tmp/driftcheck-testpypi/bin/python --no-deps \
     --index-url https://test.pypi.org/simple/ driftcheck==0.1.0
   cd /tmp
   /tmp/driftcheck-testpypi/bin/driftcheck --version
   /tmp/driftcheck-testpypi/bin/driftcheck check --help
   ```

   These commands use POSIX paths; on Windows use `Scripts/python.exe` and
   `Scripts/driftcheck.exe` inside a temporary environment. Run a check against
   your test API as well. TestPyPI indexing can take a short time after upload.
3. Run the same workflow from the same tag, selecting `pypi`. Before publication,
   it requires the SHA-256 hashes of both distributions to match the files already
   uploaded to TestPyPI. It then publishes those artifacts to PyPI using the
   `pypi` environment.
4. Check a fresh `uv tool install driftcheck==0.1.0` installation, then add the
   PyPI installation command to the README and publish the GitHub release notes.

Keep a release tag fixed after TestPyPI publication. Package indexes do not allow
replacing an uploaded filename with different bytes. If a fix is needed, bump
the version and create a new tag instead of moving the existing tag or enabling
`skip-existing` to hide an upload conflict.
