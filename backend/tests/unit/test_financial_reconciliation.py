"""CF-V3-E13-02 — claims financial totals, netted per the BCDA rules.

"Given month-end for Fidelis claims, when the financial pack runs, then
 paid/allowed/billed by claim type match source control totals within
 tolerance, adjustment chains net correctly, and the pack attaches to
 the month's certification."
— CF-V3-E13-02
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.core.certification import Certification, Check, CheckKind, Verdict, certify
from cinqflow.core.claim_lineage import ClaimAdjustmentType
from cinqflow.core.financial_reconciliation import (
    ClaimFinancials,
    financial_variance,
    net_financial_totals,
)
from cinqflow.core.variance import VarianceError, VarianceKind, VarianceOutcome, Waiver

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 1, tzinfo=UTC)
_PASSING_MANDATORY_CHECKS = tuple(
    Check(kind=kind, passed=True, evidence="ok")
    for kind in (CheckKind.BALANCE, CheckKind.DROP_LEDGER, CheckKind.RECONCILIATION)
)


def test_an_original_and_its_adjustment_net_to_their_sum() -> None:
    claims = (
        ClaimFinancials(
            claim_id="A100",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(500)},
        ),
        ClaimFinancials(
            claim_id="A100-ADJ",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ADJUSTMENT,
            amounts={"paid": Decimal(450)},
        ),
    )
    (total,) = net_financial_totals(claims)
    assert total.net_amount == Decimal(950)


def test_a_cancellation_negates_and_the_family_nets_to_the_survivor() -> None:
    """The exact worked example from CF-V3-E6-05's own BCDA source document,
    reused here at the pack level: original 500, cancellation -500,
    adjustment 450 -> net 450."""
    claims = (
        ClaimFinancials(
            claim_id="A100",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(500)},
        ),
        ClaimFinancials(
            claim_id="A100-CANCEL",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.CANCELLATION,
            amounts={"paid": Decimal(500)},
        ),
        ClaimFinancials(
            claim_id="A100-ADJ",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ADJUSTMENT,
            amounts={"paid": Decimal(450)},
        ),
    )
    (total,) = net_financial_totals(claims)
    assert total.net_amount == Decimal(450)


def test_multiple_metrics_on_one_claim_each_net_independently() -> None:
    """paid/allowed/billed — CF-V3-E13-02's own words — each get their own
    total, all netted by the SAME adjustment type."""
    claims = (
        ClaimFinancials(
            claim_id="A100",
            payer="fidelis",
            claim_type="institutional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(400), "allowed": Decimal(500), "billed": Decimal(800)},
        ),
        ClaimFinancials(
            claim_id="A100-CANCEL",
            payer="fidelis",
            claim_type="institutional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.CANCELLATION,
            amounts={"paid": Decimal(400), "allowed": Decimal(500), "billed": Decimal(800)},
        ),
    )
    totals = {t.metric: t.net_amount for t in net_financial_totals(claims)}
    assert totals == {"paid": Decimal(0), "allowed": Decimal(0), "billed": Decimal(0)}


def test_totals_group_separately_by_payer_claim_type_and_period() -> None:
    claims = (
        ClaimFinancials(
            claim_id="A1",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(100)},
        ),
        ClaimFinancials(
            claim_id="A2",
            payer="optum",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(200)},
        ),
        ClaimFinancials(
            claim_id="A3",
            payer="fidelis",
            claim_type="institutional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(300)},
        ),
        ClaimFinancials(
            claim_id="A4",
            payer="fidelis",
            claim_type="professional",
            period="2026-07",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(400)},
        ),
    )
    totals = {t.key: t.net_amount for t in net_financial_totals(claims)}
    assert totals[("fidelis", "professional", "2026-08", "paid")] == Decimal(100)
    assert totals[("optum", "professional", "2026-08", "paid")] == Decimal(200)
    assert totals[("fidelis", "institutional", "2026-08", "paid")] == Decimal(300)
    assert totals[("fidelis", "professional", "2026-07", "paid")] == Decimal(400)


def test_no_claims_produce_no_totals() -> None:
    assert net_financial_totals(()) == ()


# ── financial_variance ───────────────────────────────────────────────────────


def test_a_total_within_tolerance_of_control_is_still_a_variance_object() -> None:
    """`financial_variance` always builds one — the CALLER decides whether
    to open/persist it, by checking `beyond_tolerance`, exactly like the
    manual variance route already does."""
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
    (total,) = net_financial_totals(claims)
    variance = financial_variance(
        total,
        control_total=Decimal(22000),
        tolerance=Decimal(50),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )
    assert not variance.beyond_tolerance
    assert variance.kind is VarianceKind.FINANCIAL


def test_a_total_beyond_tolerance_is_a_critical_financial_variance() -> None:
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
    (total,) = net_financial_totals(claims)
    variance = financial_variance(
        total,
        control_total=Decimal(22000),
        tolerance=Decimal(10),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )
    assert variance.beyond_tolerance
    assert variance.critical  # FINANCIAL is always critical — core.variance's own rule
    assert "paid" in variance.explanation
    assert "22000" in variance.explanation or "22,000" in variance.explanation


def test_a_critical_financial_variance_cannot_be_waived() -> None:
    claims = (
        ClaimFinancials(
            claim_id="A1",
            payer="fidelis",
            claim_type="professional",
            period="2026-08",
            adjustment_type=ClaimAdjustmentType.ORIGINAL,
            amounts={"paid": Decimal(0)},
        ),
    )
    (total,) = net_financial_totals(claims)
    variance = financial_variance(
        total,
        control_total=Decimal(48000),
        tolerance=Decimal(10),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )
    with pytest.raises(VarianceError, match="cannot be waived"):
        variance.waive(
            Waiver(
                waived_by="dev-steward@cinqcare.test",
                reason="known payer quirk",
                granted_on=NOW.date(),
                expires_on=NOW.date().replace(day=28),
            )
        )


def test_an_open_financial_variance_blocks_certification_with_no_change_to_certify() -> None:
    """The exact mechanism named in the happy path: 'the pack attaches to
    the month's certification.' `certify()` needs no new argument, no new
    check kind — a blocking variance is already reason enough."""
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
    (total,) = net_financial_totals(claims)
    variance = financial_variance(
        total,
        control_total=Decimal(22000),
        tolerance=Decimal(10),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )

    verdict = certify(
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        checks=_PASSING_MANDATORY_CHECKS,
        variances=(variance,),
        now=NOW,
    )

    assert isinstance(verdict, Certification)
    assert verdict.verdict is Verdict.NOT_CERTIFIED
    assert variance in verdict.blocking


def test_a_corrected_financial_variance_no_longer_blocks() -> None:
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
    (total,) = net_financial_totals(claims)
    variance = financial_variance(
        total,
        control_total=Decimal(22000),
        tolerance=Decimal(10),
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        opened_by="cinqflow.reconciliation_packs",
        now=NOW,
        variance_id="var-1",
    )
    corrected = variance.correct(
        by="dev-steward@cinqcare.test", note="duplicate cancellation file identified and removed"
    )
    assert corrected.outcome is VarianceOutcome.CORRECTED

    verdict = certify(
        batch_id="batch-8842",
        feed_id="fidelis-downstate-roster",
        checks=_PASSING_MANDATORY_CHECKS,
        variances=(corrected,),
        now=NOW,
    )
    assert verdict.verdict is Verdict.CERTIFIED
