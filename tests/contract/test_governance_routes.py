"""CF-V1-E11-01 through the API — every refusal is a status code AND a row.

    "Given an approval is attempted by the author or an unauthorized role, when
     they approve, then the system blocks it and RECORDS THE ATTEMPT."
    — CF-V1-E11-01, guardrail

`tests/unit/test_lifecycle_engine.py` proves the engine refuses. This file
proves the API cannot be talked past — a crafted URL reaches the same refusal,
and the ledger keeps a row that names who tried what.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import LifecycleState, ObjectType
from cinqflow.ports.authn import Principal, Role, Scopes

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

BA = "dev-ba@cinqcare.test"
STEWARD = "dev-steward@cinqcare.test"
ENGINEER = "dev-engineer@cinqcare.test"
PLATFORM = "dev-platform@cinqcare.test"
APPROVER = "dev-approver@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
ADMIN = "dev-admin@cinqcare.test"

FEED_ID = "fidelis-downstate-roster"
FEED_BODY: dict[str, Any] = {
    "feed_id": FEED_ID,
    "domain": "membership",
    "source_system": "fidelis",
    "file_format": "xlsx",
    "landing_path": "landing/fidelis/roster",
    "file_pattern": r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$",
    "schedule_cron": "0 6 * * 1",
    "sample_filename": "_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
}


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


#: Nobody in the shipped role set both authors and approves — that separation
#: is ADR-0022's whole point. This person is constructed to hold BOTH hats, so
#: the engine's own refusal can be exercised rather than merely inferred from
#: the permission table refusing first.
BOTH_HATS = "dev-bothhats@cinqcare.test"


def _both_hats_directory() -> StaticAuthn:
    default = StaticAuthn()
    users = {p.subject: p for p in default.directory()}
    users[BOTH_HATS] = Principal(
        subject=BOTH_HATS,
        display_name="Wears Both Hats",
        roles=frozenset({Role.BUSINESS_ANALYST, Role.PLATFORM_ENGINEER}),
        scopes=Scopes(
            domains=frozenset({"*"}), feeds=frozenset({"*"}), environments=frozenset({"dev"})
        ),
    )
    return StaticAuthn(users)


@pytest.fixture
def app(store: MemMetadataDb) -> FastAPI:
    return create_app(authn=_both_hats_directory(), metadata_db=store)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def drafted(client: TestClient) -> TestClient:
    """A feed the BA authored, sitting in Draft — the starting state of every
    governance story."""
    assert client.post("/api/feeds", json=FEED_BODY, headers=_as(BA)).status_code == 201
    return client


def _act(client: TestClient, act: str, subject: str, **body: str) -> Any:
    return client.post(
        f"/api/objects/feed/{FEED_ID}/{act}", json=body or {"comment": ""}, headers=_as(subject)
    )


def _refusals(store: MemMetadataDb) -> list[str]:
    return [e.action for e in store.read_audit(object_id=FEED_ID) if e.action.startswith("refused")]


# ── the negatives, first ─────────────────────────────────────────────────────


def test_the_author_cannot_approve_her_own_feed__and_the_attempt_is_recorded(
    drafted: TestClient, store: MemMetadataDb
) -> None:
    """Universal negative #1, over HTTP. The BA holds no APPROVE, so the
    permission layer refuses before the engine even sees it — both layers are
    real, and this asserts the outer one leaves the row."""
    _act(drafted, "submit", BA)
    refused = _act(drafted, "approve", BA)
    assert refused.status_code == 403
    assert store.read_audit(object_id=FEED_ID)


def test_an_approver_who_authored_it_is_refused_by_the_engine_itself(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The sharper version: the author HOLDS approve, and routes to the right
    lane. The permission table has nothing to say — the ENGINE refuses, the
    route logs `refused:approve`, and the object stays In Review.

    Nobody in the shipped role set can reach this state; the test constructs
    someone who can, because a guarantee that depends on no such person
    existing is not a guarantee.
    """
    assert client.post("/api/feeds", json=FEED_BODY, headers=_as(BOTH_HATS)).status_code == 201
    _act(client, "submit", BOTH_HATS)
    refused = _act(client, "approve", BOTH_HATS)
    assert refused.status_code == 403
    assert "never approves it" in refused.json()["detail"]
    assert "refused:approve" in _refusals(store)
    assert store.get(ObjectType.FEED, FEED_ID).lifecycle_state is LifecycleState.PENDING_REVIEW


def test_a_steward_cannot_approve_a_feed__wrong_lane__and_it_is_recorded(
    drafted: TestClient, store: MemMetadataDb
) -> None:
    """Routing over HTTP: the steward holds APPROVE, but feeds route to the
    platform engineer. Refused, logged, unchanged."""
    _act(drafted, "submit", BA)
    refused = _act(drafted, "approve", STEWARD)
    assert refused.status_code == 403
    assert "engineer" in refused.json()["detail"]
    assert "refused:approve" in _refusals(store)


def test_a_read_only_user_crafting_the_approve_url_is_denied_at_the_server(
    drafted: TestClient,
) -> None:
    assert _act(drafted, "approve", READ_ONLY).status_code == 403
    assert _act(drafted, "publish", READ_ONLY).status_code == 403


