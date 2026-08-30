"""The wire shapes. The OpenAPI document generated from these IS the UI's contract.

    "types generated from OpenAPI — no hand-written client"
    — the Wave-0 plan, §2

Which is why these models are deliberate rather than dumped dataclasses: every
figure the UI renders is wrapped with its `citation_id`, so an uncited number
cannot reach a screen. That is enforced here, at the boundary, rather than by a
convention the frontend is asked to remember.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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


class OwnerModel(BaseModel):
    """A named person who is accountable. CF-V1-E3-02.

    `display_name` is required by the type, not checked in a handler: a feed
    owned by an address is a feed nobody will admit to owning when it breaks.
    """

    role: str
    subject: str
    display_name: str


class ServiceLevelModel(BaseModel):
    """When the file is due. `timezone` is an IANA NAME, never an offset —
    an offset is right for half the year."""

    expected_by_local_time: str
    timezone: str
    calendar: str = "business_days"
    grace_minutes: int = 30
    escalate_after_minutes: int = 120


class VolumeModel(BaseModel):
    """What a normal delivery looks like, so an abnormal one is visible."""

    minimum_records: int | None = None
    maximum_records: int | None = None
    typical_records: int | None = None
    tolerance_percent: int = 20


class AlertTierModel(BaseModel):
    """One rung of the escalation ladder: after how long, and who."""

    after_minutes: int
    channel: str
    notify: list[str] = Field(default_factory=list)


class LinkedDocumentModel(BaseModel):
    """A spec, a companion guide, a runbook. The reference is data — but a
    reference carrying a credential is refused by core."""

    kind: str
    label: str
    reference: str


class OperationsModel(BaseModel):
    """The operational envelope around a feed's six engine fields.

    ONE model in both directions, deliberately: there is no field here a
    client may read and not write, so two models would be two places for the
    same list of fields to drift apart.
    """

    source_id: str = ""
    direction: str = "inbound"
    delivery_method: str = "sftp"
    #: The connection profile's NAME for the endpoint. Never a host — core
    #: refuses anything that looks like a location.
    endpoint_ref: str = ""
    owners: list[OwnerModel] = Field(default_factory=list)
    service_level: ServiceLevelModel | None = None
    volume: VolumeModel | None = None
    alert_chain: list[AlertTierModel] = Field(default_factory=list)
    documents: list[LinkedDocumentModel] = Field(default_factory=list)
    notes: str = ""


class ChecklistItemOut(BaseModel):
    """One thing that must be true before a feed can be operated.

    Three strings, not one. A checklist that says only "owner is required"
    gets `data@company.com` typed into it; `why_it_matters` and `how_to_fix`
    are what make somebody do the real thing instead.
    """

    key: str
    question: str
    satisfied: bool
    why_it_matters: str
    how_to_fix: str


class ReadinessOut(BaseModel):
    """Whether this feed can be activated, and what is missing if not.

    Sent with every feed so the form shows the same checklist the lifecycle
    enforces — one function, one answer, no screen showing green while the
    submit button returns 403.
    """

    feed_id: str
    is_ready: bool
    outstanding: int
    items: list[ChecklistItemOut]
    explanation: str


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
    operations: OperationsModel = Field(default_factory=OperationsModel)
    readiness: ReadinessOut | None = None


class SourceIn(BaseModel):
    """An organisation that sends or receives data. CF-V1-E3-02."""

    source_id: str
    name: str
    kind: str = "payer"
    endpoint_ref: str = ""
    line_of_business: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    owners: list[OwnerModel] = Field(default_factory=list)
    counterparty_contact: str = ""
    notes: str = ""


class SourceOut(SourceIn):
    version: int
    lifecycle_state: str
    status: StatusWord
    feed_ids: list[str] = Field(default_factory=list)


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
    #: CF-V1-E3-02. Optional on save and required for ACTIVATION — a
    #: half-gathered feed must be storable, or an analyst waiting three days
    #: for a payer's SLA has nowhere to keep what they already have.
    operations: OperationsModel | None = None


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
    stays in `body`; everything governance needs is a first-class field.

    `warnings` (CF-V1-W1-28) is `CloneOut`'s own pattern, reused rather than
    reinvented: a fact a caller should know about a side effect of this act
    that is NOT part of the object itself — RUNBOOK publish's knowledge-embed
    step failing to run is the first tenant. `body` is the object's own
    persisted content and is the wrong place for that: stuffing an ephemeral
    operational note into it would make a client reading `body` believe the
    note was part of the runbook. Empty for every act that has no such side
    effect, which today is every act but a RUNBOOK publish.
    """

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
    warnings: list[str] = Field(default_factory=list)


class CanonicalFieldOut(BaseModel):
    """One field of the canonical model, with its definition inline. CF-V1-E6-01.

    `definition_missing` is a first-class boolean rather than something a
    client infers from an empty string: "we have no business definition for
    this column" is a finding a steward acts on, and the wire should say it.
    """

    name: str
    entity: str
    domains: list[str]
    definition: str
    definition_missing: bool
    glossary_id: str | None = None
    term: str = ""
    synonyms: list[str] = Field(default_factory=list)
    is_phi: bool = False
    type: str | None = None
    nullable: bool | None = None
    deployed: bool = False


class CanonicalEntityOut(BaseModel):
    """One entity, and whether it exists yet.

    `deployed` distinguishes "this is in the database" from "the client has
    designed this". Both belong in the browser; conflating them is how a
    mapping gets written against a table nobody has created.
    """

    name: str
    domains: list[str]
    schema_name: str = ""
    deployed: bool = False
    comment: str = ""
    field_count: int = 0
    defined_count: int = 0
    phi_count: int = 0
    fields: list[CanonicalFieldOut] = Field(default_factory=list)


class CanonicalModelOut(BaseModel):
    """Domains → entities → fields, generated from the deployed spec and the
    client's own glossary. There is no third list to drift from."""

    domains: list[str]
    entities: list[CanonicalEntityOut]
    deployed_entities: int
    designed_not_deployed: list[str]
    defined_fields: int
    total_fields: int
    unclaimed_tables: list[str] = Field(default_factory=list)


class PauseFeedIn(BaseModel):
    """Stop new work on a feed. CF-V1-E3-04.

    `reason` is required by the type: somebody will find this paused next week
    and have to decide whether to lift it, and an unexplained pause is one
    nobody dares touch.

    `resumes_after` is optional and strongly preferred — a pause with an end
    lifts itself, and a pause without one is lifted when somebody remembers.
    """

    reason: str = Field(min_length=1)
    resumes_after: datetime | None = None


class ResumeFeedIn(BaseModel):
    """Start the feed again. No reason required, deliberately asymmetric with
    pausing: an operator at 3am with a payer on the phone should be able to
    turn the tap back on and explain afterwards. The row records who did it."""

    reason: str = ""


