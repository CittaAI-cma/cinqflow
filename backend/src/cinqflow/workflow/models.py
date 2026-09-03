"""First-class workflow artifacts (templates.md section 1). Stage 1 subset."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from cinqflow.workflow.states import RunState, UploadStatus

ClaimKind = Literal["observed_fact", "governed_knowledge", "inference", "recommendation"]


class Upload(BaseModel):
    upload_id: str
    fingerprint: str
    filename: str
    file_type: Literal["csv", "xlsx"]
    size_bytes: int
    uploader: str
    source_system: str
    feed: str
    domain: str
    business_date: str
    landing_key: str
    status: UploadStatus
    error: str | None = None
    created_ts: datetime


class ColumnFacts(BaseModel):
    name: str
    inferred_type: Literal["string", "int", "decimal", "date", "timestamp", "bool", "code"]
    null_count: int
    distinct_count: int
    sample_values: list[str]
    patterns: dict[str, float | bool]
    phi_candidate: bool


class SheetFacts(BaseModel):
    name: str
    rows: int


class ProfileFacts(BaseModel):
    """Deterministic observations. Computed by code, never by a model."""

    row_count: int
    columns: list[ColumnFacts]
    candidate_keys: list[list[str]]
    duplicate_rows: int
    phi_candidates: list[str]
    sheets: list[SheetFacts] = Field(default_factory=list)
    sample_rows: list[dict[str, str]] = Field(default_factory=list)


class Profile(BaseModel):
    profile_id: str
    upload_id: str
    profiler_version: str
    facts: ProfileFacts
    profiled_ts: datetime


class Claim(BaseModel):
    kind: ClaimKind
    field: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str]


class Provenance(BaseModel):
    prompt: str
    model: str
    knowledge: list[str]


class InterpretationContent(BaseModel):
    """The structured AI output. No free-form text is authoritative."""

    claims: list[Claim]
    risks: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class Interpretation(BaseModel):
    interpretation_id: str
    upload_id: str
    profile_id: str
    version: int
    status: Literal["draft", "superseded"]
    provenance: Provenance
    content: InterpretationContent
    created_ts: datetime


class InterpretationStepRecord(BaseModel):
    """One LangGraph node that finished running, as persisted for polling."""

    node: str
    at_ts: datetime


class InterpretationRun(BaseModel):
    """Live progress of one `interpret_file` graph execution.

    Written incrementally by the worker (one node at a time, each committed as
    it lands) so a poll mid-run sees real progress instead of a single opaque
    `interpreting` status for however long the LLM call takes.
    """

    upload_id: str
    profile_id: str
    status: Literal["running", "completed", "failed"]
    completed_steps: list[InterpretationStepRecord]
    error: str | None = None
    started_ts: datetime
    finished_ts: datetime | None = None


class Approval(BaseModel):
    """An analyst decision. Append-only: decisions are never edited."""

    approval_id: str
    gate: Literal["G1", "G2"]
    artifact_type: Literal["interpretation", "mapping_version"]
    artifact_id: str
    artifact_version: int
    upload_id: str
    decision: Literal["approved", "rejected"]
    approver: str
    note: str | None = None
    decided_ts: datetime


class RunCounts(BaseModel):
    records_in: int
    records_out: int
    quarantined: int = 0
    attributed_drops: int = 0

    @property
    def balanced(self) -> bool:
        return self.records_in == self.records_out + self.quarantined + self.attributed_drops


class Run(BaseModel):
    """One execution against the data plane, identified by its batch and kind.

    A batch has two runs over its life: the landing that created it and the
    promotion that mapped it. They are separate executions with separate counts.
    """

    batch_id: str
    upload_id: str
    feed: str
    kind: Literal["land_bronze", "promote_silver"]
    #: The approved mapping version this run executed. `promote_silver` only.
    mapping_version: int | None = None
    state: RunState
    counts: RunCounts | None = None
    balanced: bool | None = None
    error: str | None = None
    started_ts: datetime
    finished_ts: datetime | None = None


class Lineage(BaseModel):
    """upload -> file -> batch -> Bronze -> mapping vN -> Silver Raw.

    Written at landing and completed at promotion; queryable from either end.
    """

    batch_id: str
    upload_id: str
    fingerprint: str
    landing_key: str
    bronze_table: str | None = None
    mapping_version: int | None = None
    #: The primary entity, as the mapping spec declares it (templates.md 1.9).
    silver_table: str | None = None
    #: Every entity this batch actually wrote, and how many rows each received.
    silver_tables: dict[str, int] = Field(default_factory=dict)
    created_ts: datetime


class BronzeProfile(BaseModel):
    """Deterministic facts about what actually landed, over a bounded window."""

    profile_id: str
    batch_id: str
    bronze_table: str
    profiler_version: str
    rows_in_batch: int
    rows_profiled: int
    facts: ProfileFacts
    profiled_ts: datetime

    @property
    def is_sample(self) -> bool:
        return self.rows_profiled < self.rows_in_batch


#: candidate  - a defensible target exists
#: ambiguous  - more than one plausible target, none decisive
#: unknown    - no defensible target; deliberately not guessed
#: invalid    - the model named a target the canonical model does not have
FieldStatus = Literal["candidate", "ambiguous", "unknown", "invalid"]


class Transform(BaseModel):
    """A named operation, never code. Stage 4 constrains the vocabulary further."""

    op: str
    args: dict[str, str] = Field(default_factory=dict)


class FieldCandidate(BaseModel):
    source: str
    target: str | None = None
    #: What the column means, independent of whether `target` could be set.
    #: Never validated, never executable - understanding, not a decision.
    concept: str | None = None
    transform: Transform | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    status: FieldStatus = "candidate"
    rejected_target: str | None = None
    reason: str | None = None


class ProposalContent(BaseModel):
    fields: list[FieldCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for field in self.fields:
            tally[field.status] = tally.get(field.status, 0) + 1
        return tally


class Proposal(BaseModel):
    """An AI mapping proposal. Never authoritative: Stage 4 is where the analyst
    takes ownership, and only an approved version reaches Silver."""

    proposal_id: str
    batch_id: str
    upload_id: str
    feed: str
    domain: str
    bronze_profile_id: str
    status: Literal["proposed", "invalid"]
    provenance: Provenance
    content: ProposalContent
    created_ts: datetime


class CanonicalFieldProposal(BaseModel):
    """A request to extend the governed canonical model. Never applied
    automatically - accepting one is a steward's signal to hand-edit the YAML;
    this record is the request, not a second source of legal targets."""

    proposal_id: str
    domain: str
    entity: str
    field_name: str
    type: str
    concept: str | None = None
    reason: str
    evidence: list[str] = Field(default_factory=list)
    source_batch_id: str | None = None
    source_upload_id: str | None = None
    requested_by: str
    status: Literal["pending_review", "accepted", "rejected"] = "pending_review"
    decided_by: str | None = None
    decision_note: str | None = None
    created_ts: datetime
    decided_ts: datetime | None = None


#: draft     - mutable, the analyst is shaping it
#: previewed - a deterministic preview exists for this exact spec (Stage 5)
#: approved  - frozen at G2; never edited again (Stage 6)
#: superseded- a later version was approved
MappingStatus = Literal["draft", "previewed", "approved", "superseded"]


class MappingField(BaseModel):
    """One source column's journey to one canonical field.

    Data, never code: every step is a named operation from a closed vocabulary,
    so the executor in Stage 5 can run it deterministically.
    """

    model_config = {"extra": "forbid"}

    source: str
    target: str
    cast: str = "string"
    transform: Transform | None = None
    value_map: dict[str, str] = Field(default_factory=dict)
    on_null: str = "pass"
    default: str | None = None
    on_unmapped_value: str = "pass"
    #: True once an analyst has touched a field the AI proposed.
    edited: bool = False
    note: str | None = None


class MappingSpec(BaseModel):
    """The whole mapping. `target_table` records the primary entity; field targets
    stay fully qualified (`table.field`) because one feed legitimately populates
    several canonical entities."""

    model_config = {"extra": "forbid"}

    target_table: str
    fields: list[MappingField] = Field(default_factory=list)

    @property
    def targets(self) -> list[str]:
        return [f.target for f in self.fields]

    @property
    def sources(self) -> list[str]:
        return [f.source for f in self.fields]


class MappingVersion(BaseModel):
    """The analyst-owned artifact. Drafts are mutable; approved versions are not."""

    feed: str
    version: int
    domain: str
    status: MappingStatus
    derived_from: int | None = None
    origin_proposal_id: str | None = None
    spec: MappingSpec
    created_by: str
    created_ts: datetime
    updated_ts: datetime | None = None

    @property
    def origin(self) -> str:
        """Where this version came from, in the order that explains it best."""
        if self.origin_proposal_id:
            return "proposal"
        if self.derived_from is not None:
            return f"derived from v{self.derived_from}"
        return "analyst_created"

    @property
    def editable(self) -> bool:
        return self.status in ("draft", "previewed")


class PreviewFieldResult(BaseModel):
    source: str
    target: str
    source_value: str | None = None
    mapped_value: str | None = None
    outcome: Literal["ok", "defaulted", "null", "failure", "quarantined", "rejected"]
    reason: str | None = None


class PreviewRowResult(BaseModel):
    row_number: int
    outcome: Literal["ok", "failure", "quarantined", "rejected"]
    fields: list[PreviewFieldResult] = Field(default_factory=list)


class PreviewSample(BaseModel):
    """Which rows were previewed, so the numbers can be read honestly."""

    batch_id: str
    bronze_table: str
    rows: int
    rows_in_batch: int
    selector: str

    @property
    def is_sample(self) -> bool:
        return self.rows < self.rows_in_batch


class PreviewAggregates(BaseModel):
    rows_previewed: int
    rows_ok: int
    rows_with_failures: int
    rows_quarantined: int
    rows_rejected: int
    failures_by_rule: dict[str, int] = Field(default_factory=dict)
    null_or_invalid: dict[str, int] = Field(default_factory=dict)
    affected_sources: dict[str, int] = Field(default_factory=dict)

    @property
    def writable_rows(self) -> int:
        return self.rows_ok


class Preview(BaseModel):
    """Immutable per (mapping version, spec fingerprint, sample).

    `spec_fingerprint` is what makes a preview current: edit the draft and the
    fingerprints no longer match, so the preview is visibly stale rather than
    silently wrong.
    """

    preview_id: str
    feed: str
    version: int
    spec_fingerprint: str
    sample: PreviewSample
    aggregates: PreviewAggregates
    row_results: list[PreviewRowResult] = Field(default_factory=list)
    created_ts: datetime


class UploadDetail(BaseModel):
    upload: Upload
    profile: Profile | None = None
    interpretation: Interpretation | None = None
    approvals: list[Approval] = Field(default_factory=list)
    runs: list[Run] = Field(default_factory=list)


class BatchDetail(BaseModel):
    run: Run
    lineage: Lineage | None = None
    approvals: list[Approval] = Field(default_factory=list)
    upload: Upload | None = None
    bronze_profile: BronzeProfile | None = None
    proposal: Proposal | None = None


StageState = Literal["pending", "running", "done", "failed"]


class InterpretStep(BaseModel):
    """One LangGraph node, as shown to a poller."""

    node: str
    label: str
    state: StageState
    at_ts: datetime | None = None


class Stage(BaseModel):
    """One leg of the upload's journey, for a progress poll to render as a step."""

    key: Literal["profile", "interpret", "gate", "land"]
    label: str
    state: StageState
    steps: list[InterpretStep] | None = None


