"""CF-V2-E12-02 — where exactly is this batch, and what happened at each stage.

    "I want to see every batch across Landing, Bronze, Silver Raw, Identity and
     Silver ODS with counts, durations, SLA state, and drill-down to its errors
     and quarantined records, so that the daily_status spreadsheet retires: the
     control tables already hold the truth, and this screen finally shows it to
     everyone."
    "Offer any mutation from this screen except through the governed action
     surface (next story)."
    — CF-V2-E12-02, and its first don't

THIS SCREEN ADDS NO FACTS. Every number on it was already written by the
engine — `batch_stage_status` holds records_in, records_out, quarantined and
attributed_drops because "for an engine story, the control rows ARE the
observable behaviour". The spreadsheet existed because nobody could see them,
not because they were missing. So this module READS and ARRANGES, and there is
no code path here that computes a count the engine did not already record.

RECONCILIATION IS SHOWN INLINE, NOT ON ANOTHER SCREEN. `StageView.balances`
runs the balance equation on the row itself, and `BatchView.flow` renders the
record-count chain stage by stage. An operator asking "where did the rows go?"
is asking a reconciliation question one click into a monitor, and sending them
to a separate reconciliation page is how the two views end up disagreeing —
which is precisely what happened at `Claim_ProfHeader` (incident #11: 6.67M
profiled against 13.76M rows, found by a person reading two screens).

CASCADE SEPARATION LIVES HERE, AND CF-V2-E12-04 IMPORTS IT. "Three errors
logged; two are consequences of the first" is in both stories, and it must be
one definition or the monitor and the incident will count differently on the
same batch. It is DETERMINISTIC — a time window, the spine's own ordering, and
whether a distinct rule fired — because a cascade rule that needed a model
would leave the monitor unable to draw the screen when the model is down.

WHAT IS ABSENT IS THE POINT OF THE DON'T. There is no `retry`, `pause`,
`acknowledge` or `assign` in this module, and none of its types carries a
mutation. The action surface (CF-V2-E12-03) owns those, with its allowed-state
matrix and its approval identifiers, and a "quick retry" added here would be
the one path around all of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, Layer, StatusWord

#: How close in time a later error must be to count as fallout from an earlier
#: one. Five minutes, because the client's own cascade — a task failing and its
#: two downstream tasks failing on the missing key — happens inside one
#: scheduler tick, and a wider window starts absorbing genuinely separate
#: failures into somebody else's incident.
CASCADE_WINDOW = timedelta(minutes=5)

#: How much longer than usual a running batch has to be before the board says
#: something. The story's own figure.
STUCK_FACTOR = 3.0

#: Below this many prior runs, "typical duration" is not a number worth
#: comparing against. Two samples make any third run either half or double the
#: mean, and a board that cried stuck on every new feed would be muted inside a
#: week.
MIN_RUNS_FOR_TYPICAL = 5


# ── what the monitor reads ───────────────────────────────────────────────────
#
# Structural, for the reason `core.scheduling` states: the control-tables port
# imports FROM core.


@runtime_checkable
class BatchLike(Protocol):
    @property
    def batch_id(self) -> str: ...
    @property
    def feed_id(self) -> str: ...
    @property
    def business_date(self) -> str: ...
    @property
    def state(self) -> BatchState: ...
    @property
    def started_ts(self) -> datetime: ...
    @property
    def completed_ts(self) -> datetime | None: ...


@runtime_checkable
class StageLike(Protocol):
    @property
    def batch_id(self) -> str: ...
    @property
    def stage(self) -> Layer: ...
    @property
    def state(self) -> BatchState: ...
    @property
    def started_ts(self) -> datetime: ...
    @property
    def completed_ts(self) -> datetime | None: ...
    @property
    def records_in(self) -> int: ...
    @property
    def records_out(self) -> int: ...
    @property
    def quarantined(self) -> int: ...
    @property
    def attributed_drops(self) -> int: ...


@runtime_checkable
class ErrorLike(Protocol):
    @property
    def error_id_hash(self) -> str: ...
    @property
    def batch_id(self) -> str: ...
    @property
    def stage(self) -> Layer: ...
    @property
    def category(self) -> ErrorCategory: ...
    @property
    def message(self) -> str: ...
    @property
    def occurred_ts(self) -> datetime: ...
    @property
    def rule_id(self) -> str | None: ...


# ── one stage ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StageView:
    """One `batch_stage_status` row, arranged for reading.

    The four counts are not statistics — they are the terms of the balance
    equation, and `balances` evaluates it here so reconciliation is visible on
    the row rather than one screen away.
    """

    layer: Layer
    state: BatchState
    started_ts: datetime
    completed_ts: datetime | None = None
    records_in: int = 0
    records_out: int = 0
    quarantined: int = 0
    attributed_drops: int = 0

    @property
    def duration(self) -> timedelta | None:
        return self.completed_ts - self.started_ts if self.completed_ts else None

    @property
    def unexplained(self) -> int:
        """What the equation cannot account for. Zero, or the stage is wrong.

        The same arithmetic `core.recon.StageReconciliation` performs, on the
        stage row rather than on the recon row — one definition applied to what
        this screen happens to have loaded, so the monitor and the ledger can
        never disagree about whether a batch balanced.
        """
        return self.records_in - (self.records_out + self.quarantined + self.attributed_drops)

    @property
    def balances(self) -> bool:
        return self.unexplained == 0

    @property
    def status(self) -> StatusWord:
        return self.state.status_word

    def flow(self) -> str:
        """The record-count flow, as one readable line."""
        parts = [f"{self.records_in:,} in", f"{self.records_out:,} out"]
        if self.quarantined:
            parts.append(f"{self.quarantined:,} quarantined")
        if self.attributed_drops:
            parts.append(f"{self.attributed_drops:,} attributed")
        if not self.balances:
            parts.append(f"{self.unexplained:,} UNEXPLAINED")
        return f"{self.layer.value}: " + " · ".join(parts)


# ── errors, and which of them are somebody's problem ─────────────────────────
@dataclass(frozen=True)
class ErrorView:
    """One `error_log` row, with whether it is a cause or a consequence."""

    error_id_hash: str
    stage: Layer
    category: ErrorCategory
    message: str
    occurred_ts: datetime
    rule_id: str | None = None
    #: True when this error is fallout from an earlier one in the same batch.
    is_consequence: bool = False
    #: The error this one followed from, when it is a consequence.
    caused_by: str | None = None

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.ERROR, subject=self.error_id_hash)

    @property
    def route(self) -> str:
        return self.citation.route


@dataclass(frozen=True)
class Cascade:
    """The errors of one batch, sorted into causes and fallout.

        "three errors of which two are consequences"

    `actionable` is what a person works on. `consequences` is kept and shown —
    not hidden — because an operator who sees three errors on the batch and two
    on the incident will go looking for the missing one.
    """

    actionable: tuple[ErrorView, ...] = ()
    consequences: tuple[ErrorView, ...] = ()

    @property
    def all(self) -> tuple[ErrorView, ...]:
        return tuple(sorted(self.actionable + self.consequences, key=lambda e: e.occurred_ts))

    @property
    def first(self) -> ErrorView | None:
        """The first ACTIONABLE error — the one the 3 AM question is about."""
        return self.actionable[0] if self.actionable else None

    def explain(self) -> str:
        total = len(self.all)
        if not total:
            return "No errors were logged for this batch."
        if not self.consequences:
            return f"{total} error(s) logged; none is a consequence of another."
        return (
            f"{total} errors logged; {len(self.consequences)} "
            f"{'is a consequence' if len(self.consequences) == 1 else 'are consequences'} "
            "of the first."
        )


def separate_cascade(errors: Sequence[ErrorLike], *, window: timedelta = CASCADE_WINDOW) -> Cascade:
    """Split a batch's errors into causes and fallout. DETERMINISTIC.

    A later error is fallout from an earlier one when ALL FOUR hold, and each
    condition is here because dropping it produced a wrong answer on the
    client's own `BH-AF-002` example:

      1. it happened AFTER the earlier one — obviously, and stated because the
         control rows are not guaranteed to arrive in time order;
      2. at the SAME LAYER OR A LATER ONE. Data does not travel backwards, so a
         Bronze error cannot be fallout from a Silver Raw one;
      3. WITHIN `window`. The client's cascade — a task failing and its two
         downstream tasks failing on the key it never wrote — happens inside
         one scheduler tick. A wider window absorbs genuinely separate failures
         into somebody else's incident;
      4. it names NO DISTINCT RULE. A different `rule_id` firing is a different
         finding about the data, however close in time: two DQ rules failing
         together are two things wrong, not one thing and its shadow.

    Anything that fails one of the four is a SECOND ACTIONABLE ERROR, and gets
    its own place at the top of the list. Over-grouping is the dangerous
    direction: an operator told "two of these are consequences" stops reading
    them, and a real second fault hidden in that pile is one nobody finds.
    """
    ordered = sorted(errors, key=lambda e: (e.occurred_ts, e.error_id_hash))
    spine = list(Layer)
    actionable: list[ErrorView] = []
    consequences: list[ErrorView] = []

    for error in ordered:
        cause = _cause_for(error, actionable, spine=spine, window=window)
        view = ErrorView(
            error_id_hash=error.error_id_hash,
            stage=error.stage,
            category=error.category,
            message=error.message,
            occurred_ts=error.occurred_ts,
            rule_id=error.rule_id,
            is_consequence=cause is not None,
            caused_by=cause.error_id_hash if cause else None,
        )
        (consequences if cause else actionable).append(view)

    return Cascade(actionable=tuple(actionable), consequences=tuple(consequences))


def _cause_for(
    error: ErrorLike,
    actionable: Sequence[ErrorView],
    *,
    spine: list[Layer],
    window: timedelta,
) -> ErrorView | None:
    for candidate in actionable:
        if error.occurred_ts < candidate.occurred_ts:
            continue
        if spine.index(error.stage) < spine.index(candidate.stage):
            continue
        if error.occurred_ts - candidate.occurred_ts > window:
            continue
        if error.rule_id is not None and error.rule_id != candidate.rule_id:
            continue
        return candidate
    return None


# ── the batch ────────────────────────────────────────────────────────────────
@unique
class SlaState(StrEnum):
    """How a running batch compares to what this feed usually takes.

    Three, and `UNKNOWN` is one of them. A feed with four prior runs has no
    typical duration worth comparing against, and a board that guessed would
    cry stuck on every new feed until somebody muted it.
    """

    ON_TIME = "on_time"
    STUCK = "stuck"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BatchView:
    """One batch, everything about it, in one object.

    "Answer 'where exactly is this batch and what happened at each stage' in
    two clicks" — so the second click has to find everything already here. A
    view that made the errors a separate fetch would be a third click.
    """

    batch_id: str
    feed_id: str
    business_date: str
    state: BatchState
    started_ts: datetime
    completed_ts: datetime | None = None
    stages: tuple[StageView, ...] = ()
    cascade: Cascade = field(default_factory=Cascade)
    sla: SlaState = SlaState.UNKNOWN
    typical_duration: timedelta | None = None

    @property
    def status(self) -> StatusWord:
        """A STUCK batch reports `Needs Attention`, not `Processing`.

        The exception in the story is exactly this: a batch far beyond its
        usual duration must not sit quietly green. `BatchState.IN_PROGRESS`
        says Processing, and that is right for the state machine and wrong for
        the board, so the board's word is computed with the SLA in hand.
        """
        if self.sla is SlaState.STUCK:
            return StatusWord.NEEDS_ATTENTION
        return self.state.status_word

    @property
    def duration(self) -> timedelta | None:
        return self.completed_ts - self.started_ts if self.completed_ts else None

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.BATCH, subject=self.batch_id)

    @property
    def failed_at(self) -> Layer | None:
        """The FIRST stage that failed, in spine order.

        First rather than last: a failure at Bronze leaves Silver Raw
        untouched, and naming the furthest stage recorded would send an
        operator to a layer the data never reached.
        """
        spine = list(Layer)
        failed = [s for s in self.stages if s.state in {BatchState.FAILED, BatchState.BLOCKED}]
        return min(failed, key=lambda s: spine.index(s.layer)).layer if failed else None

    @property
    def reached(self) -> tuple[Layer, ...]:
        spine = list(Layer)
        return tuple(sorted((s.layer for s in self.stages), key=spine.index))

    @property
    def balances(self) -> bool:
        return all(stage.balances for stage in self.stages)

    @property
    def rows_written(self) -> int:
        """What actually landed. Zero on a batch that failed at Bronze, which
        is the figure the story's own example leads with."""
        return self.stages[-1].records_out if self.stages else 0

    def flow(self) -> tuple[str, ...]:
        """The record-count chain, stage by stage. Reconciliation, inline."""
        spine = list(Layer)
        return tuple(
            stage.flow() for stage in sorted(self.stages, key=lambda s: spine.index(s.layer))
        )

    def explain(self) -> str:
        """The sentence the story writes for batch #1244, generalised."""
        when = self.started_ts.strftime("%H:%M")
        if self.failed_at is None:
            return (
                f"{self.batch_id} ({self.feed_id}, {self.business_date}) is "
                f"{self.status.value.lower()}; {self.rows_written:,} rows written."
            )
        first = self.cascade.first
        named = f" with error {first.rule_id or first.error_id_hash[:12]}" if first else ""
        return (
            f"{self.batch_id} failed at {self.failed_at.value} at {when}{named}. "
            f"{self.cascade.explain()} {self.rows_written:,} rows written."
        )


