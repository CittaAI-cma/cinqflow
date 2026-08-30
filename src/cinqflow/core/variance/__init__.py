"""CF-V2-E13-03 — a variance is a decision with a name on it, not a report.

    "every reconciliation variance beyond tolerance opens a tracked
     investigation that ends in exactly ONE of: corrected, approved-with-
     explanation, or waived by an authorized person"
    "Block Silver ODS publication on critical variances MECHANICALLY — not by
     convention."
    "Keep waivers rare and loud: time-boxed, reasoned, and reported."
    — CF-V2-E13-03

THE WAIVER IS THE INTERESTING PART, and it is where this story earns its epic.
A waiver is the platform being asked to accept something it believes is wrong.
Three properties make that safe rather than corrosive, and all three are
enforced in the type rather than in a review:

  • an EXPIRY is mandatory and bounded — `Waiver` will not construct without
    one, and 90 days is the ceiling. A permanent waiver is a silently lowered
    standard, and the whole point is that the standard stays visible;
  • a REASON is mandatory and free of filler — "known payer quirk" passes,
    "n/a" is refused, because a waiver nobody explained is one nobody can
    review at renewal;
  • an AUTHORIZED ROLE is required, and the author-approves-own-change negative
    from `core/model/governed` still bites: `waived_by` may not equal the
    person who opened the investigation.

CRITICAL VARIANCES ARE NOT WAIVABLE AT ALL. `blocks_publication` is computed
from the category and the magnitude, and `Waiver` refuses to attach to one. The
story says publication is blocked mechanically; making the block un-waivable is
what "mechanically" means.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind

#: The ceiling. A waiver that outlives a quarter is a decision, not a waiver.
MAX_WAIVER_DAYS = 90

#: Reasons that are not reasons. Refused at construction.
_EMPTY_REASONS = frozenset({"n/a", "na", "none", "-", "tbd", "known issue", "as agreed"})


class VarianceError(ValueError):
    """A variance record that would not survive an audit."""


@unique
class VarianceKind(StrEnum):
    """The four categories, so patterns emerge ACROSS feeds.

    "Categorize variances (count, financial, member, duplicate) so patterns
     emerge across feeds."
    """

    COUNT = "count"
    FINANCIAL = "financial"
    MEMBER = "member"
    DUPLICATE = "duplicate"


@unique
class VarianceOutcome(StrEnum):
    """Exactly one of three. `OPEN` is the absence of an outcome, not a fourth."""

    OPEN = "open"
    CORRECTED = "corrected"
    APPROVED_WITH_EXPLANATION = "approved_with_explanation"
    WAIVED = "waived"


@dataclass(frozen=True)
class Waiver:
    """Time-boxed, reasoned, attributed. Constructed only if all three hold."""

    waived_by: str
    reason: str
    granted_on: date
    expires_on: date

    def __post_init__(self) -> None:
        if not self.waived_by.strip():
            raise VarianceError("a waiver names the person who granted it, or it is not one")
        cleaned = self.reason.strip().lower()
        if not cleaned or cleaned in _EMPTY_REASONS:
            raise VarianceError(
                f"{self.reason!r} is not a reason. A waiver is the platform accepting "
                "something it believes is wrong; the reason is what makes that "
                "reviewable at renewal."
            )
        if self.expires_on <= self.granted_on:
            raise VarianceError("a waiver without a future expiry is a lowered standard")
        if (self.expires_on - self.granted_on).days > MAX_WAIVER_DAYS:
            raise VarianceError(
                f"a waiver may not run longer than {MAX_WAIVER_DAYS} days — beyond that "
                "it is a tolerance change, which is an approved config change"
            )

    def active_on(self, day: date) -> bool:
        return self.granted_on <= day < self.expires_on

    def lapsed_on(self, day: date) -> bool:
        """Re-alerting when it lapses is an acceptance criterion, not a nicety."""
        return day >= self.expires_on


@dataclass(frozen=True)
class Variance:
    """One reconciliation difference, and what was decided about it."""

    variance_id: str
    batch_id: str
    feed_id: str
    kind: VarianceKind
    expected: Decimal
    actual: Decimal
    tolerance: Decimal
    opened_by: str
    opened_ts: datetime
    outcome: VarianceOutcome = VarianceOutcome.OPEN
    explanation: str = ""
    waiver: Waiver | None = None

    @property
    def delta(self) -> Decimal:
        return self.actual - self.expected

    @property
    def beyond_tolerance(self) -> bool:
        return abs(self.delta) > self.tolerance

    @property
    def critical(self) -> bool:
        """Which variances block publication mechanically.

        FINANCIAL and MEMBER always: money and identity are the two things a
        value-based-care contract is settled on. COUNT and DUPLICATE become
        critical only past 10x tolerance — a small persistent count variance is
        the story's own waivable example.
        """
        if not self.beyond_tolerance:
            return False
        if self.kind in {VarianceKind.FINANCIAL, VarianceKind.MEMBER}:
            return True
        return abs(self.delta) > self.tolerance * 10

    def blocks_publication(self, on: date) -> bool:
        """Critical and unresolved blocks. A waiver cannot clear a critical one.

        Note the asymmetry: a non-critical variance blocks while OPEN and stops
        blocking once decided; a CRITICAL one blocks until CORRECTED. That is
        the mechanical part — there is no path from critical to publishable
        that does not go through fixing it.
        """
        if self.critical:
            return self.outcome is not VarianceOutcome.CORRECTED
        if self.outcome is VarianceOutcome.OPEN:
            return True
        if self.outcome is VarianceOutcome.WAIVED and self.waiver is not None:
            return not self.waiver.active_on(on)
        return False

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RECON, subject=self.batch_id, fragment=self.variance_id)

    # ── the three endings ────────────────────────────────────────────────────

    def correct(self, *, by: str, note: str) -> Variance:
        if not note.strip():
            raise VarianceError("a correction says what was corrected")
        _not_the_author(self, by)
        return replace(self, outcome=VarianceOutcome.CORRECTED, explanation=note.strip())

    def approve_with_explanation(self, *, by: str, explanation: str) -> Variance:
        if self.critical:
            raise VarianceError(
                f"variance {self.variance_id} is critical ({self.kind.value}) — it is "
                "corrected or it blocks publication. An explanation is not a fix."
            )
        if not explanation.strip():
            raise VarianceError("an approval-with-explanation requires the explanation")
        _not_the_author(self, by)
        return replace(
            self,
            outcome=VarianceOutcome.APPROVED_WITH_EXPLANATION,
            explanation=explanation.strip(),
        )

    def waive(self, waiver: Waiver) -> Variance:
        if self.critical:
            raise VarianceError(
                f"variance {self.variance_id} is critical and cannot be waived — "
                "publication is blocked mechanically, not by convention"
            )
        _not_the_author(self, waiver.waived_by)
        return replace(self, outcome=VarianceOutcome.WAIVED, waiver=waiver)


def _not_the_author(variance: Variance, actor: str) -> None:
    """The universal negative, applied to variances too.

    `core/model/governed` raises this for governed objects; a variance is not
    one, so the guarantee is restated here rather than inherited. Restating it
    is a cost — but the alternative is a decision surface where the one person
    who found the problem also signs it off.
    """
    if actor.strip() == variance.opened_by.strip():
        raise VarianceError(
            f"{actor} opened variance {variance.variance_id} and may not also decide it. "
            "The author of a change never approves it."
        )


def open_waivers(variances: Sequence[Variance], *, on: date) -> tuple[Variance, ...]:
    """For the monthly recon report — waivers are meant to be LOUD."""
    return tuple(
        v
        for v in variances
        if v.outcome is VarianceOutcome.WAIVED and v.waiver is not None and v.waiver.active_on(on)
    )


def lapsed_waivers(variances: Sequence[Variance], *, on: date) -> tuple[Variance, ...]:
    """'re-alerts when it lapses' — the exception path, as a query."""
    return tuple(
        v
        for v in variances
        if v.outcome is VarianceOutcome.WAIVED and v.waiver is not None and v.waiver.lapsed_on(on)
    )


def default_expiry(granted: date) -> date:
    return granted + timedelta(days=MAX_WAIVER_DAYS)