class UploadProgress(BaseModel):
    upload_id: str
    status: UploadStatus
    error: str | None = None
    #: Set once the `land_bronze` run exists (i.e. as soon as landing starts,
    #: not only once it finishes - `PipelineRunner.land_bronze` opens the run
    #: and commits before any row moves, engine/runner.py). A poller can jump
    #: straight to `/api/batches/{batch_id}` the moment this is non-null.
    batch_id: str | None = None
    stages: list[Stage]


def mask_row(row: dict[str, Any], phi_columns: set[str]) -> dict[str, Any]:
    """PHI candidate values are masked wherever rows leave the store."""
    return {k: ("•••" if k in phi_columns and v not in (None, "") else v) for k, v in row.items()}


def mask_facts(facts: ProfileFacts) -> ProfileFacts:
    """PHI never leaves the API unmasked: example values are dropped for PHI
    candidate columns, and sample rows are masked field by field."""
    phi = set(facts.phi_candidates)
    return facts.model_copy(
        update={
            "columns": [
                column.model_copy(update={"sample_values": []})
                if column.phi_candidate
                else column
                for column in facts.columns
            ],
            "sample_rows": [mask_row(row, phi) for row in facts.sample_rows],
        }
    )


#: The `interpret_file` graph's nodes, in the order they run (see
#: `intelligence/graphs/interpret_file.py`). Fixed here, not derived from the
#: graph, so this module - the one thing a poller depends on - never has to
#: import the LangGraph/LLM layer to describe a run in progress.
INTERPRET_FILE_NODES: tuple[str, ...] = ("ground", "infer", "assemble")

