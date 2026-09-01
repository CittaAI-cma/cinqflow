"""CF-V3-E10-03 through the API — the ALREADY-GENERIC governance routes,
proven for `ObjectType.ODS_BATCH_CERTIFICATION`, plus the ONE genuinely new
thing publish does for this type: notifying registered consumers.

No new lifecycle routes exist for this story either, for the same reason
CF-V3-E10-01 needed none: `/api/objects/ods_batch_certification/{id}/
approve|publish` already work for any routed object type.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.certification import Certification, Check, CheckKind, Verdict
from cinqflow.core.lifecycle import submit
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.ods_batch_certification import as_governed, from_certification
from cinqflow.core.registry.ods_model_member_domain import (
    MEMBER_DOMAIN_DISCREPANCIES,
    MEMBER_DOMAIN_V1,
)
from cinqflow.ports.notification import Alert
from cinqflow.workers.ods_model import publish_ods_model

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

BATCH_ID = "batch-8842"
DATA_ENGINEER = "dev-engineer@cinqcare.test"
PLATFORM_ENGINEER = "dev-platform@cinqcare.test"
DATA_STEWARD = "dev-steward@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
GATE_AUTHOR = Actor(subject="cinqflow.ods_certification", actor_type=ActorType.SYSTEM)
MODEL_AUTHOR = Actor(subject=DATA_ENGINEER, actor_type=ActorType.HUMAN, display_name="Arun Menon")
PLATFORM = Actor(subject=PLATFORM_ENGINEER, actor_type=ActorType.HUMAN, display_name="Sam Patel")
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _passing_certification() -> Certification:
    return Certification(
        batch_id=BATCH_ID,
        feed_id="fidelis-downstate-roster",
        verdict=Verdict.CERTIFIED,
        checks=(
            Check(kind=CheckKind.BALANCE, passed=True, evidence="ok"),
            Check(kind=CheckKind.RELATIONSHIP_INTEGRITY, passed=True, evidence="0 orphans"),
        ),
        derived_ts=NOW,
        as_of=NOW.date(),
    )


def _seeded_certification(store: MemMetadataDb) -> GovernedObject:
    record = from_certification(_passing_certification(), model_version="1")
    draft = store.save(as_governed(record, author=GATE_AUTHOR, created_ts=NOW))
    submitted, entry = submit(draft, actor=GATE_AUTHOR, comment="Automated gate.", now=NOW)
    return store.record_transition(submitted, entry)


def _seeded_mapping(store: MemMetadataDb, *, business_consumers: tuple[str, ...]) -> None:
    store.save(
        GovernedObject(
            object_type=ObjectType.MAPPING,
            object_id="fidelis-ny",
            version=1,
            lifecycle_state=LifecycleState.PUBLISHED,
            created_by=MODEL_AUTHOR,
            created_ts=NOW,
            approved_by=PLATFORM,
            approved_ts=NOW,
            body={
                "lines": [{"target_entity": "Members", "target_field": "OurId"}],
                "business_consumers": list(business_consumers),
            },
        )
    )


@pytest.fixture
def metadata() -> MemMetadataDb:
    store = MemMetadataDb()
    publish_ods_model(
        store,
        MEMBER_DOMAIN_V1,
        MEMBER_DOMAIN_DISCREPANCIES,
        author=MODEL_AUTHOR,
        reviewer=PLATFORM,
        reviewer_roles=frozenset({Role.PLATFORM_ENGINEER}),
        publisher=PLATFORM,
        publisher_roles=frozenset({Role.PLATFORM_ENGINEER}),
        review_comment="Member domain v1.",
        approval_comment="Reviewed.",
        now=NOW,
    )
    _seeded_certification(store)
    return store


class _RecordingNotification:
    def __init__(self) -> None:
        self.dispatched: list[Alert] = []

    def alert(self, alert: Alert) -> None:
        self.dispatched.append(alert)


class _FailingNotification:
    def alert(self, alert: Alert) -> None:
        raise RuntimeError("the webhook is unreachable")


@pytest.fixture
def notify() -> _RecordingNotification:
    return _RecordingNotification()


@pytest.fixture
def client(metadata: MemMetadataDb, notify: _RecordingNotification) -> Iterator[TestClient]:
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=metadata,
        control_tables=MemStoreControlTables(),
        notify=notify,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_a_data_stewards_work_queue_shows_the_pending_certification_for_free(
    client: TestClient,
) -> None:
    """CF-V3-E10-03 needs no new screen: `/api/work-queue` already iterates
    every `ObjectType`, so the gate's own submitted draft appears the
    moment `ObjectType.ODS_BATCH_CERTIFICATION` is routed — the same
    "one lifecycle, learned once" proof E10-01 gave for the ODS model."""
    queue = client.get("/api/work-queue", headers=_as(DATA_STEWARD)).json()
    ids = {item["object_id"] for item in queue["awaiting_my_review"]}
    assert BATCH_ID in ids


def test_a_data_steward_approves_and_publishes_a_clean_certification(client: TestClient) -> None:
    approved = client.post(
        "/api/objects/ods_batch_certification/batch-8842/approve",
        json={"comment": "Evidence reviewed; relationships clean."},
        headers=_as(DATA_STEWARD),
    )
    assert approved.status_code == 200
    assert approved.json()["lifecycle_state"] == "approved"

    published = client.post(
        "/api/objects/ods_batch_certification/batch-8842/publish", headers=_as(DATA_STEWARD)
    )
    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "published"


def test_publishing_notifies_the_registered_consumers(
    client: TestClient, metadata: MemMetadataDb, notify: _RecordingNotification
) -> None:
    _seeded_mapping(metadata, business_consumers=("CMS Quality Report",))
    client.post(
        "/api/objects/ods_batch_certification/batch-8842/approve",
        json={"comment": "ok"},
        headers=_as(DATA_STEWARD),
    )
    published = client.post(
        "/api/objects/ods_batch_certification/batch-8842/publish", headers=_as(DATA_STEWARD)
    )
    assert published.status_code == 200
    assert published.json()["warnings"] == []
    (alert,) = notify.dispatched
    assert "CMS Quality Report" in alert.detail


def test_publishing_with_no_registered_consumers_notifies_nobody(
    client: TestClient, notify: _RecordingNotification
) -> None:
    client.post(
        "/api/objects/ods_batch_certification/batch-8842/approve",
        json={"comment": "ok"},
        headers=_as(DATA_STEWARD),
    )
    client.post(
        "/api/objects/ods_batch_certification/batch-8842/publish", headers=_as(DATA_STEWARD)
    )
    assert notify.dispatched == []


def test_a_notification_failure_is_a_warning_not_a_500(metadata: MemMetadataDb) -> None:
    _seeded_mapping(metadata, business_consumers=("CMS Quality Report",))
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=metadata,
        control_tables=MemStoreControlTables(),
        notify=_FailingNotification(),
    )
    with TestClient(app) as client:
        client.post(
            "/api/objects/ods_batch_certification/batch-8842/approve",
            json={"comment": "ok"},
            headers=_as(DATA_STEWARD),
        )
        published = client.post(
            "/api/objects/ods_batch_certification/batch-8842/publish", headers=_as(DATA_STEWARD)
        )
    assert published.status_code == 200
    assert published.json()["lifecycle_state"] == "published"
    assert "unreachable" in published.json()["warnings"][0]


def test_publishing_with_no_notifier_fitted_degrades_silently(metadata: MemMetadataDb) -> None:
    app = create_app(
        authn=StaticAuthn(), metadata_db=metadata, control_tables=MemStoreControlTables()
    )
    with TestClient(app) as client:
        client.post(
            "/api/objects/ods_batch_certification/batch-8842/approve",
            json={"comment": "ok"},
            headers=_as(DATA_STEWARD),
        )
        published = client.post(
            "/api/objects/ods_batch_certification/batch-8842/publish", headers=_as(DATA_STEWARD)
        )
    assert published.status_code == 200
    assert published.json()["warnings"] == []


def test_a_platform_engineer_holds_approve_but_not_this_objects_lane(client: TestClient) -> None:
    """The steward's own lane (plate 14) — an engineering role does not
    substitute for it, the same segregation E10-01 proved in reverse."""
    response = client.post(
        "/api/objects/ods_batch_certification/batch-8842/approve",
        json={"comment": "x"},
        headers=_as(PLATFORM_ENGINEER),
    )
    assert response.status_code == 403


def test_a_read_only_caller_may_not_approve(client: TestClient) -> None:
    response = client.post(
        "/api/objects/ods_batch_certification/batch-8842/approve",
        json={"comment": "x"},
        headers=_as(READ_ONLY),
    )
    assert response.status_code == 403