def typical_duration(
    history: Sequence[BatchLike], *, minimum_runs: int = MIN_RUNS_FOR_TYPICAL
) -> timedelta | None:
    """How long this feed's batches usually take, or None when nobody knows.

    The MEDIAN, not the mean. One 40-minute recovery after an outage drags a
    mean far enough that the next three normal runs look fast and the fourth
    slow one looks fine — and the whole point is to notice the slow one.

    Only COMPLETED batches count. Including failed ones would mix "how long
    this takes" with "how long it took to give up".
    """
    finished = sorted(
        (b.completed_ts - b.started_ts).total_seconds()
        for b in history
        if b.state is BatchState.COMPLETED and b.completed_ts is not None
    )
    if len(finished) < minimum_runs:
        return None
    middle = len(finished) // 2
    seconds = (
        finished[middle] if len(finished) % 2 else (finished[middle - 1] + finished[middle]) / 2
    )
    return timedelta(seconds=seconds)


def sla_state(
    batch: BatchLike,
    *,
    typical: timedelta | None,
    now: datetime,
    factor: float = STUCK_FACTOR,
) -> SlaState:
    """Whether a running batch is taking far longer than this feed usually does.

    Only a RUNNING batch can be stuck. A finished one took what it took, and
    labelling a completed batch stuck after the fact tells an operator nothing
    they can act on — that is a reliability trend (CF-V2-E12-05), not a board
    state.
    """
    if batch.state not in {BatchState.IN_PROGRESS, BatchState.RECEIVED, BatchState.RESTARTED}:
        return SlaState.ON_TIME
    if typical is None:
        return SlaState.UNKNOWN
    running = now - batch.started_ts
    return SlaState.STUCK if running > typical * factor else SlaState.ON_TIME