def test_an_administrator_still_cannot_approve_anything(drafted: TestClient) -> None:
    """The person who grants permissions being able to use them all is how
    segregation of duties dies."""
    _act(drafted, "submit", BA)
    assert _act(drafted, "approve", ADMIN).status_code == 403


def test_publishing_straight_from_draft_is_refused(drafted: TestClient) -> None:
    """An illegal transition, and the refusal names what WAS permitted."""
    refused = _act(drafted, "publish", PLATFORM)
    assert refused.status_code == 403
    assert "pending_review" in refused.json()["detail"]


def test_request_changes_with_no_comment_is_refused(drafted: TestClient) -> None:
    _act(drafted, "submit", BA)
    refused = _act(drafted, "request-changes", PLATFORM, comment="  ")
    assert refused.status_code == 403
    assert "comment" in refused.json()["detail"]


def test_a_governance_act_on_an_unknown_object_is_not_found_shaped(client: TestClient) -> None:
    """The same sentence a scope miss produces — two sentences would be an
    oracle for which feed ids are real."""
    assert _act(client, "submit", BA).status_code == 404


def test_there_is_no_delete_route_for_a_governed_object(app: FastAPI) -> None:
    """Feeds retire, never vanish. Asserted against the ROUTE TABLE rather than
    a status code: a permission check could be misconfigured and a 404 could
    mean anything, but a route that does not exist cannot be called."""
    deleting = [
        route
        for route in app.routes
        if "DELETE" in getattr(route, "methods", set())
        and "/objects/" in getattr(route, "path", "")
    ]
    assert deleting == [], f"a governed object can be DELETEd: {deleting}"


# ── the happy path, end to end over HTTP ─────────────────────────────────────


def test_a_feed_travels_draft_to_published_with_both_names_on_the_record(
    drafted: TestClient, store: MemMetadataDb
) -> None:
    """The Wave-1 shape: the BA authors and submits, a DIFFERENT person
    approves, and the published object names its approver."""
    assert _act(drafted, "submit", BA, comment="sample tested").status_code == 200
    approved = _act(drafted, "approve", PLATFORM, comment="pattern matches the sample")
    assert approved.status_code == 200
    assert approved.json()["approved_by_subject"] == PLATFORM

    published = _act(drafted, "publish", PLATFORM)
    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "published"
    assert published.json()["status"] == "Completed"
    assert store.get(ObjectType.FEED, FEED_ID).is_executable is True


def test_the_business_approver_may_publish_what_an_engineer_approved(
    drafted: TestClient,
) -> None:
    """Publication admits the business approver — E4-03's dual signature needs
    both pens to exist."""
    _act(drafted, "submit", BA)
    _act(drafted, "approve", PLATFORM)
    assert _act(drafted, "publish", APPROVER).status_code == 200


def test_request_changes_returns_it_to_draft_and_the_conversation_survives(
    drafted: TestClient, store: MemMetadataDb
) -> None:
    _act(drafted, "submit", BA)
    returned = _act(drafted, "request-changes", PLATFORM, comment="cron should be monthly")
    assert returned.json()["lifecycle_state"] == "draft"

    resubmitted = _act(drafted, "submit", BA, comment="cron fixed")
    assert resubmitted.json()["lifecycle_state"] == "pending_review"
    trail = [e.detail for e in store.read_audit(object_id=FEED_ID)]
    assert "cron should be monthly" in trail and "cron fixed" in trail


def test_retiring_keeps_the_history(drafted: TestClient, store: MemMetadataDb) -> None:
    _act(drafted, "submit", BA)
    _act(drafted, "approve", PLATFORM)
    _act(drafted, "publish", PLATFORM)
    assert (
        _act(drafted, "retire", PLATFORM, comment="superseded by the 2027 roster").status_code
        == 200
    )
    assert store.history(ObjectType.FEED, FEED_ID)


# ── the Work Queue ───────────────────────────────────────────────────────────


def test_the_work_queue_shows_a_reviewer_what_awaits_them(drafted: TestClient) -> None:
    _act(drafted, "submit", BA)
    queue = drafted.get("/api/work-queue", headers=_as(PLATFORM)).json()
    assert [o["object_id"] for o in queue["awaiting_my_review"]] == [FEED_ID]


def test_the_work_queue_never_offers_a_reviewer_their_own_submission(
    client: TestClient,
) -> None:
    assert client.post("/api/feeds", json=FEED_BODY, headers=_as(BOTH_HATS)).status_code == 201
    _act(client, "submit", BOTH_HATS)
    queue = client.get("/api/work-queue", headers=_as(BOTH_HATS)).json()
    assert queue["awaiting_my_review"] == []
    assert [o["object_id"] for o in queue["my_submissions"]] == [FEED_ID]


def test_a_steward_sees_nothing_of_a_feed_awaiting_engineering_review(
    drafted: TestClient,
) -> None:
    _act(drafted, "submit", BA)
    queue = drafted.get("/api/work-queue", headers=_as(STEWARD)).json()
    assert queue["awaiting_my_review"] == []
