"""CF-V2-E12-01 / E12-05 — what should have arrived by now.

    "Given twelve feeds expected today, ten received, one missing, one at
     risk, when an operator opens the home page, then the counters read
     12/10/1/1, the missing UHC file leads the attention list with
     'expected 6:00 AM — not received'."
    — CF-V2-E12-01

    sla_instance — Per-cycle SLA tracking: expected vs actual, with computed
    status.  UNIQUE (feed_id, cycle_date).  sla_status IN
    ('On-Time','Delayed','Breached')
    — core/schema_spec, the control plane, declared in Wave 0 and unwritten
      until now

THIS MODULE IS THE MISSING WRITER. Wave 0 declared eleven control tables and
wrote eight; `feed_sla_config`, `sla_instance` and `sla_alerts` were provisioned
and left empty, recorded honestly as three xfail(strict=True) tests. Five
Wave-2 stories all ask one question in different words — *what should have
happened by now?* — so the clock is built once, here, and the screens become
queries over it.

PURE BY CONSTRUCTION. Nothing here opens a connection or reads a clock it was
not given: `now` is always a parameter. That is what makes "the file was two
minutes late" testable without freezing time globally, and what lets the whole
SLA surface be examined in milliseconds.

THE ONE SUBTLETY WORTH THE READING TIME — three timestamps, not two:

    cycle_date      the business period the delivery belongs to
    expected_ts     when the payer said it would arrive
    deadline_ts     expected_ts + grace, after which we are entitled to shout

A feed that arrives at expected_ts + 3 minutes with a 30-minute grace is
On-Time, not Delayed. Collapsing grace into expected_ts is how a platform
manufactures its own alert fatigue on day one.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import StatusWord


class SlaError(ValueError):
    """A schedule that cannot be turned into expectations."""


@unique
class SlaStatus(StrEnum):
    """The three the control table's CHECK constraint permits. No fourth.

    These are INTERNAL. `StatusWord` is what a person sees — the mapping is
    `user_status` below, and it exists so the seven-word lexicon test has one
    place to look rather than seven screens.
    """

    ON_TIME = "On-Time"
    DELAYED = "Delayed"
    BREACHED = "Breached"


@unique
class AlertSeverity(StrEnum):
    """Matches `sla_alerts.severity IN ('info','warning','critical')`."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ── the schedule ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Schedule:
    """A feed's delivery contract, as the registry holds it.

    `cron` is the five-field expression already stored on `feed_sla_config`.
    We evaluate it with `croniter` at the ADAPTER, never here — this type
    receives the occurrences it is asked about, because a core module that
    imports a cron library is a core module that has started doing I/O's
    cousin: interpreting the outside world's formats.
    """

    feed_id: str
    feed_version: int
    domain: str
    cron: str
    grace: timedelta = timedelta(minutes=30)
    expected_file_count: int = 1
    #: Business calendar. A monthly roster due on the 1st does not become
    #: Breached because the 1st was a Sunday — the payer never works Sundays.
    skip_weekends: bool = False
    holidays: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        if self.grace < timedelta(0):
            raise SlaError(f"{self.feed_id}: a negative grace period is not a schedule")
        if self.expected_file_count < 1:
            raise SlaError(f"{self.feed_id}: a feed that expects no files is not a feed")

    def observed(self, moment: datetime) -> bool:
        """Is this a day the payer actually delivers?"""
        if self.skip_weekends and moment.weekday() >= 5:
            return False
        return moment.date() not in self.holidays


