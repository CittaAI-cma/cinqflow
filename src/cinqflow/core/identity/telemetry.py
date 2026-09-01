"""CF-V3-E9-04 — daily identity accounting and coverage telemetry.

    "daily identity accounting — submitted equals resolved plus unresolved
     plus failed, per feed — and coverage telemetry: what share of records
     carry LinkId and legacy OurID, per source, trending over time, so that
     the decision to cut over from the legacy SQL Server key becomes
     evidence-based: when parity holds daily and coverage is complete,
     cutover is a checklist, not a leap of faith."
    "Automate the parity check that was historically done by hand once
     ('validate LinkID matches between lake and legacy') into a standing
     daily scorecard."
    "Alert when coverage regresses for any source."
    "Keep the legacy comparison strictly read-only."
    "Report coverage without its denominator visible." (a documented don't)
    — CF-V3-E9-04

    "CINQFLOW de-risks this decision with daily parity evidence; it does not
     make it." — 08-open/00-open-questions.md, Q16, on OurID staying in SQL
     Server. This module IS that evidence, and nothing here writes a cutover
     decision anywhere — only ADR-0013's instrumentation.

WHY THIS IS ALL ARITHMETIC ON DATA THE CALLER ALREADY HAS. Every number here
derives from `CrosswalkEntry` rows the identity worker already persisted
(CF-V3-E9-01) or from the legacy pin's own read. Nothing is queried by this
module directly — `accounting_from_entries`/`coverage_from_entries` take the
entries as an argument, the same reason `core.recon.reconcile` takes counts
rather than a batch id: the arithmetic is provably correct with no database
in the room, and the worker that gathers the entries is a separate, boring
concern.

R0, ALWAYS. `06-product/02-agent-roster.md` names this "recon_intelligence"
and grades it "R0 · balances, always" — not because a model narrates it (none
does, anywhere in this module) but because the platform's own risk ladder
reserves R0 for exactly this: deterministic recomputation nobody approves,
the same class CF-V0-E16-10 occupies. A parity check is instrumentation, not
a decision — ADR-0013 is explicit that the key-generation question itself
stays a human engineering call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from cinqflow.core.identity import CrosswalkEntry

#: "Given a source's match rate drops 4 points overnight" is the story's own
#: example — set one point under it so that example itself would have
#: alerted, without making a single flaky point of drift page anyone.
DEFAULT_REGRESSION_THRESHOLD_POINTS = Decimal("3")


class IdentityTelemetryError(RuntimeError):
    """Identity telemetry refused to compute something."""


def _pct(numerator: int, denominator: int) -> Decimal:
    """One decimal place, HALF_UP — the same rounding a percentage a human
    reads on a dashboard expects, never banker's rounding nobody asked for.
    Zero denominator is 0.000%, not a division error: a source with no
    records today has not regressed, it has nothing to report yet."""
    if denominator <= 0:
        return Decimal("0.0")
    return (Decimal(numerator) / Decimal(denominator) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class FeedAccounting:
    """One feed's G4 equation, rolled up across every batch it ran on one
    business date — the SAME arithmetic `IdentityDisposition` proves per
    batch, summed. `explain()` mirrors `IdentityDisposition.explain()` and
    `core.recon.StageReconciliation.explain()` on purpose: one accounting
    sentence, everywhere the platform states one."""

    feed_id: str
    business_date: str
    submitted: int
    resolved: int
    unresolved: int
    failed: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("submitted", self.submitted),
            ("resolved", self.resolved),
            ("unresolved", self.unresolved),
            ("failed", self.failed),
        ):
            if value < 0:
                raise IdentityTelemetryError(f"{self.feed_id}: {field_name}={value} is not a count")

    @property
    def balances(self) -> bool:
        return self.submitted == self.resolved + self.unresolved + self.failed

    def explain(self) -> str:
        summary = (
            f"{self.feed_id} {self.business_date}: {self.submitted:,} submitted = "
            f"{self.resolved:,} resolved + {self.unresolved:,} unresolved + "
            f"{self.failed:,} failed"
        )
        return f"{summary}. Balanced." if self.balances else f"{summary}. UNBALANCED."


def accounting_from_entries(
    entries: Sequence[CrosswalkEntry], *, feed_id: str, business_date: str
) -> FeedAccounting:
    """Roll up a feed's whole day from the crosswalk entries its batches
    produced. `submitted` is `len(entries)` — every entry IS one submitted
    record, by `IdentityDisposition`'s own construction, so accounting can
    never disagree with the batches it was built from."""
    resolved = sum(1 for e in entries if e.outcome.value == "resolved")
    unresolved = sum(1 for e in entries if e.outcome.value == "unresolved")
    failed = sum(1 for e in entries if e.outcome.value == "failed")
    return FeedAccounting(
        feed_id=feed_id,
        business_date=business_date,
        submitted=len(entries),
        resolved=resolved,
        unresolved=unresolved,
        failed=failed,
    )


@dataclass(frozen=True)
class CoverageSnapshot:
    """One source, one day: how many records carry Verato's LinkId, the
    legacy OurId, or both. `total` is ALWAYS rendered beside a percentage —
    "report coverage without its denominator visible" is a documented don't,
    and a `CoverageSnapshot` cannot be asked for one without the other."""

    source_system: str
    business_date: str
    total: int
    with_link_id: int
    with_our_id: int
    with_both: int

    def __post_init__(self) -> None:
        if not (0 <= self.with_link_id <= self.total and 0 <= self.with_our_id <= self.total):
            raise IdentityTelemetryError(
                f"{self.source_system} {self.business_date}: a coverage count cannot exceed "
                "the total it is a share of"
            )
        if self.with_both > min(self.with_link_id, self.with_our_id):
            raise IdentityTelemetryError(
                f"{self.source_system} {self.business_date}: a record cannot carry both keys "
                "more often than it carries either one alone"
            )

    @property
    def link_id_coverage_pct(self) -> Decimal:
        return _pct(self.with_link_id, self.total)

    @property
    def our_id_coverage_pct(self) -> Decimal:
        return _pct(self.with_our_id, self.total)

    @property
    def both_coverage_pct(self) -> Decimal:
        """The cutover-readiness number: "Fidelis at 99.8% both-keys
        coverage" is this property, quoted."""
        return _pct(self.with_both, self.total)


