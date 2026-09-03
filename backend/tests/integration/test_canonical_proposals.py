"""Canonical field proposals: a controlled request to extend the governed model,
never an automatic write to it."""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from cinqflow.api.app import create_app
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def client(conn, settings) -> TestClient:  # conn creates/drops the schemas
    return TestClient(create_app(settings))


def _create(client, **overrides) -> dict:
    body = {
        "domain": "enrollment",
        "entity": "member",
        "field_name": "enrollment_status",
        "type": "string",
        "reason": "no governed target exists for current enrollment status",
        "concept": "Member enrollment status",
        "evidence": ["domains/enrollment.yaml known_gaps"],
        "requested_by": "analyst@cinqcare.com",
        **overrides,
    }
    resp = client.post("/api/canonical-proposals", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _knowledge_checksums(root) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.yaml"))
    }


def test_create_persists_the_request_pending_review(client):
    created = _create(client)
    assert created["status"] == "pending_review"
    assert created["domain"] == "enrollment"
    assert created["field_name"] == "enrollment_status"
    assert created["concept"] == "Member enrollment status"
    assert created["decided_by"] is None
    assert created["decided_ts"] is None


def test_get_returns_what_was_created(client):
    created = _create(client)
    fetched = client.get(f"/api/canonical-proposals/{created['proposal_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_get_unknown_proposal_is_404(client):
    resp = client.get("/api/canonical-proposals/6f0c0000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_list_filters_by_domain_and_status(client):
    _create(client, domain="enrollment", field_name="enrollment_status")
    _create(client, domain="claims", field_name="drg_code", entity="claim_header")

    enrollment_only = client.get("/api/canonical-proposals", params={"domain": "enrollment"})
    assert enrollment_only.status_code == 200
    domains = {p["domain"] for p in enrollment_only.json()["proposals"]}
    assert domains == {"enrollment"}

    pending = client.get("/api/canonical-proposals", params={"status": "pending_review"})
    assert len(pending.json()["proposals"]) >= 2


def test_decide_accepts_and_records_who(client):
    created = _create(client)
    decided = client.post(
        f"/api/canonical-proposals/{created['proposal_id']}/decide",
        json={"decision": "accepted", "decided_by": "steward@cinqcare.com", "note": "adding it"},
    )
    assert decided.status_code == 202, decided.text
    body = decided.json()
    assert body["status"] == "accepted"
    assert body["decided_by"] == "steward@cinqcare.com"
    assert body["decision_note"] == "adding it"
    assert body["decided_ts"] is not None


def test_decide_rejects_too(client):
    created = _create(client)
    decided = client.post(
        f"/api/canonical-proposals/{created['proposal_id']}/decide",
        json={"decision": "rejected", "decided_by": "steward@cinqcare.com"},
    )
    assert decided.status_code == 202
    assert decided.json()["status"] == "rejected"


def test_decide_twice_is_refused(client):
    created = _create(client)
    client.post(
        f"/api/canonical-proposals/{created['proposal_id']}/decide",
        json={"decision": "accepted", "decided_by": "steward@cinqcare.com"},
    )
    second = client.post(
        f"/api/canonical-proposals/{created['proposal_id']}/decide",
        json={"decision": "rejected", "decided_by": "someone_else@cinqcare.com"},
    )
    assert second.status_code == 409


def test_decide_with_an_invalid_decision_is_refused(client):
    created = _create(client)
    resp = client.post(
        f"/api/canonical-proposals/{created['proposal_id']}/decide",
        json={"decision": "maybe", "decided_by": "steward@cinqcare.com"},
    )
    assert resp.status_code == 422


def test_decide_unknown_proposal_is_404(client):
    resp = client.post(
        "/api/canonical-proposals/6f0c0000-0000-0000-0000-000000000000/decide",
        json={"decision": "accepted", "decided_by": "steward@cinqcare.com"},
    )
    assert resp.status_code == 404


def test_accepting_a_proposal_never_writes_to_any_knowledge_file(client, settings):
    """Accepting only records the decision. A steward hand-edits the YAML
    afterward - nothing here may pre-empt that by writing it automatically."""
    before = _knowledge_checksums(settings.knowledge_root)

    created = _create(client)
    client.post(
        f"/api/canonical-proposals/{created['proposal_id']}/decide",
        json={"decision": "accepted", "decided_by": "steward@cinqcare.com"},
    )

    after = _knowledge_checksums(settings.knowledge_root)
    assert after == before