def build_batch_view(
    batch: BatchLike,
    *,
    stages: Sequence[StageLike] = (),
    errors: Sequence[ErrorLike] = (),
    history: Sequence[BatchLike] = (),
    now: datetime | None = None,
) -> BatchView:
    """One batch, assembled. Reads and arranges; computes no new count."""
    stamp = now or datetime.now(UTC)
    typical = typical_duration(history)
    spine = list(Layer)
    return BatchView(
        batch_id=batch.batch_id,
        feed_id=batch.feed_id,
        business_date=batch.business_date,
        state=batch.state,
        started_ts=batch.started_ts,
        completed_ts=batch.completed_ts,
        stages=tuple(
            sorted(
                (
                    StageView(
                        layer=stage.stage,
                        state=stage.state,
                        started_ts=stage.started_ts,
                        completed_ts=stage.completed_ts,
                        records_in=stage.records_in,
                        records_out=stage.records_out,
                        quarantined=stage.quarantined,
                        attributed_drops=stage.attributed_drops,
                    )
                    for stage in stages
                    if stage.batch_id == batch.batch_id
                ),
                key=lambda s: spine.index(s.layer),
            )
        ),
        cascade=separate_cascade([e for e in errors if e.batch_id == batch.batch_id]),
        sla=sla_state(batch, typical=typical, now=stamp),
        typical_duration=typical,
    )