# ── one expectation ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cycle:
    """One delivery the platform is owed, and what became of it.

    A row of `sla_instance`, before it is a row. `actual_ts` is written by the
    PIPELINE when the file registers — never by the clock, which is why
    materialising a cycle twice cannot erase an arrival.
    """

    feed_id: str
    cycle_date: date
    expected_ts: datetime
    grace: timedelta = timedelta(minutes=30)
    actual_ts: datetime | None = None
    batch_id: str | None = None
    files_received: int = 0
    files_expected: int = 1

    def __post_init__(self) -> None:
        if self.expected_ts.tzinfo is None:
            raise SlaError(f"{self.feed_id}: expected_ts must be timezone-aware (store UTC)")
        if self.actual_ts is not None and self.actual_ts.tzinfo is None:
            raise SlaError(f"{self.feed_id}: actual_ts must be timezone-aware (store UTC)")

    @property
    def deadline_ts(self) -> datetime:
        """When we are entitled to shout. Not the same as when we expected it."""
        return self.expected_ts + self.grace

    @property
    def complete(self) -> bool:
        return self.files_received >= self.files_expected

    def status(self, now: datetime) -> SlaStatus:
        """The three-valued verdict the control table stores.

        Note the order of the tests: a delivery that ARRIVED is judged on when
        it arrived, and only a delivery that has NOT arrived is judged against
        the wall clock. Reversing those two makes a late-but-received file flip
        back to Breached every time the page refreshes.
        """
        if self.actual_ts is not None and self.complete:
            if self.actual_ts <= self.deadline_ts:
                return SlaStatus.ON_TIME
            return SlaStatus.BREACHED
        if now > self.deadline_ts:
            return SlaStatus.BREACHED
        if now > self.expected_ts:
            return SlaStatus.DELAYED
        return SlaStatus.ON_TIME

    def user_status(self, now: datetime) -> StatusWord:
        """The word a PERSON sees. One mapping, in core, for the lexicon test.

        `Missing` and `Needs Attention` are not synonyms and the difference is
        operationally real: a Breached delivery that never arrived is Missing —
        go and chase the payer. A Breached delivery that arrived late and is
        now failing its batch is Needs Attention — go and look at the batch.
        """
        if self.actual_ts is None:
            if now > self.deadline_ts:
                return StatusWord.MISSING
            return StatusWord.EXPECTED
        if not self.complete:
            # Some files of a multi-file set landed. Partial is not arrival.
            return StatusWord.NEEDS_ATTENTION if now > self.deadline_ts else StatusWord.RECEIVED
        if self.batch_id is None:
            return StatusWord.RECEIVED
        return StatusWord.PROCESSING

    @property
    def lateness(self) -> timedelta | None:
        """How late, against the DEADLINE. None if it has not arrived."""
        if self.actual_ts is None:
            return None
        late = self.actual_ts - self.deadline_ts
        return late if late > timedelta(0) else timedelta(0)

    @property
    def citation(self) -> CitationId:
        """`feed:<id>` — a cycle is an observation of a feed, and the feed row
        is what a person wants opened when they click the counter."""
        return CitationId(kind=CitationKind.FEED, subject=self.feed_id)

    def why(self, now: datetime) -> str:
        """The attention-list sentence. The story quotes it verbatim:
        'expected 6:00 AM — not received'."""
        stamp = self.expected_ts.strftime("%-I:%M %p")
        if self.actual_ts is None:
            return f"expected {stamp} — not received"
        if not self.complete:
            missing = self.files_expected - self.files_received
            return f"expected {stamp} — {missing} of {self.files_expected} files still missing"
        late = self.lateness
        if late:
            return f"expected {stamp} — arrived {_human(late)} late"
        return f"expected {stamp} — received"


# ── the board ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArrivalBoard:
    """`CF-V2-E12-01`'s counters, computed once from the cycles.

    The story requires the page to load in under two seconds and to show
    nothing that cannot be traced to a control-table query. Computing the
    counters HERE, from the same objects the attention list is built from,
    is what makes the two provably agree — a counter derived by one query and
    a list by another is how a dashboard says 1 missing and shows none.
    """

    cycles: tuple[Cycle, ...]
    now: datetime

    @property
    def expected(self) -> int:
        return len(self.cycles)

    @property
    def received(self) -> int:
        return sum(1 for c in self.cycles if c.actual_ts is not None and c.complete)

    @property
    def missing(self) -> int:
        return sum(1 for c in self.cycles if c.user_status(self.now) is StatusWord.MISSING)

    @property
    def at_risk(self) -> int:
        """Not yet Missing, but past its expected time. This is the counter the
        morning routine is actually for — the one that lets somebody act
        BEFORE the deadline rather than explain afterwards."""
        return sum(
            1
            for c in self.cycles
            if c.actual_ts is None and c.status(self.now) is SlaStatus.DELAYED
        )

    def counters(self) -> dict[str, int]:
        return {
            "expected": self.expected,
            "received": self.received,
            "missing": self.missing,
            "at_risk": self.at_risk,
        }

    def needs_attention(self, *, harm: dict[str, int] | None = None) -> tuple[Cycle, ...]:
        """The attention list, ranked by BUSINESS IMPACT, not by timestamp.

            "Rank the attention list by business impact, not by timestamp."
            — CF-V2-E12-01

        `harm` is downstream-consumer count per feed, computed from the impact
        graph (`core/impact`) — the same lineage walk `CF-V1-E11-02` already
        uses for approval packets. Passed in rather than imported so this stays
        a pure ranking over data somebody else gathered.

        Ties break on lateness, then feed_id, so the order is STABLE. An
        attention list that reshuffles between refreshes is one nobody can say
        "the third one" about.
        """
        weight = harm or {}
        flagged = [
            c
            for c in self.cycles
            if c.user_status(self.now) in {StatusWord.MISSING, StatusWord.NEEDS_ATTENTION}
            or (c.actual_ts is None and c.status(self.now) is SlaStatus.DELAYED)
        ]
        return tuple(
            sorted(
                flagged,
                key=lambda c: (
                    -weight.get(c.feed_id, 0),
                    -(self.now - c.deadline_ts).total_seconds(),
                    c.feed_id,
                ),
            )
        )