INTERPRET_FILE_NODE_LABELS: dict[str, str] = {
    "ground": "Gathering knowledge and context",
    "infer": "Interpreting with the LLM",
    "assemble": "Assembling structured claims",
}

_PROFILE_STAGE_STATE: dict[UploadStatus, StageState] = {
    UploadStatus.RECEIVED: "pending",
    UploadStatus.PROFILING: "running",
    UploadStatus.PROFILE_FAILED: "failed",
}

_INTERPRET_STAGE_STATE: dict[UploadStatus, StageState] = {
    UploadStatus.RECEIVED: "pending",
    UploadStatus.PROFILING: "pending",
    UploadStatus.PROFILE_FAILED: "pending",
    UploadStatus.PROFILED: "pending",
    UploadStatus.INTERPRETING: "running",
    UploadStatus.INTERPRET_FAILED: "failed",
}

_GATE_STAGE_STATE: dict[UploadStatus, StageState] = {
    UploadStatus.RECEIVED: "pending",
    UploadStatus.PROFILING: "pending",
    UploadStatus.PROFILE_FAILED: "pending",
    UploadStatus.PROFILED: "pending",
    UploadStatus.INTERPRETING: "pending",
    UploadStatus.INTERPRET_FAILED: "pending",
    UploadStatus.INTERPRETED: "running",  # awaiting the analyst's G1 decision
    UploadStatus.REJECTED: "failed",
}

