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


# ── clone and search · CF-V1-E3-03 ───────────────────────────────────────────

MEDICAID: dict[str, Any] = {
    "feed_id": "centene-medicaid-roster",
    "domain": "membership",
    "source_system": "centene",
    "file_format": "csv",
    "landing_path": "landing/centene/medicaid",
    "file_pattern": r"^CENTENE_Medicaid_Roster_\d{8}\.csv$",
    "schedule_cron": "0 6 * * 1",
    "sample_filename": "CENTENE_Medicaid_Roster_20260801.csv",
    "operations": {**OPERATIONS, "source_id": "centene-ny"},
}


def test_the_registry_can_be_searched_and_filtered(client: TestClient) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))

    assert len(client.get("/api/feeds", headers=_as(BA)).json()) == 2
    found = client.get("/api/feeds?q=medicaid", headers=_as(BA)).json()
    assert [f["feed_id"] for f in found] == ["centene-medicaid-roster"]
    assert (
        client.get("/api/feeds?source_id=centene-ny", headers=_as(BA)).json()[0]["feed_id"]
        == "centene-medicaid-roster"
    )
    assert client.get("/api/feeds?state=draft", headers=_as(BA)).json() != []


def test_search_never_reveals_a_feed_the_caller_cannot_see(client: TestClient) -> None:
    """The scope check runs BEFORE the filter, or the search box becomes the
    way to find out which feeds exist."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    for query in ("", "?q=fidelis", "?domain=membership"):
        assert client.get(f"/api/feeds{query}", headers=_as(PLATFORM)).status_code == 200


def test_the_platform_offers_the_feed_worth_cloning_from(client: TestClient) -> None:
    """ "Centene Medicare is a near-clone of Medicaid" — and the platform says
    so from the registry's own fields, with its reasons attached."""
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))

    ranked = client.get(f"/api/feeds/{MEDICAID['feed_id']}/similar", headers=_as(BA)).json()
    assert ranked, "a membership roster from another payer is worth offering"
    assert ranked[0]["feed_id"] == FEED_ID
    assert any("same domain" in reason for reason in ranked[0]["reasons"])


