"""CF-V2-E12-01/05 — the clock materialised, and the alerts it raises.

`SlaWorker` is the one place `core.sla`'s pure functions meet the control
tables: `materialise` writes what a feed owes for a day, `sweep` reads what is
owed and raises the deterministic alert set. Both are plain synchronous
methods, exactly like `PipelineRunner.run` — callable directly in a test or a
CLI command, and ALSO reachable through `workers.consumer.Consumer` once
something enqueues a tick. Which route reached them is not this worker's
concern.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.notification import ConsoleNotification
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.sla import AlertSeverity
from cinqflow.ports.control_tables import FeedSlaConfig
from cinqflow.ports.notification import Severity
from cinqflow.workers.sla import SlaWorker

pytestmark = pytest.mark.unit

DAY = date(2026, 8, 30)
NOW = datetime(2026, 8, 30, 9, tzinfo=UTC)


def config(**overrides: object) -> FeedSlaConfig:
    base: dict[str, object] = {
        "feed_id": "uhc_md_daily",
        "feed_version": 1,
        "domain": "enrollment",
        "source_system": "uhc",
        "file_format": "xlsx",
        "landing_path": "enrollment/uhc_md/",
        "file_pattern": r"UHC_MD_Roster_.*\.xlsx",
        "schedule_cron": "0 6 * * *",
        "expected_file_count": 1,
        "grace_period_minutes": 30,
        "created_ts": NOW,
    }
    base.update(overrides)
    return FeedSlaConfig(**base)


def worker() -> tuple[SlaWorker, MemStoreControlTables, ConsoleNotification]:
    control = MemStoreControlTables()
    notify = ConsoleNotification()
    return SlaWorker(control=control, notify=notify), control, notify


# ── materialise ───────────────────────────────────────────────────────────────
def test_a_daily_feed_owes_exactly_one_cycle_for_the_day() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())

    written = sla.materialise(on=DAY, now=NOW)

    assert written == 1
    (cycle,) = control.sla_instances(cycle_date=DAY)
    assert cycle.feed_id == "uhc_md_daily"
    assert cycle.expected_ts == datetime(2026, 8, 30, 6, tzinfo=UTC)


def test_materialising_twice_writes_the_cycle_once() -> None:
    """The worker's whole idempotency claim: it can run on any cadence,
    restart mid-run, or be replayed, and the second pass is a no-op."""
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())

    sla.materialise(on=DAY, now=NOW)
    written_again = sla.materialise(on=DAY, now=NOW)

    assert written_again == 1
    assert len(control.sla_instances(cycle_date=DAY)) == 1


def test_materialising_never_erases_an_arrival_the_pipeline_already_recorded() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())
    sla.materialise(on=DAY, now=NOW)
    landed = datetime(2026, 8, 30, 6, 12, tzinfo=UTC)
    control.record_sla_arrival(
        feed_id="uhc_md_daily", cycle_date=DAY, actual_ts=landed, status="On-Time", batch_id="1244"
    )

    sla.materialise(on=DAY, now=NOW)

    (cycle,) = control.sla_instances(cycle_date=DAY)
    assert cycle.actual_ts == landed
    assert cycle.batch_id == "1244"


def test_a_monthly_feed_owes_nothing_on_a_day_that_is_not_its_day() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config(feed_id="fidelis_monthly", schedule_cron="0 6 1 * *"))

    written = sla.materialise(on=date(2026, 8, 15), now=NOW)

    assert written == 0
    assert control.sla_instances(cycle_date=date(2026, 8, 15)) == ()


def test_a_feed_that_skips_weekends_owes_nothing_on_a_sunday() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())
    sunday = date(2026, 8, 30)
    assert sunday.weekday() == 6

    written = sla.materialise(on=sunday, now=NOW, skip_weekends=True)

    assert written == 0


def test_a_holiday_owes_nothing() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())

    written = sla.materialise(on=DAY, now=NOW, holidays=frozenset({DAY}))

    assert written == 0


def test_a_sub_daily_cron_materialises_only_its_last_occurrence_of_the_day() -> None:
    """A DOCUMENTED LIMITATION, not a silent one. `sla_instance` is UNIQUE on
    (feed_id, cycle_date) — a DAY, not a window — which is Wave 0's own DDL.
    A 15-minute ADT feed genuinely owes many deliveries a day; this worker can
    only ever record the last one until that schema changes. Materialising the
    LATEST occurrence (rather than the first) is the least-wrong choice
    available today: a board reading "expected 23:45" is closer to true for a
    feed still arriving than "expected 00:00" would be.
    """
    sla, control, _ = worker()
    control.upsert_feed_sla_config(
        config(feed_id="adt_healthelink_v2", schedule_cron="*/15 * * * *", grace_period_minutes=5)
    )

    written = sla.materialise(on=DAY, now=NOW)

    assert written == 1
    (cycle,) = control.sla_instances(cycle_date=DAY)
    assert cycle.expected_ts == datetime(2026, 8, 30, 23, 45, tzinfo=UTC)


def test_a_feed_with_no_occurrence_on_the_day_at_all_writes_nothing() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config(schedule_cron="0 6 29 2 *"))  # Feb 29th only
    assert sla.materialise(on=DAY, now=NOW) == 0


def test_every_active_feed_is_materialised_in_one_call() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config(feed_id="uhc_md_daily"))
    control.upsert_feed_sla_config(config(feed_id="fidelis_downstate", schedule_cron="0 5 * * *"))

    assert sla.materialise(on=DAY, now=NOW) == 2
    assert len(control.sla_instances(cycle_date=DAY)) == 2


# ── sweep ─────────────────────────────────────────────────────────────────────
def test_a_missing_file_raises_a_critical_alert_and_notifies() -> None:
    sla, control, notify = worker()
    control.upsert_feed_sla_config(config())
    sla.materialise(on=DAY, now=NOW)

    raised = sla.sweep(on=DAY, now=NOW)

    assert len(raised) == 1
    assert raised[0].severity is AlertSeverity.CRITICAL
    assert "expected 6:00 AM — not received" in raised[0].summary
    assert len(notify.dispatched) == 1
    assert notify.dispatched[0].severity is Severity.CRITICAL


def test_an_alert_is_written_to_the_control_table_with_its_citations() -> None:
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())
    sla.materialise(on=DAY, now=NOW)

    sla.sweep(on=DAY, now=NOW)

    (row,) = control.sla_alerts(cycle_date=DAY)
    assert row.feed_id == "uhc_md_daily"
    assert row.citations == (str(CitationId(kind=CitationKind.FEED, subject="uhc_md_daily")),)


def test_an_on_time_arrival_raises_no_alert() -> None:
    sla, control, notify = worker()
    control.upsert_feed_sla_config(config())
    sla.materialise(on=DAY, now=NOW)
    control.record_sla_arrival(
        feed_id="uhc_md_daily",
        cycle_date=DAY,
        actual_ts=datetime(2026, 8, 30, 6, 5, tzinfo=UTC),
        status="On-Time",
        batch_id="1244",
    )

    raised = sla.sweep(on=DAY, now=NOW)

    assert raised == ()
    assert notify.dispatched == []


def test_five_feeds_on_one_window_notify_as_a_single_grouped_alert() -> None:
    """The incident library's second structural lesson, reaching the
    notification channel: five ADT feeds missing an identical cycle boundary
    is one upstream fault, not five pages."""
    sla, control, notify = worker()
    for n in range(5):
        control.upsert_feed_sla_config(
            config(feed_id=f"adt_source_{n}", schedule_cron="0 6 * * *", grace_period_minutes=5)
        )
    sla.materialise(on=DAY, now=NOW)

    raised = sla.sweep(on=DAY, now=NOW)

    assert len(raised) == 5
    assert len(notify.dispatched) == 1
    assert len(control.sla_alerts(cycle_date=DAY)) == 5


def test_sweeping_twice_does_not_double_the_alert_rows() -> None:
    """Alerts are re-derived from the cycles each sweep, not accumulated —
    otherwise a worker running on any cadence pages the same missed file
    once per tick forever."""
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())
    sla.materialise(on=DAY, now=NOW)

    sla.sweep(on=DAY, now=NOW)
    sla.sweep(on=DAY, now=NOW + timedelta(minutes=5))

    assert len(control.sla_alerts(cycle_date=DAY)) == 1


def test_the_written_sla_status_reflects_the_worker_s_clock_not_the_cycle_s_own_time() -> None:
    """THE REGRESSION FOR A REAL DEFECT.

    A cycle materialised well past its deadline, with nothing arrived, must be
    recorded as Breached — not On-Time, which is what deriving `now` from the
    cycle's own `expected_ts` silently produced. The board reads this column
    directly; a wrong status here is a wrong status everywhere it is shown.
    """
    sla, control, _ = worker()
    control.upsert_feed_sla_config(config())  # due 6:00 AM, 30 min grace

    sla.materialise(on=DAY, now=datetime(2026, 8, 30, 9, tzinfo=UTC))

    (cycle,) = control.sla_instances(cycle_date=DAY)
    assert cycle.sla_status == "Breached"
