"""CF-V3-E9-04 — daily identity accounting and coverage telemetry.

"leadership sees Fidelis at 99.8% both-keys coverage for 30 straight
 days, one source at 97.2% and flagged" — the happy path, proven here
 with the exact arithmetic behind those two numbers.
"Given a source's match rate drops 4 points overnight ... an alert
 names the source and the drop." — the exception path.
— CF-V3-E9-04
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.identity.telemetry import (
    CoverageSnapshot,
    FeedAccounting,
    IdentityTelemetryError,
    ParityCheckSummary,
    accounting_from_entries,
    coverage_by_source,
    coverage_from_entries,
    detect_regressions,
)

pytestmark = pytest.mark.unit

DATE = "2026-08-31"


def _entry(
    *,
    source_system: str = "fidelis",
    source_member_id: str = "M-1",
    outcome: MatchOutcome = MatchOutcome.RESOLVED,
    internal_member_id: str = "OUR-1",
    verato_person_id: str | None = "LINK-1",
) -> CrosswalkEntry:
    return CrosswalkEntry(
        source_system=source_system,
        source_member_id=source_member_id,
        internal_member_id=internal_member_id,
        verato_person_id=verato_person_id if outcome is MatchOutcome.RESOLVED else None,
        batch_id="B-1",
        outcome=outcome,
    )


# ── FeedAccounting / accounting_from_entries ─────────────────────────────────


def test_accounting_balances_by_construction_from_real_entries() -> None:
    entries = (
        *[_entry(source_member_id=f"M-{i}") for i in range(3)],
        _entry(source_member_id="M-unresolved", outcome=MatchOutcome.UNRESOLVED),
        _entry(source_member_id="M-failed", outcome=MatchOutcome.FAILED),
    )
    accounting = accounting_from_entries(entries, feed_id="fidelis-roster", business_date=DATE)
    assert accounting.submitted == 5
    assert accounting.resolved == 3
    assert accounting.unresolved == 1
    assert accounting.failed == 1
    assert accounting.balances
    assert "Balanced" in accounting.explain()


def test_accounting_of_no_entries_is_zero_not_an_error() -> None:
    accounting = accounting_from_entries((), feed_id="a-quiet-feed", business_date=DATE)
    assert accounting.submitted == 0
    assert accounting.balances


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(IdentityTelemetryError):
        FeedAccounting(
            feed_id="f", business_date=DATE, submitted=-1, resolved=0, unresolved=0, failed=0
        )


# ── CoverageSnapshot / coverage_from_entries / coverage_by_source ────────────


def test_coverage_counts_both_keys_and_the_overlap() -> None:
    entries = (
        _entry(source_member_id="M-both", internal_member_id="OUR-1", verato_person_id="LINK-1"),
        _entry(source_member_id="M-link-only", internal_member_id="", verato_person_id="LINK-2"),
        _entry(
            source_member_id="M-unresolved",
            outcome=MatchOutcome.UNRESOLVED,
            internal_member_id="OUR-3",
        ),
    )
    snapshot = coverage_from_entries(entries, source_system="fidelis", business_date=DATE)
    assert snapshot.total == 3
    assert snapshot.with_link_id == 2
    assert snapshot.with_our_id == 2
    assert snapshot.with_both == 1
    assert snapshot.both_coverage_pct == Decimal("33.3")


def test_the_happy_path_numbers_reproduce_exactly() -> None:
    """ "Fidelis at 99.8% both-keys coverage" — 998 of 1000."""
    entries = tuple(_entry(source_member_id=f"M-{i}") for i in range(998)) + tuple(
        _entry(source_member_id=f"M-gap-{i}", internal_member_id="") for i in range(2)
    )
    snapshot = coverage_from_entries(entries, source_system="fidelis", business_date=DATE)
    assert snapshot.total == 1000
    assert snapshot.both_coverage_pct == Decimal("99.8")


def test_coverage_of_zero_records_is_zero_percent_not_a_division_error() -> None:
    snapshot = coverage_from_entries((), source_system="fidelis", business_date=DATE)
    assert snapshot.total == 0
    assert snapshot.both_coverage_pct == Decimal("0.0")


def test_a_coverage_count_beyond_the_total_is_refused() -> None:
    with pytest.raises(IdentityTelemetryError):
        CoverageSnapshot(
            source_system="fidelis",
            business_date=DATE,
            total=10,
            with_link_id=11,
            with_our_id=0,
            with_both=0,
        )


def test_both_cannot_exceed_either_key_alone() -> None:
    with pytest.raises(IdentityTelemetryError):
        CoverageSnapshot(
            source_system="fidelis",
            business_date=DATE,
            total=10,
            with_link_id=5,
            with_our_id=5,
            with_both=6,
        )


def test_coverage_by_source_never_rolls_sources_up_together() -> None:
    entries = (
        _entry(source_system="fidelis", source_member_id="M-1"),
        _entry(source_system="fidelis", source_member_id="M-2"),
        _entry(source_system="optum", source_member_id="M-3"),
    )
    snapshots = coverage_by_source(entries, business_date=DATE)
    by_source = {s.source_system: s for s in snapshots}
    assert by_source["fidelis"].total == 2
    assert by_source["optum"].total == 1


# ── detect_regressions ────────────────────────────────────────────────────────


def test_a_four_point_overnight_drop_is_flagged() -> None:
    """ "Given a source's match rate drops 4 points overnight, when the
    scorecard computes, then an alert names the source and the drop."""
    baseline = (
        CoverageSnapshot(
            source_system="optum",
            business_date="2026-08-30",
            total=1000,
            with_link_id=950,
            with_our_id=950,
            with_both=950,
        ),
    )
    today = (
        CoverageSnapshot(
            source_system="optum",
            business_date=DATE,
            total=1000,
            with_link_id=910,
            with_our_id=910,
            with_both=910,
        ),
    )
    (regression,) = detect_regressions(today, baseline)
    assert regression.source_system == "optum"
    assert regression.drop_points == Decimal("4.0")


def test_a_drop_under_the_threshold_is_not_flagged() -> None:
    baseline = (
        CoverageSnapshot(
            source_system="fidelis",
            business_date="2026-08-30",
            total=1000,
            with_link_id=998,
            with_our_id=998,
            with_both=998,
        ),
    )
    today = (
        CoverageSnapshot(
            source_system="fidelis",
            business_date=DATE,
            total=1000,
            with_link_id=997,
            with_our_id=997,
            with_both=997,
        ),
    )
    assert detect_regressions(today, baseline) == ()


def test_a_source_with_no_baseline_never_regresses_on_its_first_day() -> None:
    today = (
        CoverageSnapshot(
            source_system="cms",
            business_date=DATE,
            total=100,
            with_link_id=10,
            with_our_id=10,
            with_both=10,
        ),
    )
    assert detect_regressions(today, ()) == ()


def test_an_improved_source_is_never_flagged() -> None:
    baseline = (
        CoverageSnapshot(
            source_system="fidelis",
            business_date="2026-08-30",
            total=1000,
            with_link_id=900,
            with_our_id=900,
            with_both=900,
        ),
    )
    today = (
        CoverageSnapshot(
            source_system="fidelis",
            business_date=DATE,
            total=1000,
            with_link_id=950,
            with_our_id=950,
            with_both=950,
        ),
    )
    assert detect_regressions(today, baseline) == ()


# ── ParityCheckSummary ────────────────────────────────────────────────────────


def test_a_parity_summary_that_balances_constructs_cleanly() -> None:
    summary = ParityCheckSummary(
        source_system="fidelis", business_date=DATE, checked=100, matched=98, mismatched=2
    )
    assert summary.balances
    assert summary.match_rate_pct == Decimal("98.0")


def test_a_parity_summary_that_does_not_balance_is_refused() -> None:
    with pytest.raises(IdentityTelemetryError):
        ParityCheckSummary(
            source_system="fidelis", business_date=DATE, checked=100, matched=90, mismatched=5
        )
