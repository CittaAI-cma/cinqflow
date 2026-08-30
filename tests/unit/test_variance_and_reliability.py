"""CF-V2-E13-03 / E12-05 — the monthly report's queries, and the trend's shape.

These cover the public functions the Phase-4 surfaces will call: the waiver
report ("keep waivers rare and LOUD"), the lapse re-alert, and the reliability
trend a sparkline renders. They are tested here rather than when those screens
arrive, because an untested function that a screen depends on is a screen that
discovers its own bugs in front of an operator.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from cinqflow.core.reliability import (
    Band,
    Bands,
    Signal,
    Weights,
    deteriorating,
    score_for,
    trend,
)
from cinqflow.core.variance import (
    MAX_WAIVER_DAYS,
    Variance,
    VarianceKind,
    VarianceOutcome,
    Waiver,
    default_expiry,
    lapsed_waivers,
    open_waivers,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, tzinfo=UTC)
GRANTED = date(2026, 8, 30)
EXPIRES = date(2026, 11, 28)


def count_variance(variance_id: str = "V1") -> Variance:
    return Variance(
        variance_id=variance_id,
        batch_id="1244",
        feed_id="claims",
        kind=VarianceKind.COUNT,
        expected=Decimal("1000"),
        actual=Decimal("998"),
        tolerance=Decimal("5"),
        opened_by="steward@cinqcare.test",
        opened_ts=NOW,
    )


def waiver() -> Waiver:
    return Waiver("other@cinqcare.test", "known payer cancellation lag", GRANTED, EXPIRES)


# ── the monthly recon report ─────────────────────────────────────────────────
def test_an_active_waiver_appears_in_the_monthly_report() -> None:
    """ "Keep waivers rare and loud: time-boxed, reasoned, and REPORTED"."""
    waived = count_variance().waive(waiver())
    assert open_waivers([waived], on=date(2026, 9, 1)) == (waived,)


def test_a_variance_nobody_waived_is_not_in_the_waiver_report() -> None:
    assert open_waivers([count_variance()], on=date(2026, 9, 1)) == ()


def test_a_lapsed_waiver_leaves_the_open_report_and_enters_the_lapsed_one() -> None:
    """ "re-alerts when it lapses" — the exception path, as two queries."""
    waived = count_variance().waive(waiver())
    after = date(2026, 12, 1)

    assert open_waivers([waived], on=after) == ()
    assert lapsed_waivers([waived], on=after) == (waived,)


def test_a_waiver_is_not_lapsed_on_the_day_it_was_granted() -> None:
    waived = count_variance().waive(waiver())
    assert lapsed_waivers([waived], on=GRANTED) == ()


def test_the_default_expiry_is_the_ceiling_not_a_shorter_habit() -> None:
    """A waiver that outlives a quarter is a decision, not a waiver — and the
    default should sit at the boundary the type enforces."""
    assert (default_expiry(GRANTED) - GRANTED).days == MAX_WAIVER_DAYS
    Waiver("other@cinqcare.test", "known quirk", GRANTED, default_expiry(GRANTED))


def test_a_corrected_variance_never_appears_in_the_waiver_report() -> None:
    corrected = count_variance().correct(by="other@cinqcare.test", note="duplicate file removed")
    assert corrected.outcome is VarianceOutcome.CORRECTED
    assert open_waivers([corrected], on=date(2026, 9, 1)) == ()
    assert lapsed_waivers([corrected], on=date(2026, 12, 1)) == ()


# ── the trend a sparkline renders ────────────────────────────────────────────
def score_on(day: date, dq: float) -> object:
    return score_for(
        feed_id="fidelis_roster",
        as_of=day,
        observations={
            Signal.DQ: (dq, f"{dq} on {day}", 6),
            Signal.SLA: (dq, "arrivals", 30),
            Signal.RECONCILIATION: (dq, "balances", 6),
            Signal.SCHEMA: (dq, "drift", 6),
            Signal.PIPELINE: (dq, "runs", 30),
        },
        weights=Weights(),
    )


def test_the_trend_reads_oldest_first_so_a_chart_reads_left_to_right() -> None:
    scores = [
        score_on(date(2026, 8, 30), 80.0),
        score_on(date(2026, 8, 28), 95.0),
        score_on(date(2026, 8, 29), 90.0),
    ]
    assert trend(scores) == (95.0, 90.0, 80.0)


def test_three_consecutive_falls_is_a_feed_getting_worse() -> None:
    """ "this feed has been getting worse for three cycles" is actionable in a
    way that a single low number is not."""
    falling = [
        score_on(date(2026, 8, 28), 95.0),
        score_on(date(2026, 8, 29), 90.0),
        score_on(date(2026, 8, 30), 80.0),
    ]
    assert deteriorating(falling)


def test_a_single_dip_is_not_a_deterioration() -> None:
    """A feed that dropped once and recovered is not trending down, and
    alerting on it is how the trend becomes noise."""
    dipped = [
        score_on(date(2026, 8, 28), 95.0),
        score_on(date(2026, 8, 29), 80.0),
        score_on(date(2026, 8, 30), 94.0),
    ]
    assert not deteriorating(dipped)


def test_too_few_points_is_not_a_deterioration() -> None:
    assert not deteriorating([score_on(date(2026, 8, 30), 80.0)])


# ── the bands, which are illustrative and movable ────────────────────────────
def test_the_bands_sort_a_score_into_healthy_at_risk_or_critical() -> None:
    bands = Bands()
    assert bands.band(95.0) is Band.HEALTHY
    assert bands.band(75.0) is Band.AT_RISK
    assert bands.band(40.0) is Band.CRITICAL


def test_bands_are_overridable_per_feed() -> None:
    """A daily ADT feed at 100% change rate and a monthly roster do not deserve
    the same threshold, and a band nobody can move is one people learn to
    ignore."""
    lenient = Bands(healthy_at=70.0, at_risk_at=50.0)
    assert lenient.band(75.0) is Band.HEALTHY


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match=r"1\.0"):
        Weights(dq=0.5, sla=0.5, reconciliation=0.5, schema=0.0, identity=0.0, pipeline=0.0)
