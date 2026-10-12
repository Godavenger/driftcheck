"""Conformance engine: tie the HTTP client, spec model, and validator together.

Produces a per-endpoint :class:`EndpointResult` and an aggregate
:class:`RunReport`. The engine is clock-free: the ``generated_at`` timestamp is
supplied by the caller so the pure functions stay deterministic and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from driftcheck.client import call_endpoint
from driftcheck.models import Config, EndpointCheck, EndpointResult, Mismatch, RunReport
from driftcheck.validate import validate_response

if TYPE_CHECKING:
    from driftcheck.spec import SpecModel


def check_endpoint(config: Config, spec: SpecModel, check: EndpointCheck) -> EndpointResult:
    """Call one endpoint and judge its live response against the spec."""
    actual = call_endpoint(config, check)

    # 1. Transport/timeout failure: no usable response to validate.
    if actual.error:
        return EndpointResult(
            name=check.name,
            path=check.path,
            method=check.method,
            conformant=False,
            mismatches=[
                Mismatch(
                    kind="transport",
                    location="",
                    expected="a response from the endpoint",
                    actual=actual.error,
                    message=actual.error,
                )
            ],
        )

    # 2. Status the spec never documents for this operation.
    documented = spec.documented_statuses(check.path, check.method)
    if actual.status not in documented:
        return EndpointResult(
            name=check.name,
            path=check.path,
            method=check.method,
            conformant=False,
            mismatches=[
                Mismatch(
                    kind="undocumented_status",
                    location="",
                    expected=f"one of {documented}",
                    actual=str(actual.status),
                    message=f"status {actual.status} is not documented",
                )
            ],
        )

    # 2b. An explicit expected_status pins the exact status the caller requires.
    if check.expected_status is not None and actual.status != check.expected_status:
        return EndpointResult(
            name=check.name,
            path=check.path,
            method=check.method,
            conformant=False,
            mismatches=[
                Mismatch(
                    kind="status",
                    location="",
                    expected=str(check.expected_status),
                    actual=str(actual.status),
                    message=f"expected status {check.expected_status}, got {actual.status}",
                )
            ],
        )

    # 3. Documented status but no JSON body schema to check against.
    expected = spec.expected_response(check.path, check.method, actual.status)
    if expected is None:
        return EndpointResult(
            name=check.name,
            path=check.path,
            method=check.method,
            conformant=True,
            mismatches=[],
        )

    # 4. Documented status with a schema: validate the body.
    mismatches = validate_response(expected, actual)
    return EndpointResult(
        name=check.name,
        path=check.path,
        method=check.method,
        conformant=not mismatches,
        mismatches=mismatches,
    )


def run(config: Config, spec: SpecModel, generated_at: str) -> RunReport:
    """Check every configured endpoint and aggregate the conformance counts."""
    results = [check_endpoint(config, spec, check) for check in config.endpoints]
    total = len(results)
    conformant = sum(result.conformant for result in results)
    return RunReport(
        results=results,
        total=total,
        conformant=conformant,
        nonconformant=total - conformant,
        generated_at=generated_at,
    )
