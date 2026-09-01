"""The `orchestration` pin — schedule, trigger and pause.

    verb: register/trigger/pause   mock: inproc   dev: airflow_oss
    target: airflow_rest_aks
    — docs/architecture/plates/04-pin-out-map.md

The Airflow adapter is THE SAME CODE at rung 1 and rung 4; only the base URL
and the auth move. That is why this pin costs PROFILE rather than ADAPTER.

The subtler trap is not the vendor, it is the shape: generating one DAG per
feed re-introduces per-feed code — it just hides it in Airflow instead of the
engine, somewhere nobody is looking, and CF-V0-E8-01's "contain any
feed-specific code" don't would be violated invisibly.

So: ONE generic DAG, parameterised by feed_id, reading the registry at runtime.
Adding a feed adds a registry row and nothing else, which is the platform's
entire thesis.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Schedule:
    """A feed's cadence, from its registry record."""

    cron: str
    timezone: str = "UTC"
    grace_period_minutes: int = 0


@dataclass(frozen=True)
class ScheduledRun:
    feed_id: str
    scheduled_for: datetime
    triggered_ts: datetime | None = None
    batch_id: str | None = None


@runtime_checkable
class OrchestrationPort(Protocol):
    def register(self, feed_id: str, schedule: Schedule) -> None:
        """Register a feed's schedule against the ONE generic DAG."""
        ...

    def trigger(self, feed_id: str, *, business_date: str) -> ScheduledRun: ...

    def pause(self, feed_id: str, *, reason: str) -> None:
        """Pause a feed. `reason` is required — a paused feed with no stated
        reason becomes a mystery nobody dares unpause."""
        ...

    def resume(self, feed_id: str) -> None: ...

    def due(self, as_of: datetime) -> Sequence[ScheduledRun]: ...
