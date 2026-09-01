"""CF-V3-E13-02 — member-universe comparisons, set-wise.

"Given the member universe shows 312 members present last month and
 absent now, unexplained by terminations, when the pack runs, then a
 variance opens with the 312 identified set-wise and linked to the
 suspect batch — an investigation, not a mystery."
— CF-V3-E13-02
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.core.member_universe import compare_member_universe, member_universe_variance
from cinqflow.core.variance import VarianceKind

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def test_identical_universes_have_nothing_missing_or_extra() -> None:
    delta = compare_member_universe(["M1", "M2", "M3"], ["M1", "M2", "M3"])
    assert delta.missing_count == 0
    assert delta.extra_count == 0
    assert delta.unexplained == 0


def test_a_missing_member_is_reported_by_id_not_just_by_count() -> None:
    delta = compare_member_universe(["M1", "M2"], ["M1"])
    assert delta.missing_count == 1
    assert delta.missing == ("M2",)


def test_an_extra_member_is_reported_separately_from_a_missing_one() -> None:
    delta = compare_member_universe(["M1"], ["M1", "M2"])
    assert delta.missing_count == 0
    assert delta.extra_count == 1
    assert delta.extra == ("M2",)


def test_a_terminated_member_is_not_reported_as_missing() -> None:
    """'unexplained by terminations' — a scheduled departure is not a
    variance."""
    delta = compare_member_universe(["M1", "M2"], ["M1"], terminated_ids=["M2"])
    assert delta.missing_count == 0


def test_the_story_own_worked_example_312_missing_members() -> None:
    previous = [f"M{i}" for i in range(1000)]
    current = [f"M{i}" for i in range(1000) if i >= 312]  # the first 312 vanished

    delta = compare_member_universe(previous, current)

    assert delta.missing_count == 312
    assert delta.previous_total == 1000
    assert delta.current_total == 688
    assert "312" in delta.explain()


def test_the_sample_is_capped_but_the_true_count_is_not() -> None:
    previous = [f"M{i}" for i in range(100)]
    current: list[str] = []
    delta = compare_member_universe(previous, current, sample_size=10)
    assert delta.missing_count == 100
    assert len(delta.missing) == 10
    assert "100" in delta.explain()
    assert "90 more" in delta.explain()


def test_identifiers_of_different_types_still_compare_as_the_same_member() -> None:
    """A surrogate key read back as an int and one read from a legacy CSV
    as a string must not be reported as two different members."""
    delta = compare_member_universe([700001, 700002], ["700001", "700002"])
    assert delta.unexplained == 0


# ── member_universe_variance ─────────────────────────────────────────────────


def test_a_delta_within_tolerance_opens_no_variance() -> None:
    delta = compare_member_universe(["M1", "M2"], ["M1"])
    variance = member_universe_variance(
        delta,
        tolerance=Decimal(5),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )
    assert variance is None


def test_a_delta_beyond_tolerance_opens_a_critical_member_variance() -> None:
    previous = [f"M{i}" for i in range(1000)]
    current = [f"M{i}" for i in range(1000) if i >= 312]
    delta = compare_member_universe(previous, current)

    variance = member_universe_variance(
        delta,
        tolerance=Decimal(0),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )

    assert variance is not None
    assert variance.kind is VarianceKind.MEMBER
    assert variance.critical
    assert variance.actual == Decimal(312)
    assert "312" in variance.explanation
    assert "M0" in variance.explanation  # the first missing id is named, not just counted
