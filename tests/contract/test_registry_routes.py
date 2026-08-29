"""CF-V1-E3-02 through the API — the full aggregate, its checklist, its sources.

    "activation blocked without SLA/owner with plain-language checklist ·
     unique ID + v1 on save · referenced-everywhere view"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

`tests/unit/test_feed_operations.py` proves the rules. This proves the routes
cannot be talked past them, that a half-filled feed still SAVES, and that the
checklist a form renders is the same computation the submit button enforces.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import ObjectType

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

BA = "dev-ba@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
PLATFORM = "dev-platform@cinqcare.test"

FEED_ID = "fidelis-downstate-roster"
SOURCE_ID = "fidelis-ny"

OPERATIONS: dict[str, Any] = {
    "source_id": SOURCE_ID,
    "direction": "inbound",
    "delivery_method": "sftp",
    "endpoint_ref": "fidelis-downstate-sftp",
    "owners": [
        {"role": "business", "subject": "meera@cinqcare.test", "display_name": "Meera Rao"},
        {"role": "technical", "subject": "sam@cinqcare.test", "display_name": "Sam Okafor"},
    ],
    "service_level": {
        "expected_by_local_time": "06:00",
        "timezone": "America/New_York",
        "calendar": "business_days",
        "grace_minutes": 30,
        "escalate_after_minutes": 120,
    },
    "volume": {"typical_records": 40000, "tolerance_percent": 20},
    "alert_chain": [
        {"after_minutes": 30, "channel": "email", "notify": ["sam@cinqcare.test"]},
        {"after_minutes": 120, "channel": "pager", "notify": ["meera@cinqcare.test"]},
    ],
    "documents": [
        {
            "kind": "companion_guide",
            "label": "Fidelis 834 companion guide",
            "reference": "https://wiki.example.test/fidelis/834",
        }
    ],
}

BARE_FEED: dict[str, Any] = {
    "feed_id": FEED_ID,
    "domain": "membership",
    "source_system": "fidelis",
    "file_format": "xlsx",
    "landing_path": "landing/fidelis/roster",
    "file_pattern": r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$",
    "schedule_cron": "0 6 * * 1",
    "sample_filename": "_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
}
FULL_FEED: dict[str, Any] = {**BARE_FEED, "operations": OPERATIONS}

SOURCE: dict[str, Any] = {
    "source_id": SOURCE_ID,
    "name": "Fidelis Care of New York",
    "kind": "payer",
    "endpoint_ref": "fidelis-sftp",
    "line_of_business": ["Medicaid", "Medicare"],
    "states": ["NY"],
    "owners": [{"role": "business", "subject": "meera@cinqcare.test", "display_name": "Meera Rao"}],
    "counterparty_contact": "R. Adeyemi, Fidelis data operations",
}


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def client(store: MemMetadataDb) -> Iterator[TestClient]:
    with TestClient(create_app(authn=StaticAuthn(), metadata_db=store)) as test_client:
        yield test_client


# ── save is permissive ───────────────────────────────────────────────────────


def test_a_half_gathered_feed_still_saves(client: TestClient) -> None:
    """An analyst waiting three days for a payer's SLA needs somewhere to keep
    what they already have. Validation-at-save is how a registry fills up with
    `owner@example.com`."""
    created = client.post("/api/feeds", json=BARE_FEED, headers=_as(BA))
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["lifecycle_state"] == "draft"


def test_a_saved_feed_reports_what_is_still_missing(client: TestClient) -> None:
    client.post("/api/feeds", json=BARE_FEED, headers=_as(BA))
    body = client.get(f"/api/feeds/{FEED_ID}", headers=_as(BA)).json()

    assert body["readiness"]["is_ready"] is False
    assert body["readiness"]["outstanding"] == 7
    questions = [i["question"] for i in body["readiness"]["items"] if not i["satisfied"]]
    assert "Who in the business owns this feed?" in questions


def test_the_readiness_route_and_the_feed_agree(client: TestClient) -> None:
    """ONE computation. A screen showing green while the submit button returns
    403 is the classic shape of a rule implemented twice."""
    client.post("/api/feeds", json=BARE_FEED, headers=_as(BA))
    inline = client.get(f"/api/feeds/{FEED_ID}", headers=_as(BA)).json()["readiness"]
    standalone = client.get(f"/api/feeds/{FEED_ID}/readiness", headers=_as(BA)).json()
    assert inline == standalone


# ── activation is not ────────────────────────────────────────────────────────


def test_a_feed_nobody_could_operate_cannot_be_submitted(
    client: TestClient, store: MemMetadataDb
) -> None:
    client.post("/api/feeds", json=BARE_FEED, headers=_as(BA))
    refused = client.post(
        f"/api/objects/feed/{FEED_ID}/submit", json={"comment": ""}, headers=_as(BA)
    )
    assert refused.status_code == 403
    detail = refused.json()["detail"]
    assert "cannot be activated yet" in detail
    assert "Why it matters:" in detail and "To fix:" in detail
    assert any(e.action == "refused:submit" for e in store.read_audit(object_id=FEED_ID))


def test_a_complete_feed_submits(client: TestClient) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    submitted = client.post(
        f"/api/objects/feed/{FEED_ID}/submit", json={"comment": "ready"}, headers=_as(BA)
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["lifecycle_state"] == "pending_review"


def test_completing_the_envelope_unblocks_activation(client: TestClient) -> None:
    """The full journey: save what you have, gather the rest, then submit."""
    client.post("/api/feeds", json=BARE_FEED, headers=_as(BA))
    assert (
        client.post(f"/api/objects/feed/{FEED_ID}/submit", json={}, headers=_as(BA)).status_code
        == 403
    )

    amended = client.put(f"/api/feeds/{FEED_ID}", json=FULL_FEED, headers=_as(BA))
    assert amended.status_code == 200, amended.text
    assert amended.json()["version"] == 2
    assert amended.json()["readiness"]["is_ready"] is True
    assert (
        client.post(f"/api/objects/feed/{FEED_ID}/submit", json={}, headers=_as(BA)).status_code
        == 200
    )


def test_an_edit_that_omits_the_envelope_keeps_it(client: TestClient) -> None:
    """A PUT that quietly empties fields the caller did not mention is how a
    feed becomes un-activatable without anybody touching it."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    amended = client.put(
        f"/api/feeds/{FEED_ID}",
        json={**BARE_FEED, "schedule_cron": "0 7 * * 1"},
        headers=_as(BA),
    ).json()

    assert amended["schedule_cron"] == "0 7 * * 1"
    assert amended["readiness"]["is_ready"] is True
    assert len(amended["operations"]["owners"]) == 2