class SuspensionOut(BaseModel):
    """Whether a feed is paused right now, and what that means."""

    feed_id: str
    is_paused: bool
    reason: str = ""
    paused_by: str | None = None
    paused_ts: datetime | None = None
    resumes_after: datetime | None = None
    may_start_new_work: bool = True
    #: Stated on the wire because it is the half of the acceptance criterion a
    #: reader is most likely to assume the other way round.
    affects_work_already_running: bool = False
    explanation: str = ""


class SuspensionEventOut(BaseModel):
    """One pause or resume, from the append-only ledger."""

    feed_id: str
    action: str
    reason: str
    actor_subject: str
    actor_name: str = ""
    occurred_ts: datetime
    resumes_after: datetime | None = None


class SimilarFeedOut(BaseModel):
    """A feed worth cloning from, and WHY it ranked. CF-V1-E3-03.

    `reasons` travels because a ranked list with no explanation is a ranked
    list somebody scrolls past — and because the score is deterministic
    arithmetic a BA is entitled to check.
    """

    feed_id: str
    score: int
    reasons: list[str]
    lifecycle_state: str
    domain: str
    source_system: str


class CloneFeedIn(BaseModel):
    """Clone this feed's configuration into a new draft. CF-V1-E3-03."""

    new_feed_id: str
    #: Applied over the copied body. `operations` merges one level deep, so a
    #: BA changing the line of business does not restate the owners and the SLA.
    overrides: dict[str, Any] = Field(default_factory=dict)
    #: Which related object types to bring across. Absent means all of them —
    #: the contract, the mappings and the rules, which are the morning's work
    #: somebody already did.
    include: list[str] | None = None


class InheritedOut(BaseModel):
    """One object the clone received, and whether anybody had approved it."""

    object_type: str
    source_object_id: str
    source_version: int
    source_state: str
    new_object_id: str
    was_approved: bool


class DifferenceOut(BaseModel):
    """One field where the clone and its original disagree."""

    object_type: str
    field_path: str
    original: Any = None
    clone: Any = None


class VersionDiffOut(BaseModel):
    """Two versions of one governed object, side by side. CF-V1-E3-04.

    The same `Difference` shape the clone panel uses, so "how does this
    differ" has ONE answer in the platform rather than one per screen.
    """

    object_type: str
    object_id: str
    from_version: int
    to_version: int
    from_state: str
    to_state: str
    from_author: str
    to_author: str
    differences: list[DifferenceOut]


class CloneOut(BaseModel):
    """What the clone produced, and everything a reviewer should know.

    `warnings` is "unapproved inherited parts marked" — a mapping copied from
    a published feed carries somebody's signature behind it, and the same
    mapping copied from a draft carries nobody's.
    """

    feed_id: str
    cloned_from: str
    cloned_from_version: int
    created: list[GovernedOut]
    inherited: list[InheritedOut]
    differences: list[DifferenceOut]
    warnings: list[str]


class TransitionIn(BaseModel):
    """A governance act's payload. `comment` is optional on submit/approve and
    REQUIRED on request-changes and reject — enforced by the lifecycle engine,
    not by this schema, so a second client cannot relax it."""

    comment: str = ""
    #: CF-V1-E6-04. The target fields this approval accepts will stop being
    #: populated. NAMES, not a boolean: a checkbox is clicked, and the failure
    #: this gate prevents is precisely the change nobody looked at. Ignored for
    #: every object type but MAPPING, and for a mapping with no published
    #: predecessor.
    accepts_loss: list[str] = Field(default_factory=list)


class GlossaryTermOut(BaseModel):
    """One approved business definition, as every screen and agent sees it.

    `synonyms` is the payload a mapping suggestion reasons from — the client's
    own analysts recorded that `Member Date of Birth` arrives as `Patient_dob`
    from one payer and `MemberDateOfBirth` from another, and that knowledge is
    the difference between a semantic mapper and a string comparison.
    """

    glossary_id: str
    term: str
    definition: str
    domain_category: str
    sub_category: str
    classification: str
    regulatory_reference: str
    mapped_domains: list[str]
    mapped_tables: list[str]
    synonyms: list[str]
    sensitivity: str
    is_phi: bool
    notes: str
    lifecycle_state: str
    status: StatusWord
    version: int
    citation_id: str
    route: str


class TouchedOut(BaseModel):
    """One object a change would reach, and the reference path that found it —
    so an approver can check the reasoning rather than trust the count."""

    object_type: str
    object_id: str
    version: int
    lifecycle_state: str
    via: str


class UnknownImpactOut(BaseModel):
    """A declared consumer lineage could not resolve. Shown explicitly, and it
    blocks production approval — a blank where a downstream item should be is
    how rubber-stamping hides."""

    name: str
    reason: str


class ReferenceOut(BaseModel):
    """One object that would be affected by changing this one, and the path
    that found it — so a reader can check the reasoning, not just the count."""

    object_type: str
    object_id: str
    version: int
    lifecycle_state: str
    via: str


class ReferencesOut(BaseModel):
    """The "referenced everywhere" view. CF-V1-E3-02.

    COMPUTED from the reference graph, never from a list somebody maintains.
    A registry whose "used by" column is hand-kept is a registry whose "used
    by" column is wrong.
    """

    object_type: str
    object_id: str
    version: int
    references: list[ReferenceOut]
    unknowns: list[UnknownImpactOut] = Field(default_factory=list)


class ImpactPacketOut(BaseModel):
    """CF-V1-E11-02 on one screen: the change, both sides of its impact, and
    the evidence. Every field is COMPUTED — nothing here is the author's
    recollection of what their change touches."""

    object_type: str
    object_id: str
    version: int
    lifecycle_state: str
    author_subject: str
    diff: list[str]
    engineering_impact: list[TouchedOut]
    business_impact: list[TouchedOut]
    unknowns: list[UnknownImpactOut]
    evidence: dict[str, Any] = {}
    blocks_production: bool
    is_empty: bool


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


# ── CF-V1-E5-01 · the deterministic profiler ─────────────────────────────────
class TypeCandidateOut(BaseModel):
    """A type and the count that fits it. Both integers travel, so a reader can
    do the division rather than trust a rounded share."""

    type: str
    matched: int
    considered: int
    share: float


class DateFormatOut(BaseModel):
    label: str
    matched: int