# ── history is a filter, not a project ───────────────────────────────────────
@dataclass(frozen=True)
class BatchFilter:
    """ "show me all failed Fidelis batches in July" — as one object.

    Every field optional and ANDed. A filter type rather than a query string
    keeps the certified-tool boundary intact: `list_batches` is a whitelisted
    read, and a screen that assembled predicates would be a screen that could
    assemble one nobody certified.
    """

    feed_id: str | None = None
    states: frozenset[BatchState] = frozenset()
    from_business_date: str | None = None
    to_business_date: str | None = None
    failed_at: Layer | None = None
    stuck_only: bool = False

    def matches(self, view: BatchView) -> bool:
        if self.feed_id is not None and view.feed_id != self.feed_id:
            return False
        if self.states and view.state not in self.states:
            return False
        # ISO business dates, so string order is chronological order and no
        # parsing is needed — the same reasoning `core.scheduling` uses for
        # prior-period comparison, and for the same reason: a feed's period
        # granularity is its own business.
        if self.from_business_date and view.business_date < self.from_business_date:
            return False
        if self.to_business_date and view.business_date > self.to_business_date:
            return False
        if self.failed_at is not None and view.failed_at is not self.failed_at:
            return False
        return not (self.stuck_only and view.sla is not SlaState.STUCK)

    def describe(self) -> str:
        parts: list[str] = []
        if self.feed_id:
            parts.append(self.feed_id)
        if self.states:
            parts.append("/".join(sorted(s.value.lower() for s in self.states)))
        if self.from_business_date or self.to_business_date:
            parts.append(f"{self.from_business_date or 'any'}..{self.to_business_date or 'any'}")
        if self.failed_at:
            parts.append(f"failed at {self.failed_at.value}")
        if self.stuck_only:
            parts.append("stuck only")
        return " · ".join(parts) or "everything"


def search(views: Sequence[BatchView], criteria: BatchFilter) -> tuple[BatchView, ...]:
    """Newest business date first — the order an operator scans in."""
    return tuple(
        sorted(
            (view for view in views if criteria.matches(view)),
            key=lambda v: (v.business_date, v.started_ts),
            reverse=True,
        )
    )
