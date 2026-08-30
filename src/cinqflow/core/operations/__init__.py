"""CF-V2-E12-01 — the morning question, answered from the control tables.

    "I want a home screen answering the morning question at a glance: how many
     files were expected, received, missing, at risk — filterable by domain —
     with a Needs-Your-Attention list of the items requiring action today, so
     that the whole company shares one truthful picture of data health."
    "Show any number that cannot be traced to a control-table query — no
     hand-maintained figures anywhere."
    — CF-V2-E12-01, and its first don't

THE DON'T IS THE DESIGN, AND IT IS AIMED AT SOMETHING SPECIFIC. The screen this
replaces is `daily_status.xlsx`, a spreadsheet somebody typed every morning
from four other places. Its numbers were not wrong because the person was
careless; they were wrong because a figure that is TYPED has no way to be
right. So every counter here carries `derived_from` — the certified query it
came from — and a test asserts none is blank. A number whose provenance is
empty cannot be rendered, which is the spreadsheet made unbuildable rather
than discouraged.

RANKED BY BUSINESS IMPACT, NOT BY TIMESTAMP — and impact is COMPUTED, from
things the platform already knows. Sorting an attention list by time puts the
overnight batch that nobody consumes above the roster that eleven feeds wait
for, every single morning, and operators learn to scroll past the top. So
`impact_of` reads three facts the estate already holds:

  • the BLAST RADIUS from `core.scheduling.DependencyGraph` — how many
    downstream feeds stop if this one does not arrive. CF-V1-E8-03 built that
    graph to gate runs; the same edges answer "what does this cost";
  • HOW LATE against the feed's own `ServiceLevel` — not a global threshold,
    because a roster due at 06:00 and a claims extract due weekly are late at
    different speeds;
  • whether it has ALREADY missed, or is merely at risk of missing.

None of the three is a number anybody types, which is the same discipline the
counters are held to.

"AT RISK" IS NOT ONE OF THE SEVEN WORDS, AND THAT IS DELIBERATE. The story's
counters read expected / received / missing / at risk, and the seven status
words are what a user is ever SHOWN. So at-risk is a COUNTER, and the row it
counts displays `Needs Attention` — the counter is arithmetic about a
condition, the word is what the condition is called. Adding an eighth word to
make the two match would have broken the lexicon test to save a subtraction.

NEVER SILENTLY STALE. The exception in the story is the one most dashboards get
wrong: when the control tables are unreachable, the honest answer is the last
numbers WITH their age and a banner, not zeros and not a spinner. Zeros read as
"nothing expected today", which on a morning when the plane is down is the most
dangerous thing this screen could say.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import BatchState, StatusWord
from cinqflow.core.registry.operations import ServiceLevel
from cinqflow.core.scheduling import DependencyGraph
from cinqflow.core.sla import Cycle, SlaStatus


class OperationsError(RuntimeError):
    """A board that could not be built from what it was given."""


class UntraceableNumberError(OperationsError):
    """A counter with no query behind it.

    The spreadsheet, refused at construction. `daily_status.xlsx` was not wrong
    because somebody was careless — it was wrong because a typed figure has no
    way to be right.
    """


# ── what the board reads ─────────────────────────────────────────────────────
#
# STRUCTURAL, NOT IMPORTED, for the reason `core.scheduling` states: the
# control-tables port imports FROM core, so core importing it back would invert
# the dependency the chip rests on. The port's own `BatchControl` and
# `InputFile` satisfy these with no adapter and nothing to keep in step.


@runtime_checkable
class BatchLike(Protocol):
    """One `batch_control` row, as far as the board is concerned."""

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


@unique
class ArrivalCondition(StrEnum):
    """What has happened to one expected delivery. FOUR, matching the counters.

    Internal, like `BatchState`. Each maps onto one of the seven words, and the
    mapping lives here once so a screen, a tool result and an agent's answer
    cannot disagree about whether an at-risk file is `Needs Attention`.
    """

    #: Due later today; nothing to do.
    EXPECTED = "expected"
    #: A batch exists for it.
    RECEIVED = "received"
    #: Past due plus grace. Somebody has to chase a payer.
    MISSING = "missing"
    #: Inside grace, or past due on a feed whose grace has not run out —
    #: late enough to watch, not late enough to chase.
    AT_RISK = "at_risk"

    @property
    def status_word(self) -> StatusWord:
        return {
            ArrivalCondition.EXPECTED: StatusWord.EXPECTED,
            ArrivalCondition.RECEIVED: StatusWord.RECEIVED,
            ArrivalCondition.MISSING: StatusWord.MISSING,
            # NOT an eighth word. At-risk is a counter; what the operator is
            # shown is that this row needs them.
            ArrivalCondition.AT_RISK: StatusWord.NEEDS_ATTENTION,
        }[self]

    @property
    def needs_action_today(self) -> bool:
        return self in {ArrivalCondition.MISSING, ArrivalCondition.AT_RISK}


@dataclass(frozen=True)
class Expectation:
    """One delivery the platform is waiting for today.

    Built from the feed's own `ServiceLevel` — its due time, its grace, its
    escalation — rather than from a global threshold, because a roster due at
    06:00 and a weekly claims extract are late at different speeds and a single
    number would page about one and stay silent about the other.
    """

    feed_id: str
    domain: str
    business_date: str
    due_ts: datetime
    grace_minutes: int = 30
    escalate_after_minutes: int = 120

    @classmethod
    def from_service_level(
        cls,
        *,
        feed_id: str,
        domain: str,
        business_date: str,
        service_level: ServiceLevel,
        due_ts: datetime,
    ) -> Expectation:
        """The feed's declared SLA, as one day's expectation.

        `due_ts` is supplied rather than computed from the local time and
        timezone, because resolving `06:00 America/New_York` for a given date
        is calendar work — DST, business days, holidays — and `core/` doing it
        with a hand-rolled offset is exactly the March bug `ServiceLevel`'s own
        docstring warns about. The caller resolves it with a real timezone
        database; this holds the answer.
        """
        return cls(
            feed_id=feed_id,
            domain=domain,
            business_date=business_date,
            due_ts=due_ts,
            grace_minutes=service_level.grace_minutes,
            escalate_after_minutes=service_level.escalate_after_minutes,
        )

    def minutes_late(self, now: datetime) -> int:
        """How far past due, floored at zero. Negative would read as 'early',
        and nothing is early — it is either here or not."""
        return max(0, int((now - self.due_ts).total_seconds() // 60))

    def as_cycle(
        self,
        *,
        actual_ts: datetime | None = None,
        batch_id: str | None = None,
        files_received: int = 0,
        files_expected: int = 1,
    ) -> Cycle:
        """This expectation as a row of `control.sla_instance`, before it is one.

        THE CLOCK IS DEFINED ONCE, IN `core.sla`, AND THIS PROJECTS ONTO IT.
        Two implementations of "is this late" is precisely how a board reports
        one missing file while the feed's reliability trend reports none — and
        the two would be built months apart by different people, each correct
        in isolation.

        `cycle_date` is the BUSINESS date, not the due date's calendar day.
        They usually coincide and occasionally must not: a monthly roster for
        August delivered on 1 September belongs to August, and a cycle keyed on
        the delivery day would file it under the wrong month forever.
        """
        return Cycle(
            feed_id=self.feed_id,
            cycle_date=date.fromisoformat(self.business_date),
            expected_ts=self.due_ts,
            grace=timedelta(minutes=self.grace_minutes),
            actual_ts=actual_ts,
            batch_id=batch_id,
            files_received=files_received,
            files_expected=files_expected,
        )

    def condition(self, *, arrived: bool, now: datetime) -> ArrivalCondition:
        """What the BOARD shows. Four values, matching the four counters.

        Distinct from `sla_status` below, and the difference is the point. An
        operator looking at the morning board wants to know the file is here;
        the control table has to remember that it arrived outside its window.
        Both are true, and collapsing them loses the second one silently.
        """
        if arrived:
            return ArrivalCondition.RECEIVED
        match self.as_cycle().status(now):
            case SlaStatus.ON_TIME:
                return ArrivalCondition.EXPECTED
            case SlaStatus.DELAYED:
                return ArrivalCondition.AT_RISK
            case SlaStatus.BREACHED:
                return ArrivalCondition.MISSING

    def sla_status(self, *, actual_ts: datetime | None, now: datetime) -> SlaStatus:
        """What `control.sla_instance.sla_status` records. Three values, and
        they are the CHECK constraint's own.

        A delivery that ARRIVED is judged on when it arrived; only one that has
        not arrived is judged against the wall clock. Reversing those two makes
        a late-but-received file flip back to Breached on every page refresh —
        and makes the reliability trend forget every late delivery that
        eventually turned up.
        """
        return self.as_cycle(actual_ts=actual_ts, files_received=1 if actual_ts else 0).status(now)


# ── one row on the board ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class ArrivalRow:
    """One feed's day, as the board shows it."""

    feed_id: str
    domain: str
    business_date: str
    condition: ArrivalCondition
    due_ts: datetime
    minutes_late: int = 0
    batch_id: str | None = None
    batch_state: BatchState | None = None

    @property
    def status(self) -> StatusWord:
        """The seven words. A RECEIVED row reports its BATCH's word, because
        "received" stops being the useful fact the moment loading starts and
        an operator wants to know whether it is processing or has failed."""
        if self.condition is ArrivalCondition.RECEIVED and self.batch_state is not None:
            return self.batch_state.status_word
        return self.condition.status_word

    @property
    def citation(self) -> CitationId:
        return (
            CitationId(kind=CitationKind.BATCH, subject=self.batch_id)
            if self.batch_id
            else CitationId(kind=CitationKind.FEED, subject=self.feed_id)
        )

    def headline(self) -> str:
        """The sentence the story writes for the missing UHC file, generalised.

        "expected 6:00 AM — not received"
        """
        due = self.due_ts.strftime("%-I:%M %p") if _AM_PM else self.due_ts.strftime("%H:%M")
        match self.condition:
            case ArrivalCondition.MISSING:
                return f"expected {due} — not received ({self.minutes_late} min late)"
            case ArrivalCondition.AT_RISK:
                return f"expected {due} — inside its grace period, {self.minutes_late} min late"
            case ArrivalCondition.RECEIVED:
                return f"received; batch {self.batch_id or '-'} is {self.status.value.lower()}"
            case ArrivalCondition.EXPECTED:
                return f"expected {due}"


