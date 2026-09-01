"""CF-V3-E13-02 — trending, shared by both packs.

    "Trend both packs over time so gradual drift is as visible as sudden
     breaks."
    — CF-V3-E13-02

ONE ALGORITHM, TWO CALLERS. A financial pack's series is netted-total-minus-
control-total per month; a member-universe pack's series is unexplained-
member-count per batch. Neither `core.financial_reconciliation` nor `core.
member_universe` needs its own idea of "is this drifting" — the question is
the same shape (a labelled series of numbers, is it moving one direction
every step) regardless of what the numbers measure, so it is answered once,
here, and imported by both.

A SINGLE BREACH AND A SLOW DRIFT ARE DIFFERENT FAILURES, AND THIS IS WHY
`certify()` DOES NOT NEED TO KNOW ABOUT EITHER. `Variance.beyond_tolerance`
already catches the sudden break — one point past the line. `Trend.drifting`
catches the OTHER failure the story names: a series of small movements each
individually inside tolerance, which never opens a `Variance` at all and so
would be invisible to certification by design. `drifting` is deliberately
NOT a `Variance` and NOT a `Check` — it is what the recon report and its
screen show an operator BEFORE a breach happens, not a gate that blocks a
batch for a trend that has not (yet) crossed anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TrendPoint:
    """One period's value. `period` is whatever label orders the series for
    the caller — a batch id, a `YYYY-MM` month key — this module never
    parses it, only sorts it."""

    period: str
    value: Decimal


@dataclass(frozen=True)
class Trend:
    """A series, ordered by period, and the one question it can answer
    about itself without any external tolerance: has every step moved the
    same direction."""

    points: tuple[TrendPoint, ...]

    @property
    def drifting(self) -> bool:
        """Three points minimum: two points have exactly one direction by
        definition and would flag every ordinary two-month history as
        'drifting', which is not a trend, it is a single data point with a
        neighbour."""
        if len(self.points) < 3:
            return False
        diffs = [
            later.value - earlier.value
            for earlier, later in zip(self.points, self.points[1:], strict=False)
        ]
        return all(diff > 0 for diff in diffs) or all(diff < 0 for diff in diffs)

    @property
    def latest(self) -> TrendPoint | None:
        return self.points[-1] if self.points else None


def trend(points: Sequence[TrendPoint]) -> Trend:
    """Order a series by period. The only computation here — `drifting` is
    a property, not a second function, so a caller cannot read `points` in
    the wrong order and get a different answer than the report does."""
    return Trend(points=tuple(sorted(points, key=lambda point: point.period)))