_LAND_STAGE_STATE: dict[UploadStatus, StageState] = {
    UploadStatus.RECEIVED: "pending",
    UploadStatus.PROFILING: "pending",
    UploadStatus.PROFILE_FAILED: "pending",
    UploadStatus.PROFILED: "pending",
    UploadStatus.INTERPRETING: "pending",
    UploadStatus.INTERPRET_FAILED: "pending",
    UploadStatus.INTERPRETED: "pending",
    #  Rejected is terminal (states.py) - landing will never run, but "pending"
    #  is the closest fit StageState has to "not reached"; there is no
    #  "will never happen" state and inventing one for this alone isn't worth it.
    UploadStatus.REJECTED: "pending",
    UploadStatus.APPROVED: "running",
    UploadStatus.LANDING: "running",
    UploadStatus.LANDED: "done",
    UploadStatus.LAND_FAILED: "failed",
}


def _interpret_steps(run: InterpretationRun | None) -> list[InterpretStep] | None:
    """The graph's fixed node list, annotated with what this run has reached.

    Nodes are strictly sequential (no branching in `interpret_file`), so the
    first node missing from `completed_steps` is always the one either running
    now or the one that failed - there is nothing else it could be.
    """
    if run is None:
        return None
    completed_at = {s.node: s.at_ts for s in run.completed_steps}
    next_node = next((n for n in INTERPRET_FILE_NODES if n not in completed_at), None)
    steps = []
    for node in INTERPRET_FILE_NODES:
        if node in completed_at:
            state: StageState = "done"
        elif node == next_node:
            state = "running" if run.status == "running" else "failed"
        else:
            state = "pending"
        steps.append(
            InterpretStep(
                node=node,
                label=INTERPRET_FILE_NODE_LABELS[node],
                state=state,
                at_ts=completed_at.get(node),
            )
        )
    return steps


def build_upload_progress(
    upload: Upload,
    run: InterpretationRun | None,
    land_run: Run | None = None,
) -> UploadProgress:
    """The upload's journey stage by stage, with LangGraph node detail for the
    AI interpretation stage - the one step whose duration is an LLM call, and
    the one a poll would otherwise see only as a single opaque `interpreting`."""
    return UploadProgress(
        upload_id=upload.upload_id,
        status=upload.status,
        error=upload.error,
        batch_id=land_run.batch_id if land_run else None,
        stages=[
            Stage(
                key="profile",
                label="Parsing and profiling the file",
                state=_PROFILE_STAGE_STATE.get(upload.status, "done"),
            ),
            Stage(
                key="interpret",
                label="AI interpretation",
                state=_INTERPRET_STAGE_STATE.get(upload.status, "done"),
                steps=_interpret_steps(run),
            ),
            Stage(
                key="gate",
                label="Analyst decision (G1)",
                state=_GATE_STAGE_STATE.get(upload.status, "done"),
            ),
            Stage(
                key="land",
                label="Landing to Bronze",
                state=_LAND_STAGE_STATE.get(upload.status, "done"),
            ),
        ],
    )
