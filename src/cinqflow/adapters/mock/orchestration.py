"""inproc — schedules held in memory, evaluated with croniter's semantics."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from cinqflow.ports import port
from cinqflow.ports.orchestration import Schedule, ScheduledRun


@port("orchestration", "mock")
class InProcOrchestration:
    """One generic registration per feed — never a DAG per feed.

    The mock enforces the same shape as the real adapter: `register` takes a
    feed_id and a schedule and nothing else. There is no hook for per-feed
    logic, which is what stops feed-specific code hiding in the orchestrator
    where CF-V0-E8-01's lint would never look.
    """

    def __init__(self) -> None:
        self._schedules: dict[str, Schedule] = {}
        self._paused: dict[str, str] = {}
        self.triggered: list[ScheduledRun] = []

    def register(self, feed_id: str, schedule: Schedule) -> None:
        self._schedules[feed_id] = schedule

    def trigger(self, feed_id: str, *, business_date: str) -> ScheduledRun:
        if feed_id in self._paused:
            raise RuntimeError(f"{feed_id} is paused: {self._paused[feed_id]}")
        run = ScheduledRun(
            feed_id=feed_id,
            scheduled_for=datetime.fromisoformat(business_date),
            triggered_ts=datetime.fromisoformat(business_date),
        )
        self.triggered.append(run)
        return run

    def pause(self, feed_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError(
                "a paused feed with no stated reason becomes a mystery nobody dares unpause"
            )
        self._paused[feed_id] = reason

    def resume(self, feed_id: str) -> None:
        self._paused.pop(feed_id, None)

    def due(self, as_of: datetime) -> Sequence[ScheduledRun]:
        return tuple(
            ScheduledRun(feed_id=feed_id, scheduled_for=as_of)
            for feed_id in sorted(self._schedules)
            if feed_id not in self._paused
        )