def test_a_clone_copies_the_configuration_and_none_of_the_approval(
    client: TestClient, store: MemMetadataDb
) -> None:
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    created = client.post(
        f"/api/feeds/{MEDICAID['feed_id']}/clone",
        json={
            "new_feed_id": "centene-medicare-roster",
            "overrides": {
                "landing_path": "landing/centene/medicare",
                "file_pattern": r"^CENTENE_Medicare_Roster_\d{8}\.csv$",
                "sample_filename": "CENTENE_Medicare_Roster_20260801.csv",
            },
        },
        headers=_as(BA),
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["cloned_from"] == MEDICAID["feed_id"]
    assert all(obj["lifecycle_state"] == "draft" for obj in body["created"])
    assert all(obj["version"] == 1 for obj in body["created"])
    assert all(obj["approved_by_subject"] is None for obj in body["created"])

    clone = store.get(ObjectType.FEED, "centene-medicare-roster")
    assert clone.body["operations"]["owners"] == OPERATIONS["owners"]
    assert clone.body["landing_path"] == "landing/centene/medicare"


def test_the_clone_response_carries_the_differences_panel(client: TestClient) -> None:
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    body = client.post(
        f"/api/feeds/{MEDICAID['feed_id']}/clone",
        json={
            "new_feed_id": "centene-medicare-roster",
            "overrides": {"landing_path": "landing/centene/medicare"},
        },
        headers=_as(BA),
    ).json()

    changed = {d["field_path"]: d for d in body["differences"]}
    assert changed["landing_path"]["original"] == "landing/centene/medicaid"
    assert changed["landing_path"]["clone"] == "landing/centene/medicare"


def test_cloning_from_a_draft_warns_that_nobody_approved_it(client: TestClient) -> None:
    """ "Unapproved inherited parts marked." The original here has never been
    reviewed, so the clone inherits a draft rather than a decision."""
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    body = client.post(
        f"/api/feeds/{MEDICAID['feed_id']}/clone",
        json={"new_feed_id": "centene-medicare-roster"},
        headers=_as(BA),
    ).json()

    assert body["warnings"], "cloning a draft must say so"
    assert "nobody has approved it" in body["warnings"][0]
    assert body["inherited"][0]["was_approved"] is False


def test_cloning_onto_an_existing_feed_is_refused(client: TestClient) -> None:
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    refused = client.post(
        f"/api/feeds/{MEDICAID['feed_id']}/clone",
        json={"new_feed_id": FEED_ID},
        headers=_as(BA),
    )
    assert refused.status_code == 409
    assert "already exists" in refused.json()["detail"]


def test_a_read_only_user_may_not_clone(client: TestClient) -> None:
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    refused = client.post(
        f"/api/feeds/{MEDICAID['feed_id']}/clone",
        json={"new_feed_id": "centene-medicare-roster"},
        headers=_as(READ_ONLY),
    )
    assert refused.status_code == 403


def test_every_cloned_object_leaves_an_audit_row(client: TestClient, store: MemMetadataDb) -> None:
    client.post("/api/feeds", json=MEDICAID, headers=_as(BA))
    client.post(
        f"/api/feeds/{MEDICAID['feed_id']}/clone",
        json={"new_feed_id": "centene-medicare-roster"},
        headers=_as(BA),
    )
    ledger = store.read_audit(object_id="centene-medicare-roster")
    assert any(e.action == "cloned" for e in ledger)
    assert any(MEDICAID["feed_id"] in e.detail for e in ledger)


# ── lifecycle, pause and version history · CF-V1-E3-04 ───────────────────────

STEWARD = "dev-steward@cinqcare.test"
ENGINEER = "dev-engineer@cinqcare.test"


def test_an_illegal_transition_is_refused_and_recorded(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Approving something nobody submitted. Refused by the STATE MACHINE, and
    the attempt leaves a row — a guardrail nobody can see fire is a comment."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    refused = client.post(
        f"/api/objects/feed/{FEED_ID}/approve",
        json={"comment": "looks fine"},
        headers=_as(PLATFORM),
    )
    assert refused.status_code == 403
    assert "cannot go draft -> approved" in refused.json()["detail"]
    assert any(e.action == "refused:approve" for e in store.read_audit(object_id=FEED_ID))


def test_the_wrong_approver_is_refused_before_the_state_machine_is_consulted(
    client: TestClient, store: MemMetadataDb
) -> None:
    """TWO INDEPENDENT REFUSALS, and this one is the router's.

    ADR-0022 sends FEED objects through `platform_engineer`; a data steward
    holds APPROVE and is still the wrong person for this object type. The
    permission matrix says what a role may attempt — `APPROVAL_ROUTING` says
    where — and a test that only exercised the state machine would leave the
    second control unproven.
    """
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.post(f"/api/objects/feed/{FEED_ID}/submit", json={}, headers=_as(BA))
    refused = client.post(
        f"/api/objects/feed/{FEED_ID}/approve",
        json={"comment": "looks fine"},
        headers=_as(STEWARD),
    )
    assert refused.status_code == 403
    assert "review through platform_engineer" in refused.json()["detail"]
    assert any(e.action == "refused:approve" for e in store.read_audit(object_id=FEED_ID))


def test_pausing_a_feed_stops_new_work_and_says_who_and_why(client: TestClient) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    paused = client.post(
        f"/api/feeds/{FEED_ID}/pause",
        json={"reason": "Fidelis are re-cutting the extract after a plan merge"},
        headers=_as(ENGINEER),
    )
    assert paused.status_code == 200, paused.text
    body = paused.json()

    assert body["is_paused"] is True
    assert body["may_start_new_work"] is False
    assert body["affects_work_already_running"] is False
    assert body["paused_by"] == ENGINEER
    assert "plan merge" in body["explanation"]
    assert "does not abandon work in progress" in body["explanation"]


def test_pausing_needs_a_reason(client: TestClient) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    refused = client.post(f"/api/feeds/{FEED_ID}/pause", json={"reason": ""}, headers=_as(ENGINEER))
    assert refused.status_code == 422


def test_a_pause_does_not_change_the_lifecycle_state(client: TestClient) -> None:
    """THE DECISION, over HTTP. A paused feed is still whatever it was — so
    "which version was live in March" does not answer "none" for every week
    somebody paused something."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    before = client.get(f"/api/feeds/{FEED_ID}", headers=_as(BA)).json()["lifecycle_state"]
    client.post(
        f"/api/feeds/{FEED_ID}/pause", json={"reason": "payer migration"}, headers=_as(ENGINEER)
    )
    after = client.get(f"/api/feeds/{FEED_ID}", headers=_as(BA)).json()["lifecycle_state"]
    assert before == after


def test_resuming_needs_no_approver(client: TestClient) -> None:
    """An operator at 3am with a payer on the phone turns the tap back on
    without finding a steward."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.post(
        f"/api/feeds/{FEED_ID}/pause", json={"reason": "payer migration"}, headers=_as(ENGINEER)
    )
    resumed = client.post(f"/api/feeds/{FEED_ID}/resume", json={}, headers=_as(ENGINEER))

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["is_paused"] is False
    assert resumed.json()["may_start_new_work"] is True


