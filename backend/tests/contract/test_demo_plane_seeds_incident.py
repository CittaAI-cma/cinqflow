"""W2-33 · CF-V2-E12-04 — the demo plane's anchor batch gets a ledger row.

`plane()` (`intelligence/demo.py`) seeds batch 8842 with a real error, but
opening its incident needs `workers.incidents.IncidentWorker` — a layer
`intelligence` may not import (`lint-imports`'s own
"api -> workers/installer/simulator -> intelligence" contract forbids it).
So the wiring happens one layer up, in `api/dev.py`'s `build()`, which is
free to reach into `workers` the way `api/app.py` already does.

Tested through `build()` rather than by reaching into `plane()` directly: the
whole point is that the ANCHOR BATCH's incident is reachable over the wire,
because that is what the incidents screen and its own Playwright coverage
depend on. Without this, `GET /api/operations/incidents` — which reads the
LEDGER, deliberately, never recomputed evidence (see that route's own
docstring) — would show nothing for a batch that visibly failed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cinqflow.api.dev import build
from cinqflow.intelligence.demo import BATCH_ID, FEED_ID

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

OPERATOR = "dev-operations@cinqcare.test"


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


@pytest.fixture
def client(tmp_path) -> TestClient:  # type: ignore[no-untyped-def]
    # A real temp directory, never the shared `.cinqflow/landing` a person
    # might be demonstrating from — the same isolation the Playwright config
    # gives its own throwaway `--landing-root`.
    return TestClient(build(landing_root=str(tmp_path)))


def test_the_anchor_batch_has_an_open_incident_on_the_wire(client: TestClient) -> None:
    listed = client.get("/api/operations/incidents?state=open", headers=_as(OPERATOR)).json()
    assert [row["batch_id"] for row in listed] == [BATCH_ID]
    (row,) = listed
    assert row["feed_id"] == FEED_ID
    assert row["state"] == "open"


def test_the_same_incident_is_reachable_from_the_batch_view(client: TestClient) -> None:
    """The list route and the per-batch route read the same ledger row —
    `hydrate` folds the recomputed evidence and the stored decision onto one
    incident id, never two."""
    listed = client.get("/api/operations/incidents?state=open", headers=_as(OPERATOR)).json()
    (row,) = listed

    view = client.get(f"/api/operations/batches/{BATCH_ID}/incident", headers=_as(OPERATOR)).json()
    assert view["incident_id"] == row["incident_id"]
    assert view["state"] == "open"
    # A real error, not an empty cascade — the seeded DQ-002 failure.
    assert view["root_cause"] is not None