class ColumnProfileOut(BaseModel):
    """One column's computed facts.

    `narrowest_type` is null where the evidence does not determine one — two
    types fitting equally is CF-V1-E5-02's "needs your input", and a screen
    that showed a guess there would be inventing the answer the story exists to
    refuse.
    """

    name: str
    position: int
    row_count: int
    null_count: int
    null_like_count: int
    distinct_count: int
    distinct_is_exact: bool
    is_unique: bool | None
    min_length: int
    max_length: int
    padded_count: int
    typed_cell_count: int
    narrowest_type: str | None
    type_candidates: list[TypeCandidateOut]
    date_formats: list[DateFormatOut]
    observed_precision: int | None
    observed_scale: int | None
    examples: list[str]
    top_values: list[list[Any]]
    min_value: str | None
    max_value: str | None
    values_redacted: bool
    citation_id: str
    route: str


class FindingOut(BaseModel):
    quirk: str
    detail: str
    occurrences: int
    first_lines: list[int]
    columns: list[str]
    blocks_ingestion: bool


class KeyCandidateOut(BaseModel):
    columns: list[str]
    distinct_count: int
    populated_rows: int
    null_rows: int
    duplicate_values: int
    is_unique: bool
    examples: list[list[Any]]
    values_redacted: bool


class RefusalOut(BaseModel):
    """Why nothing could be profiled, and what to ask the payer for.

    `ask_the_payer` is its own field because it is the only part the BA acts
    on, and a screen should be able to show it alone.
    """

    reason: str
    explanation: str
    ask_the_payer: str


class FileStructureOut(BaseModel):
    file_format: str
    encoding: str
    declared_encoding: str
    byte_order_mark: str | None
    delimiter: str | None
    quote_char: str | None
    line_ending: str
    column_count: int
    data_rows: int
    bytes_total: int
    bytes_read: int
    sampled: bool


class KeySearchOut(BaseModel):
    """What was examined and what was not. A bound nobody states reads as
    completeness."""

    single_columns_examined: int
    composite_width: int
    pairs_examined: int
    pairs_skipped: int
    rows_retained: int
    excluded_columns: list[str]
    note: str


class FileProfileOut(BaseModel):
    """The profile, as the wizard renders it.

    `would_load` is the answer step 1 owes the BA: this file profiled fine, and
    the pipeline will still refuse it — here is why, in plain language, weeks
    before publication rather than at it.
    """

    profile_id: str
    profiler_version: str
    feed_id: str
    source_key: str
    source_fingerprint: str
    readable: bool
    would_load: bool
    refusal: RefusalOut | None
    structure: FileStructureOut
    columns: list[ColumnProfileOut]
    findings: list[FindingOut]
    blockers: list[FindingOut]
    key_candidates: list[KeyCandidateOut]
    key_search: KeySearchOut
    duplicate_rows: int
    duplicate_groups: int
    values_redacted: bool
    profiled_by: str
    profiled_ts: datetime
    citation_id: str
    route: str


# ── CF-V1-E3-05 · delivery ───────────────────────────────────────────────────
class DeliveryOut(BaseModel):
    """What happened to a file somebody just sent.

    The landing DECISION is the headline, not the upload's success. A request
    that returns 200 has delivered the bytes; whether the platform accepted
    them is a separate fact, and conflating the two is how a rejected file
    reads as a loaded one on the screen that matters most.
    """

    outcome: str
    headline: str
    reason: str | None = None
    check_name: str | None = None
    feed_id: str
    filename: str
    #: Where the connector PUT it — always under `incoming/`.
    key: str
    #: Where it IS, after landing moved it. Both are true, and this is the one
    #: to show a person: a receipt that named the delivered key told somebody
    #: their accepted file was in `incoming/` while it was in `processed/`.
    landed_key: str
    size_bytes: int
    fingerprint: str
    business_date: str
    #: The PERSON who pressed Upload.
    delivered_by: str
    #: The CONNECTOR that landed it — `upload-endpoint`, `folder-drop`,
    #: `fidelis-sftp`. Separate from `delivered_by` because a poller has a
    #: source and no person, and an approver asking "who put this here" wants
    #: the person.
    source: str
    #: `file:<fingerprint>` — the address the explorer routes and the insight
    #: agent cites. A delivery needed no new citation kind.
    citation_id: str
    route: str
    #: Present only for an ACCEPTED file. Profiling a rejected one would
    #: produce facts about bytes the platform declined to load.
    profile_id: str | None = None
    profile: FileProfileOut | None = None
    #: What a person should do next, in their words. Empty when the answer is
    #: "nothing — it worked".
    next_step: str = ""


class DeliveriesOut(BaseModel):
    """Everything one poll of a pull connector landed."""

    source: str
    delivered: list[DeliveryOut]
    #: Said out loud rather than left as an empty list, because "the poller ran
    #: and found nothing" and "the poller did not run" look identical otherwise.
    summary: str


class ConnectionCheckOut(BaseModel):
    """Whether the delivery source can be reached at all."""

    reachable: bool
    source: str
    detail: str = ""


class ProfileIn(BaseModel):
    """Profile a file that is already in the landing zone.

    The key rather than an upload body: a file the platform has not landed has
    not been fingerprinted, registered or size-checked, and profiling it would
    be the second door landing controls exist to prevent.
    """

    file_key: str
    file_format: str = "csv"
    encoding: str = "utf-8"
    delimiter: str | None = None


# ── CF-V1-E5-02 · AI schema inference ────────────────────────────────────────
class ProposedColumnOut(BaseModel):
    """One proposed contract column, and where each part of it came from.

    `settled_by` is on the wire because a BA reviewing forty columns needs to
    know which five a model touched — and because an eval number that did not
    separate them would read as a claim about the model that it has not earned.
    """

    source_name: str
    position: int
    name: str | None
    type: str | None
    nullable: bool
    is_phi: bool
    glossary_id: str | None
    date_format: str | None
    precision: int | None
    scale: int | None
    confidence: float
    settled_by: str
    needs_input: bool
    rationale: str
    citations: list[str]


class CorrectionOut(BaseModel):
    field_path: str
    proposed: Any = None
    accepted: Any = None
    is_addition: bool


class PhiColumnOut(BaseModel):
    """One column's PHI verdict, with the basis it rests on. CF-V1-E5-03.

    `basis` is on the wire rather than derived in the client because the whole
    review screen turns on it: "the glossary says so" and "nothing identified
    this, so we are protecting it" are the same flag and completely different
    asks of a steward's attention.
    """

    source_name: str
    position: int
    is_phi: bool
    basis: str
    phi_kind: str | None = None
    code_set: str | None = None
    confidence: float = 0.0
    needs_steward_review: bool = False
    glossary_id: str | None = None
    rationale: str = ""
    citations: list[str] = Field(default_factory=list)


# ── NL rules and their preview · CF-V1-E7-01, CF-V1-E7-02 ────────────────────