#: Whether the platform's clock formatting supports `%-I` (POSIX). Resolved
#: once at import rather than per row: a `strftime` that raises on Windows
#: would take down the busiest page in the product, and the 24-hour fallback is
#: correct everywhere.
try:  # pragma: no cover - platform probe, one branch runs per OS
    _AM_PM = bool(datetime(2026, 1, 1, 6, 0, tzinfo=UTC).strftime("%-I:%M %p"))
except ValueError:  # pragma: no cover
    _AM_PM = False


# ── the counters, and where each number came from ────────────────────────────
@dataclass(frozen=True)
class Counter:
    """One number, and the query behind it.

    `derived_from` is not documentation. `__post_init__` refuses a blank one,
    so a hand-maintained figure cannot be constructed — which is
    `daily_status.xlsx` made unbuildable rather than discouraged.
    """

    label: str
    value: int
    derived_from: str

    def __post_init__(self) -> None:
        if not self.derived_from.strip():
            raise UntraceableNumberError(
                f"the {self.label!r} counter names no query. Every number on this board is "
                "traceable to a control-table read — a figure with no provenance is the "
                "spreadsheet this screen replaces."
            )


@dataclass(frozen=True)
class Freshness:
    """When these numbers were true, and whether they still are.

        "Given the control tables are momentarily unreachable, when the page
         loads, then it shows its last refreshed time and a clear 'data may be
         stale' banner — never silently stale numbers."

    Zeros would read as "nothing expected today", which on a morning when the
    plane is down is the most dangerous thing this screen could say. So an
    unreachable plane keeps the last numbers and says how old they are.
    """

    as_of: datetime
    reachable: bool = True

    @property
    def may_be_stale(self) -> bool:
        return not self.reachable

    def banner(self, now: datetime) -> str:
        if self.reachable:
            return ""
        minutes = max(0, int((now - self.as_of).total_seconds() // 60))
        age = f"{minutes} minute(s) ago" if minutes else "moments ago"
        return (
            f"Data may be stale — the control tables could not be reached. These numbers "
            f"were last refreshed {age}."
        )


# ── the attention list ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class AttentionItem:
    """One thing requiring action today, and what it costs.

    `impact` is computed and CARRIED, not just used to sort. An operator who
    cannot see why the roster outranks the overnight extract will re-sort the
    list by time, and then the ranking has bought nothing.
    """

    feed_id: str
    domain: str
    headline: str
    impact: int
    why: str
    citation: CitationId
    status: StatusWord

    @property
    def route(self) -> str:
        """One click to the investigation view. `CitationId.route` — the
        platform's own address space, so this needed no link scheme."""
        return self.citation.route


#: What one blocked downstream feed is worth, in impact points.
_BLOCKED_FEED_POINTS = 100

#: What having actually MISSED is worth, over merely being at risk.
_MISSED_POINTS = 60

#: THE INVARIANT THAT MAKES THIS A RANKING AND NOT A CLOCK: the most lateness
#: can ever contribute is less than ONE blocked downstream feed. A roster
#: fifteen minutes late that eleven feeds wait on outranks a standalone extract
#: four hours late — and it still does at nine hours, which is the part a
#: minutes-per-point scheme gets wrong by mid-morning.
#:
#: `test_lateness_can_never_outweigh_a_single_blocked_feed` asserts it over the
#: whole range rather than trusting these two constants to stay in proportion.
_LATE_POINTS_CAP = 40

#: How late a feed has to be to score the full `_LATE_POINTS_CAP`. Four hours,
#: because past that the number a person acts on is "it has not come", not
#: "it is later than it was an hour ago".
_LATE_MINUTES_FOR_FULL = 240


def late_points(minutes: int) -> int:
    """Lateness, scaled into a bounded contribution.

    Bounded rather than linear so that the ranking answers "what does this
    cost?" and not "what happened longest ago?". See `_LATE_POINTS_CAP`.
    """
    capped = min(max(minutes, 0), _LATE_MINUTES_FOR_FULL)
    return capped * _LATE_POINTS_CAP // _LATE_MINUTES_FOR_FULL


def impact_of(row: ArrivalRow, graph: DependencyGraph) -> tuple[int, str]:
    """How much this costs, and the sentence that explains the number.

    Three facts the estate already holds — blast radius, lateness against the
    feed's own SLA, and whether it has already missed — and not one of them is
    typed by anybody. Returns the reason alongside the score because a ranking
    nobody can see the reasoning for is a ranking operators will override.
    """
    blocked = graph.blast_radius(row.feed_id)
    score = len(blocked) * _BLOCKED_FEED_POINTS + late_points(row.minutes_late)
    if row.condition is ArrivalCondition.MISSING:
        score += _MISSED_POINTS

    reasons: list[str] = []
    if blocked:
        reasons.append(
            f"{len(blocked)} downstream feed(s) wait on it ({', '.join(blocked[:3])}"
            + (f" and {len(blocked) - 3} more" if len(blocked) > 3 else "")
            + ")"
        )
    if row.condition is ArrivalCondition.MISSING:
        reasons.append(f"past its grace period by {row.minutes_late - 0} minutes")
    elif row.minutes_late:
        reasons.append(f"{row.minutes_late} minutes late, still inside grace")
    return score, "; ".join(reasons) or "no downstream dependency and not yet late"


# ── the board ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Board:
    """The morning question, answered. One screen, one truth."""

    business_date: str
    rows: tuple[ArrivalRow, ...] = ()
    counters: tuple[Counter, ...] = ()
    attention: tuple[AttentionItem, ...] = ()
    freshness: Freshness = field(
        default_factory=lambda: Freshness(as_of=datetime.now(UTC), reachable=True)
    )

    def counter(self, label: str) -> int:
        for candidate in self.counters:
            if candidate.label == label:
                return candidate.value
        raise OperationsError(f"no counter called {label!r}")

    @property
    def totals(self) -> tuple[int, int, int, int]:
        """(expected, received, missing, at risk) — the story's four figures,
        in the story's order, so a test reads like the acceptance criterion."""
        return (
            self.counter("expected"),
            self.counter("received"),
            self.counter("missing"),
            self.counter("at risk"),
        )

    def in_domain(self, domain: str) -> Board:
        """Filter by domain, keeping the counters HONEST.

        Recomputed rather than filtered: a board that showed twelve expected
        and two rows would be a screen whose header and body disagree, and the
        header is the half people quote in stand-up.
        """
        rows = tuple(row for row in self.rows if row.domain == domain)
        return Board(
            business_date=self.business_date,
            rows=rows,
            counters=_counters(rows),
            attention=tuple(item for item in self.attention if item.domain == domain),
            freshness=self.freshness,
        )

    def explain(self) -> str:
        expected, received, missing, at_risk = self.totals
        head = (
            f"{self.business_date}: {expected} expected · {received} received · "
            f"{missing} missing · {at_risk} at risk"
        )
        if not self.attention:
            return f"{head}\nNothing needs you this morning."
        lines = [head, "Needs your attention:"]
        lines.extend(f"  {item.feed_id} — {item.headline}" for item in self.attention)
        return "\n".join(lines)


#: The certified reads each counter is derived from. Named rather than
#: described, so `derived_from` points at something a person can run.
_SOURCE = "control.batch_control via list_batches"


def _counters(rows: Sequence[ArrivalRow]) -> tuple[Counter, ...]:
    """The four figures, each traceable. Computed from the SAME rows the board
    renders, so the header cannot disagree with the body."""
    counted = dict.fromkeys(ArrivalCondition, 0)
    for row in rows:
        counted[row.condition] += 1
    return (
        Counter(label="expected", value=len(rows), derived_from="registry.feed schedules"),
        Counter(label="received", value=counted[ArrivalCondition.RECEIVED], derived_from=_SOURCE),
        Counter(label="missing", value=counted[ArrivalCondition.MISSING], derived_from=_SOURCE),
        Counter(label="at risk", value=counted[ArrivalCondition.AT_RISK], derived_from=_SOURCE),
    )


def build_board(
    *,
    business_date: str,
    expectations: Sequence[Expectation],
    batches: Sequence[BatchLike],
    graph: DependencyGraph | None = None,
    now: datetime,
    reachable: bool = True,
) -> Board:
    """The whole board, computed. Pure — the caller does the reading.

    `expected` counts EXPECTATIONS, not batches. A file that arrived without
    being expected is a real thing (`InputFile.is_unexpected`) and it belongs
    on the batch monitor, not in a count of what was due — putting it here
    would make "12 expected / 13 received" a number people have to explain
    every morning.
    """
    arrived = {
        (batch.feed_id, batch.business_date): batch
        for batch in sorted(batches, key=lambda b: b.started_ts)
    }
    dependencies = graph or DependencyGraph()

    rows = tuple(
        _row(expectation, arrived.get((expectation.feed_id, expectation.business_date)), now)
        for expectation in expectations
    )
    return Board(
        business_date=business_date,
        rows=rows,
        counters=_counters(rows),
        attention=_attention(rows, dependencies),
        freshness=Freshness(as_of=now, reachable=reachable),
    )


def _row(expectation: Expectation, batch: BatchLike | None, now: datetime) -> ArrivalRow:
    condition = expectation.condition(arrived=batch is not None, now=now)
    return ArrivalRow(
        feed_id=expectation.feed_id,
        domain=expectation.domain,
        business_date=expectation.business_date,
        condition=condition,
        due_ts=expectation.due_ts,
        minutes_late=expectation.minutes_late(now),
        batch_id=batch.batch_id if batch else None,
        batch_state=batch.state if batch else None,
    )


def _attention(rows: Sequence[ArrivalRow], graph: DependencyGraph) -> tuple[AttentionItem, ...]:
    """Everything requiring action today, most costly first.

    A RECEIVED row whose batch FAILED belongs here too. The morning question is
    not only "did the file come" — a batch that arrived and broke is the one
    thing worse than a file that did not arrive, because somebody may already
    be reading its half-loaded output.
    """
    items: list[AttentionItem] = []
    for row in rows:
        needs = row.condition.needs_action_today or (
            row.batch_state in {BatchState.FAILED, BatchState.BLOCKED}
        )
        if not needs:
            continue
        score, why = impact_of(row, graph)
        if row.batch_state in {BatchState.FAILED, BatchState.BLOCKED}:
            score += _MISSED_POINTS
            why = f"its batch is {row.batch_state.value.lower()}; {why}"
        items.append(
            AttentionItem(
                feed_id=row.feed_id,
                domain=row.domain,
                headline=row.headline(),
                impact=score,
                why=why,
                citation=row.citation,
                status=row.status,
            )
        )
    # Highest impact first; ties broken by feed id so the order is stable
    # between refreshes — a list that reshuffles under the cursor is a list
    # people stop clicking.
    return tuple(sorted(items, key=lambda item: (-item.impact, item.feed_id)))


def stale_board(previous: Board, *, now: datetime) -> Board:
    """The last known board, marked stale. Never zeros.

    Returned when the control tables cannot be reached. The numbers are the
    ones that were true; what changes is that the screen says so.
    """
    return Board(
        business_date=previous.business_date,
        rows=previous.rows,
        counters=previous.counters,
        attention=previous.attention,
        freshness=Freshness(as_of=previous.freshness.as_of, reachable=False),
    )


def resolve_due(
    *, business_day: datetime, expected_by_local_time: str, offset: timedelta
) -> datetime:
    """When today's delivery is due, in UTC.

    `offset` is supplied by the caller, which owns the timezone database.
    `core/` performs no I/O and imports no `zoneinfo` data files, and — more to
    the point — the offset for `America/New_York` on a given date is a fact
    about that date, not about the string. Taking it as a parameter is what
    keeps `ServiceLevel`'s own warning honest: an offset baked in here would be
    wrong for half the year.
    """
    hour, minute = (int(part) for part in expected_by_local_time.split(":"))
    local_midnight = business_day.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight + timedelta(hours=hour, minutes=minute) - offset


def domains(board: Board) -> tuple[str, ...]:
    """Every domain on the board, for the filter control."""
    seen: Mapping[str, None] = {row.domain: None for row in board.rows}
    return tuple(sorted(seen))
