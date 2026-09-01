"""CF-V2-E12-01 — the clock, and the idempotency the worker depends on.

`core.sla` is the missing writer for `control.sla_instance`. Its public surface
is materialisation (`cycles_for`) and alerting (`alerts_for`, `grouped`), and
both are exercised here BEFORE `workers/sla.py` exists — because the worker's
whole claim is "run me on any cadence, restart me mid-run, replay me in a chaos
test", and that claim is a property of these functions rather than of the loop
that calls them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from cinqflow.core.model.vocabulary import StatusWord
from cinqflow.core.sla import (
    AlertSeverity,
    Cycle,
    Schedule,
    SlaError,
    SlaStatus,
    alerts_for,
    cycles_for,
    grouped,
)

pytestmark = pytest.mark.unit

DAY = date(2026, 8, 30)
SIX_AM = datetime(2026, 8, 30, 6, tzinfo=UTC)
NOW = datetime(2026, 8, 30, 9, tzinfo=UTC)


def schedule(**overrides: object) -> Schedule:
    base: dict[str, object] = {
        "feed_id": "uhc_md_daily",
        "feed_version": 1,
        "domain": "enrollment",
        "cron": "0 6 * * *",
    }
    base.update(overrides)
    return Schedule(**base)  # type: ignore[arg-type]


# ── materialising what a feed owes ───────────────────────────────────────────
def test_a_cron_occurrence_becomes_a_cycle_the_feed_owes() -> None:
    cycles = cycles_for(schedule(), [SIX_AM])
    assert len(cycles) == 1
    assert cycles[0].feed_id == "uhc_md_daily"
    assert cycles[0].cycle_date == DAY
    assert cycles[0].expected_ts == SIX_AM


def test_materialising_the_same_cycle_twice_is_a_no_op() -> None:
    """THE WORKER'S WHOLE IDEMPOTENCY CLAIM.

    `sla_instance` is UNIQUE on (feed_id, cycle_date). A second pass returns
    the cycle already known, unchanged, so the worker can run every minute,
    restart mid-run, or be replayed without producing a second expectation.
    """
    first = cycles_for(schedule(), [SIX_AM])
    again = cycles_for(schedule(), [SIX_AM], known=first)
    assert again == first


def test_the_clock_never_erases_an_arrival() -> None:
    """Arrival is recorded by the PIPELINE, not by the clock. A second
    materialisation that blanked `actual_ts` would lose the fact that a file
    turned up."""
    arrived = Cycle(
        feed_id="uhc_md_daily",
        cycle_date=DAY,
        expected_ts=SIX_AM,
        actual_ts=datetime(2026, 8, 30, 6, 12, tzinfo=UTC),
        files_received=1,
        batch_id="1244",
    )
    replayed = cycles_for(schedule(), [SIX_AM], known=[arrived])
    assert replayed[0].actual_ts == arrived.actual_ts
    assert replayed[0].batch_id == "1244"


def test_a_day_the_payer_does_not_deliver_owes_nothing() -> None:
    """A monthly roster due on the 1st does not become Breached because the
    1st was a Sunday — the payer never works Sundays."""
    sunday = datetime(2026, 8, 30, 6, tzinfo=UTC)
    assert sunday.weekday() == 6
    assert cycles_for(schedule(skip_weekends=True), [sunday]) == ()


def test_a_holiday_owes_nothing() -> None:
    assert cycles_for(schedule(holidays=frozenset({DAY})), [SIX_AM]) == ()


def test_a_naive_occurrence_is_refused() -> None:
    """Storage is UTC by explicit rule; a naive timestamp is an ambiguity
    somebody would resolve differently later."""
    with pytest.raises(SlaError, match="timezone-aware"):
        cycles_for(schedule(), [datetime(2026, 8, 30, 6)])  # noqa: DTZ001


def test_a_feed_expecting_no_files_is_not_a_feed() -> None:
    with pytest.raises(SlaError):
        schedule(expected_file_count=0)


def test_a_negative_grace_is_not_a_schedule() -> None:
    with pytest.raises(SlaError):
        schedule(grace=timedelta(minutes=-5))


# ── multi-file deliveries ────────────────────────────────────────────────────
def test_a_partial_delivery_is_not_an_arrival() -> None:
    """Two of three files landed. The board must not report this as Received —
    the batch cannot run on two thirds of a claims extract."""
    partial = Cycle(
        feed_id="fidelis_ip_claims",
        cycle_date=DAY,
        expected_ts=SIX_AM,
        actual_ts=datetime(2026, 8, 30, 6, 5, tzinfo=UTC),
        files_received=2,
        files_expected=3,
    )
    assert not partial.complete
    assert partial.user_status(NOW) is StatusWord.NEEDS_ATTENTION
    assert "1 of 3 files still missing" in partial.why(NOW)


# ── the alerts, and the grouping that ends alert fatigue ─────────────────────
def test_an_on_time_delivery_raises_no_alert() -> None:
    arrived = Cycle(
        feed_id="uhc_md_daily",
        cycle_date=DAY,
        expected_ts=SIX_AM,
        actual_ts=datetime(2026, 8, 30, 6, 12, tzinfo=UTC),
        files_received=1,
    )
    assert arrived.status(NOW) is SlaStatus.ON_TIME
    assert alerts_for([arrived], NOW) == ()


def test_a_missing_file_raises_a_critical_alert_that_says_why() -> None:
    alerts = alerts_for([Cycle("uhc_md_daily", DAY, SIX_AM)], NOW)
    assert len(alerts) == 1
    assert alerts[0].severity is AlertSeverity.CRITICAL
    assert "expected 6:00 AM — not received" in alerts[0].summary
    assert alerts[0].cited


def test_five_feeds_on_one_window_are_one_group_and_a_sixth_elsewhere_is_another() -> None:
    """The incident library's second structural lesson: five ADT feeds missing
    the SAME cycle boundary through Mirth is one upstream fault, not five feed
    incidents."""
    same_window = [Cycle(f"adt_source_{n}", DAY, SIX_AM) for n in range(5)]
    elsewhere = [Cycle("optum_ny", DAY, datetime(2026, 8, 30, 7, tzinfo=UTC))]

    groups = list(grouped(alerts_for([*same_window, *elsewhere], NOW)))

    assert len(groups) == 2
    # Largest group first, so the console leads with the shared fault.
    assert len(groups[0][1]) == 5
    assert len(groups[1][1]) == 1


def test_a_group_of_one_is_still_a_group() -> None:
    """Yielding singletons keeps the caller free of a special case, which is
    where "five feeds, one alert" usually breaks."""
    groups = list(grouped(alerts_for([Cycle("optum_ny", DAY, SIX_AM)], NOW)))
    assert len(groups) == 1
    assert len(groups[0][1]) == 1


def test_a_late_arrival_warns_rather_than_pages() -> None:
    late = Cycle(
        feed_id="uhc_md_daily",
        cycle_date=DAY,
        expected_ts=SIX_AM,
        actual_ts=datetime(2026, 8, 30, 8, 15, tzinfo=UTC),
        files_received=1,
    )
    alerts = alerts_for([late], NOW)
    assert alerts[0].severity is AlertSeverity.WARNING
    assert "late" in alerts[0].summary