class AuthorRulesIn(BaseModel):
    """Sentences a BA typed. CF-V1-E7-01.

    Plain English in, a governed rule out — and the model in between never
    writes SQL: it chooses a check from a closed vocabulary and the platform
    renders every notation from it.
    """

    stated: list[str] = Field(default_factory=list)
    #: The stored profile to preview against. Optional: a BA may want the rule
    #: written before a sample exists, and a preview that could not run says so
    #: rather than reporting nothing found.
    profile_id: str | None = None


class ProposedRuleOut(BaseModel):
    """One proposed rule, as the reviewer reads it.

    BOTH TEXTS. `stated` is the BA's own sentence, verbatim; `explanation` is
    generated from the check, so it cannot drift from what runs. Where the two
    disagree the rule is wrong, and a screen showing one of them would hide it.
    """

    stated: str
    unsupported: bool = False
    unsupported_reason: str = ""
    rule_id: str | None = None
    name: str = ""
    explanation: str = ""
    check_kind: str | None = None
    column: str | None = None
    dimension: str | None = None
    severity: str | None = None
    glossary_id: str | None = None
    confidence: float | None = None
    settled_by: str = "inference"
    rationale: str = ""
    #: Rendered by the PLATFORM from the check, never by the model. Shown so an
    #: engineer can read exactly what will run.
    sql: str = ""
    pyspark: str = ""


class FailingRowOut(BaseModel):
    """One row a rule caught, already masked. CF-V1-E7-02.

    The masking happens where the row is BUILT, not here — this shape never
    holds an unmasked value, so there is no moment at which a route could
    serialise one.
    """

    row_number: int
    values: dict[str, str] = Field(default_factory=dict)


class RulePreviewOut(BaseModel):
    """What one rule does to the sample. The evidence an approval rests on."""

    rule_id: str
    stated: str
    explanation: str
    tested: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failure_rate: float = 0.0
    failing_rows: list[FailingRowOut] = Field(default_factory=list)
    masked_columns: list[str] = Field(default_factory=list)
    #: Set when the check could not be run against a sample at all. Distinct
    #: from "ran and found nothing", which is the whole point.
    not_previewable: str = ""
    summary: str = ""


class RulePreviewPackOut(BaseModel):
    """Every preview for a feed's rules, with the sample size.

    `sample_rows` travels because "3 rows failed" means one thing in 200 rows
    and another in 200,000 — a stored figure whose denominator is missing is
    one somebody will quote wrongly.
    """

    feed_id: str
    sample_rows: int
    rules_previewed: int
    rules_not_previewable: int
    total_failures: int
    previews: list[RulePreviewOut] = Field(default_factory=list)


class ProposedMappingOut(BaseModel):
    """One proposed mapping line. CF-V1-E6-02.

    `settled_by` is on the wire because a mapping that is 90% glossary lookups
    is not evidence that a model is good at mapping — and a steward reading the
    queue should be able to see at a glance which lines a model touched.

    `like_feed_id` is the precedent it reasoned from, so "maps like Fidelis
    did" is a link a reviewer can open rather than a claim they must take.
    """

    source_column: str
    target_entity: str
    target_field: str
    unmapped: bool = False
    unmapped_reason: str = ""
    glossary_id: str | None = None
    confidence: float = 0.0
    settled_by: str = "inference"
    rationale: str = ""
    like_feed_id: str | None = None
    citations: list[str] = Field(default_factory=list)


class ProposalOut(BaseModel):
    """One agent proposal, as the review screen renders it.

    Note what is NOT here: any way to make this executable. A proposal becomes
    real only by a human approving it, and approval creates a DRAFT governed
    object authored by that human — which then travels the ordinary lifecycle.
    """

    proposal_id: str
    agent: str
    capability: str
    risk_class: str
    state: str
    feed_id: str | None
    run_id: str
    confidence: float | None
    prompt_hash: str
    created_by: str
    created_ts: datetime
    decided_by: str | None
    decision_comment: str
    decided_ts: datetime | None
    applied_object_type: str | None
    applied_object_id: str | None
    applied_version: int | None
    grounding_citations: list[str]
    columns: list[ProposedColumnOut]
    needs_input: list[str]
    refusals: list[str]
    corrections: list[CorrectionOut]
    model_called: bool = True
    # ── CF-V1-E5-03 and CF-V1-E6-02 · one list per agent, only one filled ────
    #
    # A second list rather than a second response model, so ONE review queue
    # renders every R2 agent's output. `agent` says which list is populated;
    # the other is empty, and a client that renders both renders correctly for
    # either without knowing the agent's name.
    phi_columns: list[PhiColumnOut] = Field(default_factory=list)
    mapping_lines: list[ProposedMappingOut] = Field(default_factory=list)
    rules: list[ProposedRuleOut] = Field(default_factory=list)
    needs_steward_review: list[str] = Field(default_factory=list)
    masked_columns: list[str] = Field(default_factory=list)


class DetectPhiIn(BaseModel):
    """Ask the agent to classify a stored profile's columns."""

    profile_id: str


class MaskingPolicyOut(BaseModel):
    """What E2 masks, as the approved classification left it.

    Read from the proposal's stored payload rather than recomputed, so the
    masking a steward approved and the masking that runs are provably one
    document.
    """

    feed_id: str
    profile_id: str
    proposal_id: str
    state: str
    masked_columns: list[str]
    unmasked_columns: list[str]
    pending_steward: list[str]


class PhiRecallOut(BaseModel):
    """The gate, computed against the client's own glossary.

    `expected` is how many of this file's columns the glossary flags;
    `protected` is how many of those the classification protected. The gate is
    equality — reported as two integers so it can be checked rather than
    trusted.
    """

    protected: int
    expected: int
    passes: bool
    missed: list[str]
    over_flagged: list[str]
    report: str


class InferSchemaIn(BaseModel):
    """Ask the agent to read a profile and propose a contract.

    Takes a profile id rather than a file: the facts are computed once, stored,
    and cited — so two inference runs over one sample are grounded in provably
    the same evidence.
    """

    profile_id: str


class ColumnDecisionIn(BaseModel):
    """What the human decided about one column. Absent fields keep the
    proposal's value, so a reviewer changing one type does not have to restate
    the other forty."""

    source_name: str
    name: str | None = None
    type: str | None = None
    nullable: bool | None = None
    is_phi: bool | None = None
    date_format: str | None = None


class MappingDecisionIn(BaseModel):
    """What the human decided about one proposed mapping line. CF-V1-E6-02.

    Keyed by SOURCE COLUMN, like `ColumnDecisionIn`, and for the same reason:
    absent fields keep the proposal's value, so a reviewer redirecting one
    column does not restate the other forty.

    Note what cannot be set here: a transform's PARAMETERS. The agent proposes
    a column pairing and never a separator, a lookup table or a set of cases —
    those are business rules, and they are set in the manual editor, which is
    the correction surface CF-V1-E6-03 exists to be.
    """

    source_column: str
    target_entity: str | None = None
    target_field: str | None = None
    unmapped: bool | None = None
    unmapped_reason: str | None = None