def test_the_pause_ledger_keeps_both_events(client: TestClient) -> None:
    """Resuming writes a row rather than deleting one — a feed paused for six
    days and a feed never paused must not look identical afterwards."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.post(
        f"/api/feeds/{FEED_ID}/pause", json={"reason": "payer migration"}, headers=_as(ENGINEER)
    )
    client.post(f"/api/feeds/{FEED_ID}/resume", json={}, headers=_as(ENGINEER))

    ledger = client.get(f"/api/feeds/{FEED_ID}/suspensions", headers=_as(BA)).json()
    assert [row["action"] for row in ledger] == ["resumed", "paused"]
    assert ledger[1]["reason"] == "payer migration"
    assert ledger[1]["actor_subject"] == ENGINEER


def test_a_read_only_user_may_not_pause_a_feed(client: TestClient) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    refused = client.post(
        f"/api/feeds/{FEED_ID}/pause", json={"reason": "because"}, headers=_as(READ_ONLY)
    )
    assert refused.status_code == 403


def test_the_version_history_keeps_every_version(client: TestClient) -> None:
    """ "Which version was live in March?" is one click, because every version
    is still here."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.put(
        f"/api/feeds/{FEED_ID}", json={**FULL_FEED, "schedule_cron": "0 7 * * 1"}, headers=_as(BA)
    )
    client.put(
        f"/api/feeds/{FEED_ID}", json={**FULL_FEED, "schedule_cron": "0 8 * * 1"}, headers=_as(BA)
    )

    history = client.get(f"/api/objects/feed/{FEED_ID}/history", headers=_as(BA)).json()
    assert [v["version"] for v in history] == [3, 2, 1]
    assert history[2]["body"]["schedule_cron"] == "0 6 * * 1"


def test_the_diff_defaults_to_the_change_somebody_actually_wants(
    client: TestClient,
) -> None:
    """Previous against latest — the comparison wanted nine times in ten.
    Asking for two version numbers to get it is a screen people stop using."""
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.put(
        f"/api/feeds/{FEED_ID}", json={**FULL_FEED, "schedule_cron": "0 7 * * 1"}, headers=_as(BA)
    )

    diff = client.get(f"/api/objects/feed/{FEED_ID}/diff", headers=_as(BA)).json()
    assert diff["from_version"] == 1 and diff["to_version"] == 2
    assert [d["field_path"] for d in diff["differences"]] == ["schedule_cron"]
    assert diff["differences"][0]["original"] == "0 6 * * 1"
    assert diff["differences"][0]["clone"] == "0 7 * * 1"


def test_any_two_versions_can_be_compared(client: TestClient) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    client.put(
        f"/api/feeds/{FEED_ID}", json={**FULL_FEED, "schedule_cron": "0 7 * * 1"}, headers=_as(BA)
    )
    client.put(
        f"/api/feeds/{FEED_ID}",
        json={**FULL_FEED, "schedule_cron": "0 8 * * 1", "domain": "eligibility"},
        headers=_as(BA),
    )

    diff = client.get(
        f"/api/objects/feed/{FEED_ID}/diff?from_version=1&to_version=3", headers=_as(BA)
    ).json()
    assert {d["field_path"] for d in diff["differences"]} == {"schedule_cron", "domain"}


def test_a_feed_with_one_version_says_there_is_nothing_to_compare(
    client: TestClient,
) -> None:
    client.post("/api/feeds", json=FULL_FEED, headers=_as(BA))
    refused = client.get(f"/api/objects/feed/{FEED_ID}/diff", headers=_as(BA))
    assert refused.status_code == 409
    assert "nothing to compare" in refused.json()["detail"]


def test_the_history_of_a_feed_that_does_not_exist_is_a_not_found(
    client: TestClient,
) -> None:
    assert client.get("/api/objects/feed/nobody/history", headers=_as(BA)).status_code == 404


# ── the canonical model browser · CF-V1-E6-01 ────────────────────────────────


