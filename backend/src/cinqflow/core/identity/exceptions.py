"""CF-V3-E9-02 — one queue, deduplicated by person, aged and assignable.

    "one queue holding every identity exception — failed calls,
     retry-exhausted requests, low-confidence matches, unresolved records —
     triaged, deduplicated, aged and assignable, so that identity problems,
     the highest-stakes error class in a PHI platform, get owners and
     deadlines instead of the ad-hoc handling the incumbent design left them
     with."
    — CF-V3-E9-02

THE DEDUPE KEY IS A PERSON, NEVER A BATCH. `ops.incident_event` keys to
`batch_id + signature` because an incident IS a batch's failure; an identity
exception OUTLIVES the batch that first raised it — "the same person failing
in three batches is one exception with three occurrences, not three items" is
the story's own words. `exception_key(source_system, source_member_id)` is
that identity, and it is the ONLY thing `merge_occurrence` folds on.

AGING MEASURES FROM THE FIRST OCCURRENCE, NEVER THE LATEST. A third failure
must make an exception look WORSE, not younger — resetting the clock on every
new occurrence would let a chronically-failing record's SLA never breach.

AUTO-RESOLVE IS STRUCTURALLY ABSENT, NOT JUST UNCALLED. `merge_occurrence` —
the function every batch ingestion calls — has no path to `ExceptionState.
RESOLVED` in its own code; only `resolve()`, a distinct function nothing in
this module calls internally, can produce that state. "This queue prepares
decisions; the next story governs making them" is a fact about which
functions exist, not a convention someone could forget.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum, unique

from cinqflow.core.identity import MatchOutcome


class IdentityExceptionError(RuntimeError):
    """The identity exception queue refused something."""


@unique
class ExceptionState(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


def exception_key(source_system: str, source_member_id: str) -> str:
    """The dedupe identity: a PERSON, never a batch."""
    return f"{source_system}:{source_member_id}"


@dataclass(frozen=True)
class ExceptionOccurrence:
    """One batch's contribution to an exception. Never merged away — the
    occurrence COUNT is exactly what tells a steward a payer's data quality
    is getting worse, not better."""

    batch_id: str
    outcome: MatchOutcome
    occurred_ts: datetime
    detail: str = ""


@dataclass(frozen=True)
class IdentityException:
    """Current state of one person's identity problem."""

    source_system: str
    source_member_id: str
    state: ExceptionState
    occurrences: tuple[ExceptionOccurrence, ...]
    assigned_to: str | None = None

    def __post_init__(self) -> None:
        if not self.occurrences:
            raise IdentityExceptionError(
                f"{self.key}: an exception with no occurrences never happened"
            )

    @property
    def key(self) -> str:
        return exception_key(self.source_system, self.source_member_id)

    @property
    def opened_ts(self) -> datetime:
        """The FIRST occurrence — aging's anchor. Never the latest."""
        return min(o.occurred_ts for o in self.occurrences)

    @property
    def latest_ts(self) -> datetime:
        return max(o.occurred_ts for o in self.occurrences)

    @property
    def occurrence_count(self) -> int:
        return len(self.occurrences)

    def age(self, *, now: datetime) -> timedelta:
        return now - self.opened_ts

    def is_breached(self, *, sla: timedelta, now: datetime) -> bool:
        """A resolved exception is never breached, at any age — the SLA
        measures how long a PROBLEM has stood open, not how old the record is."""
        if self.state is ExceptionState.RESOLVED:
            return False
        return self.age(now=now) > sla


def merge_occurrence(
    existing: IdentityException | None,
    occurrence: ExceptionOccurrence,
    *,
    source_system: str,
    source_member_id: str,
) -> IdentityException:
    """Fold one more batch's failure into the SAME exception, never a new
    item. `existing=None` is the ONLY path that opens a fresh exception, and
    it always opens OPEN with exactly one occurrence.

    This function has no branch that produces `ExceptionState.RESOLVED` —
    see the module docstring on why that is structural, not incidental.
    """
    if existing is None:
        return IdentityException(
            source_system=source_system,
            source_member_id=source_member_id,
            state=ExceptionState.OPEN,
            occurrences=(occurrence,),
        )
    return replace(existing, occurrences=(*existing.occurrences, occurrence))


def assign(exception: IdentityException, *, to: str) -> IdentityException:
    if exception.state is ExceptionState.RESOLVED:
        raise IdentityExceptionError(f"{exception.key} is resolved; nothing to assign")
    return replace(exception, state=ExceptionState.ASSIGNED, assigned_to=to)


def escalate_if_breached(
    exceptions: Sequence[IdentityException], *, sla: timedelta, now: datetime
) -> tuple[IdentityException, ...]:
    """The daily evaluation: every exception past its SLA and not already
    resolved moves to ESCALATED. Everything else returns unchanged — this is
    a full pass over the queue, not a partial update the caller has to merge."""
    return tuple(
        replace(exc, state=ExceptionState.ESCALATED)
        if exc.is_breached(sla=sla, now=now) and exc.state is not ExceptionState.ESCALATED
        else exc
        for exc in exceptions
    )


def resolve(exception: IdentityException) -> IdentityException:
    """The steward's own action. Nothing in this module calls this
    internally — see `merge_occurrence`."""
    return replace(exception, state=ExceptionState.RESOLVED)