class ApproveProposalIn(BaseModel):
    """Approve, with any corrections. Both travel together deliberately: an
    approval recorded now and its corrections recorded later is an eval set
    that under-counts every crash in between."""

    comment: str = ""
    columns: list[ColumnDecisionIn] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)
    #: CF-V1-E6-02. Present only on a mapping proposal, where the records are
    #: mapping lines rather than contract columns.
    mappings: list[MappingDecisionIn] = Field(default_factory=list)


class ReclassifyIn(BaseModel):
    """A steward's decision about what columns hold. CF-V1-E5-03.

    `rationale` is required by the type rather than checked in the handler,
    because this is the one request in the platform that can reduce the
    protection on a field — and the schema is the earliest place to say a
    reason is not optional.
    """

    rationale: str = Field(min_length=1)
    columns: list[ColumnDecisionIn] = Field(default_factory=list)

    def as_approval(self) -> ApproveProposalIn:
        """The same shape the approve path uses, so both routes fold a
        reviewer's decisions in through ONE function and cannot drift."""
        return ApproveProposalIn(comment=self.rationale, columns=self.columns)


class RejectProposalIn(BaseModel):
    """A reason is required — it is the most informative thing an agent's
    output can produce."""

    comment: str


class AcceptanceOut(BaseModel):
    """The eval arithmetic, with the model's share separated out."""

    total: int
    accepted: int
    corrected: int
    rate: float
    deterministic_total: int
    deterministic_corrected: int
    inferred_total: int
    inferred_corrected: int
    inferred_rate: float
    additions: int
    report: str


# ── the mapping studio · CF-V1-E6-03 ─────────────────────────────────────────


class CaseModel(BaseModel):
    """One branch of a CONDITIONAL: equality against a listed set.

    Never a predicate. The moment a case can hold an expression, approving a
    mapping means reading a language rather than reading configuration.
    """

    when_in: list[str] = Field(default_factory=list)
    then: str = ""


class TransformModel(BaseModel):
    """What to do with the source value. Parameters only — there is
    deliberately no `expression`, `sql` or `formula` field on the wire, because
    there is none in the object."""

    kind: str = "direct"
    target_type: str | None = None
    date_format: str | None = None
    separator: str | None = None
    part: int | None = None
    lookup: list[list[str]] = Field(default_factory=list)
    on_unlisted: str = "reject_row"
    cases: list[CaseModel] = Field(default_factory=list)
    literal: str | None = None
    default_value: str | None = None
    describe: str = ""


class MappingLineModel(BaseModel):
    """One target field, and where its value comes from.

    Keyed by the TARGET: a target field has exactly one answer to "what
    populates this?", which is the question a reviewer, a lineage graph and a
    row-loss investigation all ask.
    """

    target_entity: str
    target_field: str
    source_columns: list[str] = Field(default_factory=list)
    transform: TransformModel = Field(default_factory=TransformModel)
    null_policy: str = "pass_through"
    default_value: str | None = None
    platform_supplied: bool = False
    unmapped_reason: str = ""
    glossary_id: str | None = None
    notes: str = ""
    confidence: float | None = None
    citations: list[str] = Field(default_factory=list)
    status: str = "mapped"
    describe: str = ""


class MappingFindingOut(BaseModel):
    """One thing wrong, or worth knowing, about a mapping.

    Three strings for the same reason `ChecklistItemOut` has three: a finding
    that only names a field gets a placeholder typed into it.
    """

    key: str
    address: str
    severity: str
    blocks: bool
    what: str
    why_it_matters: str
    how_to_fix: str


class MappingIn(BaseModel):
    """A mapping as a BA authors it by hand. CF-V1-E6-03.

    The manual editor is the fallback AND the correction surface: everything
    CF-V1-E6-02's agent can propose is expressible here, which is what makes
    "humans must be able to do by hand everything the AI proposes" a property
    of the taxonomy rather than a promise.
    """

    contract_version: int | None = None
    lines: list[MappingLineModel] = Field(default_factory=list)
    business_consumers: list[str] = Field(default_factory=list)


class MappingOut(BaseModel):
    feed_id: str
    version: int
    lifecycle_state: str
    status: StatusWord
    contract_version: int | None = None
    citation_id: str
    route: str
    mapped_count: int
    total_count: int
    unmapped_count: int
    lines: list[MappingLineModel] = Field(default_factory=list)
    findings: list[MappingFindingOut] = Field(default_factory=list)
    blocking_count: int = 0


class MappingDiffLineOut(BaseModel):
    """One target field that differs between two mapping versions.

    `loses_its_source` is the field a row-loss investigation starts from, and
    it is computed rather than described: a line that went from MAPPED to
    UNMAPPED is how a column silently empties after a release.
    """

    address: str
    change: str
    before: str = ""
    after: str = ""
    loses_its_source: bool = False
    #: A full sentence, not a shorthand. An approver reading
    #: `null_policy: 1 -> 3` has been shown a diff rather than told a fact.
    explanation: str = ""


class MappingDiffOut(BaseModel):
    feed_id: str
    from_version: int
    to_version: int
    #: Whether the EARLIER version was live. Only then is a lost source a loss,
    #: and only then does the approval gate ask for an acknowledgement.
    from_published: bool = False
    lines: list[MappingDiffLineOut] = Field(default_factory=list)
    fields_losing_their_source: list[str] = Field(default_factory=list)
    summary: str = ""


# ── CF-V1-E4-01 · the five-step wizard ───────────────────────────────────────
class ObstacleOut(BaseModel):
    """One thing standing between the BA and done.

    Three strings and a ROUTE. The route is `CitationId.route` — the platform's
    own address space, already resolved server-side — so "one-click navigation
    back to them" is a link the client renders rather than a mapping the client
    has to know how to build.
    """

    key: str
    what: str
    why_it_matters: str
    how_to_fix: str
    citation: str | None = None
    route: str = ""
    blocking: bool = True


class WizardStepOut(BaseModel):
    """One of the five, as it actually is.

    `state` and `status` are both here on purpose. `status` is one of the seven
    words a user is ever shown; `state` is the richer machine the wizard needs
    to decide which control to draw. The UI reads the word and never invents an
    eighth.
    """

    step: str
    ordinal: int
    label: str
    state: str
    status: StatusWord
    is_complete: bool
    version: int | None = None
    citation: str | None = None
    obstacles: list[ObstacleOut] = Field(default_factory=list)


