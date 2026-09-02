"""`run_once` — the tick-then-drain pairing `serve-worker` repeats forever.

Built entirely from mock ports, the same way `test_scheduler_worker.py` and
`test_worker_consumer.py` each certify their own half of this pairing — this
file only certifies that putting the two halves together in one call does
what `installer.cli.serve_worker`'s loop needs: one tick, then every named
topic drained once, reported back rather than only logged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.queue import MemQueue
from cinqflow.ports.orchestration import ScheduledRun
from cinqflow.workers.consumer import Consumer
from cinqflow.workers.loop import run_once
from cinqflow.workers.scheduler import RUN_FEED_TOPIC, SchedulerWorker

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)


class _StubOrchestration:
    """Only the two verbs `SchedulerWorker.tick` uses — same minimal shape
    `test_scheduler_worker.py`'s own stub is, kept local rather than shared
    since each test file's stub only needs to be as wide as that file's own
    scenarios."""

    def __init__(self, due: tuple[ScheduledRun, ...]) -> None:
        self._due = due
        self.triggered: list[tuple[str, str]] = []

    def due(self, as_of: datetime) -> tuple[ScheduledRun, ...]:
        return self._due

    def trigger(self, feed_id: str, *, business_date: str) -> ScheduledRun:
        self.triggered.append((feed_id, business_date))
        return ScheduledRun(feed_id=feed_id, scheduled_for=NOW, triggered_ts=NOW)


def test_a_tick_with_nothing_due_drains_nothing() -> None:
    scheduler = SchedulerWorker(
        orchestration=_StubOrchestration(()),
        metadata=MemMetadataDb(),
        control=MemStoreControlTables(),
        queue=MemQueue(),
    )
    consumer = Consumer(MemQueue())
    consumer.register(RUN_FEED_TOPIC, lambda payload: None)

    iteration = run_once(scheduler, consumer, topics=(RUN_FEED_TOPIC,))

    assert iteration.tick.due_count == 0
    assert iteration.processed == 0


def test_a_released_run_is_ticked_and_then_drained_in_one_call() -> None:
    """The point of `run_once`: a feed that becomes due is not just enqueued —
    it is handed to its handler in the SAME call, which is what makes a loop
    of nothing but `run_once` on an interval a working worker."""
    queue = MemQueue()
    scheduler = SchedulerWorker(
        orchestration=_StubOrchestration((ScheduledRun(feed_id="fidelis-roster", scheduled_for=NOW),)),
        metadata=MemMetadataDb(),
        control=MemStoreControlTables(),
        queue=queue,
    )
    seen: list[dict[str, object]] = []
    consumer = Consumer(queue)
    consumer.register(RUN_FEED_TOPIC, seen.append)

    iteration = run_once(scheduler, consumer, topics=(RUN_FEED_TOPIC,))

    assert len(iteration.tick.released) == 1
    assert iteration.processed == 1
    assert seen == [{"feed_id": "fidelis-roster", "business_date": NOW.date().isoformat()}]


def test_drains_every_named_topic_not_only_the_first() -> None:
    queue = MemQueue()
    queue.enqueue("agent.run", {"job_id": "job-1"})
    scheduler = SchedulerWorker(
        orchestration=_StubOrchestration(()),
        metadata=MemMetadataDb(),
        control=MemStoreControlTables(),
        queue=queue,
    )
    seen: list[dict[str, object]] = []
    consumer = Consumer(queue)
    consumer.register(RUN_FEED_TOPIC, lambda payload: None)
    consumer.register("agent.run", seen.append)

    iteration = run_once(scheduler, consumer, topics=(RUN_FEED_TOPIC, "agent.run"))

    assert iteration.processed == 1
    assert seen == [{"job_id": "job-1"}]
