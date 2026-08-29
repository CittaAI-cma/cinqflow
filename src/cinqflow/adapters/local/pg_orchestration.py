"""queue.schedule / queue.scheduled_run on Postgres — the rung-0.5 scheduler.

    "orchestration: schedule/trigger/pause   mock: inproc   dev: pg_scheduler
     target: airflow_rest"
    — docs/architecture/plates/04-pin-out-map.md

ONE generic registration per feed — never a DAG per feed. `register` takes a
feed_id and a schedule and nothing else; there is no hook for per-feed logic,
which is what stops feed-specific code hiding in the orchestrator.

Cron evaluation is a five-field matcher implemented here rather than a
dependency: the platform needs "when did this schedule last come due", not a
scheduler framework — and CF-V1-E8-03's worker composes THIS pin with the
queue pin (`due` -> `enqueue(dedupe_key=feed/business_date)`), so idempotency
comes from the queue's UNIQUE constraint, not from cron arithmetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.ports import port
from cinqflow.ports.orchestration import Schedule, ScheduledRun

#: How far back `due` searches for a schedule's most recent occurrence. Two
#: months covers every cadence in the estate (the rarest is monthly).
_LOOKBACK = timedelta(days=62)


class CronError(ValueError):
    """A cron expression the matcher cannot honour. Refused at registration,
    never discovered at fire time."""


def _parse_field(field: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            step = int(raw_step)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            raw_start, raw_end = part.split("-", 1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(part)
        if not (low <= start <= end <= high):
            raise CronError(f"{field!r}: {part} is outside {low}..{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


class Cron:
    """A five-field cron expression: minute hour day-of-month month day-of-week.

    Vixie semantics for the one subtle rule: when BOTH day-of-month and
    day-of-week are restricted, a time matches if EITHER matches.
    """

    def __init__(self, expression: str) -> None:
        fields = expression.split()
        if len(fields) != 5:
            raise CronError(f"{expression!r}: a cron expression has five fields")
        self.expression = expression
        self.minutes = _parse_field(fields[0], 0, 59)
        self.hours = _parse_field(fields[1], 0, 23)
        self.days = _parse_field(fields[2], 1, 31)
        self.months = _parse_field(fields[3], 1, 12)
        self.weekdays = _parse_field(fields[4], 0, 6)  # 0 = Sunday
        self._day_restricted = fields[2] != "*"
        self._weekday_restricted = fields[4] != "*"

    def matches(self, at: datetime) -> bool:
        if at.minute not in self.minutes or at.hour not in self.hours:
            return False
        if at.month not in self.months:
            return False
        day_ok = at.day in self.days
        weekday_ok = (at.weekday() + 1) % 7 in self.weekdays
        if self._day_restricted and self._weekday_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok

    def previous_fire(self, as_of: datetime) -> datetime | None:
        """The most recent occurrence at or before `as_of`, or None within the
        lookback window. A minute-resolution scan: correct and obviously so,
        and called per registered feed per tick — dozens of feeds, not
        millions."""
        candidate = as_of.replace(second=0, microsecond=0)
        floor = as_of - _LOOKBACK
        while candidate >= floor:
            if self.matches(candidate):
                return candidate
            candidate -= timedelta(minutes=1)
        return None


@port("orchestration", "pg-scheduler")
class PostgresOrchestration:
    """Requires a connection, which is why the contract suite constructs it
    with one rather than with defaults."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def register(self, feed_id: str, schedule: Schedule) -> None:
        Cron(schedule.cron)  # refused at registration, never at fire time
        self._db.execute(
            "INSERT INTO queue.schedule (feed_id, cron, timezone, grace_period_minutes, "
            "registered_ts) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (feed_id) DO UPDATE SET cron = EXCLUDED.cron, "
            "timezone = EXCLUDED.timezone, "
            "grace_period_minutes = EXCLUDED.grace_period_minutes",
            (
                feed_id,
                schedule.cron,
                schedule.timezone,
                schedule.grace_period_minutes,
                datetime.now(UTC),
            ),
        )

    def trigger(self, feed_id: str, *, business_date: str) -> ScheduledRun:
        paused = self._db.fetch_one(
            "SELECT paused_reason FROM queue.schedule WHERE feed_id = %s "
            "AND paused_reason IS NOT NULL",
            (feed_id,),
        )
        if paused is not None:
            raise RuntimeError(f"{feed_id} is paused: {paused[0]}")
        scheduled_for = datetime.fromisoformat(business_date)
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=UTC)
        run = ScheduledRun(feed_id=feed_id, scheduled_for=scheduled_for, triggered_ts=scheduled_for)
        self._db.execute(
            "INSERT INTO queue.scheduled_run (run_id, feed_id, scheduled_for, triggered_ts) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (feed_id, scheduled_for) DO NOTHING",
            (str(uuid.uuid4()), feed_id, scheduled_for, scheduled_for),
        )
        return run

    def pause(self, feed_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError(
                "a paused feed with no stated reason becomes a mystery nobody dares unpause"
            )
        self._db.execute(
            "UPDATE queue.schedule SET paused_reason = %s WHERE feed_id = %s",
            (reason, feed_id),
        )

    def resume(self, feed_id: str) -> None:
        self._db.execute(
            "UPDATE queue.schedule SET paused_reason = NULL WHERE feed_id = %s",
            (feed_id,),
        )

    def due(self, as_of: datetime) -> Sequence[ScheduledRun]:
        """Every non-paused feed whose most recent cron occurrence has not been
        triggered yet. The worker turns each into `queue.enqueue(dedupe_key=
        feed/business_date)`, so a tick that fires twice enqueues once."""
        rows: list[tuple[Any, ...]] = self._db.fetch_all(
            "SELECT feed_id, cron FROM queue.schedule WHERE paused_reason IS NULL ORDER BY feed_id"
        )
        runs: list[ScheduledRun] = []
        for feed_id, cron in rows:
            occurrence = Cron(cron).previous_fire(as_of)
            if occurrence is None:
                continue
            triggered = self._db.fetch_one(
                "SELECT 1 FROM queue.scheduled_run WHERE feed_id = %s AND scheduled_for = %s",
                (feed_id, occurrence),
            )
            if triggered is None:
                runs.append(ScheduledRun(feed_id=feed_id, scheduled_for=occurrence))
        return tuple(runs)
