"""CF-V1-E8-03 — the tick, and the four things it must not get wrong.

    "Given claims processing depends on the month's enrollment batch, when
     enrollment completes, then claims start automatically."
    "Given enrollment fails at Silver Raw, when the claims schedule fires,
     then claims hold in Waiting-on-Upstream with the blocking batch linked,
     Operations is notified once (not per held run), and everything resumes
     when enrollment recovers."
    — CF-V1-E8-03

`core.scheduling` has decided since Wave 1 and `OrchestrationPort.due()` has
listed what is owed since the pin was fitted. What did not exist was a caller:
`due()` had exactly two references in the whole repository, both of them
tests. These prove the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.notification import ConsoleNotification
from cinqflow.adapters.mock.queue import MemQueue
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, BatchState
from cinqflow.core.registry.suspension import SuspensionAction, SuspensionEvent
from cinqflow.ports.control_tables import BatchControl, StageStatus
from cinqflow.ports.notification import Alert
from cinqflow.ports.orchestration import ScheduledRun
from cinqflow.workers.scheduler import RUN_FEED_TOPIC, SchedulerWorker

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
PERIOD = "2026-09-01"
ACTOR = Actor(subject="platform", actor_type=ActorType.SYSTEM, display_name="platform")
#: Pausing is an operator's decision with a name on it: `core.registry
#: .suspension` refuses a SYSTEM actor outright, so the paused-feed test uses
#: a human rather than working around the refusal.
OPERATOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")


class StubOrchestration:
    """Only the two verbs the worker uses, and it records the second."""

    def __init__(self, due: tuple[ScheduledRun, ...]) -> None:
        self._due = due
        self.triggered: list[tuple[str, str]] = []

    def register(self, feed_id: str, schedule: object) -> None: ...
    def pause(self, feed_id: str, *, reason: str) -> None: ...
    def resume(self, feed_id: str) -> None: ...

    def due(self, as_of: datetime) -> tuple[ScheduledRun, ...]:
        return self._due

    def trigger(self, feed_id: str, *, business_date: str) -> ScheduledRun:
        self.triggered.append((feed_id, business_date))
        return ScheduledRun(feed_id=feed_id, scheduled_for=NOW, triggered_ts=NOW)


class RecordingNotify(ConsoleNotification):
    def __init__(self) -> None:
        super().__init__()
        self.alerts: list[Alert] = []

    def alert(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _feed(feed_id: str, *, depends_on: tuple[str, ...] = ()) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=feed_id,
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        body={"feed_id": feed_id, "depends_on": list(depends_on)},
        created_by=ACTOR,
        created_ts=NOW,
        approved_by=ACTOR,
        approved_ts=NOW,
    )


def _batch(feed_id: str, *, state: BatchState, batch_id: str) -> BatchControl:
    return BatchControl(
        batch_id=batch_id,
        feed_id=feed_id,
        feed_version=1,
        business_date=PERIOD,
        state=state,
        started_ts=NOW,
    )


def _worker(
    *,
    due: tuple[ScheduledRun, ...],
    feeds: tuple[GovernedObject, ...] = (),
    batches: tuple[BatchControl, ...] = (),
    stages: dict[str, tuple[StageStatus, ...]] | None = None,
) -> tuple[SchedulerWorker, StubOrchestration, MemQueue, RecordingNotify]:
    store = MemMetadataDb()
    for feed in feeds:
        store.save(feed)
    control = MemStoreControlTables()
    for batch in batches:
        control.open_batch(batch)
    for rows in (stages or {}).values():
        for stage in rows:
            control.record_stage(stage)
    orchestration = StubOrchestration(due)
    queue = MemQueue()
    notify = RecordingNotify()
    worker = SchedulerWorker(
        orchestration=orchestration,
        metadata=store,
        control=control,
        queue=queue,
        notify=notify,
    )
    return worker, orchestration, queue, notify


# ── nothing due ──────────────────────────────────────────────────────────────


def test_a_tick_with_nothing_due_does_nothing_and_says_so() -> None:
    worker, orchestration, queue, _ = _worker(due=())
    report = worker.tick(NOW)
    assert report.due_count == 0
    assert "nothing due" in report.explain()
    assert orchestration.triggered == []
    assert list(queue.drain(RUN_FEED_TOPIC)) == []


# ── the happy path: released, enqueued, occurrence taken ─────────────────────


def test_an_unblocked_feed_is_enqueued_and_its_occurrence_recorded() -> None:
    worker, orchestration, queue, _ = _worker(
        due=(ScheduledRun(feed_id="enrollment", scheduled_for=NOW),),
        feeds=(_feed("enrollment"),),
    )
    report = worker.tick(NOW)
    assert len(report.released) == 1
    assert report.held == ()
    messages = list(queue.drain(RUN_FEED_TOPIC))
    assert len(messages) == 1
    assert messages[0].payload == {"feed_id": "enrollment", "business_date": PERIOD}
    assert orchestration.triggered == [("enrollment", PERIOD)]


def test_a_tick_that_fires_twice_enqueues_once() -> None:
    """`pg_orchestration.due()`'s own docstring: "a tick that fires twice
    enqueues once". The dedupe key is the mechanism, not a check here."""
    worker, _, queue, _ = _worker(
        due=(ScheduledRun(feed_id="enrollment", scheduled_for=NOW),),
        feeds=(_feed("enrollment"),),
    )
    worker.tick(NOW)
    worker.tick(NOW)
    assert len(list(queue.drain(RUN_FEED_TOPIC))) == 1


