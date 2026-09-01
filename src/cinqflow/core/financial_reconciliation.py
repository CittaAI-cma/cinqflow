"""CF-V3-E13-02 — claims financial totals by payer and month, netted.

    "reconciliation extended beyond counts: claims financial totals by payer
     and month (with adjustment chains netted correctly)... so that the
     failure counts can't see — right row counts with wrong money — is
     caught by the platform, not by a downstream analyst months later."
    "Net adjustment chains per the documented rules so cancellations and
     corrections don't double-count."
    — CF-V3-E13-02

    "Given month-end for Fidelis claims, when the financial pack runs, then
     paid/allowed/billed by claim type match source control totals within
     tolerance, adjustment chains net correctly, and the pack attaches to
     the month's certification."
    — CF-V3-E13-02, happy path

REUSES `core.claim_lineage.normalize_payment` RATHER THAN RE-DERIVING IT. That
function's own signature — `(adjustment_type, raw_amount) -> Decimal` — is
already metric-agnostic despite its name: BCDA Handling's "Recommended
Financial Normalization Logic" (§8) says how to SIGN a claim's dollar amount
given its adjustment type, and nothing in that rule is specific to which
dollar amount it is. Calling it once per named metric (`paid`, `allowed`,
`billed`) on the SAME claim event is the whole of "adjustment chains netted
correctly" for this story — a second netting function would be a second,
driftable definition of the one rule CF-V3-E6-05 already grounded in the
harvested document.

THIS PRODUCES `core.variance.Variance` OBJECTS, NOT A NEW CHECK KIND. A
financial variance is not a yes/no gate — it is "a discrepancy with a name on
it" that gets investigated to one of three endings (corrected, approved-with-
explanation, waived), and `core.variance` already built that whole lifecycle
in CF-V2-E13-03, including the exact categorisation this story needs
(`VarianceKind.FINANCIAL`, already treated as critical — unwaivable — because
"money... [is] what a value-based-care contract is settled on"). `certify()`
already fails a batch on any BLOCKING variance regardless of check kind, so
"the pack attaches to the month's certification" costs this module nothing
more than handing its variances to the same `certify(variances=...)` call
every other batch already goes through.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from cinqflow.core.claim_lineage import ClaimAdjustmentType, normalize_payment
from cinqflow.core.variance import Variance, VarianceKind


@dataclass(frozen=True)
class ClaimFinancials:
    """One claim event's payer/type/period context, and its own named
    dollar amounts.

    `amounts` is a mapping rather than fixed `paid`/`allowed`/`billed`
    fields because which amounts a feed reports varies by source — CCLF1
    carries `CLM_PMT_AMT` and a total charge amount; another feed may carry
    others under other names. The netting rule below applies identically to
    whichever metrics a claim actually carries.
    """

    claim_id: str
    payer: str
    claim_type: str
    period: str
    adjustment_type: ClaimAdjustmentType
    amounts: Mapping[str, Decimal]


@dataclass(frozen=True)
class FinancialTotal:
    """One (payer, claim type, period, metric)'s netted total across every
    claim event that contributed to it."""

    payer: str
    claim_type: str
    period: str
    metric: str
    net_amount: Decimal

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.payer, self.claim_type, self.period, self.metric)


def net_financial_totals(claims: Sequence[ClaimFinancials]) -> tuple[FinancialTotal, ...]:
    """Group by (payer, claim type, period, metric) and sum each claim's
    NETTED amount for that metric — never the raw amount, which double-
    counts every adjustment chain by construction (the story's own don't).
    """
    totals: dict[tuple[str, str, str, str], Decimal] = {}
    for claim in claims:
        for metric, raw_amount in claim.amounts.items():
            key = (claim.payer, claim.claim_type, claim.period, metric)
            netted = normalize_payment(claim.adjustment_type, raw_amount)
            totals[key] = totals.get(key, Decimal(0)) + netted
    return tuple(
        FinancialTotal(
            payer=payer, claim_type=claim_type, period=period, metric=metric, net_amount=amount
        )
        for (payer, claim_type, period, metric), amount in sorted(totals.items())
    )


def financial_variance(
    total: FinancialTotal,
    *,
    control_total: Decimal,
    tolerance: Decimal,
    batch_id: str,
    feed_id: str,
    opened_by: str,
    now: datetime,
    variance_id: str,
) -> Variance:
    """One netted total against its source control total, as a `Variance`
    ready for `certify(variances=...)` — critical and unwaivable per
    `core.variance`'s own rule for `FINANCIAL`, exactly as this story's
    financial figures deserve.
    """
    return Variance(
        variance_id=variance_id,
        batch_id=batch_id,
        feed_id=feed_id,
        kind=VarianceKind.FINANCIAL,
        expected=control_total,
        actual=total.net_amount,
        tolerance=tolerance,
        opened_by=opened_by,
        opened_ts=now,
        explanation=(
            f"{total.metric} for {total.payer}, claim type {total.claim_type}, {total.period}: "
            f"netted to {total.net_amount} against a source control total of {control_total} "
            "(adjustment chains netted per the BCDA Handling document's rules)."
        ),
    )
