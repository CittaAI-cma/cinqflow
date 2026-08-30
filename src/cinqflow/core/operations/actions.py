"""CF-V2-E12-03 — act exactly where you watch, and prove it worked.

    "I want to act exactly where I watch: acknowledge and assign issues, add
     resolution notes, pause/resume a feed, retry a failed operation — with
     production actions requiring an approval identifier, so that operations
     stop being a relay race between a dashboard, a chat thread and an
     engineer's terminal — see, decide, act, all audited, in one place."
    "Verify and display the action's outcome — 'retry requested' is not 'retry
     succeeded'."
    — CF-V2-E12-03

THE SENTENCE THAT SHAPES THE WHOLE MODULE IS THE SECOND ONE. Every operations
console in this estate's history has had a Retry button that turned green when
the request was accepted, and the number of times somebody walked away from a
retry that then failed is the reason the morning meeting existed. So
`ActionRecord` has no boolean `succeeded`: it has a PHASE, it starts at
REQUESTED, and `is_complete` is False until something OBSERVED the outcome and
called `verify`. A request that nobody verified reads as unfinished forever,
which is what it is.

THE ALLOWED-STATE MATRIX IS DATA, AND `offered` IS ITS ONLY CONSUMER. Archetype
E's recipe opens with it for a reason: a console that draws a button and then
refuses it teaches people that refusals are noise. `offered` returns exactly
the actions `authorize` would permit, so the screen cannot show one the surface
would decline — the same discipline `core.lifecycle.awaiting_review_by` uses,
where the queue never offers work the engine would refuse.

THERE IS NO COMMAND FIELD, AND A TEST ASSERTS THERE IS NOT. "Offer free-form
commands or raw SQL anywhere" is the don't, and the way to mean it is to make
an action a closed enum with typed parameters — so there is no string on any
type here that an executor could interpret. `core.mapping` refuses an
`expression` for the same reason and `core.rules` refuses a `sql`; this is that
rule applied to operations.

REFUSALS ARE OUTCOMES, NOT EXCEPTIONS THAT VANISH. A breaker-open refusal has
to reach a person and leave a row — the guardrail says so in as many words —
so `authorize` raises a `RefusedError` carrying a `Refusal`, and the caller
persists it exactly as it persists a success. An operations surface that logged
its successes and swallowed its refusals would be one where "I clicked retry
and nothing happened" is unanswerable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer, StatusWord


class ActionError(RuntimeError):
    """An operations action that will not happen."""


class RefusedError(ActionError):
    """The surface declined. Carries the `Refusal` so the caller can record it.

    Raised rather than returned because every call site must handle it; a
    refusal returned as a value is a refusal somebody eventually forgets to
    check, and the one that gets forgotten is the breaker.
    """

    def __init__(self, refusal: Refusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.explain())


# ── where this is running ────────────────────────────────────────────────────
@unique
class Environment(StrEnum):
    """Development or production, as far as approvals are concerned.

    Two, not five. The question this answers is "does this action need an
    approval identifier?", and every environment where real member data can
    exist answers yes. Resolved by the CALLER from the connection profile —
    Law 3 puts all environment difference there, so a module that sniffed its
    own environment would be the first thing to break that.
    """

    DEVELOPMENT = "development"
    PRODUCTION = "production"

    @property
    def requires_approval_identifier(self) -> bool:
        return self is Environment.PRODUCTION


# ── what may be done ─────────────────────────────────────────────────────────
@unique
class OpsAction(StrEnum):
    """The whole vocabulary of what an operator may do from a screen.

    Six, closed, and each one costs a row in `ALLOWED_STATES`, a branch in
    `preview` and a line in the permission matrix. That toll is what stops a
    seventh arriving as a text box.
    """

    #: "I have seen this." Changes nothing about the data.
    ACKNOWLEDGE = "acknowledge"
    #: "This is yours." Changes nothing about the data.
    ASSIGN = "assign"
    #: Resolution context, threaded on the issue.
    NOTE = "note"
    #: CF-V1-E3-04's second axis, reached from the screen that noticed.
    PAUSE = "pause"
    RESUME = "resume"
    #: The one that touches the pipeline.
    RETRY = "retry"

    @property
    def mutates_production(self) -> bool:
        """Whether this changes what the platform DOES, rather than what it
        knows about who is handling something.

        Acknowledging and assigning are bookkeeping. Pausing stops work,
        resuming starts it, retrying runs it — those three need an approval
        identifier in production, and the other three would make the
        requirement meaningless if they did.
        """
        return self in {OpsAction.PAUSE, OpsAction.RESUME, OpsAction.RETRY}

    @property
    def needs_reason(self) -> bool:
        """Every action but acknowledgement says why.

        A pause with no stated reason becomes a mystery nobody dares unpause —
        `core.registry.suspension` learned that already, and it is the same
        lesson for a retry nobody explained.
        """
        return self is not OpsAction.ACKNOWLEDGE


#: THE ALLOWED-STATE MATRIX. Data, so a new action is a row and the
#: completeness test can enumerate them.
#:
#: An empty set means "not gated on batch state" — acknowledging, assigning and
#: noting are things a person does about an issue whatever the batch is doing,
#: and gating them on state would mean an operator could not write down what
#: they found while a batch was still running.
ALLOWED_STATES: dict[OpsAction, frozenset[BatchState]] = {
    OpsAction.ACKNOWLEDGE: frozenset(),
    OpsAction.ASSIGN: frozenset(),
    OpsAction.NOTE: frozenset(),
    OpsAction.PAUSE: frozenset(),
    OpsAction.RESUME: frozenset(),
    # A retry only makes sense on something that stopped. Retrying a running
    # batch is how two writers end up in the same target table, and retrying a
    # completed one is how a month gets loaded twice.
    OpsAction.RETRY: frozenset({BatchState.FAILED, BatchState.BLOCKED}),
}


# ── refusals ─────────────────────────────────────────────────────────────────
@unique
class RefusalReason(StrEnum):
    """Why the surface said no. Each one sends the operator somewhere else."""

    WRONG_STATE = "wrong_state"
    FEED_PAUSED = "feed_paused"
    BREAKER_OPEN = "breaker_open"
    RATE_LIMITED = "rate_limited"
    NO_APPROVAL_IDENTIFIER = "no_approval_identifier"
    NO_REASON_GIVEN = "no_reason_given"
    NOT_A_HUMAN = "not_a_human"

    @property
    def notifies_a_human(self) -> bool:
        """The guardrail: a breaker-open refusal PAGES somebody.

        Only the breaker. A wrong-state refusal is the console being helpful
        and paging on it would train people to ignore the channel — but a
        breaker that is open while somebody is trying to act is a fact an
        engineer needs to know about now.
        """
        return self is RefusalReason.BREAKER_OPEN


@dataclass(frozen=True)
class Refusal:
    """One declined action, recorded. Never silent.

    `citation` points at whatever the operator should read next — the pause
    record for a paused feed, the batch for a wrong-state refusal — because
    "refused" without somewhere to go is where a relay race restarts.
    """

    action: OpsAction
    reason: RefusalReason
    detail: str
    target: str
    citation: CitationId | None = None

    @property
    def notifies(self) -> bool:
        return self.reason.notifies_a_human

    @property
    def route(self) -> str:
        return self.citation.route if self.citation else ""

    def explain(self) -> str:
        return f"{self.action.value} refused on {self.target}: {self.detail}"


@dataclass(frozen=True)
class Breaker:
    """Whether the platform is currently allowed to act on this target.

    A value object supplied by the caller, not managed here: the breaker's
    state is operational fact living beside the control tables, and a module
    that both decided the breaker and consulted it would be able to talk
    itself into acting.
    """

    is_open: bool = False
    reason: str = ""
    citation: CitationId | None = None


@dataclass(frozen=True)
class RateLimit:
    """How often one target may be acted on.

    Retry storms are the documented shape here: a transient cluster error looks
    identical to a permanent one for the first two attempts, and an operator
    with a button will press it. The limit is per TARGET rather than per
    person, because three operators each retrying twice is still six retries at
    the cluster.
    """

    max_actions: int = 3
    window: timedelta = timedelta(minutes=15)

    def exceeded(self, recent: Sequence[datetime], *, now: datetime) -> bool:
        inside = [stamp for stamp in recent if now - stamp <= self.window]
        return len(inside) >= self.max_actions


# ── the request, the preview and the record ──────────────────────────────────
@dataclass(frozen=True)
class ActionRequest:
    """What an operator is asking for.

    NOTE WHAT IS ABSENT: there is no `command`, no `sql`, no `parameters` dict
    an executor could interpret. The action is an enum and everything else is a
    typed field, so this type cannot carry an instruction — which is the don't
    ("offer free-form commands or raw SQL anywhere") made structural.
    """

    action: OpsAction
    target: str
    actor: Actor
    reason: str = ""
    #: The change ticket, in production. A NAME, checked for presence only —
    #: this platform does not own the client's change system and pretending to
    #: validate its identifiers would be a second source of truth about them.
    approval_identifier: str = ""
    #: RETRY only. Where to resume from; None means the last completed stage.
    resume_from: Layer | None = None
    assignee: str = ""
    note: str = ""

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.BATCH, subject=self.target)


@dataclass(frozen=True)
class Preview:
    """What would happen, before it happens.

        "Show a preview of scope and estimated compute before confirmation."
        — the archetype's recipe, and CF-V2-E8-04 inherits this

    `scope` counts what the action touches. An operator who is about to
    reprocess is entitled to know whether that is 200 rows or 22 million BEFORE
    they press the button, because the incumbent platform's answer to that
    question was to find out afterwards.
    """

    action: OpsAction
    target: str
    what_will_happen: str
    scope_records: int = 0
    scope_stages: tuple[Layer, ...] = ()
    estimated_minutes: int = 0
    requires_approval_identifier: bool = False

    def explain(self) -> str:
        parts = [self.what_will_happen]
        if self.scope_records:
            parts.append(f"about {self.scope_records:,} record(s)")
        if self.scope_stages:
            parts.append("stages: " + ", ".join(layer.value for layer in self.scope_stages))
        if self.estimated_minutes:
            parts.append(f"roughly {self.estimated_minutes} minute(s)")
        if self.requires_approval_identifier:
            parts.append("an approval identifier is required")
        return " · ".join(parts)


@unique
class ActionPhase(StrEnum):
    """REQUESTED -> VERIFIED | FAILED. Three, and the first is not success.

    "'retry requested' is not 'retry succeeded'" is the whole reason this is a
    phase rather than a boolean. A record that stops at REQUESTED is one nobody
    checked, and the screen says so rather than showing a green tick.
    """

    REQUESTED = "requested"
    VERIFIED = "verified"
    FAILED = "failed"

    @property
    def status_word(self) -> StatusWord:
        return {
            ActionPhase.REQUESTED: StatusWord.PROCESSING,
            ActionPhase.VERIFIED: StatusWord.COMPLETED,
            ActionPhase.FAILED: StatusWord.NEEDS_ATTENTION,
        }[self]


@dataclass(frozen=True)
class ActionRecord:
    """One action, from request to verified outcome.

    Immutable: `verify` and `fail` return new records, so a screen holding a
    reference to a requested action cannot find it has quietly become a
    successful one.
    """

    action: OpsAction
    target: str
    actor: Actor
    requested_ts: datetime
    reason: str = ""
    approval_identifier: str = ""
    phase: ActionPhase = ActionPhase.REQUESTED
    verified_ts: datetime | None = None
    outcome: str = ""

    @property
    def is_complete(self) -> bool:
        """False until somebody OBSERVED what happened.

        Not `phase is not REQUESTED` — spelled out because this property is
        what a screen renders a tick from, and the tick must mean verified.
        """
        return self.phase is ActionPhase.VERIFIED

    @property
    def status(self) -> StatusWord:
        return self.phase.status_word

    def explain(self) -> str:
        who = self.actor.display_name or self.actor.subject
        match self.phase:
            case ActionPhase.REQUESTED:
                return (
                    f"{who} requested {self.action.value} on {self.target} — "
                    "outcome not yet verified."
                )
            case ActionPhase.VERIFIED:
                return f"{who} {self.action.value}d {self.target}: {self.outcome}"
            case ActionPhase.FAILED:
                return (
                    f"{who} requested {self.action.value} on {self.target} and it did not "
                    f"succeed: {self.outcome}"
                )


def verify(record: ActionRecord, *, outcome: str, now: datetime | None = None) -> ActionRecord:
    """Record what was OBSERVED after the action ran.

    `outcome` is required and must say something. "Succeeded" with no detail is
    the green tick this module exists to refuse — the useful verification is
    "resumed from silver_raw, 9,992 rows loaded", which is a fact somebody
    checked rather than a status somebody assumed.
    """
    if record.phase is not ActionPhase.REQUESTED:
        raise ActionError(
            f"{record.action.value} on {record.target} is already {record.phase.value}"
        )
    if not outcome.strip():
        raise ActionError(
            "a verification must say what was observed. 'Succeeded' with no detail is the "
            "green tick this surface exists to refuse."
        )
    return replace(
        record,
        phase=ActionPhase.VERIFIED,
        verified_ts=now or datetime.now(UTC),
        outcome=outcome.strip(),
    )


def fail(record: ActionRecord, *, outcome: str, now: datetime | None = None) -> ActionRecord:
    """The action ran and did not work. Recorded, not silent."""
    if record.phase is not ActionPhase.REQUESTED:
        raise ActionError(
            f"{record.action.value} on {record.target} is already {record.phase.value}"
        )
    return replace(
        record,
        phase=ActionPhase.FAILED,
        verified_ts=now or datetime.now(UTC),
        outcome=outcome.strip() or "no detail recorded",
    )


def unverified(
    records: Sequence[ActionRecord], *, now: datetime, after: timedelta
) -> tuple[ActionRecord, ...]:
    """Actions requested long enough ago that nobody verifying them is itself a
    finding.

    The measurable is "production actions ... are verified after execution",
    and this is how that is checked rather than assumed: a list that should be
    empty, exposed so the API can answer it too.
    """
    return tuple(
        record
        for record in records
        if record.phase is ActionPhase.REQUESTED and now - record.requested_ts > after
    )


# ── what may be offered, and what may be done ────────────────────────────────
def offered(
    *,
    batch_state: BatchState | None,
    feed_paused: bool = False,
    breaker: Breaker | None = None,
) -> tuple[OpsAction, ...]:
    """Exactly the actions `authorize` would permit right now.

    "Expose only actions the platform defines as safe for the object's current
    state." A console that draws a button and then refuses it teaches people
    that refusals are noise — so this and `authorize` read the same matrix, and
    a test asserts every offered action actually authorizes.
    """
    open_breaker = breaker is not None and breaker.is_open
    available: list[OpsAction] = []
    for action in OpsAction:
        if open_breaker and action.mutates_production:
            continue
        if feed_paused and action in {OpsAction.PAUSE, OpsAction.RETRY}:
            continue
        if not feed_paused and action is OpsAction.RESUME:
            continue
        states = ALLOWED_STATES[action]
        if states and (batch_state is None or batch_state not in states):
            continue
        available.append(action)
    return tuple(available)


def authorize(
    request: ActionRequest,
    *,
    environment: Environment,
    batch_state: BatchState | None = None,
    feed_paused: bool = False,
    paused_reason: str = "",
    pause_citation: CitationId | None = None,
    breaker: Breaker | None = None,
    rate_limit: RateLimit | None = None,
    recent_actions: Sequence[datetime] = (),
    now: datetime | None = None,
) -> None:
    """Refuse, or return. Six gates, ordered by what the operator can act on.

    THE ORDER IS THE MESSAGE. Telling somebody their approval identifier is
    missing when the real problem is that the feed is paused sends them to
    raise a change ticket for an action that would be refused anyway. So the
    state of the world comes first, the caller's own inputs last.
    """
    stamp = now or datetime.now(UTC)

    if request.actor.actor_type is not ActorType.HUMAN:
        _refuse(
            request,
            RefusalReason.NOT_A_HUMAN,
            f"{request.actor.subject} is a {request.actor.actor_type.value} actor. Agents "
            "propose; humans dispose — an operations action is a human act.",
        )

    if breaker is not None and breaker.is_open and request.action.mutates_production:
        _refuse(
            request,
            RefusalReason.BREAKER_OPEN,
            breaker.reason or "the circuit breaker is open for this target",
            citation=breaker.citation,
        )

    if feed_paused and request.action in {OpsAction.PAUSE, OpsAction.RETRY}:
        _refuse(
            request,
            RefusalReason.FEED_PAUSED,
            paused_reason or "this feed is paused",
            citation=pause_citation,
        )

    states = ALLOWED_STATES[request.action]
    if states and (batch_state is None or batch_state not in states):
        allowed = ", ".join(sorted(s.value for s in states))
        seen = batch_state.value if batch_state else "no batch"
        _refuse(
            request,
            RefusalReason.WRONG_STATE,
            f"{request.action.value} applies to a batch that is {allowed}; this one is {seen}.",
        )

    if rate_limit is not None and rate_limit.exceeded(recent_actions, now=stamp):
        _refuse(
            request,
            RefusalReason.RATE_LIMITED,
            f"{rate_limit.max_actions} action(s) already in the last "
            f"{int(rate_limit.window.total_seconds() // 60)} minutes on this target. A "
            "transient error looks identical to a permanent one for the first two "
            "attempts — the limit is what stops the third becoming a storm.",
        )

    if request.action.needs_reason and not request.reason.strip():
        _refuse(
            request,
            RefusalReason.NO_REASON_GIVEN,
            "say why. A pause with no stated reason becomes a mystery nobody dares "
            "unpause, and a retry nobody explained is the same lesson.",
        )

    if (
        environment.requires_approval_identifier
        and request.action.mutates_production
        and not request.approval_identifier.strip()
    ):
        _refuse(
            request,
            RefusalReason.NO_APPROVAL_IDENTIFIER,
            "production actions carry an approval identifier. Attach the change record.",
        )


def _refuse(
    request: ActionRequest,
    reason: RefusalReason,
    detail: str,
    *,
    citation: CitationId | None = None,
) -> None:
    raise RefusedError(
        Refusal(
            action=request.action,
            reason=reason,
            detail=detail,
            target=request.target,
            citation=citation or request.citation,
        )
    )


def request_action(request: ActionRequest, *, now: datetime | None = None) -> ActionRecord:
    """Turn an authorized request into a record awaiting verification.

    Deliberately does NOT authorize. Splitting them means a caller cannot
    accidentally record an action it never checked — `authorize` raises, so a
    call site that skipped it would be visibly missing a line rather than
    silently passing a flag.
    """
    return ActionRecord(
        action=request.action,
        target=request.target,
        actor=request.actor,
        requested_ts=now or datetime.now(UTC),
        reason=request.reason.strip(),
        approval_identifier=request.approval_identifier.strip(),
    )


def preview(
    request: ActionRequest,
    *,
    environment: Environment,
    scope_records: int = 0,
    scope_stages: Sequence[Layer] = (),
    estimated_minutes: int = 0,
) -> Preview:
    """What this would do, in the operator's language, before they confirm."""
    what = {
        OpsAction.ACKNOWLEDGE: f"Mark {request.target} as seen by you.",
        OpsAction.ASSIGN: f"Assign {request.target} to {request.assignee or 'somebody'}.",
        OpsAction.NOTE: f"Add a note to {request.target}.",
        OpsAction.PAUSE: (f"Stop new work on {request.target}. Anything already running finishes."),
        OpsAction.RESUME: f"Allow new work on {request.target} again.",
        OpsAction.RETRY: (
            f"Re-run {request.target} from "
            + (request.resume_from.value if request.resume_from else "its last completed stage")
            + "."
        ),
    }[request.action]
    return Preview(
        action=request.action,
        target=request.target,
        what_will_happen=what,
        scope_records=scope_records,
        scope_stages=tuple(scope_stages),
        estimated_minutes=estimated_minutes,
        requires_approval_identifier=(
            environment.requires_approval_identifier and request.action.mutates_production
        ),
    )