# ── materialising cycles ─────────────────────────────────────────────────────


def cycles_for(
    schedule: Schedule,
    occurrences: Sequence[datetime],
    *,
    known: Sequence[Cycle] = (),
) -> tuple[Cycle, ...]:
    """Turn cron occurrences into the cycles a feed owes, skipping non-days.

    `occurrences` come from the adapter's croniter walk. `known` are the cycles
    already in `sla_instance` — returned UNCHANGED, because the clock never
    overwrites an arrival. That is the whole of the idempotency story: the
    worker can run every minute, restart mid-run, or be replayed by a chaos
    test, and the second pass is a no-op.
    """
    existing = {c.cycle_date: c for c in known}
    out: list[Cycle] = []
    for moment in occurrences:
        if moment.tzinfo is None:
            raise SlaError(f"{schedule.feed_id}: occurrence {moment!r} is not timezone-aware")
        if not schedule.observed(moment):
            continue
        cycle_date = moment.date()
        if cycle_date in existing:
            out.append(existing[cycle_date])
            continue
        out.append(
            Cycle(
                feed_id=schedule.feed_id,
                cycle_date=cycle_date,
                expected_ts=moment,
                grace=schedule.grace,
                files_expected=schedule.expected_file_count,
            )
        )
    return tuple(out)


# ── alerts ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlaAlert:
    """A row of `sla_alerts`, before it is a row.

    `citations` is not decoration: `CF-V2-E12-05` requires every cause
    hypothesis to be grounded and cited, and an alert whose facts cannot be
    opened is the context-free alert the story exists to abolish.
    """

    feed_id: str
    cycle_date: date
    severity: AlertSeverity
    summary: str
    citations: tuple[CitationId, ...] = field(default_factory=tuple)
    group_key: str = ""

    @property
    def cited(self) -> bool:
        return bool(self.citations)


def alerts_for(cycles: Sequence[Cycle], now: datetime) -> tuple[SlaAlert, ...]:
    """The DETERMINISTIC alert set. No model, no narrative — facts only.

    `CF-V2-E12-05` enriches these; it does not create them. Keeping creation
    deterministic means the kill switch degrades enrichment to R0 and alerting
    keeps working exactly as it does today, which is the guarantee ADR-0013
    trades on.

    GROUPING IS DETERMINISTIC TOO, and it matters more than it looks. The
    incident library's second structural lesson: five ADT feeds missing the
    SAME cycle boundary with IDENTICAL expected times, all arriving through
    Mirth, is ONE upstream fault, not five feed incidents. The group key is
    (cycle_date, expected time-of-day) — computable, and it is what lets the
    enrichment agent say "identical window, likely a shared upstream fault"
    with evidence rather than with a hunch.
    """
    out: list[SlaAlert] = []
    for cycle in cycles:
        status = cycle.status(now)
        if status is SlaStatus.ON_TIME:
            continue
        if cycle.actual_ts is not None and cycle.complete:
            severity = AlertSeverity.WARNING
            summary = f"{cycle.feed_id}: arrived {_human(cycle.lateness or timedelta(0))} late"
        elif status is SlaStatus.BREACHED:
            severity = AlertSeverity.CRITICAL
            summary = f"{cycle.feed_id}: {cycle.why(now)}"
        else:
            severity = AlertSeverity.WARNING
            summary = f"{cycle.feed_id}: {cycle.why(now)}"
        out.append(
            SlaAlert(
                feed_id=cycle.feed_id,
                cycle_date=cycle.cycle_date,
                severity=severity,
                summary=summary,
                citations=(cycle.citation,),
                group_key=_group_key(cycle),
            )
        )
    return tuple(out)


def grouped(alerts: Sequence[SlaAlert]) -> Iterator[tuple[str, tuple[SlaAlert, ...]]]:
    """Alerts sharing a window, largest group first.

    A group of one is still a group — the caller decides whether to render it
    as a grouped alert. Yielding singletons keeps the caller free of a special
    case, which is where the "five feeds, one alert" behaviour usually breaks.
    """
    buckets: dict[str, list[SlaAlert]] = {}
    for alert in alerts:
        buckets.setdefault(alert.group_key, []).append(alert)
    for key, members in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        yield key, tuple(sorted(members, key=lambda a: a.feed_id))


def _group_key(cycle: Cycle) -> str:
    return f"{cycle.cycle_date.isoformat()}T{cycle.expected_ts.strftime('%H:%M')}"


def _human(span: timedelta) -> str:
    """'2h 14m', '18 minutes', '40 seconds'. Never '0:18:00'."""
    total = int(span.total_seconds())
    if total < 60:
        return f"{total} seconds"
    minutes, _ = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes} minutes"
