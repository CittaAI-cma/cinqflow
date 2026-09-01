"""CF-V3-E13-02 — the seam between the pure recon packs and the fitted pins.

    "reconciliation extended beyond counts: claims financial totals by payer
     and month..., and member-universe comparisons across layers and against
     the legacy system during coexistence"
    — CF-V3-E13-02

`core.financial_reconciliation`, `core.member_universe` and `core.
recon_trend` compute; this module SEQUENCES — reads member-id universes
through the `ods_load` pin, writes any opened `Variance` through the SAME
`metadata.record_variance_event` the manual variance route
(`POST /operations/batches/{batch_id}/variances`) already writes to, and
reads a feed's history back through the SAME `metadata.list_variances` that
route's screen already reads. A pack-opened variance and a human-opened one
are the same row shape travelling the same ledger — `workers.ods_
certification.OdsCertificationGate` already folds whatever is in that
ledger into `certify()`, so nothing downstream needs to know which kind
opened any given variance.

FINANCIAL CONTROL TOTALS ARE NOT FETCHED HERE, AND NEITHER IS THE MEMBER
PACK'S "PREVIOUS" UNIVERSE. A payer's own control total for a month is not
yet modelled as anything this platform reads from a port — it is a number
operations currently gets from the payer's own transmittal, the same way
`core.recon.StageReconciliation`'s `records_in` comes from the file's own
header rather than from a query this module runs. `run_member_universe_
pack`'s `previous_ids` is the same kind of boundary, for a sharper reason:
`Members` is CURRENT-ONLY (SCD-1, CF-V3-E8-05), so it has no history of its
own for a batch id to filter back into — a member a later batch re-touches
simply loses its earlier `BatchId` value, which an earlier version of this
module got wrong by trying to filter the SAME table for "what a past batch
loaded." There is no query against a current-only table that answers "what
did this look like last month"; the caller supplies that side (a retained
roster, a legacy extract during coexistence, last month's own certification
evidence), and this module compares it against the CURRENT universe, read
live and unfiltered — the only version of "current" an SCD-1 table can
honestly answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from cinqflow.core.financial_reconciliation import (
    ClaimFinancials,
    financial_variance,
    net_financial_totals,
)
from cinqflow.core.member_universe import compare_member_universe, member_universe_variance
from cinqflow.core.recon_trend import Trend, TrendPoint, trend
from cinqflow.core.variance import Variance, VarianceKind
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.ods_load import OdsLoadPort


@dataclass(frozen=True)
class ReconciliationPacks:
    ods: OdsLoadPort
    metadata: MetadataDbPort

    def run_financial_pack(
        self,
        *,
        claims: Sequence[ClaimFinancials],
        control_totals: Mapping[tuple[str, str, str, str], Decimal],
        tolerance: Decimal,
        batch_id: str,
        feed_id: str,
        opened_by: str,
        now: datetime | None = None,
    ) -> tuple[Variance, ...]:
        """Every netted total with a known control total, beyond
        tolerance, opened as a `Variance` and appended to the ledger.

        A total with no entry in `control_totals` is skipped rather than
        compared to zero — "no control total supplied" and "the control
        total is zero" are different facts, and treating the first as the
        second would open a variance over a number nobody actually
        reported.
        """
        stamp = now or datetime.now(UTC)
        opened: list[Variance] = []
        for total in net_financial_totals(claims):
            control_total = control_totals.get(total.key)
            if control_total is None:
                continue
            variance = financial_variance(
                total,
                control_total=control_total,
                tolerance=tolerance,
                batch_id=batch_id,
                feed_id=feed_id,
                opened_by=opened_by,
                now=stamp,
                variance_id=str(uuid4()),
            )
            if not variance.beyond_tolerance:
                continue
            self.metadata.record_variance_event(
                variance, actor_subject=opened_by, occurred_ts=stamp
            )
            opened.append(variance)
        return tuple(opened)

    def run_member_universe_pack(
        self,
        *,
        entity: str,
        id_column: str,
        previous_ids: Sequence[object],
        terminated_ids: Sequence[object] = (),
        tolerance: Decimal,
        batch_id: str,
        feed_id: str,
        opened_by: str,
        now: datetime | None = None,
    ) -> Variance | None:
        """`previous_ids` — a retained prior roster, or a legacy system's
        own extract during coexistence — against the WHOLE current
        universe this entity carries right now, read live and unfiltered.

        See the module docstring for why `previous_ids` is supplied rather
        than fetched: `entity` is current-only, so there is no batch-scoped
        query against it that means "what this looked like before."
        """
        stamp = now or datetime.now(UTC)
        current_ids = self.ods.column_values(entity, id_column)
        delta = compare_member_universe(previous_ids, current_ids, terminated_ids)
        variance = member_universe_variance(
            delta,
            tolerance=tolerance,
            batch_id=batch_id,
            feed_id=feed_id,
            opened_by=opened_by,
            now=stamp,
            variance_id=str(uuid4()),
        )
        if variance is None:
            return None
        self.metadata.record_variance_event(variance, actor_subject=opened_by, occurred_ts=stamp)
        return variance

    def trend_for(self, *, feed_id: str, kind: VarianceKind, limit: int = 50) -> Trend:
        """'Trend both packs over time' — the same ledger `certify()`
        already reads, folded into a series by delta rather than a
        verdict. Two packs, one trend function (`core.recon_trend`);
        which `VarianceKind` selects the series is the only thing that
        differs between calling this for the financial pack and calling
        it for the member-universe pack.
        """
        variances = self.metadata.list_variances(feed_id=feed_id, limit=limit)
        points = tuple(
            TrendPoint(period=variance.batch_id, value=variance.delta)
            for variance in variances
            if variance.kind is kind
        )
        return trend(points)
