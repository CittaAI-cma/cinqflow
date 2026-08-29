"""The wire shapes. The OpenAPI document generated from these IS the UI's contract.

    "types generated from OpenAPI — no hand-written client"
    — the Wave-0 plan, §2

Which is why these models are deliberate rather than dumped dataclasses: every
figure the UI renders is wrapped with its `citation_id`, so an uncited number
cannot reach a screen. That is enforced here, at the boundary, rather than by a
convention the frontend is asked to remember.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cinqflow.core.citations import CitationId
from cinqflow.core.model.vocabulary import ActorType, StatusWord


class Cited(BaseModel):
    """A value and where it came from. The UI's `<Cited>` primitive, on the wire.

    `citation_id` is not decoration: it parses to a route, so every figure is
    clickable and every claim is openable. `route` travels with it so the
    browser never has to re-implement the parser.
    """

    value: str | int | float | None
    citation_id: str
    route: str

    @classmethod
    def of(cls, value: str | int | float | None, citation: CitationId) -> Cited:
        return cls(value=value, citation_id=str(citation), route=citation.route)


class HomeSlotOut(BaseModel):
    """One card on the persona-shaped home, and the question it answers.

    The RANK is the order of the list — which is the ranking half of ADR-0020's
    merge rule, decided in core/persona.py and never in the browser.
    """

    key: str
    answers: str


class PrincipalOut(BaseModel):
    """Who the caller is, and what the UI may therefore offer.

    `permitted_actions` is sent so the UI can hide what the server would refuse.
    Hiding is a courtesy; the refusal is the control, and it lives on the route.

    `home_slots` is the ordered persona home. It is sent rather than derived in
    the UI so that "what an Engineer sees first" is a server fact with a test,
    not a ternary in a page component.
    """

    subject: str
    display_name: str
    roles: list[str]
    has_access: bool
    permitted_actions: list[str]
    home_slots: list[HomeSlotOut] = []


class FeedOut(BaseModel):
    feed_id: str
    domain: str
    source_system: str
    file_format: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    version: int
    lifecycle_state: str
    status: StatusWord = Field(
        description="One of the seven words. There is no eighth, and no synonym."
    )
    citation_id: str
    route: str


class FeedIn(BaseModel):
    """A feed as a Business Analyst states it — six fields plus a real sample.

    `sample_filename` is required on creation because the pattern is validated
    against a real filename BEFORE save; a pattern nobody tested is the defect
    this story exists to prevent (incident #1, the leading underscore).
    """

    feed_id: str
    domain: str
    source_system: str
    file_format: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    sample_filename: str
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    allows_leading_underscore: bool = True


class AuditOut(BaseModel):
    object_type: str
    object_id: str
    version: int
    action: str
    actor_subject: str
    actor_type: ActorType = Field(
        description="human, system or ai — recorded, never inferred from the route"
    )
    occurred_ts: datetime
    detail: str


class UnknownOut(BaseModel):
    question: str
    owner: str
    blocks: bool


class ContractOut(BaseModel):
    story_id: str
    reads: list[str]
    writes: list[str]
    unknowns: list[UnknownOut]


class GovernedOut(BaseModel):
    """Any governed object, as the lifecycle sees it — one shape for all ten
    types, because there is one state machine (ADR-0006). Type-specific detail
    stays in `body`; everything governance needs is a first-class field."""

    object_type: str
    object_id: str
    version: int
    lifecycle_state: str
    status: StatusWord
    created_by_subject: str
    created_by_name: str
    created_ts: datetime
    approved_by_subject: str | None = None
    approved_by_name: str | None = None
    approved_ts: datetime | None = None
    body: dict[str, Any] = {}


class TransitionIn(BaseModel):
    """A governance act's payload. `comment` is optional on submit/approve and
    REQUIRED on request-changes and reject — enforced by the lifecycle engine,
    not by this schema, so a second client cannot relax it."""

    comment: str = ""


class WorkQueueOut(BaseModel):
    """Everything awaiting one person, across every object type — the proof
    there is ONE lifecycle. `awaiting_my_review` never contains the caller's
    own work: the queue does not offer what the engine would refuse."""

    awaiting_my_review: list[GovernedOut]
    my_submissions: list[GovernedOut]


class ProblemOut(BaseModel):
    """A refusal that explains itself.

    Every "don't" in this platform is a negative test that makes the attempt;
    the response it asserts against needs to say WHY, or the test is asserting
    on a status code and the user is left guessing.
    """

    detail: str


# ── the intelligence plane, on the wire ──────────────────────────────────────


class ClaimOut(BaseModel):
    """One sentence, and the rows it came from.

    `citation_ids` is never empty on a claim that reaches here — the agent
    drops uncited claims before the API sees them, so the UI can render a
    citation chip unconditionally rather than guarding for its absence.
    """

    text: str
    citation_ids: list[str]
    routes: list[str]


class AskIn(BaseModel):
    question: str


class TraceStepOut(BaseModel):
    """One node of the graph. This is the UI's HOW I GOT THERE panel."""

    node: str
    duration_ms: int


class AskOut(BaseModel):
    claims: list[ClaimOut]
    confidence: str
    unanswered: list[str]
    intent: str
    tools_called: list[str]
    trace: list[TraceStepOut]
    cost_usd: str
    refused: bool
    refusal: str
    run_id: str


class AgentActionOut(BaseModel):
    """A row of audit.agent_action — including the refusals.

    Refusals are the half people leave out, and they are what the LLM
    Observability screen exists to show: a governed AI layer nobody can see is
    an ungoverned AI layer with paperwork.
    """

    run_id: str
    agent: str
    action: str
    outcome: str
    is_refusal: bool
    actor_subject: str
    actor_type: ActorType
    risk_class: str
    prompt_ref: str
    prompt_hash: str
    model: str
    model_version: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str
    latency_ms: int
    occurred_ts: datetime
    detail: str


class ToolOut(BaseModel):
    name: str
    answers: str
    reads: list[str]
    cites: list[str]
    parameters: list[str]
    note: str


class BudgetOut(BaseModel):
    agent: str
    spent_today_usd: str
    daily_cap_usd: str
    per_run_cap_usd: str
    runs_today: int
    refusals_today: int
    grounded_claims: int
    uncited_claims_blocked: int


class BatchOut(BaseModel):
    batch_id: str
    feed_id: str
    business_date: str
    state: str
    status: StatusWord
    started_ts: datetime | None = None
    completed_ts: datetime | None = None
    citation_id: str
    route: str


class RowsOut(BaseModel):
    """A tool result, as the UI consumes it.

    Deliberately the SAME shape the agent gets. One projection, so a figure on
    a screen and a figure in an answer cannot disagree — and the drawer a
    citation opens is rendered from the rows that citation cites.
    """

    tool: str
    rows: list[dict[str, Any]]
    citations: list[str]
    row_count: int
    out_of_scope: bool
    marker: str
    note: str


class DestinationOut(BaseModel):
    key: str
    label: str
    route: str
    group: str
    answers: str
    prominent: bool


class NavigationOut(BaseModel):
    """The nav, generated from the wave-activation manifest.

    Wave-1 destinations are ABSENT, not disabled. A greyed-out menu item is a
    promise the build cannot keep.
    """

    active_wave: int
    destinations: list[DestinationOut]