# ── the envelope's own refusals, over HTTP ───────────────────────────────────


def test_a_timezone_offset_is_refused_at_the_boundary(client: TestClient) -> None:
    body = {
        **FULL_FEED,
        "operations": {
            **OPERATIONS,
            "service_level": {**OPERATIONS["service_level"], "timezone": "-05:00"},
        },
    }
    refused = client.post("/api/feeds", json=body, headers=_as(BA))
    assert refused.status_code == 422
    assert "IANA timezone name" in refused.json()["detail"]


def test_an_endpoint_that_is_a_host_is_refused(client: TestClient) -> None:
    body = {
        **FULL_FEED,
        "operations": {**OPERATIONS, "endpoint_ref": "sftp://files.fidelis.example.com/roster"},
    }
    refused = client.post("/api/feeds", json=body, headers=_as(BA))
    assert refused.status_code == 422
    assert "looks like a location" in refused.json()["detail"]


def test_a_credentialled_document_link_is_refused(client: TestClient) -> None:
    body = {
        **FULL_FEED,
        "operations": {
            **OPERATIONS,
            "documents": [
                {
                    "kind": "specification",
                    "label": "Spec",
                    "reference": "https://files.example.test/spec.pdf?sas=abc",
                }
            ],
        },
    }
    refused = client.post("/api/feeds", json=body, headers=_as(BA))
    assert refused.status_code == 422
    assert "carries a credential" in refused.json()["detail"]


# ── sources ──────────────────────────────────────────────────────────────────


def test_a_source_is_created_as_a_draft_at_v1(client: TestClient) -> None:
    created = client.post("/api/sources", json=SOURCE, headers=_as(BA))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 1
    assert body["lifecycle_state"] == "draft"
    assert body["name"] == "Fidelis Care of New York"


def test_a_source_lists_the_feeds_that_name_it(client: TestClient) -> None:
    """COMPUTED from the feeds, never a maintained list — which is what makes
    "which of Fidelis's feeds are late" answerable at all."""
    client.post("/api/sources", json=SOURCE, headers=_as(BA))
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))

    body = client.get(f"/api/sources/{SOURCE_ID}", headers=_as(BA)).json()
    assert body["feed_ids"] == [FEED_ID]


def test_a_source_id_that_is_not_an_identifier_is_refused(client: TestClient) -> None:
    refused = client.post(
        "/api/sources", json={**SOURCE, "source_id": "Fidelis NY!"}, headers=_as(BA)
    )
    assert refused.status_code == 422
    assert "not a source id" in refused.json()["detail"]


def test_editing_a_source_creates_a_new_version_in_draft(
    client: TestClient, store: MemMetadataDb
) -> None:
    client.post("/api/sources", json=SOURCE, headers=_as(BA))
    amended = client.put(
        f"/api/sources/{SOURCE_ID}",
        json={**SOURCE, "counterparty_contact": "K. Osei, Fidelis data operations"},
        headers=_as(BA),
    ).json()

    assert amended["version"] == 2
    assert amended["lifecycle_state"] == "draft"
    assert len(store.history(ObjectType.SOURCE, SOURCE_ID)) == 2


def test_renaming_a_source_through_its_own_url_is_refused(client: TestClient) -> None:
    client.post("/api/sources", json=SOURCE, headers=_as(BA))
    refused = client.put(
        f"/api/sources/{SOURCE_ID}", json={**SOURCE, "source_id": "centene-ny"}, headers=_as(BA)
    )
    assert refused.status_code == 400
    assert "create in disguise" in refused.json()["detail"]


def test_a_read_only_user_may_not_create_a_source(client: TestClient) -> None:
    refused = client.post("/api/sources", json=SOURCE, headers=_as(READ_ONLY))
    assert refused.status_code == 403


def test_a_missing_source_is_a_not_found(client: TestClient) -> None:
    assert client.get("/api/sources/nobody", headers=_as(BA)).status_code == 404


# ── referenced everywhere ────────────────────────────────────────────────────


def test_the_references_view_is_computed_not_maintained(client: TestClient) -> None:
    """A registry whose "used by" column is hand-kept is a registry whose
    "used by" column is wrong — and the version people trust most is the one
    nobody has updated since March."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    body = client.get(f"/api/feeds/{FEED_ID}/references", headers=_as(BA)).json()

    assert body["object_id"] == FEED_ID
    assert body["version"] == 1
    assert isinstance(body["references"], list)
    assert isinstance(body["unknowns"], list)


def test_the_references_view_is_scoped_like_everything_else(client: TestClient) -> None:
    """An out-of-scope feed must be indistinguishable from one that does not
    exist, or the refusal itself tells the caller the feed is real."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    missing = client.get("/api/feeds/not-a-feed/references", headers=_as(BA))
    assert missing.status_code == 404
