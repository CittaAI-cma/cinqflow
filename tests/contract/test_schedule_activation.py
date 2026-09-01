"""CF-V1-E4-03 / CF-V1-E8-03 — publication is what starts the clock.

    "Activate scheduling only at publication — nothing runs on a schedule
     before."
    — CF-V1-E4-03

    "Pause must stop new processing immediately while letting in-flight
     batches finish safely."
    — CF-V1-E3-04

NOTHING REGISTERED A SCHEDULE ON ANY DEPLOYMENT, EVER. `core.onboarding
.release.registrable_schedule` was written for "the orchestration wiring" and
had no caller; `OrchestrationPort.register` had none either; and `create_app`
did not take the pin at all. `queue.schedule` stayed empty, `due()` returned
nothing on a plane full of feeds, and `cinqflow tick` was right to report
"nothing due" forever. The clause was not unenforced — the mechanism it gates
was unreachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.ports.orchestration import Schedule, ScheduledRun

pytestmark = pytest.mark.contract

NOW = datetime(2026, 9, 2, tzinfo=UTC)
AUTHOR = Actor(subject="ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="BA")
APPROVER = Actor(
    subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steward"
)
#: Publishing is `platform_engineer` or `business_approver`; pausing is
#: `operations`. Two identities on purpose — the same split the permission
#: matrix draws, and a test that used one role for both would prove less than
#: it looks like it proves.
PUBLISHER = {"authorization": "Bearer dev-platform@cinqcare.test"}
OPERATOR = {"authorization": "Bearer dev-operations@cinqcare.test"}
FEED = "roster"


class RecordingOrchestration:
    """Records the three verbs the API now reaches for."""

    def __init__(self) -> None:
        self.registered: list[tuple[str, Schedule]] = []
        self.paused: list[tuple[str, str]] = []
        self.resumed: list[str] = []

    def register(self, feed_id: str, schedule: Schedule) -> None:
        self.registered.append((feed_id, schedule))

    def trigger(self, feed_id: str, *, business_date: str) -> ScheduledRun:
        return ScheduledRun(feed_id=feed_id, scheduled_for=NOW)

    def pause(self, feed_id: str, *, reason: str) -> None:
        self.paused.append((feed_id, reason))

    def resume(self, feed_id: str) -> None:
        self.resumed.append(feed_id)

    def due(self, as_of: datetime) -> tuple[ScheduledRun, ...]:
        return ()


class RefusingOrchestration(RecordingOrchestration):
    def register(self, feed_id: str, schedule: Schedule) -> None:
        raise RuntimeError("the scheduler is down")


def _approved_feed(*, cron: str = "0 3 1 * *") -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.APPROVED,
        created_by=AUTHOR,
        created_ts=NOW,
        approved_by=APPROVER,
        approved_ts=NOW,
        body={
            "feed_id": FEED,
            "domain": "enrollments",
            "source_system": "fidelis",
            "file_format": "csv",
            "file_pattern": ".*",
            "landing_path": "landing/x",
            "schedule_cron": cron,
            "operations": {"service_level": {"timezone": "America/New_York", "grace_minutes": 45}},
        },
    )


def _client(orchestration: object | None) -> Iterator[tuple[TestClient, MemMetadataDb]]:
    store = MemMetadataDb()
    store.save(_approved_feed())
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        orchestration=orchestration,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        yield client, store


@pytest.fixture
def wired() -> Iterator[tuple[TestClient, MemMetadataDb, RecordingOrchestration]]:
    scheduler = RecordingOrchestration()
    for client, store in _client(scheduler):
        yield client, store, scheduler


# ── publication starts the clock ─────────────────────────────────────────────


def test_publishing_a_feed_registers_its_cron(
    wired: tuple[TestClient, MemMetadataDb, RecordingOrchestration],
) -> None:
    client, _, scheduler = wired
    assert scheduler.registered == [], "nothing runs on a schedule before publication"

    response = client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER)
    assert response.status_code == 200, response.text

    assert len(scheduler.registered) == 1
    feed_id, schedule = scheduler.registered[0]
    assert feed_id == FEED
    assert schedule.cron == "0 3 1 * *"


def test_the_registered_schedule_carries_the_feeds_own_timezone_and_grace(
    wired: tuple[TestClient, MemMetadataDb, RecordingOrchestration],
) -> None:
    """A monthly file due at 03:00 New York is not due at 03:00 UTC, and a
    scheduler told UTC would start it five hours early for half the year."""
    client, _, scheduler = wired
    client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER)
    _, schedule = scheduler.registered[0]
    assert schedule.timezone == "America/New_York"
    assert schedule.grace_period_minutes == 45


def test_a_published_feed_with_no_cron_says_so_rather_than_silently_scheduling_nothing() -> None:
    """A manual-upload roster arrives when the payer sends it. That is a
    legitimate feed — and a warning is better than silence, because "nothing
    will start this automatically" is exactly what somebody assumes wrongly."""
    store = MemMetadataDb()
    store.save(_approved_feed(cron=""))
    scheduler = RecordingOrchestration()
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        orchestration=scheduler,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        result = client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER).json()
    assert scheduler.registered == []
    assert any("no schedule" in w for w in result.get("warnings", [])), result


def test_a_scheduler_that_refuses_leaves_the_feed_published_and_warns() -> None:
    """The feed IS published — the lifecycle already committed — so a 500
    here would be a lie about what the ledger records. What must not happen is
    silence: a published feed that will not run is worth a sentence."""
    store = MemMetadataDb()
    store.save(_approved_feed())
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        orchestration=RefusingOrchestration(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER)
    assert response.status_code == 200
    assert any("not activated" in w for w in response.json().get("warnings", []))
    assert store.get(ObjectType.FEED, FEED).lifecycle_state is LifecycleState.PUBLISHED


def test_a_deployment_with_no_scheduler_publishes_silently() -> None:
    """The mock socket fits none, and a warning on every publish there would
    be noise about a pin nobody asked for."""
    store = MemMetadataDb()
    store.save(_approved_feed())
    app = create_app(authn=StaticAuthn(), metadata_db=store)
    with TestClient(app) as client:
        response = client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER)
    assert response.status_code == 200
    assert response.json().get("warnings", []) == []


# ── pause reaches the thing that starts runs ─────────────────────────────────


def test_pausing_a_feed_tells_the_scheduler(
    wired: tuple[TestClient, MemMetadataDb, RecordingOrchestration],
) -> None:
    client, _, scheduler = wired
    client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER)
    response = client.post(
        f"/api/feeds/{FEED}/pause", json={"reason": "payer investigating"}, headers=OPERATOR
    )
    assert response.status_code in (200, 201), response.text
    assert scheduler.paused == [(FEED, "payer investigating")]


def test_resuming_a_feed_tells_the_scheduler(
    wired: tuple[TestClient, MemMetadataDb, RecordingOrchestration],
) -> None:
    client, _, scheduler = wired
    client.post(f"/api/objects/feed/{FEED}/publish", headers=PUBLISHER)
    client.post(f"/api/feeds/{FEED}/pause", json={"reason": "payer"}, headers=OPERATOR)
    response = client.post(f"/api/feeds/{FEED}/resume", json={}, headers=OPERATOR)
    assert response.status_code in (200, 201), response.text
    assert scheduler.resumed == [FEED]
