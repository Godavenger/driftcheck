"""Shared data contracts for driftcheck.

Every other module implements against these dataclasses. Keep this module free of
side effects and I/O so it stays a pure, importable contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EndpointCheck:
    name: str
    path: str  # e.g. "/pet/{petId}"
    method: str = "GET"
    path_params: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    expected_status: int | None = None  # if set, the live status must equal this


@dataclass
class Config:
    spec: str  # file path or URL
    base_url: str
    endpoints: list[EndpointCheck]
    auth_header_env: str | None = None  # NAME of env var holding the token
    auth_header_name: str = "Authorization"
    timeout_seconds: float = 10.0


@dataclass
class ExpectedResponse:
    status: int
    schema: dict  # JSON Schema for the body


@dataclass
class ActualResponse:
    status: int | None
    body: Any
    elapsed_ms: float
    error: str | None = None  # transport/timeout error, if any


@dataclass
class Mismatch:
    kind: str  # status|type|required|nullable|enum|extra|transport|undocumented_status
    location: str  # e.g. "$.data[0].price"
    expected: str
    actual: str
    message: str


@dataclass
class EndpointResult:
    name: str
    path: str
    method: str
    conformant: bool
    mismatches: list[Mismatch]


@dataclass
class RunReport:
    results: list[EndpointResult]
    total: int
    conformant: int
    nonconformant: int
    generated_at: str  # ISO string, PASSED IN (keep pure fns clock-free)
