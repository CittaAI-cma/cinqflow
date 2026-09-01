"""GET /api/batches/{batch_id}/{panel} — the batch drawer's five tabs.

Each tab is one certified tool, scoped by the batch_id in the URL — the same
"no private query" mechanism `test_tool_rows_route.py` covers for citations
that are not a batch. This suite is what would have caught the INPUTS tab's
500: it was wired to `get_input_registry`, a tool whose ONE required
parameter is `feed_id`, which this route never supplies — only `batch_id`.
`invoke()` raises `ArgumentError` for the missing parameter, and this route,
unlike `tool_rows`, does not catch `ToolError`, so the 422 `tool_rows` would
have produced surfaced instead as a raw 500.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from tests.contract.seeded_plane import BATCH_ID, FINGERPRINT, build_plane

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

ENGINEER = "dev-engineer@cinqcare.test"


@pytest.fixture
def client() -> Iterator[TestClient]:
    store, control = build_plane()
    app: FastAPI = create_app(authn=StaticAuthn(), metadata_db=store, control_tables=control)
    with TestClient(app) as test_client:
        yield test_client


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


@pytest.mark.parametrize("panel", ["stages", "inputs", "errors", "quarantine", "recon", "drift"])
def test_every_panel_renders_for_a_real_batch(client: TestClient, panel: str) -> None:
    response = client.get(f"/api/batches/{BATCH_ID}/{panel}", headers=_as(ENGINEER))
    assert response.status_code == 200
    assert isinstance(response.json()["rows"], list)


def test_the_inputs_tab_returns_the_batch_s_own_file_not_the_feed_s_whole_history(
    client: TestClient,
) -> None:
    response = client.get(f"/api/batches/{BATCH_ID}/inputs", headers=_as(ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert body["tool"] == "list_batch_inputs"
    (row,) = body["rows"]
    assert row["fingerprint"] == FINGERPRINT
    assert row["citation_id"] == f"file:{FINGERPRINT}"


def test_an_unknown_batch_is_a_200_out_of_scope_not_a_500(client: TestClient) -> None:
    response = client.get("/api/batches/no-such-batch/inputs", headers=_as(ENGINEER))
    assert response.status_code == 200
    assert response.json()["rows"] == []