class WizardOut(BaseModel):
    """The single readiness view — what is complete, what is missing, what
    needs somebody else."""

    feed_id: str
    steps: list[WizardStepOut] = Field(default_factory=list)
    resume_at: str
    is_publishable: bool
    outstanding: list[ObstacleOut] = Field(default_factory=list)
    gaps: list[ObstacleOut] = Field(default_factory=list)
    #: CF-V1-E3-02's operational checklist, carried in the SAME view so a BA
    #: sees one list rather than a wizard and a registry disagreeing about
    #: whether the feed is ready.
    operations_outstanding: list[str] = Field(default_factory=list)
    explanation: str = ""


# ── CF-V1-E4-02 · the evidence pack ──────────────────────────────────────────
class RuleOutcomeOut(BaseModel):
    """One rule's hit rate on the sample — including the rules that fired zero
    times, because silence is data."""

    rule_id: str
    name: str
    tested: int
    flagged: int
    hit_rate: float
    quarantined: bool


class DropExplanationOut(BaseModel):
    rule_id: str
    reason: str
    record_count: int
    columns: list[str] = Field(default_factory=list)


class ExampleOut(BaseModel):
    """One row before and after, with protected values already masked.

    Masked in the PACK, not by this schema: the pack leaves the platform as a
    document, and a masking policy applied only on the way out of an API is one
    that stops applying the moment somebody exports.
    """

    row_number: int
    before: dict[str, str] = Field(default_factory=dict)
    after: dict[str, str] = Field(default_factory=dict)


class GapOut(BaseModel):
    key: str
    what: str
    why_it_is_acceptable: str
    citation: str | None = None


class FailureOut(BaseModel):
    step: str
    explanation: str
    citation: str | None = None
    route: str = ""


class EvidencePackOut(BaseModel):
    """What a reviewer receives, generated rather than assembled.

    `markdown` is the whole artifact, so a reviewer without platform access
    reads exactly what an approver saw — which is the story's own requirement
    and the reason the document is not a rendering the client composes.
    """

    feed_id: str
    fingerprint: str
    produced_ts: datetime
    rows_in: int
    rows_loaded: int
    rows_quarantined: int
    balanced: bool
    accounts_for_every_row: bool
    partial: bool
    summary: str
    sample_filename: str = ""
    drops: list[DropExplanationOut] = Field(default_factory=list)
    rules: list[RuleOutcomeOut] = Field(default_factory=list)
    examples: list[ExampleOut] = Field(default_factory=list)
    gaps: list[GapOut] = Field(default_factory=list)
    failure: FailureOut | None = None
    markdown: str = ""


# ── CF-V1-E4-03 · signatures and the narrative ───────────────────────────────
class SignatureOut(BaseModel):
    """One approver's act, and the configuration they were looking at.

    `evidence_fingerprint` is on the signature rather than only on the release,
    because two approvers who signed two different packs have approved two
    different feeds — and that has to be visible, not inferred.
    """

    approval: str
    question: str
    actor_subject: str
    actor_name: str
    signed_ts: datetime
    evidence_fingerprint: str
    comment: str = ""


class ReleasePacketOut(BaseModel):
    feed_id: str
    feed_version: int
    author_subject: str
    evidence_fingerprint: str
    signatures: list[SignatureOut] = Field(default_factory=list)
    outstanding: list[str] = Field(default_factory=list)
    signatures_agree: bool = True
    is_complete: bool = False
    explanation: str = ""


class ChapterOut(BaseModel):
    occurred_ts: datetime
    who: str
    what: str
    object_type: str
    detail: str = ""


class NarrativeOut(BaseModel):
    """The whole journey, oldest first — built from the audit ledger, so an act
    that produced no audit entry does not appear, which is correct."""

    feed_id: str
    chapters: list[ChapterOut] = Field(default_factory=list)
    story: str = ""


class SignIn(BaseModel):
    approval: str
    comment: str


# ── CF-V1-E8-03 · dependencies and holds ─────────────────────────────────────
class BlockerOut(BaseModel):
    """One thing standing between a schedule and a run.

    `chain` is the path that reached the root cause. An operator told only
    "waiting on enrollment" goes and looks at a feed that is itself blameless.
    """

    reason: str
    feed_id: str
    business_date: str
    batch_id: str | None = None
    state: str | None = None
    layer: str | None = None
    chain: list[str] = Field(default_factory=list)
    is_root: bool = True
    explanation: str = ""


class ReleaseDecisionOut(BaseModel):
    """May this run start, and if not, why. Computed on every request — there
    is no stored hold to go stale."""

    feed_id: str
    business_date: str
    may_run: bool
    batch_state: str
    status: StatusWord
    blockers: list[BlockerOut] = Field(default_factory=list)
    chain: list[str] = Field(default_factory=list)
    needs_notification: bool = False
    explanation: str = ""


class PictureNodeOut(BaseModel):
    feed_id: str
    business_date: str
    batch_id: str | None = None
    state: str | None = None
    status: StatusWord
    is_subject: bool = False
    is_root_cause: bool = False


class DependencyPictureOut(BaseModel):
    """What Operations sees beside a held run — a structure, not a rendering.

    `core/` says which feeds are in the picture and which is to blame; the UI
    decides whether that is a graph, a list or a breadcrumb.
    """

    subject: str
    business_date: str
    nodes: list[PictureNodeOut] = Field(default_factory=list)
    edges: list[list[str]] = Field(default_factory=list)
    blast_radius: list[str] = Field(default_factory=list)
    decision: ReleaseDecisionOut | None = None
    is_self_explanatory: bool = True


# ── CF-V1-E7-03 · rule severity, layer, threshold and effective dates ────────
class RulePolicyIn(BaseModel):
    """One rule's execution policy, as a steward configures it.

    `on_failure` is the six-rung ladder and NOT the harvested `Severity`. The
    two are different questions asked by different people — how much this
    matters, and what the pipeline should do — and a client that could send
    "critical" here would be answering the wrong one.
    """

    rule_id: str
    layer: str
    on_failure: str
    threshold_percent: Decimal | None = None
    execution_order: int = 100
    effective_from: date | None = None
    effective_to: date | None = None
    alert_recipient: str = ""
    owner: str = ""
    rationale: str = ""


class ConsequenceOut(BaseModel):
    """One rung of the ladder, with the sentence shown AT SELECTION TIME.

    Served rather than hard-coded in the client for the reason `core.persona`
    serves home slots: what a consequence MEANS is a product fact, and a copy
    of it in a dropdown is a copy that drifts.
    """

    value: str
    rank: int
    changes_the_batch: bool
    needs_a_person: bool
    in_plain_language: str


