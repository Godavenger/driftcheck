"""Conformance engine: tie the HTTP client, spec model, and validator together.

Produces a per-endpoint :class:`EndpointResult` and an aggregate
:class:`RunReport`. The engine is clock-free: the ``generated_at`` timestamp is
supplied by the caller so the pure functions stay deterministic and testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from driftcheck.client import call_endpoint
from driftcheck.errors import SpecError
from driftcheck.models import Config, EndpointCheck, EndpointResult, Mismatch, RunReport
from driftcheck.validate import validate_response

if TYPE_CHECKING:
    from driftcheck.spec import SpecModel


def check_endpoint(config: Config, spec: SpecModel, check: EndpointCheck) -> EndpointResult:
    """Call one endpoint and judge its live response against the spec."""
    _require_operation(spec, check)
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
    if not spec.is_documented_status(check.path, check.method, actual.status):
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

    # HEAD, 204 and 304 have no response body by HTTP semantics.
    if check.method.upper() == "HEAD" or actual.status in (204, 304):
        return EndpointResult(check.name, check.path, check.method, True, [])

    # Select the schema for the actual media type, including vendor JSON and
    # wildcard declarations. A missing header/body must not pass a JSON schema.
    content_types = spec.documented_content_types(check.path, check.method, actual.status)
    if content_types:
        media_type = actual.content_type or ""
        is_json = media_type == "application/json" or media_type.endswith("+json")
        if not actual.body_present and (not media_type or is_json):
            return _body_mismatch(
                check, "body", "a response body", "empty", "response body is empty"
            )
        if (
            spec.matching_content_type(check.path, check.method, actual.status, actual.content_type)
            is None
        ):
            return _body_mismatch(
                check,
                "content_type",
                ", ".join(content_types),
                actual.content_type or "missing",
                "response Content-Type does not match any documented media type",
            )
        if actual.json_error and is_json:
            return _body_mismatch(
                check, "invalid_json", "valid JSON", "invalid JSON", actual.json_error
            )

    # 3. Documented status but no body schema to check against.
    expected = spec.expected_response(check.path, check.method, actual.status, actual.content_type)
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
    for check in config.endpoints:
        _require_operation(spec, check)
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


def _require_operation(spec: SpecModel, check: EndpointCheck) -> None:
    if not spec.has_operation(check.path, check.method):
        raise SpecError(
            f"Operation {check.method} {check.path} is not present in the OpenAPI spec."
        )


def _body_mismatch(
    check: EndpointCheck, kind: str, expected: str, actual: str, message: str
) -> EndpointResult:
    return EndpointResult(
        check.name,
        check.path,
        check.method,
        False,
        [Mismatch(kind, "$", expected, actual, message)],
    )