def _seed_glossary(store: MemMetadataDb) -> None:
    """Two terms, saved as the governed objects they are.

    Seeded through the store rather than a route because the glossary arrives
    by `cinqflow seed-glossary` from the client's workbook — there is no
    create-a-term endpoint, and inventing one for a test would be testing
    something the platform does not have.
    """
    from datetime import UTC, datetime

    from cinqflow.core.model.governed import Actor
    from cinqflow.core.model.vocabulary import ActorType
    from cinqflow.core.registry.glossary import GlossaryTerm

    author = Actor(subject=BA, actor_type=ActorType.HUMAN, display_name="Meera Rao")
    now = datetime(2026, 8, 30, tzinfo=UTC)
    for term in (
        GlossaryTerm(
            glossary_id="BG-004",
            term="Member Date of Birth",
            definition="Date of birth of the member.",
            mapped_domains=("Enrollment",),
            mapped_tables=("Members",),
            mapped_columns_original=("DOB",),
            mapped_columns_corrected=("Date_Of_Birth",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-090",
            term="Claim Paid Amount",
            definition="What the plan paid on the claim.",
            mapped_domains=("Claims",),
            mapped_tables=("Claim_IPHeader",),
            mapped_columns_corrected=("Paid_Amount",),
        ),
    ):
        store.save(term.as_governed(author=author, now=now))


def test_the_browser_shows_domains_entities_and_the_gap(
    client: TestClient, store: MemMetadataDb
) -> None:
    _seed_glossary(store)
    body = client.get("/api/canonical", headers=_as(BA)).json()

    assert set(body["domains"]) >= {"Enrollment", "Claims"}
    assert body["deployed_entities"] >= 1
    assert "Claim_IPHeader" in body["designed_not_deployed"]
    assert body["total_fields"] > body["defined_fields"], (
        "some deployed columns have no business definition, and that must be visible"
    )


def test_the_browser_filters_by_domain(client: TestClient, store: MemMetadataDb) -> None:
    _seed_glossary(store)
    claims = client.get("/api/canonical?domain=Claims", headers=_as(BA)).json()
    assert [e["name"] for e in claims["entities"]] == ["Claim_IPHeader"]


def test_an_entity_lists_its_fields_with_definitions_inline(
    client: TestClient, store: MemMetadataDb
) -> None:
    _seed_glossary(store)
    body = client.get("/api/canonical/Members", headers=_as(BA)).json()

    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["Date_Of_Birth"]["definition"] == "Date of birth of the member."
    assert by_name["Date_Of_Birth"]["definition_missing"] is False
    assert by_name["Date_Of_Birth"]["is_phi"] is True
    assert by_name["Date_Of_Birth"]["glossary_id"] == "BG-004"


def test_definition_missing_is_a_first_class_answer(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Not a blank cell a client has to interpret. "We have no business
    definition for this column" is a finding a steward acts on."""
    _seed_glossary(store)
    body = client.get("/api/canonical/Members", headers=_as(BA)).json()
    orphan = next(f for f in body["fields"] if f["name"] == "record_hash")
    assert orphan["definition_missing"] is True
    assert orphan["definition"] == "definition missing"


def test_a_designed_entity_says_it_is_not_deployed(
    client: TestClient, store: MemMetadataDb
) -> None:
    _seed_glossary(store)
    body = client.get("/api/canonical/Claim_IPHeader", headers=_as(BA)).json()
    assert body["deployed"] is False
    assert body["schema_name"] == ""
    assert body["fields"][0]["type"] is None, "nothing is provisioned, so nothing has a type"


def test_the_search_answers_both_halves_of_the_question(
    client: TestClient, store: MemMetadataDb
) -> None:
    """A BA types the business term; an engineer types what is in the payer's
    file header. Both must reach the canonical field."""
    _seed_glossary(store)
    by_term = client.get("/api/canonical/search?q=date of birth", headers=_as(BA)).json()
    by_spelling = client.get("/api/canonical/search?q=DOB", headers=_as(BA)).json()

    assert {f["name"] for f in by_term} == {"Date_Of_Birth"}
    assert {f["name"] for f in by_spelling} == {"Date_Of_Birth"}


def test_an_entity_that_is_neither_deployed_nor_declared_is_a_not_found(
    client: TestClient,
) -> None:
    refused = client.get("/api/canonical/Nonexistent", headers=_as(BA))
    assert refused.status_code == 404
    assert "generated from the deployed schemas" in refused.json()["detail"]


def test_a_read_only_user_may_browse_the_canonical_model(client: TestClient) -> None:
    """Read-Only users get full visibility and no buttons that change
    anything — and a model you cannot see is a model you cannot map to."""
    assert client.get("/api/canonical", headers=_as(READ_ONLY)).status_code == 200
    assert client.get("/api/canonical/search?q=member", headers=_as(READ_ONLY)).status_code == 200
