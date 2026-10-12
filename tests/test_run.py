"""Integration test for :func:`driftcheck.engine.run`.

Exercises a full run across two endpoints -- one conformant, one with a drifted
body -- and asserts the aggregate counts on the resulting report. HTTP is mocked
with respx; the real network is never touched.
"""

from __future__ import annotations

import pathlib

import httpx
import respx

from driftcheck.engine import run
from driftcheck.models import Config, EndpointCheck
from driftcheck.spec import load_spec

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "petstore.json"
BASE_URL = "https://api.test"


def test_run_mixes_conformant_and_drifted_endpoints() -> None:
    """One clean endpoint and one drifted body give total=2, 1 conformant."""
    spec = load_spec(str(FIXTURE))
    inventory = EndpointCheck(name="inventory", path="/store/inventory", method="GET")
    pet = EndpointCheck(
        name="get-pet",
        path="/pet/{petId}",
        method="GET",
        path_params={"petId": "1"},
    )
    config = Config(spec=str(FIXTURE), base_url=BASE_URL, endpoints=[inventory, pet])

    with respx.mock(assert_all_called=False) as router:
        # Conformant: object of integer counts matches the inventory schema.
        inv_route = router.get(f"{BASE_URL}/store/inventory").mock(
            return_value=httpx.Response(200, json={"available": 5, "sold": 2})
        )
        # Drift: Pet requires "id" (integer) and "name"; id is the wrong type
        # and name is missing entirely.
        pet_route = router.get(f"{BASE_URL}/pet/1").mock(
            return_value=httpx.Response(200, json={"id": "oops"})
        )
        report = run(config, spec, generated_at="2026-01-01T00:00:00Z")

    assert inv_route.called
    assert pet_route.called
    assert report.total == 2
    assert report.conformant == 1
    assert report.nonconformant == 1
    assert report.generated_at == "2026-01-01T00:00:00Z"