def coverage_from_entries(
    entries: Sequence[CrosswalkEntry], *, source_system: str, business_date: str
) -> CoverageSnapshot:
    with_link = sum(1 for e in entries if e.verato_person_id)
    with_our = sum(1 for e in entries if e.internal_member_id)
    with_both = sum(1 for e in entries if e.verato_person_id and e.internal_member_id)
    return CoverageSnapshot(
        source_system=source_system,
        business_date=business_date,
        total=len(entries),
        with_link_id=with_link,
        with_our_id=with_our,
        with_both=with_both,
    )


def coverage_by_source(
    entries: Sequence[CrosswalkEntry], *, business_date: str
) -> tuple[CoverageSnapshot, ...]:
    """One snapshot per source touched today — never rolled into one
    platform-wide number, which is exactly how "a payer sending bad
    demographics becomes visible" stays true rather than averaged away."""
    by_source: dict[str, list[CrosswalkEntry]] = {}
    for entry in entries:
        by_source.setdefault(entry.source_system, []).append(entry)
    return tuple(
        coverage_from_entries(group, source_system=source, business_date=business_date)
        for source, group in sorted(by_source.items())
    )


@dataclass(frozen=True)
class CoverageRegression:
    """One source's coverage dropped from a baseline to today, past the
    threshold. `drop_points` is signed so a caller can log the exact size of
    the regression, not just that one happened."""

    source_system: str
    business_date: str
    today_pct: Decimal
    baseline_pct: Decimal

    @property
    def drop_points(self) -> Decimal:
        return self.baseline_pct - self.today_pct


def detect_regressions(
    today: Sequence[CoverageSnapshot],
    baseline: Sequence[CoverageSnapshot],
    *,
    threshold_points: Decimal = DEFAULT_REGRESSION_THRESHOLD_POINTS,
) -> tuple[CoverageRegression, ...]:
    """ "Alert when coverage regresses for any source." A source present
    today with no baseline (its first day) never regresses — there is
    nothing yet to have dropped from."""
    baseline_by_source = {b.source_system: b for b in baseline}
    regressions: list[CoverageRegression] = []
    for snapshot in today:
        base = baseline_by_source.get(snapshot.source_system)
        if base is None:
            continue
        drop = base.both_coverage_pct - snapshot.both_coverage_pct
        if drop >= threshold_points:
            regressions.append(
                CoverageRegression(
                    source_system=snapshot.source_system,
                    business_date=snapshot.business_date,
                    today_pct=snapshot.both_coverage_pct,
                    baseline_pct=base.both_coverage_pct,
                )
            )
    return tuple(regressions)


@dataclass(frozen=True)
class ParityCheckSummary:
    """The standing scorecard's own row: how many OurIds this source had on
    file with the legacy system today, and how many of Verato's answers
    agreed. The itemised differences (`ports.legacy_readonly.
    ParityDifference`) are the worker's live return value, not stored here —
    this dataclass is the TREND, the same split `IdentityDisposition` draws
    between its own counts and its `entries`."""

    source_system: str
    business_date: str
    checked: int
    matched: int
    mismatched: int

    def __post_init__(self) -> None:
        if self.checked != self.matched + self.mismatched:
            raise IdentityTelemetryError(
                f"{self.source_system} {self.business_date}: checked={self.checked} != "
                f"matched({self.matched}) + mismatched({self.mismatched}) — a parity summary "
                "that does not balance is a defect in the count, not a finding"
            )

    @property
    def balances(self) -> bool:
        return True  # __post_init__ already refused any summary that would not.

    @property
    def match_rate_pct(self) -> Decimal:
        return _pct(self.matched, self.checked)