def test_the_period_comes_from_the_occurrence_not_from_today() -> None:
    """A late tick must process the period it was OWED, not the one it woke
    up in — a monthly feed whose worker restarted a day late still runs the
    occurrence the cron named."""
    occurrence = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    worker, orchestration, _, _ = _worker(
        due=(ScheduledRun(feed_id="enrollment", scheduled_for=occurrence),),
        feeds=(_feed("enrollment"),),
    )
    worker.tick(NOW)
    assert orchestration.triggered == [("enrollment", "2026-08-01")]


# ── the exception path: held, linked, told once ──────────────────────────────


def test_claims_hold_when_enrollment_failed_and_the_blocking_batch_is_named() -> None:
    worker, orchestration, queue, notify = _worker(
        due=(ScheduledRun(feed_id="claims", scheduled_for=NOW),),
        feeds=(_feed("enrollment"), _feed("claims", depends_on=("enrollment",))),
        batches=(_batch("enrollment", state=BatchState.FAILED, batch_id="b-enr"),),
    )
    report = worker.tick(NOW)
    assert report.released == ()
    (held,) = report.held
    assert held.feed_id == "claims"
    assert "enrollment" in held.explanation
    assert list(queue.drain(RUN_FEED_TOPIC)) == [], "a held run is never enqueued"
    assert orchestration.triggered == [], "a held occurrence stays owed"
    assert held.notified is True
    assert len(notify.alerts) == 1


def test_operations_is_notified_once_not_per_held_run() -> None:
    """Twenty hourly evaluations of one broken upstream produce one message.
    The dedupe lives on the QUEUE, so this survives a worker restart."""
    worker, _, _, notify = _worker(
        due=(ScheduledRun(feed_id="claims", scheduled_for=NOW),),
        feeds=(_feed("enrollment"), _feed("claims", depends_on=("enrollment",))),
        batches=(_batch("enrollment", state=BatchState.FAILED, batch_id="b-enr"),),
    )
    for _ in range(5):
        worker.tick(NOW)
    assert len(notify.alerts) == 1


def test_a_hold_that_clears_itself_tells_nobody() -> None:
    """A team paged because last month's batch is still loading learns to
    ignore the channel that also carries the failures."""
    worker, _, _, notify = _worker(
        due=(ScheduledRun(feed_id="claims", scheduled_for=NOW),),
        feeds=(_feed("enrollment"), _feed("claims", depends_on=("enrollment",))),
        batches=(_batch("enrollment", state=BatchState.IN_PROGRESS, batch_id="b-enr"),),
    )
    report = worker.tick(NOW)
    assert report.held, "still held — the upstream has not finished"
    assert notify.alerts == [], "but nobody is paged for work that is simply in flight"


def test_a_paused_feed_holds_on_its_own_axis() -> None:
    """CF-V1-E3-04's second axis: a paused feed is still Published, and
    pausing stops new processing immediately."""
    store = MemMetadataDb()
    store.save(_feed("enrollment"))
    store.record_suspension(
        SuspensionEvent(
            feed_id="enrollment",
            action=SuspensionAction.PAUSED,
            actor=OPERATOR,
            occurred_ts=NOW,
            reason="payer investigating",
        )
    )
    worker = SchedulerWorker(
        orchestration=StubOrchestration((ScheduledRun(feed_id="enrollment", scheduled_for=NOW),)),
        metadata=store,
        control=MemStoreControlTables(),
        queue=MemQueue(),
        notify=None,
    )
    report = worker.tick(NOW)
    assert report.released == ()
    assert report.held


# ── recovery ─────────────────────────────────────────────────────────────────


def test_held_work_releases_itself_when_the_upstream_recovers() -> None:
    """ "Release held work automatically when the upstream issue clears" —
    and nothing here clears a stored hold, because no hold is stored."""
    store = MemMetadataDb()
    store.save(_feed("enrollment"))
    store.save(_feed("claims", depends_on=("enrollment",)))
    control = MemStoreControlTables()
    control.open_batch(_batch("enrollment", state=BatchState.FAILED, batch_id="b-enr"))
    queue = MemQueue()
    notify = RecordingNotify()
    orchestration = StubOrchestration((ScheduledRun(feed_id="claims", scheduled_for=NOW),))
    worker = SchedulerWorker(
        orchestration=orchestration,
        metadata=store,
        control=control,
        queue=queue,
        notify=notify,
    )
    assert worker.tick(NOW).held, "held while the upstream is broken"

    control.update_batch_state("b-enr", BatchState.COMPLETED)
    report = worker.tick(NOW)

    assert len(report.released) == 1, "released with no operator action at all"
    assert report.recovered == ("claims",)
    assert any("no longer held" in alert.summary for alert in notify.alerts)


def test_a_deployment_with_no_notification_pin_still_schedules() -> None:
    worker = SchedulerWorker(
        orchestration=StubOrchestration((ScheduledRun(feed_id="enrollment", scheduled_for=NOW),)),
        metadata=MemMetadataDb(),
        control=MemStoreControlTables(),
        queue=MemQueue(),
        notify=None,
    )
    report = worker.tick(NOW)
    assert len(report.released) == 1
