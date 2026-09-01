"""CF-V3-E13-02 — the reconciliation packs, against the mock plane.

"Given month-end for Fidelis claims, when the financial pack runs, then
 paid/allowed/billed by claim type match source control totals within
 tolerance, adjustment chains net correctly, and the pack attaches to
 the month's certification."
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

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.ods_load import MemOdsLoad
from cinqflow.core.claim_lineage import ClaimAdjustmentType
from cinqflow.core.financial_reconciliation import ClaimFinancials
from cinqflow.core.variance import VarianceKind, VarianceOutcome
from cinqflow.workers.reconciliation_packs import ReconciliationPacks

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, tzinfo=UTC)
BATCH_ID = "batch-8842"
FEED_ID = "fidelis-downstate-roster"
OPENED_BY = "cinqflow.reconciliation_packs"


@pytest.fixture
def packs() -> ReconciliationPacks:
    return ReconciliationPacks(ods=MemOdsLoad(), metadata=MemMetadataDb())


# ── run_financial_pack ────────────────────────────────────────────────────


def test_a_total_beyond_tolerance_opens_and_persists_a_variance(packs: ReconciliationPacks) -> None:
    claims = (
        ClaimFinancials(
            claim_id="A1",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(21952)},
        ),
    )
    opened = packs.run_financial_pack(
        claims=claims,
        control_totals={("fidelis", "professional", "2026-08", "paid"): Decimal(22000)},
        tolerance=Decimal(10),
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )

    assert len(opened) == 1
    assert opened[0].kind is VarianceKind.FINANCIAL
    persisted = packs.metadata.get_variance(opened[0].variance_id)
    assert persisted.variance_id == opened[0].variance_id


def test_a_total_within_tolerance_opens_nothing(packs: ReconciliationPacks) -> None:
    claims = (
        ClaimFinancials(
            claim_id="A1",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(22000)},
        ),
    )
    opened = packs.run_financial_pack(
        claims=claims,
        control_totals={("fidelis", "professional", "2026-08", "paid"): Decimal(22000)},
        tolerance=Decimal(10),
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )
    assert opened == ()


def test_a_metric_with_no_control_total_is_skipped_not_compared_to_zero(
    packs: ReconciliationPacks,
) -> None:
    claims = (
        ClaimFinancials(
            claim_id="A1",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(500)},
        ),
    )
    opened = packs.run_financial_pack(
        claims=claims,
        control_totals={},  # nothing supplied for this metric
        tolerance=Decimal(0),
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )
    assert opened == ()


# ── run_member_universe_pack ─────────────────────────────────────────────


def test_previous_ids_missing_from_the_live_universe_open_a_variance(
    packs: ReconciliationPacks,
) -> None:
    """`previous_ids` is caller-supplied — a retained July roster, say —
    and compared against whatever `Members` holds live right now."""
    previous_ids = list(range(1000))
    # August's live table holds only the survivors — the first 312 are gone.
    for our_id in range(312, 1000):
        packs.ods.upsert_current_row("Members", "OurId", {"OurId": our_id, "BatchId": BATCH_ID})

    variance = packs.run_member_universe_pack(
        entity="Members",
        id_column="OurId",
        previous_ids=previous_ids,
        tolerance=Decimal(0),
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )

    assert variance is not None
    assert variance.kind is VarianceKind.MEMBER
    assert variance.actual == Decimal(312)
    persisted = packs.metadata.get_variance(variance.variance_id)
    assert persisted.outcome is VarianceOutcome.OPEN


def test_terminated_members_do_not_open_a_variance(packs: ReconciliationPacks) -> None:
    packs.ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    # member 2 was in July's roster and is not in the live table — terminated on schedule.

    variance = packs.run_member_universe_pack(
        entity="Members",
        id_column="OurId",
        previous_ids=[1, 2],
        terminated_ids=[2],
        tolerance=Decimal(0),
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )
    assert variance is None


def test_a_member_absent_from_the_supplied_previous_roster_but_live_now_is_extra(
    packs: ReconciliationPacks,
) -> None:
    packs.ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    packs.ods.upsert_current_row("Members", "OurId", {"OurId": 2, "BatchId": BATCH_ID})

    variance = packs.run_member_universe_pack(
        entity="Members",
        id_column="OurId",
        previous_ids=[1],
        tolerance=Decimal(0),
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )
    assert variance is not None
    assert "2" in variance.explanation


# ── trend_for ─────────────────────────────────────────────────────────────


def test_trend_for_reads_only_the_requested_variance_kind(packs: ReconciliationPacks) -> None:
    claims = (
        ClaimFinancials(
            claim_id="A1",
            payer="fidelis",
            claim_type="professional",
            period="2026-06",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(100)},
        ),
    )
    packs.run_financial_pack(
        claims=claims,
        control_totals={("fidelis", "professional", "2026-06", "paid"): Decimal(150)},
        tolerance=Decimal(0),
        batch_id="batch-june",
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )
    packs.ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": "batch-july"})
    packs.run_member_universe_pack(
        entity="Members",
        id_column="OurId",
        previous_ids=[1],
        tolerance=Decimal(-1),  # force a variance even at zero delta, for this test only
        batch_id="batch-july-check",
        feed_id=FEED_ID,
        opened_by=OPENED_BY,
        now=NOW,
    )

    financial_series = packs.trend_for(feed_id=FEED_ID, kind=VarianceKind.FINANCIAL)
    assert len(financial_series.points) == 1
    assert financial_series.points[0].period == "batch-june"

    member_series = packs.trend_for(feed_id=FEED_ID, kind=VarianceKind.MEMBER)
    assert len(member_series.points) == 1
    assert member_series.points[0].period == "batch-july-check"