@dataclass(frozen=True)
class QueueHealth:
    """Volume, aging and resolution rate, per source — "a payer sending bad
    demographics becomes visible" only if the number is never rolled up
    across every payer first."""

    source_system: str
    open_count: int
    breached_count: int
    resolved_count: int


def health_by_source(
    exceptions: Sequence[IdentityException], *, sla: timedelta, now: datetime
) -> tuple[QueueHealth, ...]:
    by_source: dict[str, list[IdentityException]] = {}
    for exc in exceptions:
        by_source.setdefault(exc.source_system, []).append(exc)

    return tuple(
        QueueHealth(
            source_system=source,
            open_count=sum(1 for e in group if e.state is not ExceptionState.RESOLVED),
            breached_count=sum(1 for e in group if e.is_breached(sla=sla, now=now)),
            resolved_count=sum(1 for e in group if e.state is ExceptionState.RESOLVED),
        )
        for source, group in sorted(by_source.items())
    )


@unique
class ExceptionEventAction(StrEnum):
    """`identity_exception_event.action` — the ledger's own closed vocabulary.

    OCCURRENCE never changes `state`; the other three are pure transitions
    and carry no occurrence. A row is always exactly one or the other, never
    both — see `IdentityExceptionEvent.__post_init__`.
    """

    OCCURRENCE = "occurrence"
    ASSIGNED = "assigned"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class IdentityExceptionEvent:
    """One row of `identity_exception_event` — a DELTA, never a snapshot.

    This is the one place this module departs from `ops.incident_event`'s
    idiom, deliberately: an incident's ledger row carries the WHOLE state
    because there is only ever one current value to overwrite. An exception's
    `occurrences` tuple only grows, and a schema column cannot hold a tuple
    that grows one row at a time — so the row records what CHANGED, and
    `fold()` is what turns the sequence of changes back into the current
    `IdentityException`, the same relationship `git log` has to a checkout.

    `detail` is overloaded by convention, per `action`: the assignee's subject
    for ASSIGNED, a free-text reason for ESCALATED/RESOLVED, and the
    occurrence's own detail for OCCURRENCE — one column, because the schema
    declares one, and a second column used only three-quarters of the time is
    not simpler than a documented convention on the one that exists.
    """

    event_id: str
    exception_key: str
    action: ExceptionEventAction
    source_system: str
    source_member_id: str
    occurred_ts: datetime
    batch_id: str | None = None
    outcome: MatchOutcome | None = None
    actor_subject: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if exception_key(self.source_system, self.source_member_id) != self.exception_key:
            raise IdentityExceptionError(
                f"{self.event_id}: exception_key does not match its own source_system/"
                "source_member_id — an event that disagrees with itself cannot be folded"
            )
        if self.action is ExceptionEventAction.OCCURRENCE:
            if self.batch_id is None or self.outcome is None:
                raise IdentityExceptionError(
                    f"{self.event_id}: an occurrence with no batch_id or outcome recorded "
                    "nothing that happened"
                )
        elif self.batch_id is not None or self.outcome is not None:
            raise IdentityExceptionError(
                f"{self.event_id}: a {self.action.value} event is a pure state transition — "
                "it carries no batch_id or outcome, an occurrence does"
            )


def fold(events: Sequence[IdentityExceptionEvent]) -> IdentityException:
    """Rebuild the CURRENT `IdentityException` from every event ever recorded
    against one `exception_key`, replayed oldest first.

    `identity_exception`'s current row IS this computation, cached for the
    queue screen's filters — never a second source of truth answering a
    question the ledger disagrees with. Every transition here reuses the
    SAME pure function a live caller would (`merge_occurrence`, `assign`,
    `resolve`) — folding history is not a parallel implementation of the
    rules, it is the rules, replayed.
    """
    if not events:
        raise IdentityExceptionError("fold() of no events rebuilds nothing")
    ordered = sorted(events, key=lambda e: e.occurred_ts)
    key = ordered[0].exception_key
    state: IdentityException | None = None
    for event in ordered:
        if event.exception_key != key:
            raise IdentityExceptionError(
                f"fold() received events for both {key} and {event.exception_key} — "
                "one call folds one exception"
            )
        if event.action is ExceptionEventAction.OCCURRENCE:
            assert event.batch_id is not None and event.outcome is not None  # __post_init__
            state = merge_occurrence(
                state,
                ExceptionOccurrence(
                    batch_id=event.batch_id,
                    outcome=event.outcome,
                    occurred_ts=event.occurred_ts,
                    detail=event.detail,
                ),
                source_system=event.source_system,
                source_member_id=event.source_member_id,
            )
            continue
        if state is None:
            raise IdentityExceptionError(
                f"{key}: a {event.action.value} event with no prior occurrence — nothing "
                "opened this exception yet"
            )
        if event.action is ExceptionEventAction.ASSIGNED:
            state = assign(state, to=event.detail)
        elif event.action is ExceptionEventAction.ESCALATED:
            state = replace(state, state=ExceptionState.ESCALATED)
        elif event.action is ExceptionEventAction.RESOLVED:
            state = resolve(state)
    assert state is not None
    return state