class PolicyFindingOut(BaseModel):
    key: str
    rule_id: str
    what: str
    why_it_matters: str
    how_to_fix: str
    blocks: bool


class RulePolicyOut(BaseModel):
    rule_id: str
    layer: str
    on_failure: str
    threshold_percent: Decimal | None = None
    execution_order: int
    effective_from: date | None = None
    effective_to: date | None = None
    alert_recipient: str = ""
    owner: str = ""
    rationale: str = ""
    #: The line that appears on the feed profile.
    describes: str = ""


class RulePolicySetOut(BaseModel):
    """A feed's configured rule policies, and what stands between them and an
    approval."""

    feed_id: str
    version: int
    lifecycle_state: str
    policies: list[RulePolicyOut] = Field(default_factory=list)
    findings: list[PolicyFindingOut] = Field(default_factory=list)
    is_approvable: bool = False
    #: Rules that got LESS strict than the previous version. Named rather than
    #: refused — a softening is sometimes right; one nobody noticed is not.
    softened: list[str] = Field(default_factory=list)
    ladder: list[ConsequenceOut] = Field(default_factory=list)


# ── CF-V1-E7-04 · the technical review queue ─────────────────────────────────
class TechnicalReviewOut(BaseModel):
    """One uncertain rule, waiting for a person.

    `stated` and `machine_reading` are BOTH here and in that order — the BA's
    sentence first, because a reviewer shown the machine's reading first
    anchors on it and then checks whether the sentence agrees, which reliably
    produces agreement.
    """

    review_id: str
    feed_id: str
    stated: str
    machine_reading: str
    reason: str
    #: Plain language, for the BA. Never a code.
    explained_to_author: str
    confidence: float = 0.0
    state: str
    status: StatusWord
    created_ts: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    reviewed_by: str | None = None
    resolution_note: str = ""
    engineering_item_id: str | None = None
    citation: str | None = None


class ReviewQueueOut(BaseModel):
    """The reviewer's queue, plus the measurable that must stay empty.

    `unrouted` is exposed rather than left to CI because a control only CI can
    see is a control nobody maintains — "100% of below-threshold rules routed"
    is answerable here, by anybody, at any time.
    """

    reviews: list[TechnicalReviewOut] = Field(default_factory=list)
    open_count: int = 0
    unrouted: list[str] = Field(default_factory=list)


class CorrectReviewIn(BaseModel):
    note: str
    check: dict[str, Any]
    rule_id: str | None = None


class EscalateReviewIn(BaseModel):
    note: str
    item_id: str | None = None


class EngineeringItemOut(BaseModel):
    item_id: str
    feed_id: str
    review_id: str
    title: str
    stated: str
    reason: str
    machine_reading: str
    raised_by: str
    raised_ts: datetime
    note: str = ""
    #: What the BA is told happens next.
    explained_to_author: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


# ── CF-V2-E12-01 · the operations home board ─────────────────────────────────
class CounterOut(BaseModel):
    """One number, and the query behind it. `derived_from` is never blank —
    the type refuses to be built that way."""

    label: str
    value: int
    derived_from: str


class ArrivalRowOut(BaseModel):
    feed_id: str
    domain: str
    business_date: str
    condition: str
    status: StatusWord
    due_ts: datetime
    minutes_late: int = 0
    batch_id: str | None = None
    headline: str = ""
    citation: str | None = None
    route: str = ""


class AttentionItemOut(BaseModel):
    """One thing requiring action today. `impact` and `why` travel together —
    a ranking nobody can see the reasoning for is one operators override."""

    feed_id: str
    domain: str
    headline: str
    impact: int
    why: str
    status: StatusWord
    citation: str
    route: str


class FreshnessOut(BaseModel):
    as_of: datetime
    may_be_stale: bool = False
    banner: str = ""


class BoardOut(BaseModel):
    business_date: str
    expected: int
    received: int
    missing: int
    at_risk: int
    counters: list[CounterOut] = Field(default_factory=list)
    rows: list[ArrivalRowOut] = Field(default_factory=list)
    attention: list[AttentionItemOut] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    freshness: FreshnessOut
    explanation: str = ""


# ── CF-V2-E12-02 · the batch and stage monitor ───────────────────────────────
class StageViewOut(BaseModel):
    layer: str
    state: str
    status: StatusWord
    started_ts: datetime
    completed_ts: datetime | None = None
    duration_seconds: int | None = None
    records_in: int = 0
    records_out: int = 0
    quarantined: int = 0
    attributed_drops: int = 0
    unexplained: int = 0
    balances: bool = True
    flow: str = ""


class BatchErrorOut(BaseModel):
    error_id_hash: str
    stage: str
    category: str
    message: str
    occurred_ts: datetime
    rule_id: str | None = None
    is_consequence: bool = False
    caused_by: str | None = None
    citation: str
    route: str


class BatchViewOut(BaseModel):
    """Everything about one batch, in one response — because "two clicks"
    means the second one has to find it all already here."""

    batch_id: str
    feed_id: str
    business_date: str
    state: str
    status: StatusWord
    started_ts: datetime
    completed_ts: datetime | None = None
    failed_at: str | None = None
    rows_written: int = 0
    balances: bool = True
    sla: str = "unknown"
    typical_duration_seconds: int | None = None
    stages: list[StageViewOut] = Field(default_factory=list)
    errors: list[BatchErrorOut] = Field(default_factory=list)
    cascade_summary: str = ""
    flow: list[str] = Field(default_factory=list)
    explanation: str = ""
    citation: str = ""
    route: str = ""


# ── CF-V2-E12-03 · the governed action surface ───────────────────────────────
class ActionRequestIn(BaseModel):
    """What an operator is asking for.

    NOTE WHAT IS ABSENT: no `command`, no `sql`, no free-form `parameters`.
    The don't says no free-form commands anywhere, and the way to mean it is a
    request body with nowhere to put one.
    """

    action: str
    reason: str = ""
    approval_identifier: str = ""
    assignee: str = ""
    note: str = ""
    resume_from: str | None = None


class PreviewOut(BaseModel):
    action: str
    target: str
    what_will_happen: str
    scope_records: int = 0
    scope_stages: list[str] = Field(default_factory=list)
    estimated_minutes: int = 0
    requires_approval_identifier: bool = False
    explanation: str = ""


class ActionRecordOut(BaseModel):
    """`phase` is REQUESTED until somebody observed the outcome — 'retry
    requested' is not 'retry succeeded'.

    `record_id` is the ledger address the screen polls
    (`GET /api/operations/actions/{record_id}`) until a worker has re-read the
    control tables and the phase moved.
    """

    record_id: str = ""
    action: str
    target: str
    actor_subject: str
    requested_ts: datetime
    phase: str
    status: StatusWord
    is_complete: bool = False
    reason: str = ""
    approval_identifier: str = ""
    verified_ts: datetime | None = None
    outcome: str = ""
    explanation: str = ""


