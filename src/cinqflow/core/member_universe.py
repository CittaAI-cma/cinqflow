"""CF-V3-E13-02 — member-universe comparisons, set-wise.

    "member-universe comparisons across layers and against the legacy
     system during coexistence, so that the failure counts can't see — a
     shrunken member universe — is caught by the platform, not by a
     downstream analyst months later."
    "Compare member universes set-wise (who is missing, who is extra), not
     just count-wise."
    — CF-V3-E13-02

    "Given the member universe shows 312 members present last month and
     absent now, unexplained by terminations, when the pack runs, then a
     variance opens with the 312 identified set-wise and linked to the
     suspect batch — an investigation, not a mystery."
    — CF-V3-E13-02, exception

COUNT-WISE IS NOT ENOUGH, ON PURPOSE. `core.recon.StageReconciliation`
already proves `records_in == records_out + quarantined + attributed_drops`
for a stage — but 1,000 members in and 1,000 members out balances perfectly
even when 312 of them are the WRONG 1,000. Set difference is the only
computation that can see that, which is why this module exists alongside
`core.recon` rather than inside it: it answers a different question than a
row count ever can.

TERMINATION IS AN EXPLANATION, NOT AN EXCEPTION SWALLOWED SILENTLY.
`terminated_ids` removes members whose disappearance is already accounted
for BEFORE anything is reported missing — a member who left the plan on
schedule is not a variance, and folding that filter into the caller (rather
than into every downstream reader of the delta) is what keeps "unexplained
by terminations" true of every consumer of this function, not just the one
that remembered to apply it.

THIS PRODUCES A `core.variance.Variance` OBJECT, exactly like `core.
financial_reconciliation` does — `VarianceKind.MEMBER` is already critical
and unwaivable in `core.variance`'s own rule ("money and identity are the two
things a value-based-care contract is settled on"), so a shrunken member
universe blocks certification the same mechanical way an unbalanced ledger
does, with no change to `certify()`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from cinqflow.core.variance import Variance, VarianceKind

#: How many identifiers `explain()` names before falling back to "and N
#: more" — enough to start an investigation without turning the evidence
#: text into the whole roster.
DEFAULT_SAMPLE_SIZE = 20


@dataclass(frozen=True)
class MemberUniverseDelta:
    """The set difference between two member universes, with enough of each
    side named to investigate — never just the two counts."""

    missing: tuple[str, ...]
    missing_count: int
    extra: tuple[str, ...]
    extra_count: int
    previous_total: int
    current_total: int

    @property
    def unexplained(self) -> int:
        return self.missing_count + self.extra_count

    def explain(self) -> str:
        parts = [f"{self.previous_total} member(s) previously, {self.current_total} now."]
        if self.missing_count:
            parts.append(
                f"{self.missing_count} present before and absent now, not explained by "
                f"termination: {_named(self.missing, self.missing_count)}."
            )
        if self.extra_count:
            named = _named(self.extra, self.extra_count)
            parts.append(f"{self.extra_count} present now but not before: {named}.")
        return " ".join(parts)


def _named(sample: tuple[str, ...], total_count: int) -> str:
    listed = ", ".join(sample)
    remaining = total_count - len(sample)
    return f"{listed} and {remaining} more" if remaining > 0 else listed


def compare_member_universe(
    previous_ids: Iterable[object],
    current_ids: Iterable[object],
    terminated_ids: Iterable[object] = (),
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> MemberUniverseDelta:
    """Two member-id universes -> what changed between them, set-wise.

    Identifiers are compared as strings regardless of their source type — a
    surrogate key read back from Postgres and one read from a legacy
    extract need not share a Python type to be the same member.
    """
    previous = {str(identifier) for identifier in previous_ids}
    current = {str(identifier) for identifier in current_ids}
    terminated = {str(identifier) for identifier in terminated_ids}

    missing_all = sorted((previous - current) - terminated)
    extra_all = sorted(current - previous)

    return MemberUniverseDelta(
        missing=tuple(missing_all[:sample_size]),
        missing_count=len(missing_all),
        extra=tuple(extra_all[:sample_size]),
        extra_count=len(extra_all),
        previous_total=len(previous),
        current_total=len(current),
    )


def member_universe_variance(
    delta: MemberUniverseDelta,
    *,
    tolerance: Decimal,
    batch_id: str,
    feed_id: str,
    opened_by: str,
    now: datetime,
    variance_id: str,
) -> Variance | None:
    """A `Variance` when the delta exceeds `tolerance`, `None` when it does
    not — the pack should not manufacture an investigation over a universe
    that moved by an amount already agreed to be normal.
    """
    if Decimal(delta.unexplained) <= tolerance:
        return None
    return Variance(
        variance_id=variance_id,
        batch_id=batch_id,
        feed_id=feed_id,
        kind=VarianceKind.MEMBER,
        expected=Decimal(0),
        actual=Decimal(delta.unexplained),
        tolerance=tolerance,
        opened_by=opened_by,
        opened_ts=now,
        explanation=delta.explain(),
    )