# ── the issue, and the thread that carries a handoff ─────────────────────────
@dataclass(frozen=True)
class ThreadEntry:
    """One thing somebody said or did about an issue."""

    actor: Actor
    occurred_ts: datetime
    action: OpsAction
    text: str = ""

    def render(self) -> str:
        who = self.actor.display_name or self.actor.subject
        stamp = self.occurred_ts.strftime("%Y-%m-%d %H:%M")
        return f"{stamp} — {who} {self.action.value}: {self.text}".rstrip(": ")


@dataclass(frozen=True)
class Issue:
    """One thing needing attention, and everything said about it.

        "Thread notes and assignments on the issue so handoffs carry context."

    The thread is APPEND-ONLY and lives beside the batch rather than in a chat
    tool, which is the whole point of the story: a handoff whose context is in
    a message thread nobody can find is the relay race this replaces.
    """

    issue_id: str
    feed_id: str
    target: str
    opened_ts: datetime
    assignee: str = ""
    acknowledged_by: str = ""
    thread: tuple[ThreadEntry, ...] = field(default_factory=tuple)
    resolved: bool = False

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.BATCH, subject=self.target)

    @property
    def status(self) -> StatusWord:
        if self.resolved:
            return StatusWord.COMPLETED
        return StatusWord.NEEDS_REVIEW if self.acknowledged_by else StatusWord.NEEDS_ATTENTION

    def render(self) -> str:
        head = f"{self.issue_id} ({self.feed_id}) — {self.status.value}"
        if self.assignee:
            head += f", assigned to {self.assignee}"
        return "\n".join([head, *(entry.render() for entry in self.thread)])


def apply_to_issue(issue: Issue, record: ActionRecord, *, text: str = "") -> Issue:
    """Thread one action onto its issue.

    Takes the RECORD rather than the request, so nothing reaches the thread
    that was not authorized and recorded first — the thread is the audit
    narrative a handoff reads, and an entry with no record behind it would be a
    claim rather than a fact.
    """
    entry = ThreadEntry(
        actor=record.actor,
        occurred_ts=record.requested_ts,
        action=record.action,
        text=text or record.reason or record.outcome,
    )
    updates: dict[str, object] = {"thread": (*issue.thread, entry)}
    if record.action is OpsAction.ACKNOWLEDGE:
        updates["acknowledged_by"] = record.actor.subject
    if record.action is OpsAction.ASSIGN and text:
        updates["assignee"] = text
    return replace(issue, **updates)  # type: ignore[arg-type]
