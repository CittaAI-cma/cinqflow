"""GET /api/tools/{tool_name} — any certified tool, called with query params.

The generic counterpart to the batch drawer's `_PANEL_TOOLS`: a citation whose
destination is not a batch (contract, plan, rule, term) still opens on rows a
certified tool produced, through the same "no private query" mechanism,
without a bespoke route per citation kind.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from tests.contract.seeded_plane import FEED_ID, build_plane

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


def test_a_contract_is_returned_by_feed_id(client: TestClient) -> None:
    response = client.get(
        f"/api/tools/get_schema_contract?feed_id={FEED_ID}", headers=_as(ENGINEER)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tool"] == "get_schema_contract"
    assert body["row_count"] > 0
    assert body["citations"]


def test_a_compiled_plan_is_returned_by_feed_id(client: TestClient) -> None:
    response = client.get(f"/api/tools/get_compiled_plan?feed_id={FEED_ID}", headers=_as(ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert body["tool"] == "get_compiled_plan"
    assert body["row_count"] > 0


def test_a_rule_or_term_is_resolved_by_lexical_lookup(client: TestClient) -> None:
    response = client.get("/api/tools/lookup_reference?query=DQ-002", headers=_as(ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] > 0
    assert any("DQ-002" in citation for citation in body["citations"])


def test_an_unknown_tool_name_is_a_404_not_a_500(client: TestClient) -> None:
    response = client.get("/api/tools/drop_the_database", headers=_as(ENGINEER))
    assert response.status_code == 404


def test_a_missing_required_argument_is_a_422_not_a_500(client: TestClient) -> None:
    response = client.get("/api/tools/get_schema_contract", headers=_as(ENGINEER))
    assert response.status_code == 422
    assert "feed_id" in response.json()["detail"]


def test_a_feed_that_does_not_exist_is_out_of_scope_not_an_error(client: TestClient) -> None:
    response = client.get(
        "/api/tools/get_schema_contract?feed_id=no-such-feed", headers=_as(ENGINEER)
    )
    assert response.status_code == 200
    assert response.json()["out_of_scope"] is True


def test_no_write_tool_is_reachable_through_this_route(client: TestClient) -> None:
    """Every Wave-0 tool is R0/read-only, so there is nothing to guard beyond
    `require(Action.VIEW)` — but a 404 (never a 200) is the proof."""
    response = client.get("/api/tools/retry_batch", headers=_as(ENGINEER))
    assert response.status_code == 404