class ActionSurfaceOut(BaseModel):
    """What may be done here, right now. Exactly what `authorize` would
    permit — a console that draws a button it would refuse teaches people that
    refusals are noise."""

    target: str
    offered: list[str] = Field(default_factory=list)
    previews: list[PreviewOut] = Field(default_factory=list)
    environment: str = "development"


class ActionRefusalOut(BaseModel):
    """A declined operations action, recorded.

    Named apart from the profiler's `RefusalOut` deliberately: a profiler
    refusal is "we could not read your file", and an action refusal is "we
    will not do that" — two different sentences to two different people, and
    one shared name would have merged them on the first screen that showed
    both.
    """

    action: str
    reason: str
    detail: str
    target: str
    notifies: bool = False
    route: str = ""


# ── CF-V2-E12-04 · fingerprinting and guide matching ─────────────────────────
class PriorIncidentOut(BaseModel):
    incident_id: str
    occurred_ts: datetime
    fix_minutes: int | None = None
    batch_id: str | None = None
    citation: str


class GuideMatchOut(BaseModel):
    """A matched guide WITH its evidence. The don't says a match may not be
    claimed without showing the fingerprint."""

    guide_id: str
    title: str
    steps: list[str] = Field(default_factory=list)
    signature: str
    matched_errors: list[str] = Field(default_factory=list)
    occurrences: int = 0
    mean_fix_minutes: int | None = None
    remedy: str | None = None
    stale: bool = False
    priors: list[PriorIncidentOut] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    explanation: str = ""


class RunbookOut(BaseModel):
    """A published recovery guide, on its own — the destination a
    `runbook:<id>` citation opens (CF-V1-W1-25). Distinct from
    `GuideMatchOut`: that shape is a MATCH's evidence (errors, occurrences,
    priors), nested inside one incident; this is the guide itself, the same
    fields `_runbook_body_from` writes and `workers.incidents
    .recovery_guides` reads back."""

    guide_id: str
    title: str
    steps: list[str] = Field(default_factory=list)
    signatures: list[str] = Field(default_factory=list)
    remedy: str | None = None
    is_transient: bool = False
    version: int
    lifecycle_state: str
    status: StatusWord


class VarianceIn(BaseModel):
    """One discrepancy an operator writes down. The platform computes the
    delta and the criticality — a caller who could declare their own variance
    non-critical would be grading their own homework."""

    kind: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal = Decimal("0")


class WaiveVarianceIn(BaseModel):
    """The most consequential button in the wave. The reason is refused blank
    (and refused 'n/a') by the core; the expiry defaults to the 90-day
    ceiling and cannot exceed it."""

    reason: str
    expires_on: date | None = None


class CorrectVarianceIn(BaseModel):
    note: str


class ExplainVarianceIn(BaseModel):
    explanation: str


class VarianceOut(BaseModel):
    variance_id: str
    batch_id: str
    feed_id: str
    kind: str
    expected: Decimal
    actual: Decimal
    delta: Decimal
    tolerance: Decimal
    critical: bool
    outcome: str
    opened_by: str
    opened_ts: datetime
    explanation: str = ""
    waived_by: str = ""
    waiver_reason: str = ""
    waiver_expires_on: date | None = None
    citation: str = ""


class CertificationCheckOut(BaseModel):
    kind: str
    passed: bool
    completed: bool
    evidence: str


class CertificationOut(BaseModel):
    """CF-V2-E13-04 — the verdict, DERIVED on this read. There is no route
    that sets one, and that absence is the acceptance criterion."""

    batch_id: str
    feed_id: str
    verdict: str
    publishable: bool
    derived_ts: datetime | None = None
    checks: list[CertificationCheckOut] = Field(default_factory=list)
    variances: list[VarianceOut] = Field(default_factory=list)


class ReliabilityComponentOut(BaseModel):
    """One signal's contribution — the click's answer. `measured` is the
    difference between 'scored zero' and 'not measurable yet', which the
    screen must never collapse."""

    signal: str
    value: float
    weight: float
    evidence: str
    sample_size: int = 0
    measured: bool = False


class ReliabilityOut(BaseModel):
    """CF-V2-E12-05 — 'can I trust this feed?' as a number with its parts.

    `overall` is computed from the components on every access in core, so a
    score whose parts do not add up is unrepresentable — this model just
    carries the answer. `confidence` says how much of the intended weight was
    actually measurable: a 94 built from four of six signals is a different
    claim from a 94 built from all six.
    """

    feed_id: str
    as_of: date
    overall: float
    band: str
    confidence: float
    components: list[ReliabilityComponentOut] = Field(default_factory=list)
    citation: str = ""


class AcknowledgeIncidentIn(BaseModel):
    """Acknowledging names the person automatically (the caller); assignment
    is optional and may name somebody else."""

    assigned_to: str = ""


class ResolveIncidentIn(BaseModel):
    """The resolution text is REQUIRED by the core — it becomes the narrative
    CF-V2-E16-07 embeds on close, and an empty one teaches nothing."""

    resolution: str


class IncidentRowOut(BaseModel):
    """One line in the incident list — the ledger's current state, cheap
    enough to serve for every open incident at once. The full evidence bundle
    stays on the per-batch route, which recomputes it."""

    incident_id: str
    batch_id: str
    feed_id: str
    state: str
    signature: str = ""
    assigned_to: str = ""
    opened_ts: datetime
    resolved_ts: datetime | None = None


class IncidentOut(BaseModel):
    incident_id: str
    batch_id: str
    feed_id: str
    opened_ts: datetime
    kind: str
    status: StatusWord
    # ── the operational machine · CF-V2-E12-04 ────────────────────────────
    state: str = "open"
    acknowledged_by: str = ""
    assigned_to: str = ""
    resolution: str = ""
    resolved_ts: datetime | None = None
    signature: str = ""
    root_cause: BatchErrorOut | None = None
    consequences: list[BatchErrorOut] = Field(default_factory=list)
    match: GuideMatchOut | None = None
    #: The action an operator would press — on the action surface, with its
    #: approvals. Null for a novel failure and for a human-work fix.
    proposed_remedy: str | None = None
    evidence_bundle: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    citation: str = ""
    route: str = ""
    #: CF-V1-W1-28, `GovernedOut.warnings`'s sibling: non-empty only when a
    #: close's knowledge-embed side effect (`.embeddable`) was attempted and
    #: failed the documented way. The incident's own state above is
    #: unaffected either way — this says only that its narrative is not yet
    #: retrievable as a citation.
    warnings: list[str] = Field(default_factory=list)
