"""CF-V3-E13-02 — trending, shared by the financial and member-universe
packs.

    "Trend both packs over time so gradual drift is as visible as sudden
     breaks."
    — CF-V3-E13-02
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cinqflow.core.recon_trend import TrendPoint, trend

pytestmark = pytest.mark.unit


def test_a_flat_series_is_not_drifting() -> None:
    series = trend(
        [
            TrendPoint(period="2026-06", value=Decimal(0)),
            TrendPoint(period="2026-07", value=Decimal(0)),
            TrendPoint(period="2026-08", value=Decimal(0)),
        ]
    )
    assert not series.drifting


def test_a_series_moving_the_same_direction_every_step_is_drifting() -> None:
    """Small, individually-tolerable moves, every one the same direction —
    the exact 'gradual drift' the story names, and the one shape a single
    tolerance check on the LATEST point alone would never catch."""
    series = trend(
        [
            TrendPoint(period="2026-06", value=Decimal(10)),
            TrendPoint(period="2026-07", value=Decimal(25)),
            TrendPoint(period="2026-08", value=Decimal(40)),
        ]
    )
    assert series.drifting


def test_a_series_that_reverses_direction_is_not_drifting() -> None:
    series = trend(
        [
            TrendPoint(period="2026-06", value=Decimal(10)),
            TrendPoint(period="2026-07", value=Decimal(40)),
            TrendPoint(period="2026-08", value=Decimal(15)),
        ]
    )
    assert not series.drifting


def test_fewer_than_three_points_is_never_called_drifting() -> None:
    """Two points have exactly one direction by definition — that is a
    slope, not a trend, and flagging every two-month history would make
    'drifting' mean nothing."""
    series = trend(
        [
            TrendPoint(period="2026-07", value=Decimal(10)),
            TrendPoint(period="2026-08", value=Decimal(40)),
        ]
    )
    assert not series.drifting


def test_points_are_ordered_by_period_regardless_of_input_order() -> None:
    series = trend(
        [
            TrendPoint(period="2026-08", value=Decimal(40)),
            TrendPoint(period="2026-06", value=Decimal(10)),
            TrendPoint(period="2026-07", value=Decimal(25)),
        ]
    )
    assert [p.period for p in series.points] == ["2026-06", "2026-07", "2026-08"]
    assert series.drifting


def test_latest_is_the_last_point_by_period() -> None:
    series = trend(
        [
            TrendPoint(period="2026-06", value=Decimal(1)),
            TrendPoint(period="2026-08", value=Decimal(3)),
        ]
    )
    assert series.latest is not None
    assert series.latest.period == "2026-08"


def test_an_empty_series_has_no_latest_and_is_not_drifting() -> None:
    series = trend([])
    assert series.latest is None
    assert not series.drifting
