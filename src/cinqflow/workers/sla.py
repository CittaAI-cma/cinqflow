"""CF-V2-E12-01/05 — the clock, materialised, and the alerts it raises.

Wave 0 declared eleven control tables and wrote eight; `feed_sla_config`,
`sla_instance` and `sla_alerts` were provisioned and left empty. `core.sla`
is the pure arithmetic of what a feed owes and when it is late; this module
is where that arithmetic meets the control tables — the two verbs `workers`
has needed since the three tables were declared.

TWO PLAIN SYNCHRONOUS METHODS, exactly like `PipelineRunner.run`. Neither
touches a queue. `materialise` and `sweep` are callable directly — from a
test, from a CLI command — and separately reachable through
`workers.consumer.Consumer` once something enqueues a tick. Which route
reached them is not this worker's concern, and coupling the clock's logic to
a dispatch mechanism would make it untestable without one.

CRONITER LIVES HERE, NEVER IN `core.sla`. A core module that parses cron has
started interpreting the outside world's formats — Law 1's cousin. This file
is the adapter boundary `core.sla.Schedule`'s own docstring names.

A DOCUMENTED SCHEMA LIMIT, NOT A SILENT ONE. `control.sla_instance` is
UNIQUE on `(feed_id, cycle_date)` — a DAY, not a window — which is Wave 0's
DDL, unchanged here. A feed firing many times a day (the ADT cadence) can
only ever have ONE row per day under this constraint, so `_last_occurrence`
picks the LAST fire of the day: a board reading "expected 23:45" is closer to
true for a feed still arriving than "expected 00:00" would be. Modelling a
per-window cycle needs a schema change and an ADR, not a worker working
around the table it was given.

THE BUSINESS CALENDAR IS GLOBAL, NOT PER-FEED, for the same reason:
`feed_sla_config` carries no calendar columns. `materialise`'s
`skip_weekends`/`holidays` apply uniformly to every feed in the call. A payer
needing its own calendar is a schema question for a later wave.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from croniter import croniter

from cinqflow.core import sla as sla_core
from cinqflow.ports.control_tables import ControlTablesPort
from cinqflow.ports.control_tables import SlaAlert as SlaAlertRow
from cinqflow.ports.control_tables import SlaCycle as SlaCycleRow
from cinqflow.ports.notification import Alert, NotificationPort, Severity


class SlaWorker:
    def __init__(self, *, control: ControlTablesPort, notify: NotificationPort) -> None:
        self._control = control
        self._notify = notify

    # ── the clock ─────────────────────────────────────────────────────────────
    def materialise(
        self,
        *,
        on: date,
        now: datetime,
        skip_weekends: bool = False,
        holidays: frozenset[date] = frozenset(),
    ) -> int:
        """Write the cycles every active feed owes for `on`. Idempotent.

        Idempotent by construction, not by a check here: `cycles_for` returns
        a KNOWN cycle unchanged, and `upsert_sla_instance` never overwrites
        `actual_ts` on conflict. This method can run on any cadence, restart
        mid-run, or be replayed by a chaos test, and every pass after the
        first is a no-op.
        """
        written = 0
        for config in self._control.feed_sla_configs():
            schedule = sla_core.Schedule(
                feed_id=config.feed_id,
                feed_version=config.feed_version,
                domain=config.domain,
                cron=config.schedule_cron,
                grace=timedelta(minutes=config.grace_period_minutes or 30),
                expected_file_count=config.expected_file_count or 1,
                skip_weekends=skip_weekends,
                holidays=holidays,
            )
            occurrence = _last_occurrence(config.schedule_cron, on)
            if occurrence is None:
                continue
            known = tuple(
                _cycle_from_row(row) for row in self._control.sla_history(config.feed_id, days=2)
            )
            for cycle in sla_core.cycles_for(schedule, [occurrence], known=known):
                self._control.upsert_sla_instance(_cycle_to_row(cycle, now=now))
                written += 1
        return written

    # ── the alerts ────────────────────────────────────────────────────────────
    def sweep(self, *, on: date, now: datetime) -> tuple[sla_core.SlaAlert, ...]:
        """The full outstanding alert set for `on`, computed from the cycles —
        never stored and re-read, so it can never drift from them.

        A NEW alert is written and notified only for a cycle with no
        UNACKNOWLEDGED alert already on file. Re-deriving the same breach
        every tick and writing it every time would page an operator once per
        sweep interval for the same missing file forever — the deterministic
        set is recomputed; the WRITE is idempotent per cycle.
        """
        cycles = tuple(_cycle_from_row(row) for row in self._control.sla_instances(cycle_date=on))
        raised = sla_core.alerts_for(cycles, now)

        already_alerted = {
            row.feed_id
            for row in self._control.sla_alerts(cycle_date=on)
            if not row.acknowledged_by
        }
        fresh = tuple(alert for alert in raised if alert.feed_id not in already_alerted)

        for key, members in sla_core.grouped(fresh):
            self._notify.alert(_notification_alert(key, members))
            for member in members:
                self._control.record_sla_alert(_alert_to_row(member, raised_ts=now))

        return raised


# ── the adapter boundary: cron, and the two type pairs core/ports both name ──


def _last_occurrence(cron: str, on: date) -> datetime | None:
    """The last fire of `cron` that falls ON `on`, or None if there isn't one.

    Walked backward from the start of the NEXT day, so a schedule firing many
    times daily yields its final occurrence rather than its first — see the
    module docstring's note on the (feed_id, cycle_date) constraint.
    """
    day_after = datetime.combine(on + timedelta(days=1), time.min, tzinfo=UTC)
    occurrence: datetime = croniter(cron, day_after).get_prev(datetime)
    return occurrence if occurrence.date() == on else None


def _cycle_from_row(row: SlaCycleRow) -> sla_core.Cycle:
    return sla_core.Cycle(
        feed_id=row.feed_id,
        cycle_date=row.cycle_date,
        expected_ts=row.expected_ts,
        actual_ts=row.actual_ts,
        batch_id=row.batch_id,
        files_received=1 if row.actual_ts is not None else 0,
    )


def _cycle_to_row(cycle: sla_core.Cycle, *, now: datetime) -> SlaCycleRow:
    """`sla_status` is judged against the worker's OWN clock, not a value
    borrowed from the cycle. `Cycle.status(now)` ignores `now` entirely once
    an arrival is recorded, but for a cycle still waiting, `now` is the only
    honest answer to "is this late right now" — passing anything derived
    from the cycle itself (its own `expected_ts`, say) would report every
    still-pending cycle as On-Time regardless of how overdue it actually is.
    """
    return SlaCycleRow(
        feed_id=cycle.feed_id,
        cycle_date=cycle.cycle_date,
        expected_ts=cycle.expected_ts,
        sla_status=cycle.status(now).value,
        batch_id=cycle.batch_id,
        actual_ts=cycle.actual_ts,
    )


def _alert_to_row(alert: sla_core.SlaAlert, *, raised_ts: datetime) -> SlaAlertRow:
    return SlaAlertRow(
        alert_id=str(uuid.uuid4()),
        feed_id=alert.feed_id,
        cycle_date=alert.cycle_date,
        severity=alert.severity.value,
        summary=alert.summary,
        citations=tuple(str(c) for c in alert.citations),
        raised_ts=raised_ts,
    )


def _notification_alert(key: str, members: tuple[sla_core.SlaAlert, ...]) -> Alert:
    """One notification per GROUP, not one per feed — the whole point of
    `core.sla.grouped`: five feeds missing an identical window is one
    upstream fault, and paging on it five times teaches an operator to
    silence the channel."""
    severity = Severity(max(members, key=lambda a: _RANK[a.severity]).severity.value)
    citations = tuple(sorted({c for member in members for c in member.citations}, key=str))
    if len(members) == 1:
        summary = members[0].summary
    else:
        feeds = ", ".join(sorted(m.feed_id for m in members))
        summary = (
            f"{len(members)} feeds missing an identical window ({key}) — {feeds}. "
            "Likely a shared upstream fault; treat as one incident."
        )
    return Alert(
        severity=severity,
        summary=summary,
        citations=citations,
    )


_RANK: dict[sla_core.AlertSeverity, int] = {
    sla_core.AlertSeverity.INFO: 0,
    sla_core.AlertSeverity.WARNING: 1,
    sla_core.AlertSeverity.CRITICAL: 2,
}
