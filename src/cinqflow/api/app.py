"""The BFF. Every route states its permission; every refusal leaves a row.

    "Nobody anonymous, SSO only, Read-Only refused server-side, everything
     audited."
    — CF-V0-E2-01

The Wave-0 API is small on purpose — the wave's argument is that the SHAPE is
right, not that the surface is wide. What has to be true here, and is asserted
by `tests/contract/test_api_guardrails.py`:

  • no route reaches a handler without a verified principal;
  • no mutating route reaches a handler without a permission dependency;
  • a refusal is a 403 (or a not-found-shaped 404 for a scope miss) AND a row.

`create_app` takes its pins as arguments. There is no module-level app and no
import-time wiring, because an app that constructs its own adapters is an app
that can only be tested the way production runs it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.api.audit import AuditLog
from cinqflow.api.deps import NOT_FOUND, CurrentPrincipal, Wiring, require
from cinqflow.api.schemas import (
    AcceptanceOut,
    AgentActionOut,
    ApproveProposalIn,
    AskIn,
    AskOut,
    AuditOut,
    AuthorRulesIn,
    BatchOut,
    BudgetOut,
    CanonicalEntityOut,
    CanonicalFieldOut,
    CanonicalModelOut,
    CaseModel,
    ChecklistItemOut,
    ClaimOut,
    CloneFeedIn,
    CloneOut,
    ColumnProfileOut,
    ContractOut,
    CorrectionOut,
    DateFormatOut,
    DestinationOut,
    DetectPhiIn,
    DifferenceOut,
    FailingRowOut,
    FeedIn,
    FeedOut,
    FileProfileOut,
    FileStructureOut,
    FindingOut,
    GlossaryTermOut,
    GovernedOut,
    HomeSlotOut,
    ImpactPacketOut,
    InferSchemaIn,
    InheritedOut,
    KeyCandidateOut,
    KeySearchOut,
    MappingDiffLineOut,
    MappingDiffOut,
    MappingFindingOut,
    MappingIn,
    MappingLineModel,
    MappingOut,
    MaskingPolicyOut,
    NavigationOut,
    OperationsModel,
    OwnerModel,
    PauseFeedIn,
    PhiColumnOut,
    PhiRecallOut,
    PrincipalOut,
    ProfileIn,
    ProposalOut,
    ProposedColumnOut,
    ProposedMappingOut,
    ProposedRuleOut,
    ReadinessOut,
    ReclassifyIn,
    ReferenceOut,
    ReferencesOut,
    RefusalOut,
    RejectProposalIn,
    ResumeFeedIn,
    RowsOut,
    RulePreviewOut,
    RulePreviewPackOut,
    SimilarFeedOut,
    SourceIn,
    SourceOut,
    SuspensionEventOut,
    SuspensionOut,
    ToolOut,
    TouchedOut,
    TraceStepOut,
    TransformModel,
    TransitionIn,
    TypeCandidateOut,
    UnknownImpactOut,
    UnknownOut,
    VersionDiffOut,
    WorkQueueOut,
)
from cinqflow.core import lifecycle, proposals
from cinqflow.core import mapping as mapping_core
from cinqflow.core import rules as rules_core
from cinqflow.core.agents.mapping_suggestion.graph import AGENT as MAPPING_SUGGESTION_AGENT
from cinqflow.core.agents.phi_detection.graph import AGENT as PHI_DETECTION_AGENT
from cinqflow.core.agents.pipeline_insight.graph import AGENT
from cinqflow.core.agents.rule_authoring.graph import AGENT as RULE_AUTHORING_AGENT
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.impact import ImpactPacket, ImpactUnknownError, Touched, build_packet
from cinqflow.core.intelligence import Budget
from cinqflow.core.mapping import versioning as mapping_versioning
from cinqflow.core.mapping.versioning import UnacknowledgedLossError, refuse_unacknowledged_loss
from cinqflow.core.model.governed import (
    GovernedObject,
    LifecycleState,
    LifecycleViolationError,
    ObjectType,
)
from cinqflow.core.navigation import ACTIVE_WAVE, for_roles
from cinqflow.core.parsers import parse
from cinqflow.core.persona import home_for
from cinqflow.core.phi import Basis, ColumnClassification, PhiDowngradeRefusedError, reclassify
from cinqflow.core.profiling import ColumnProfile, FileProfile, Finding
from cinqflow.core.proposals import Proposal, ProposalState
from cinqflow.core.registry import canonical, operations, suspension
from cinqflow.core.registry import clone as registry_clone
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry import search as registry_search
from cinqflow.core.registry import source as source_registry
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.registry.execution_plane import ExecutionPlaneRegister
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.registry.wave0 import wave_0_register
from cinqflow.core.rules import preview as rule_preview
from cinqflow.core.schema_spec import TypeName
from cinqflow.core.security import Action, may
from cinqflow.core.tools import CATALOGUE, ToolError
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.agents.phi_detection import PhiDetectionAgent, RecallGateFailedError
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
from cinqflow.intelligence.agents.rule_authoring import RuleAuthoringAgent
from cinqflow.intelligence.agents.schema_inference import SchemaInferenceAgent
from cinqflow.intelligence.tools import ToolContext, ToolResult, all_dq_rule_entries, invoke
from cinqflow.ports.authn import AuthnPort, Principal
from cinqflow.ports.control_tables import BatchControl, ControlTablesPort
from cinqflow.ports.metadata_db import FileProfileRecord, MetadataDbPort, ObjectNotFoundError
from cinqflow.ports.storage import StoragePort
from cinqflow.workers.profiler import Profiler, ProfileTargetMissingError

API_PREFIX = "/api"

#: CF-V1-E5-02's gate. Declared here because the acceptance route reports
#: against it, and a threshold that lives only in a test is a threshold the
#: people it is about never see.
SCHEMA_ACCEPTANCE_GATE = 0.90

#: CF-V1-E5-03's agent name, imported rather than spelled, so a rename cannot
#: leave the routes filtering for an agent that no longer exists.
PHI_AGENT = PHI_DETECTION_AGENT

#: CF-V1-E6-02's agent name, imported for the same reason.
MAPPING_AGENT = MAPPING_SUGGESTION_AGENT

#: CF-V1-E7-01's, likewise.
RULE_AGENT = RULE_AUTHORING_AGENT

#: Build an agent for ONE caller. Never a shared agent — a shared agent is a
#: shared scope, and the tool context is where the caller's RBAC lives.
AgentFactory = Callable[[Principal, "ControlTablesPort", MetadataDbPort], PipelineInsightAgent]

#: Built per REQUEST rather than per app: the agent writes a proposal through
#: whichever metadata pin the request is served by, which on the real plane is
#: a per-request transaction.
SchemaInferenceFactory = Callable[[MetadataDbPort], SchemaInferenceAgent]

#: Same reasoning, same lifetime. CF-V1-E5-03.
PhiDetectionFactory = Callable[[MetadataDbPort], PhiDetectionAgent]

#: Same reasoning, same lifetime. CF-V1-E6-02.
MappingSuggestionFactory = Callable[[MetadataDbPort], MappingSuggestionAgent]

#: Same reasoning, same lifetime. CF-V1-E7-01.
RuleAuthoringFactory = Callable[[MetadataDbPort], RuleAuthoringAgent]


@unique
class BatchPanel(StrEnum):
    """The drawer's tabs. Exactly one depth level exists in Wave 0."""

    STAGES = "stages"
    INPUTS = "inputs"
    ERRORS = "errors"
    QUARANTINE = "quarantine"
    RECON = "recon"


#: Each panel is one certified tool. The drawer has no private query.
_PANEL_TOOLS: dict[BatchPanel, str] = {
    BatchPanel.STAGES: "get_stage_status",
    BatchPanel.INPUTS: "get_input_registry",
    BatchPanel.ERRORS: "list_errors",
    BatchPanel.QUARANTINE: "get_quarantine_summary",
    BatchPanel.RECON: "get_reconciliation",
}


# Resolved from app.state, at MODULE level. A dependency defined inside
# `create_app` is invisible to `get_type_hints`, which resolves annotations
# against module globals — FastAPI would silently read it as a query parameter.
def _store(request: Request) -> MetadataDbPort:
    return request.app.state.metadata_db  # type: ignore[no-any-return]


def _audit(request: Request) -> AuditLog:
    return request.app.state.wiring.audit  # type: ignore[no-any-return]


def _control(request: Request) -> ControlTablesPort:
    return request.app.state.control_tables  # type: ignore[no-any-return]


def _plane(request: Request) -> ExecutionPlaneRegister:
    return request.app.state.plane_register  # type: ignore[no-any-return]


def _authn(request: Request) -> AuthnPort:
    return request.app.state.wiring.authn  # type: ignore[no-any-return]


def _profiler(request: Request) -> Profiler | None:
    """The profiler, or None when no storage pin is fitted.

    None rather than a stub: a deployment with no landing zone should say so,
    and a stubbed profiler would answer a question about a file it never read.
    """
    storage = request.app.state.storage
    if storage is None:
        return None
    return Profiler(storage=storage, metadata=request.app.state.metadata_db)


Store = Annotated[MetadataDbPort, Depends(_store)]
Control = Annotated[ControlTablesPort, Depends(_control)]
Audit = Annotated[AuditLog, Depends(_audit)]
Plane = Annotated[ExecutionPlaneRegister, Depends(_plane)]
Directory = Annotated[AuthnPort, Depends(_authn)]


def create_app(
    *,
    authn: AuthnPort,
    metadata_db: MetadataDbPort,
    plane_register: ExecutionPlaneRegister | None = None,
    control_tables: ControlTablesPort | None = None,
    storage: StoragePort | None = None,
    agent_factory: AgentFactory | None = None,
    schema_inference_factory: SchemaInferenceFactory | None = None,
    phi_detection_factory: PhiDetectionFactory | None = None,
    mapping_suggestion_factory: MappingSuggestionFactory | None = None,
    rule_authoring_factory: RuleAuthoringFactory | None = None,
    budget: Budget | None = None,
) -> FastAPI:
    """Build the app from PINS.

    `agent_factory` is a callable rather than an agent, because an agent is
    per-caller: its tool context carries the principal, and a shared agent
    would be a shared scope. Passing `None` leaves the AI routes present but
    answering "not configured" — which is what a deployment without an LLM
    endpoint should do, rather than 500ing or hiding the screen.
    """
    app = FastAPI(
        title="CINQFLOW",
        version="0.1.0",
        summary="Wave 0 — Landing to Silver Raw, and the platform explaining itself.",
    )
    app.state.wiring = Wiring(authn=authn, audit=AuditLog(metadata_db))
    app.state.metadata_db = metadata_db
    app.state.plane_register = plane_register or wave_0_register()
    app.state.control_tables = control_tables or MemStoreControlTables()
    # No default. Unlike control tables, there is no in-memory landing zone
    # that would be honest here: a profiler pointed at an empty store would
    # answer "file not found" for every real sample.
    app.state.storage = storage
    app.state.agent_factory = agent_factory
    # None leaves the inference route answering "not configured" rather than
    # 500ing — the deterministic profile and the manual editor still work,
    # which is exactly what a deployment with no model endpoint should offer.
    app.state.schema_inference_factory = schema_inference_factory
    app.state.phi_detection_factory = phi_detection_factory
    app.state.mapping_suggestion_factory = mapping_suggestion_factory
    app.state.rule_authoring_factory = rule_authoring_factory
    # The cap the observability screen reports against. A screen showing spend
    # with no cap beside it is a number, not a control.
    app.state.llm_budget = budget or Budget(
        per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5.00")
    )

    # ── who am I ─────────────────────────────────────────────────────────────

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Unauthenticated on purpose, and it returns nothing about the estate.

        A health check that reports which feeds are configured is an
        unauthenticated inventory endpoint.
        """
        return {"status": "ok"}

    @app.get(f"{API_PREFIX}/me", response_model=PrincipalOut, tags=["identity"])
    def me(principal: CurrentPrincipal) -> PrincipalOut:
        """Reachable by a user in NO group, deliberately.

        That person must land on a clear "no access assigned — contact your
        administrator" page. Treating them as an error hands them a broken
        application instead of an answer.
        """
        return _principal_out(principal)

    # ── feeds ────────────────────────────────────────────────────────────────

    @app.get(f"{API_PREFIX}/feeds", response_model=list[FeedOut], tags=["intake"])
    def list_feeds(
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        q: str = "",
        domain: str = "",
        source_system: str = "",
        source_id: str = "",
        delivery_method: str = "",
        state: str = "",
        owner: str = "",
        not_ready: bool = False,
    ) -> list[FeedOut]:
        """Scope is applied while the list is BUILT, never to a finished response.

        "Apply a scope filter to results rather than to the query"
        — INVARIANTS.md, a documented don't

        CF-V1-E3-03's filters are applied in the SAME pass, and after the
        scope check rather than before it: an out-of-scope feed must be
        invisible in search exactly as it is in the list, or the search box
        becomes the way to find out which feeds exist.
        """
        visible = [
            obj
            for obj in metadata.list(ObjectType.FEED)
            if principal.scopes.covers_feed(obj.object_id)
        ]
        criteria = registry_search.FeedFilter(
            text=q,
            domain=domain,
            source_system=source_system,
            source_id=source_id,
            delivery_method=delivery_method,
            lifecycle_state=state,
            owner=owner,
            not_ready=not_ready,
        )
        readiness = {obj.object_id: operations.readiness_of(obj).is_ready for obj in visible}
        return [
            _feed_out(obj) for obj in registry_search.search(visible, criteria, readiness=readiness)
        ]

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/similar",
        response_model=list[SimilarFeedOut],
        tags=["intake"],
    )
    def similar_feeds(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        limit: int = 5,
    ) -> list[SimilarFeedOut]:
        """Which feeds this one is worth cloning from, and why. CF-V1-E3-03.

        Deterministic arithmetic over the registry's own structured fields —
        so "why is this first" has an answer in one line, and no model is
        involved in a question the data already settles.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        target = _load(metadata, feed_id)
        candidates = [
            obj
            for obj in metadata.list(ObjectType.FEED)
            if principal.scopes.covers_feed(obj.object_id)
        ]
        return [
            SimilarFeedOut(
                feed_id=match.feed_id,
                score=match.score,
                reasons=list(match.reasons),
                lifecycle_state=match.feed.lifecycle_state.value,
                domain=str(match.feed.body.get("domain", "")),
                source_system=str(match.feed.body.get("source_system", "")),
            )
            for match in registry_search.similar_to(target, candidates, limit=limit)
        ]

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/clone",
        response_model=CloneOut,
        status_code=status.HTTP_201_CREATED,
        tags=["intake"],
    )
    def clone_feed(
        feed_id: str,
        body: CloneFeedIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.CREATE_FEED))],
    ) -> CloneOut:
        """Copy a feed's configuration into fresh drafts. CF-V1-E3-03.

        CONFIGURATION IS INHERITED, HISTORY IS NOT. The clone gets the
        contract, the mappings and the rules; it gets none of the approval,
        none of the audit trail and none of the evidence. A clone that
        arrived Approved would let one review approve two feeds.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        original = _load(metadata, feed_id)

        registry: list[GovernedObject] = []
        for object_type in ObjectType:
            registry.extend(metadata.list(object_type))

        try:
            result = registry_clone.clone_feed(
                original,
                registry,
                new_feed_id=body.new_feed_id,
                author=principal.as_actor(),
                overrides=body.overrides,
                include=(
                    frozenset(ObjectType(t) for t in body.include)
                    if body.include is not None
                    else None
                ),
            )
        except (registry_clone.CloneError, ValueError) as refused:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(refused)) from None

        try:
            metadata.get(ObjectType.FEED, body.new_feed_id)
        except ObjectNotFoundError:
            pass
        else:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"feed {body.new_feed_id!r} already exists. A clone creates a new feed; "
                "editing an existing one is a new version of that feed.",
            )

        for obj in result.objects:
            metadata.save(obj)
            audit.record(
                object_type=obj.object_type,
                object_id=obj.object_id,
                version=obj.version,
                action="cloned",
                actor=principal.as_actor(),
                detail=f"from {feed_id}@v{original.version}",
            )

        return CloneOut(
            feed_id=result.feed_id,
            cloned_from=result.cloned_from,
            cloned_from_version=result.cloned_from_version,
            created=[_governed_out(obj) for obj in result.objects],
            inherited=[
                InheritedOut(
                    object_type=item.object_type.value,
                    source_object_id=item.source_object_id,
                    source_version=item.source_version,
                    source_state=item.source_state.value,
                    new_object_id=item.new_object_id,
                    was_approved=item.was_approved,
                )
                for item in result.inherited
            ],
            differences=[
                DifferenceOut(
                    object_type=d.object_type.value,
                    field_path=d.field_path,
                    original=d.original,
                    clone=d.clone,
                )
                for d in result.differences
            ],
            warnings=list(result.warnings),
        )

    @app.get(f"{API_PREFIX}/feeds/{{feed_id}}", response_model=FeedOut, tags=["intake"])
    def get_feed(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        version: int | None = None,
    ) -> FeedOut:
        """A `feed:<id>@v<n>` citation pins a version — the page it opens must
        show THAT version, or the citation is a quiet lie about what a run
        actually used."""
        return _feed_out(_load(metadata, feed_id, version))

    @app.post(
        f"{API_PREFIX}/feeds",
        response_model=FeedOut,
        status_code=status.HTTP_201_CREATED,
        tags=["intake"],
    )
    def create_feed(
        body: FeedIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.CREATE_FEED))],
    ) -> FeedOut:
        """Created as a DRAFT. Nothing arrives Published, so nothing can be
        created already-executable."""
        record = _record_from(body)
        obj = record.as_governed(author=principal.as_actor())
        obj = replace(obj, body={**obj.body, "operations": _operations_body(body)})
        saved = metadata.save(obj)
        audit.record(
            object_type=ObjectType.FEED,
            object_id=saved.object_id,
            version=saved.version,
            action="create",
            actor=principal.as_actor(),
        )
        return _feed_out(saved)

    @app.put(f"{API_PREFIX}/feeds/{{feed_id}}", response_model=FeedOut, tags=["intake"])
    def edit_feed(
        feed_id: str,
        body: FeedIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
    ) -> FeedOut:
        """THE guardrail route.

        A Read-Only user who crafts this URL never reaches this function: the
        dependency refuses first and writes the attempt to the ledger. The test
        for that was written before this handler existed.

        An edit is a NEW VERSION in Draft. The published object stays exactly as
        it was approved — which is what makes "promoted configuration is
        byte-identical to what was approved" true rather than aspirational.
        """
        current = _load(metadata, feed_id)
        record = _record_from(body)
        if record.feed_id != feed_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"body names {record.feed_id!r} but the URL names {feed_id!r} — "
                "an edit that renames the thing it edits is a create in disguise",
            )
        # An absent envelope KEEPS the stored one rather than clearing it. An
        # edit to the schedule must not silently drop the owners and the SLA,
        # and a PUT that quietly empties fields the caller did not mention is
        # how a feed becomes un-activatable without anybody touching it.
        envelope = current.body.get("operations") or {}
        if body.operations is not None:
            envelope = _operations_body(body)
        amended = current.new_version(
            {**record.as_governed(author=principal.as_actor()).body, "operations": envelope},
            actor=principal.as_actor(),
        )
        saved = metadata.save(amended)
        audit.record(
            object_type=ObjectType.FEED,
            object_id=saved.object_id,
            version=saved.version,
            action="amend",
            actor=principal.as_actor(),
        )
        return _feed_out(saved)

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/readiness",
        response_model=ReadinessOut,
        tags=["intake"],
    )
    def feed_readiness(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ReadinessOut:
        """What is still missing before this feed can be operated. CF-V1-E3-02.

        The SAME function the lifecycle refuses with, so the form and the
        submit button cannot disagree.
        """
        return _readiness_out(_load(metadata, feed_id))

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/references",
        response_model=ReferencesOut,
        tags=["intake"],
    )
    def feed_references(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ReferencesOut:
        """Everything that would be affected by changing this feed.

        The "referenced everywhere" view, COMPUTED from the reference graph.
        A registry whose "used by" column is hand-maintained is a registry
        whose "used by" column is wrong — and the version people trust most is
        the one nobody has updated since March.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return _references_out(metadata, _load(metadata, feed_id))

    # ── pause and resume · CF-V1-E3-04 ───────────────────────────────────────
    #
    # OPERATIONAL, not governance. `RUN_PIPELINE` rather than `APPROVE`,
    # because stopping and starting a feed is an operator's decision — and
    # requiring an approver to lift a pause would mean finding a steward at
    # 3am to turn the tap back on.

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/pause",
        response_model=SuspensionOut,
        tags=["operations"],
    )
    def pause_feed(
        feed_id: str,
        body: PauseFeedIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.RUN_PIPELINE))],
    ) -> SuspensionOut:
        """Stop new work. Anything already running finishes."""
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        _load(metadata, feed_id)
        try:
            event = suspension.pause(
                feed_id,
                actor=principal.as_actor(),
                reason=body.reason,
                now=datetime.now(UTC),
                resumes_after=body.resumes_after,
            )
        except suspension.SuspensionError as refused:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(refused)) from None

        metadata.record_suspension(event)
        audit.record(
            object_type=ObjectType.FEED,
            object_id=feed_id,
            action="paused",
            actor=principal.as_actor(),
            detail=body.reason,
        )
        return _suspension_out(metadata.current_suspension(feed_id))

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/resume",
        response_model=SuspensionOut,
        tags=["operations"],
    )
    def resume_feed(
        feed_id: str,
        body: ResumeFeedIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.RUN_PIPELINE))],
    ) -> SuspensionOut:
        """Start it again. No reason required and no approver — see
        `core.registry.suspension.resume` for why that asymmetry is right."""
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        _load(metadata, feed_id)
        metadata.record_suspension(
            suspension.resume(
                feed_id, actor=principal.as_actor(), now=datetime.now(UTC), reason=body.reason
            )
        )
        audit.record(
            object_type=ObjectType.FEED,
            object_id=feed_id,
            action="resumed",
            actor=principal.as_actor(),
            detail=body.reason,
        )
        return _suspension_out(metadata.current_suspension(feed_id))

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/suspension",
        response_model=SuspensionOut,
        tags=["operations"],
    )
    def feed_suspension(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> SuspensionOut:
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return _suspension_out(metadata.current_suspension(feed_id))

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/suspensions",
        response_model=list[SuspensionEventOut],
        tags=["operations"],
    )
    def feed_suspension_history(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        limit: int = 50,
    ) -> list[SuspensionEventOut]:
        """The pause ledger. Read beside the version history: together they
        answer "which version was live in March, and was it running?"."""
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return [
            SuspensionEventOut(
                feed_id=event.feed_id,
                action=event.action.value,
                reason=event.reason,
                actor_subject=event.actor.subject,
                actor_name=event.actor.display_name,
                occurred_ts=event.occurred_ts,
                resumes_after=event.resumes_after,
            )
            for event in metadata.list_suspensions(feed_id=feed_id, limit=limit)
        ]

    # ── version history and the side-by-side diff · CF-V1-E3-04 ──────────────

    @app.get(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/history",
        response_model=list[GovernedOut],
        tags=["governance"],
    )
    def object_history(
        object_type: ObjectType,
        object_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> list[GovernedOut]:
        """Every version, newest first — "which version was live in March?"
        is one click, because every version is still here."""
        if object_type is ObjectType.FEED and not principal.scopes.covers_feed(object_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        versions = metadata.history(object_type, object_id)
        if not versions:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return [_governed_out(obj) for obj in reversed(versions)]

    @app.get(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/diff",
        response_model=VersionDiffOut,
        tags=["governance"],
    )
    def object_diff(
        object_type: ObjectType,
        object_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        from_version: int | None = None,
        to_version: int | None = None,
    ) -> VersionDiffOut:
        """Two versions, side by side. CF-V1-E3-04.

        Defaults to the previous version against the latest, because that is
        the comparison somebody wants nine times in ten and asking them to
        type two numbers to get it is a screen they stop using.

        The SAME `differences` computation the clone panel uses — "how does
        this differ" has one answer in the platform, not one per screen.
        """
        if object_type is ObjectType.FEED and not principal.scopes.covers_feed(object_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        versions = metadata.history(object_type, object_id)
        if len(versions) < 2 and (from_version is None or to_version is None):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{object_type.value}:{object_id} has only {len(versions)} version — "
                "there is nothing to compare it with yet.",
            )
        by_version = {obj.version: obj for obj in versions}
        left = by_version.get(from_version if from_version is not None else versions[-2].version)
        right = by_version.get(to_version if to_version is not None else versions[-1].version)
        if left is None or right is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)

        return VersionDiffOut(
            object_type=object_type.value,
            object_id=object_id,
            from_version=left.version,
            to_version=right.version,
            from_state=left.lifecycle_state.value,
            to_state=right.lifecycle_state.value,
            from_author=left.created_by.subject,
            to_author=right.created_by.subject,
            differences=[
                DifferenceOut(
                    object_type=d.object_type.value,
                    field_path=d.field_path,
                    original=d.original,
                    clone=d.clone,
                )
                for d in registry_clone.differences_between(left, right)
            ],
        )

    # ── sources · CF-V1-E3-02 ────────────────────────────────────────────────

    @app.get(f"{API_PREFIX}/sources", response_model=list[SourceOut], tags=["intake"])
    def list_sources(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> list[SourceOut]:
        return [
            _source_out(obj, _feeds_of_source(metadata, obj.object_id))
            for obj in metadata.list(ObjectType.SOURCE)
        ]

    @app.get(f"{API_PREFIX}/sources/{{source_id}}", response_model=SourceOut, tags=["intake"])
    def get_source(
        source_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        version: int | None = None,
    ) -> SourceOut:
        try:
            obj = metadata.get(ObjectType.SOURCE, source_id, version)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        return _source_out(obj, _feeds_of_source(metadata, source_id))

    @app.post(
        f"{API_PREFIX}/sources",
        response_model=SourceOut,
        status_code=status.HTTP_201_CREATED,
        tags=["intake"],
    )
    def create_source(
        body: SourceIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.CREATE_FEED))],
    ) -> SourceOut:
        """Created as a DRAFT, at v1, like every governed object."""
        saved = metadata.save(_source_from(body).as_governed(author=principal.as_actor()))
        audit.record(
            object_type=ObjectType.SOURCE,
            object_id=saved.object_id,
            version=saved.version,
            action="create",
            actor=principal.as_actor(),
        )
        return _source_out(saved)

    @app.put(f"{API_PREFIX}/sources/{{source_id}}", response_model=SourceOut, tags=["intake"])
    def edit_source(
        source_id: str,
        body: SourceIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
    ) -> SourceOut:
        """An edit is a NEW VERSION in Draft — never an in-place change."""
        try:
            current = metadata.get(ObjectType.SOURCE, source_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        record = _source_from(body)
        if record.source_id != source_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"body names {record.source_id!r} but the URL names {source_id!r} — "
                "an edit that renames the thing it edits is a create in disguise",
            )
        saved = metadata.save(current.new_version(record.as_body(), actor=principal.as_actor()))
        audit.record(
            object_type=ObjectType.SOURCE,
            object_id=saved.object_id,
            version=saved.version,
            action="amend",
            actor=principal.as_actor(),
        )
        return _source_out(saved, _feeds_of_source(metadata, source_id))

    # ── the canonical model browser · CF-V1-E6-01 ────────────────────────────
    #
    # "You cannot map to a model you cannot see." Both halves are GENERATED —
    # the deployed one from the DDL spec the conformance kit checks the
    # database against, the designed one from the client's own glossary — so
    # there is no third list to drift from.

    @app.get(f"{API_PREFIX}/canonical", response_model=CanonicalModelOut, tags=["glossary"])
    def canonical_model(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        domain: str = "",
    ) -> CanonicalModelOut:
        """Domains, entities and their field counts. Fields on request."""
        model = _canonical_of(metadata)
        entities = model.in_domain(domain) if domain else model.entities
        return CanonicalModelOut(
            domains=list(model.domains),
            entities=[_canonical_entity_out(e, with_fields=False) for e in entities],
            deployed_entities=len(model.deployed),
            designed_not_deployed=[e.name for e in model.gap],
            defined_fields=model.coverage[0],
            total_fields=model.coverage[1],
            unclaimed_tables=list(model.unclaimed_tables),
        )

    @app.get(
        f"{API_PREFIX}/canonical/search",
        response_model=list[CanonicalFieldOut],
        tags=["glossary"],
    )
    def canonical_search(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        q: str = "",
        limit: int = 50,
    ) -> list[CanonicalFieldOut]:
        """Business term OR column name. `date of birth` finds
        `Member_Date_Of_Birth`, and so does `DOB` — the payer's spelling and
        the canonical one are the same question asked by two people."""
        return [_canonical_field_out(f) for f in _canonical_of(metadata).search(q)[:limit]]

    @app.get(
        f"{API_PREFIX}/canonical/{{entity}}",
        response_model=CanonicalEntityOut,
        tags=["glossary"],
    )
    def canonical_entity(
        entity: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> CanonicalEntityOut:
        found = _canonical_of(metadata).entity(entity)
        if found is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"the canonical model has no entity {entity!r}. It is generated from the "
                "deployed schemas and the business glossary — if this should exist, it "
                "belongs in one of those.",
            )
        return _canonical_entity_out(found, with_fields=True)

    # ── the mapping studio · CF-V1-E6-03 ─────────────────────────────────────
    #
    # The MANUAL editor, and it is built before CF-V1-E6-02's agent on purpose:
    # "humans must be able to do by hand everything the AI proposes" is only
    # true by construction if the hand-authoring vocabulary exists first. The
    # agent then proposes INTO this shape rather than inventing one.
    #
    # A mapping is a governed object routed to the DATA STEWARD, so there is no
    # route here that publishes one — saving produces a DRAFT, and the
    # lifecycle routes below carry it the rest of the way, exactly like a feed.

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/mapping",
        response_model=MappingOut,
        tags=["mapping"],
    )
    def get_mapping(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        version: int | None = None,
    ) -> MappingOut:
        """The mapping and its findings, validated against both ends.

        Findings travel with the GET rather than only with the save, because a
        mapping approved months ago can be invalidated by a change at either
        end — a contract column renamed, a canonical field's PHI flag set —
        and a reviewer opening it should see that immediately.
        """
        try:
            obj = metadata.get(ObjectType.MAPPING, feed_id, version)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no mapping for feed {feed_id!r} yet — the canonical browser is at "
                "/api/canonical, and a mapping is created by PUT on this route",
            ) from None
        return _mapping_out(metadata, obj)

    @app.put(
        f"{API_PREFIX}/feeds/{{feed_id}}/mapping",
        response_model=MappingOut,
        tags=["mapping"],
    )
    def save_mapping(
        feed_id: str,
        body: MappingIn,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> MappingOut:
        """Save a mapping as a new DRAFT version.

        SAVE IS PERMISSIVE, exactly as CF-V1-E3-02 made it for a feed: a
        half-authored mapping must store, because a BA who has settled forty
        lines and is waiting on a payer to explain the forty-first needs
        somewhere to keep the forty. What is refused is a mapping that could
        not be built at all — a duplicate target, an unknown column — because
        those are not gaps, they are contradictions, and storing one produces
        a review screen nobody can act on.

        The BLOCKING findings gate SUBMISSION, not saving. That gate lives in
        the transition route, where the readiness gate for feeds lives.
        """
        try:
            mapping = _mapping_from(feed_id, body, version=_next_mapping_version(metadata, feed_id))
        except mapping_core.MappingError as refused:
            # 400 with the core's own sentence, not a 422 shaped by the model
            # layer. `MappingError` messages say what to DO — "an unmapped
            # field is a DECISION" — and a schema complaint would replace that
            # with the name of a field the author can see is empty.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None
        findings = _validate_mapping(metadata, mapping)
        contradictions = [f for f in mapping_core.blocking(findings) if f.key == "duplicate_target"]
        if contradictions:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "; ".join(f"{f.address}: {f.what}" for f in contradictions),
            )
        draft = mapping_core.mapping_as_governed(
            mapping,
            author=principal.as_actor(),
            business_consumers=tuple(body.business_consumers),
        )
        stored = metadata.save(draft)
        audit.record(
            object_type=ObjectType.MAPPING,
            object_id=feed_id,
            version=stored.version,
            action="saved_mapping",
            actor=principal.as_actor(),
            detail=(
                f"{mapping.coverage[0]}/{mapping.coverage[1]} fields mapped, "
                f"{len(mapping.unmapped)} explicitly unmapped, "
                f"{len(mapping_core.blocking(findings))} blocking finding(s)"
            ),
        )
        return _mapping_out(metadata, stored)

    # ── the business glossary · CF-V1-E14-01 ─────────────────────────────────

    @app.get(f"{API_PREFIX}/glossary", response_model=list[GlossaryTermOut], tags=["glossary"])
    def list_glossary(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        search: str | None = None,
        phi_only: bool = False,
    ) -> list[GlossaryTermOut]:
        """The 171 terms, searchable by business term OR column name — "date of
        birth" finds `date_of_birth`, which is what makes the glossary usable
        by the people who wrote it rather than only by engineers."""
        found = _glossary_of(metadata)
        terms = found.search(search) if search else found.terms
        if phi_only:
            terms = tuple(t for t in terms if t.is_phi)
        states = _glossary_states(metadata)
        return [_glossary_out(t, *states.get(t.glossary_id, ("draft", 1))) for t in terms]

    @app.get(
        f"{API_PREFIX}/glossary/{{glossary_id}}",
        response_model=GlossaryTermOut,
        tags=["glossary"],
    )
    def get_glossary_term(
        glossary_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> GlossaryTermOut:
        """What a `term:<slug>` citation opens, and what a BA sees on hover:
        the approved definition, its PHI status, and every table that uses
        it."""
        try:
            obj = metadata.get(ObjectType.GLOSSARY_TERM, glossary_id)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no glossary term {glossary_id!r}"
            ) from None
        return _glossary_out(
            GlossaryTerm.from_governed(obj), obj.lifecycle_state.value, obj.version
        )

    @app.get(
        f"{API_PREFIX}/glossary-for-column/{{column_name}}",
        response_model=list[GlossaryTermOut],
        tags=["glossary"],
    )
    def glossary_for_column(
        column_name: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> list[GlossaryTermOut]:
        """The mapping studio's first question, answered deterministically.

        An exact synonym match costs no tokens and no model call — CF-V1-E6-02
        proposes only where this returns nothing, which is what keeps the AI
        working on the hard fields instead of the obvious ones.
        """
        states = _glossary_states(metadata)
        return [
            _glossary_out(t, *states.get(t.glossary_id, ("draft", 1)))
            for t in _glossary_of(metadata).for_column(column_name)
        ]

    # ── the deterministic profiler · CF-V1-E5-01 ─────────────────────────────
    #
    # Step 1 of the wizard. Everything these routes return is arithmetic over
    # the file's bytes: no model is called, nothing is inferred, and the AI
    # stories downstream cite `profile:<id>#<column>` for every fact they
    # interpret.

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/profile",
        response_model=FileProfileOut,
        tags=["profiling"],
    )
    def profile_sample(
        feed_id: str,
        body: ProfileIn,
        request: Request,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> FileProfileOut:
        """Profile a landed sample file.

        Synchronous, because a sample profiles in seconds and a BA staring at a
        spinner they cannot poll is worse than a request that takes a moment.
        A file that cannot be read comes back 200 with `readable: false` and a
        refusal — it is a fact about the file, not a failure of the request,
        and a 500 would tell the BA nothing they can act on.
        """
        profiler = _profiler(request)
        if profiler is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no storage pin is fitted on this deployment, so there is no landing zone "
                "to read a sample from.",
            )
        try:
            record = profiler.profile(
                feed_id=feed_id,
                file_key=body.file_key,
                file_format=body.file_format,
                encoding=body.encoding,
                delimiter=body.delimiter,
                profiled_by=principal.subject,
            )
        except ProfileTargetMissingError as missing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(missing)) from None
        audit.record(
            object_type=ObjectType.FEED,
            object_id=feed_id,
            action="profile_file",
            actor=principal.as_actor(),
            detail=f"{body.file_key} -> {record.profile_id}",
        )
        return _profile_out(record, principal)

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/profiles",
        response_model=list[FileProfileOut],
        tags=["profiling"],
    )
    def list_feed_profiles(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        limit: int = 20,
    ) -> list[FileProfileOut]:
        """Every profiling run for this feed, newest first — the status view.

        More than one is normal and informative: a payer's file changing shape
        between samples is visible here as two profiles with different
        fingerprints.
        """
        return [
            _profile_out(record, principal)
            for record in metadata.list_profiles(feed_id=feed_id, limit=limit)
        ]

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/profiles/{{profile_id}}",
        response_model=FileProfileOut,
        tags=["profiling"],
    )
    def get_feed_profile(
        feed_id: str,
        profile_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> FileProfileOut:
        """What a `profile:<id>` citation opens."""
        try:
            record = metadata.get_profile(profile_id, feed_id)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"no profile {profile_id!r} for feed {feed_id!r}"
            ) from None
        return _profile_out(record, principal)

    @app.get(
        f"{API_PREFIX}/profiles/{{profile_id}}",
        response_model=FileProfileOut,
        tags=["profiling"],
    )
    def resolve_profile_citation(
        profile_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> FileProfileOut:
        """Resolve `profile:<id>` without being told which feed it belongs to.

        A citation carries an id, not a feed — so a citation that could only be
        resolved by someone who already knew the feed would be a dead end
        wearing an address. That is the one thing the citation vocabulary is
        not allowed to contain.
        """
        found = metadata.list_profiles(profile_id=profile_id, limit=1)
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no profile {profile_id!r}")
        return _profile_out(found[0], principal)

    # ── AI schema inference · CF-V1-E5-02 ────────────────────────────────────
    #
    # R2 · config_proposal. These routes create and decide PROPOSALS. There is
    # deliberately no route that turns a proposal into a published contract:
    # approval creates a DRAFT authored by the approver, which then travels
    # E11-01's lifecycle — so the approver of the proposal cannot approve the
    # object, and the agent's output enters the world at the same door a
    # hand-typed draft does.

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/infer-schema",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def infer_schema(
        feed_id: str,
        body: InferSchemaIn,
        request: Request,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> ProposalOut:
        """Read a stored profile, propose a contract, leave one proposal row.

        503 rather than a stub when no LLM pin is fitted — but note that a feed
        whose columns the profiler and glossary both settled produces a full
        proposal with NO model call at all, so this route is useful on a
        deployment with no model endpoint whenever the payer names things
        sensibly.
        """
        agent_factory = request.app.state.schema_inference_factory
        if agent_factory is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no LLM pin is fitted on this deployment, so schema inference is not "
                "available. The deterministic profile is still on the feed's profile page, "
                "and the manual contract editor still works.",
            )
        try:
            record = metadata.get_profile(body.profile_id, feed_id)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no profile {body.profile_id!r} for feed {feed_id!r} — profile the sample first",
            ) from None

        result = agent_factory(metadata).propose(
            record.profile,
            feed_id=feed_id,
            glossary=_glossary_of(metadata),
            caller=principal.as_actor(),
        )
        audit.record(
            object_type=ObjectType.FEED,
            object_id=feed_id,
            action="propose_schema",
            actor=principal.as_actor(),
            detail=(
                f"{result.proposal.proposal_id} · {len(result.columns)} columns, "
                f"{len(result.needs_input)} needing input, "
                f"model_called={result.model_called}"
            ),
        )
        return _proposal_out(result.proposal, model_called=result.model_called)

    # ── PHI and code-set detection · CF-V1-E5-03 ─────────────────────────────

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/detect-phi",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def detect_phi(
        feed_id: str,
        body: DetectPhiIn,
        request: Request,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> ProposalOut:
        """Classify every column of a stored profile: PHI, code set, or neither.

        A 500 rather than a proposal if the recall gate fails, deliberately.
        `RecallGateFailedError` means the detector did not protect a column the
        client's own glossary flags — that is a broken control, not a
        low-quality suggestion, and it must not reach a queue where somebody
        could approve it.
        """
        agent_factory = request.app.state.phi_detection_factory
        if agent_factory is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no LLM pin is fitted on this deployment, so PHI detection is not "
                "available. Note that the glossary flags, the value-shape arithmetic "
                "and the free-text rule need no model — fit the pin to have the "
                "remaining columns named.",
            )
        try:
            record = metadata.get_profile(body.profile_id, feed_id)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no profile {body.profile_id!r} for feed {feed_id!r} — profile the sample first",
            ) from None

        try:
            result = agent_factory(metadata).propose(
                record.profile,
                feed_id=feed_id,
                glossary=_glossary_of(metadata),
                caller=principal.as_actor(),
            )
        except RecallGateFailedError as broken:
            audit.record(
                object_type=ObjectType.FEED,
                object_id=feed_id,
                action="refused:detect_phi",
                actor=principal.as_actor(),
                detail=str(broken),
            )
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(broken)) from None

        audit.record(
            object_type=ObjectType.FEED,
            object_id=feed_id,
            action="detect_phi",
            actor=principal.as_actor(),
            detail=(
                f"{result.proposal.proposal_id} · {len(result.phi_columns)} of "
                f"{len(result.classification.columns)} columns protected, "
                f"{len(result.needs_steward_review)} awaiting a steward, "
                f"model_called={result.model_called}"
            ),
        )
        return _proposal_out(result.proposal, model_called=result.model_called)

    # ── AI source→target mapping · CF-V1-E6-02 ───────────────────────────────

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/suggest-mapping",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def suggest_mapping(
        feed_id: str,
        request: Request,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> ProposalOut:
        """Propose a target for every column of the feed's contract.

        The mapping is proposed against the CONTRACT rather than a raw file:
        the contract is what a human approved as the shape of this feed, and
        mapping from anything else would map columns nobody agreed exist.

        Precedents come from PUBLISHED mappings only — never a draft. Grounding
        a suggestion in somebody's unreviewed work would launder an unapproved
        decision into a second feed, where it would arrive wearing the
        authority of precedent.
        """
        agent_factory = request.app.state.mapping_suggestion_factory
        if agent_factory is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no LLM pin is fitted on this deployment, so mapping suggestion is not "
                "available. The canonical browser and the manual mapping editor still "
                "work, and a feed whose columns the glossary already names needs no "
                "model at all.",
            )
        contract = _contract_of(metadata, feed_id)
        if contract is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has no schema contract yet. A mapping is proposed "
                "against the contract — the shape a human approved — so approve one first.",
            )

        published, approvers = _published_mappings(metadata, exclude=feed_id)
        result = agent_factory(metadata).propose(
            contract,
            feed_id=feed_id,
            glossary=_glossary_of(metadata),
            model=_canonical_of(metadata),
            caller=principal.as_actor(),
            published_mappings=published + _own_published_mapping(metadata, feed_id),
            approvers=approvers,
        )
        audit.record(
            object_type=ObjectType.MAPPING,
            object_id=feed_id,
            action="suggest_mapping",
            actor=principal.as_actor(),
            detail=(
                f"{result.proposal.proposal_id} · {len(result.lines) - len(result.unmapped)} "
                f"of {len(result.lines)} columns mapped, {len(result.unmapped)} left for a "
                f"human, {len(result.refusals)} refusal(s), "
                f"model_called={result.model_called}"
            ),
        )
        return _proposal_out(result.proposal, model_called=result.model_called)

    # ── NL rule authoring and preview · CF-V1-E7-01, CF-V1-E7-02 ─────────────
    #
    # The model in this path never produces SQL. It names a check from
    # `core.rules.CheckKind` and gives its parameters; the platform renders the
    # SQL, the PySpark and the row predicate. There is therefore no route here
    # that could accept an executable string, and none that needs to.

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/author-rules",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def author_rules(
        feed_id: str,
        body: AuthorRulesIn,
        request: Request,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> ProposalOut:
        """Plain English in, one proposal out — with a preview attached.

        The PREVIEW travels with the proposal (CF-V1-E7-02) rather than being a
        second call somebody might not make. Trust is built in the preview, not
        the prose: a rule reading "member first name must be populated" is
        agreeable on any screen, and what a reviewer needs is that it fails 3
        of 200 rows.
        """
        agent_factory = request.app.state.rule_authoring_factory
        if agent_factory is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no LLM pin is fitted on this deployment, so rule authoring is not "
                "available. The rule editor and the preview both still work — a rule "
                "written by hand previews exactly the same way.",
            )
        if not [line for line in body.stated if line.strip()]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "there is nothing to write a rule from"
            )
        contract = _contract_of(metadata, feed_id)
        if contract is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has no schema contract yet. A rule checks a contracted "
                "column, so approve a contract first.",
            )

        result = agent_factory(metadata).propose(
            tuple(body.stated),
            feed_id=feed_id,
            contract=contract,
            glossary=_glossary_of(metadata),
            caller=principal.as_actor(),
            published=_published_rules(metadata, feed_id),
            # CF-V1-E7-02 · the rows the preview runs over. Read HERE because
            # reading a file is a pin, and passed as data so the agent stays a
            # gateway and a store. Best effort: a feed with no profiled sample
            # yet still gets its rules written, and the payload says why the
            # counts are missing.
            sample=_sample_rows_or_none(
                metadata, request.app.state.storage, feed_id, body.profile_id
            ),
        )
        # CF-V1-E7-02 · SAVED AS EVIDENCE, and saved HERE rather than left to a
        # second call somebody might not make. The counts are what makes a rule
        # reviewable, and a proposal that arrived without them would be a
        # proposal reviewed on its prose — which is the failure the story
        # names. Attached to the proposal so it travels to the approver with
        # the rule it is evidence about.
        audit.record(
            object_type=ObjectType.DQ_RULE,
            object_id=feed_id,
            action="author_rules",
            actor=principal.as_actor(),
            detail=(
                f"{result.proposal.proposal_id} · {len(result.rules)} rule(s) written, "
                f"{len(result.needs_review)} needing technical review, "
                f"model_called={result.model_called}"
            ),
        )
        return _proposal_out(result.proposal, model_called=result.model_called)

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/preview-rules",
        response_model=RulePreviewPackOut,
        tags=["intelligence"],
    )
    def preview_rules(
        feed_id: str,
        body: AuthorRulesIn,
        request: Request,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> RulePreviewPackOut:
        """Run this feed's DRAFT rules over a stored sample. CF-V1-E7-02.

        VIEW rather than EDIT_FEED: seeing what a rule catches is reading, and
        a reviewer who cannot preview the rule they are being asked to approve
        is being asked to approve prose.
        """
        try:
            rules = rules_core.rules_from_governed(metadata.get(ObjectType.DQ_RULE, feed_id))
        except (ObjectNotFoundError, rules_core.RuleError):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has no rules yet — write some first",
            ) from None
        rows = _sample_rows(metadata, request.app.state.storage, feed_id, body.profile_id)
        return _preview_pack_out(feed_id, rules, rows, _contract_of(metadata, feed_id))

    @app.get(
        f"{API_PREFIX}/proposals/{{proposal_id}}/recall",
        response_model=PhiRecallOut,
        tags=["intelligence"],
    )
    def phi_recall(
        proposal_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> PhiRecallOut:
        """The 100% recall gate, recomputed from the stored classification.

        Exposed rather than kept in the eval for the same reason the
        acceptance rate is: a control only CI can see is a control nobody
        maintains. Recomputed from the payload against the CURRENT glossary,
        so a term flagged PHI after the run shows up here as a miss — which is
        exactly the alert a steward wants.
        """
        try:
            proposal = metadata.get_proposal(proposal_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal {proposal_id!r}") from None
        return _recall_out(proposal, _glossary_of(metadata))

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/masking-policy",
        response_model=MaskingPolicyOut,
        tags=["intelligence"],
    )
    def masking_policy_for(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> MaskingPolicyOut:
        """What E2 would mask for this feed, from the newest classification.

        The newest — not the newest APPROVED. A classification awaiting review
        already masks everything it flagged, because "treated PHI until a
        steward decides" means the protection is in place while the decision
        is pending. `state` travels so the caller can see which it is.
        """
        candidates = metadata.list_proposals(feed_id=feed_id, agent=PHI_AGENT, limit=1)
        if not candidates:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no PHI classification for feed {feed_id!r} — run detection on a profile first",
            )
        return _masking_policy_out(candidates[0])

    @app.get(f"{API_PREFIX}/proposals", response_model=list[ProposalOut], tags=["intelligence"])
    def list_proposals(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        feed_id: str | None = None,
        agent: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[ProposalOut]:
        """The agent review queue. `state=pending_review` is the working view."""
        return [
            _proposal_out(p)
            for p in metadata.list_proposals(
                feed_id=feed_id,
                agent=agent,
                state=ProposalState(state) if state else None,
                limit=limit,
            )
        ]

    @app.get(
        f"{API_PREFIX}/proposals/{{proposal_id}}",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def get_proposal(
        proposal_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ProposalOut:
        try:
            return _proposal_out(metadata.get_proposal(proposal_id))
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal {proposal_id!r}") from None

    @app.post(
        f"{API_PREFIX}/proposals/{{proposal_id}}/approve",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def approve_proposal(
        proposal_id: str,
        body: ApproveProposalIn,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> ProposalOut:
        """Accept the suggestion, with whatever the reviewer changed.

        EDIT_FEED, not APPROVE. Accepting an agent's draft is authoring — the
        reviewer becomes the contract's author and therefore cannot approve the
        contract itself. Requiring the APPROVE permission here would hand the
        same person both halves of the segregation the platform exists to keep.
        """
        try:
            proposal = metadata.get_proposal(proposal_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal {proposal_id!r}") from None

        if proposal.agent == MAPPING_AGENT:
            # A mapping proposal produces a DRAFT MAPPING, not a contract.
            # Routed out here rather than folded into the branches below,
            # because the two differ in the object type, the key the
            # corrections are computed over, the fields compared and the
            # version sequence — four differences, which is a second path and
            # not a third conditional.
            return _accept_mapping_proposal(metadata, proposal, body, principal, audit)

        accepted = _apply_decisions(proposal.payload, body)
        corrections = proposals.diff_fields(
            proposal.payload,
            accepted,
            key="source_name",
            fields=("name", "type", "nullable", "is_phi", "date_format"),
        )
        if proposal.agent == PHI_AGENT:
            try:
                _refuse_downgrades_on_the_approve_path(corrections)
            except PhiDowngradeRefusedError as refused:
                audit.record(
                    object_type=ObjectType.CONTRACT,
                    object_id=proposal.feed_id or proposal_id,
                    action="refused:phi_downgrade",
                    actor=principal.as_actor(),
                    detail=str(refused),
                )
                raise HTTPException(status.HTTP_403_FORBIDDEN, str(refused)) from None

        try:
            decided = proposals.approve(
                proposal,
                approver=principal.as_actor(),
                comment=body.comment,
                corrections=corrections,
            )
            applied, draft = proposals.apply(
                decided,
                object_type=ObjectType.CONTRACT,
                object_id=proposal.feed_id or "",
                body=(
                    _phi_contract_body(
                        metadata,
                        proposal.feed_id or "",
                        accepted,
                        _cleared_by_steward(corrections),
                    )
                    if proposal.agent == PHI_AGENT
                    else _contract_body(accepted, key_columns=tuple(body.key_columns))
                ),
                version=_next_contract_version(metadata, proposal.feed_id or ""),
            )
        except proposals.ProposalError as refused:
            audit.record(
                object_type=ObjectType.CONTRACT,
                object_id=proposal.feed_id or proposal_id,
                action="refused:approve_proposal",
                actor=principal.as_actor(),
                detail=str(refused),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(refused)) from None

        metadata.save(draft)
        stored = metadata.record_proposal(applied)
        audit.record(
            object_type=ObjectType.CONTRACT,
            object_id=draft.object_id,
            version=draft.version,
            action="applied_proposal",
            actor=principal.as_actor(),
            detail=f"{proposal_id} · {len(corrections)} correction(s)",
        )
        return _proposal_out(stored)

    @app.post(
        f"{API_PREFIX}/proposals/{{proposal_id}}/reclassify",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def reclassify_proposal(
        proposal_id: str,
        body: ReclassifyIn,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.APPROVE))],
        audit: Audit,
    ) -> ProposalOut:
        """A steward's decision about what a column holds. CF-V1-E5-03.

        THE ONLY PATH THAT CAN REDUCE PROTECTION, and it requires the APPROVE
        permission, a named human and a stated reason. The reason is not
        ceremony: masking, the vector store's PHI-absence guarantee and E7's
        rule suggestions all read these flags, so an unexplained downgrade is
        an unreviewable one.

        Like the approve route, this ACCEPTS the classification and produces a
        DRAFT contract authored by the steward — so their decision travels the
        same lifecycle as anything else they would have typed by hand.
        """
        try:
            proposal = metadata.get_proposal(proposal_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal {proposal_id!r}") from None
        if proposal.agent != PHI_AGENT:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"proposal {proposal_id!r} is from {proposal.agent!r}, which classifies "
                "nothing. Reclassification is a PHI decision.",
            )

        accepted = _apply_decisions(proposal.payload, body.as_approval())
        corrections = proposals.diff_fields(
            proposal.payload, accepted, key="source_name", fields=("is_phi",)
        )
        cleared = _cleared_by_steward(corrections)
        current = {
            str(r.get("source_name")): r
            for r in proposal.payload.get("records", ())
            if isinstance(r, dict)
        }
        try:
            for source_name in sorted(cleared):
                record = current.get(source_name, {})
                # Routed through the CORE function rather than re-checked here,
                # so the rule cannot come to differ between this route and the
                # steward screen a later wave builds.
                reclassify(
                    ColumnClassification(
                        source_name=source_name,
                        position=int(record.get("position", 0)),
                        is_phi=True,
                        basis=Basis(str(record.get("basis", Basis.PRECAUTION.value))),
                    ),
                    is_phi=False,
                    steward=principal.as_actor(),
                    rationale=body.rationale,
                )
            decided = proposals.approve(
                proposal,
                approver=principal.as_actor(),
                comment=body.rationale,
                corrections=corrections,
            )
            applied, draft = proposals.apply(
                decided,
                object_type=ObjectType.CONTRACT,
                object_id=proposal.feed_id or "",
                body=_phi_contract_body(metadata, proposal.feed_id or "", accepted, cleared),
                version=_next_contract_version(metadata, proposal.feed_id or ""),
            )
        except (PhiDowngradeRefusedError, proposals.ProposalError) as refused:
            audit.record(
                object_type=ObjectType.CONTRACT,
                object_id=proposal.feed_id or proposal_id,
                action="refused:reclassify",
                actor=principal.as_actor(),
                detail=str(refused),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(refused)) from None

        metadata.save(draft)
        stored = metadata.record_proposal(applied)
        audit.record(
            object_type=ObjectType.CONTRACT,
            object_id=draft.object_id,
            version=draft.version,
            action="reclassified",
            actor=principal.as_actor(),
            detail=(
                f"{proposal_id} · cleared {', '.join(sorted(cleared)) or 'nothing'} · "
                f"{body.rationale}"
            ),
        )
        return _proposal_out(stored)

    @app.post(
        f"{API_PREFIX}/proposals/{{proposal_id}}/reject",
        response_model=ProposalOut,
        tags=["intelligence"],
    )
    def reject_proposal(
        proposal_id: str,
        body: RejectProposalIn,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
    ) -> ProposalOut:
        try:
            proposal = metadata.get_proposal(proposal_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal {proposal_id!r}") from None
        try:
            decided = proposals.reject(
                proposal, approver=principal.as_actor(), comment=body.comment
            )
        except proposals.ProposalError as refused:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None
        audit.record(
            object_type=ObjectType.FEED,
            object_id=proposal.feed_id or proposal_id,
            action="rejected_proposal",
            actor=principal.as_actor(),
            detail=body.comment,
        )
        return _proposal_out(metadata.record_proposal(decided))

    @app.get(
        f"{API_PREFIX}/proposals/{{proposal_id}}/acceptance",
        response_model=AcceptanceOut,
        tags=["intelligence"],
    )
    def proposal_acceptance(
        proposal_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> AcceptanceOut:
        """The eval arithmetic for one decided proposal.

        Exposed rather than kept in the test suite because the acceptance rate
        per agent per week is THE health metric — and a metric only CI can see
        is a metric nobody acts on.
        """
        try:
            proposal = metadata.get_proposal(proposal_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal {proposal_id!r}") from None
        deterministic = frozenset(
            str(r.get("source_name"))
            for r in proposal.payload.get("records", ())
            if r.get("settled_by") == "computation"
        )
        return _acceptance_out(proposals.measure(proposal, deterministic_keys=deterministic))

    # ── governance · CF-V1-E11-01 — the one lifecycle, exposed ───────────────
    #
    # Five acts, one engine. Every refusal below is a 403 AND a ledger row —
    # the two universal negatives (author-approves-own, publish-without-named-
    # approver) are raised by core/model/governed.py; the routing refusal
    # (steward-approves-a-contract) by core/lifecycle. This layer only loads,
    # asks, persists and records — it decides nothing.

    def _governance_act(
        act: str,
        object_type: ObjectType,
        object_id: str,
        principal: Principal,
        metadata: MetadataDbPort,
        audit: AuditLog,
        perform: Callable[[GovernedObject], tuple[GovernedObject, Any]],
    ) -> GovernedOut:
        if object_type is ObjectType.FEED and not principal.scopes.covers_feed(object_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        try:
            current = metadata.get(object_type, object_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        try:
            moved, entry = perform(current)
        except (LifecycleViolationError, ImpactUnknownError) as refusal:
            # Refused AND logged — a guardrail nobody can see fire is a comment.
            audit.record(
                object_type=object_type,
                object_id=object_id,
                version=current.version,
                action=f"refused:{act}",
                actor=principal.as_actor(),
                detail=str(refusal),
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(refusal)) from None
        return _governed_out(metadata.record_transition(moved, entry))

    @app.post(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/submit",
        response_model=GovernedOut,
        tags=["governance"],
    )
    def submit_for_review(
        object_type: ObjectType,
        object_id: str,
        body: TransitionIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.SUBMIT_FOR_REVIEW))],
    ) -> GovernedOut:
        """Draft -> In Review. A resubmission after request-changes keeps the
        whole conversation: the comments live on the object's audit trail."""
        return _governance_act(
            "submit",
            object_type,
            object_id,
            principal,
            metadata,
            audit,
            lambda obj: lifecycle.submit(obj, actor=principal.as_actor(), comment=body.comment),
        )

    @app.get(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/packet",
        response_model=ImpactPacketOut,
        tags=["governance"],
    )
    def approval_packet(
        object_type: ObjectType,
        object_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ImpactPacketOut:
        """CF-V1-E11-02 — the change, its engineering impact, its business
        impact and the evidence, on one screen. Computed from lineage: an
        author who forgets to mention the four jobs their mapping feeds does
        not thereby hide them."""
        if object_type is ObjectType.FEED and not principal.scopes.covers_feed(object_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        try:
            target = metadata.get(object_type, object_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        return _packet_out(_packet_for(metadata, target))

    @app.post(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/approve",
        response_model=GovernedOut,
        tags=["governance"],
    )
    def approve_object(
        object_type: ObjectType,
        object_id: str,
        body: TransitionIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.APPROVE))],
    ) -> GovernedOut:
        """THE negative-first route. Four things refuse here, all from layers
        below: the wrong lane, a packet with unknown impact, a missing
        rationale, and the author approving their own change. Every one of
        them leaves a row, and each had a test that made the attempt before
        this handler existed.

        CF-V1-E6-04 adds a FIFTH, for mappings only: a change that empties a
        field of a PUBLISHED mapping is refused unless the approver named that
        field in `accepts_loss`. It is checked here rather than inside
        `lifecycle.approve` because it needs the PREVIOUS version, which the
        lifecycle engine has no store to read — and it is checked BEFORE the
        act so nothing is persisted by a refusal.
        """
        if object_type is ObjectType.MAPPING:
            try:
                _refuse_silent_row_loss(metadata, object_id, tuple(body.accepts_loss))
            except UnacknowledgedLossError as refused:
                audit.record(
                    object_type=ObjectType.MAPPING,
                    object_id=object_id,
                    action="refused:silent_row_loss",
                    actor=principal.as_actor(),
                    detail=str(refused),
                )
                raise HTTPException(status.HTTP_403_FORBIDDEN, str(refused)) from None

        return _governance_act(
            "approve",
            object_type,
            object_id,
            principal,
            metadata,
            audit,
            lambda obj: lifecycle.approve(
                obj,
                actor=principal.as_actor(),
                roles=frozenset(principal.roles),
                comment=body.comment,
                packet=_packet_for(metadata, obj),
            ),
        )

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/mapping/diff",
        response_model=MappingDiffOut,
        tags=["mapping"],
    )
    def mapping_diff(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        from_version: int | None = None,
        to_version: int | None = None,
    ) -> MappingDiffOut:
        """Compare two mapping versions, target field by target field.

        A first-class route rather than the generic body diff, because what an
        approver needs to know about a mapping change is not "the JSON differs"
        but WHICH FIELDS LOSE THEIR SOURCE. That is the shape silent row loss
        has, and `diff_bodies` would render it as `lines: [...] -> [...]`.
        """
        history = list(metadata.history(ObjectType.MAPPING, feed_id))
        if len(history) < 2:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has {len(history)} mapping version(s) — "
                "there is nothing to compare",
            )
        after = _version_or(history, to_version, default=max(o.version for o in history))
        before = _version_or(history, from_version, default=after.version - 1)
        return _mapping_diff_out(before, after)

    @app.post(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/request-changes",
        response_model=GovernedOut,
        tags=["governance"],
    )
    def request_changes(
        object_type: ObjectType,
        object_id: str,
        body: TransitionIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.APPROVE))],
    ) -> GovernedOut:
        """In Review -> Draft, comment REQUIRED. The author edits and resubmits
        with the history intact — the reviewer sees exactly what changed since
        their request, because versions and comments both persist."""
        return _governance_act(
            "request-changes",
            object_type,
            object_id,
            principal,
            metadata,
            audit,
            lambda obj: lifecycle.request_changes(
                obj,
                actor=principal.as_actor(),
                roles=frozenset(principal.roles),
                comment=body.comment,
            ),
        )

    @app.post(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/publish",
        response_model=GovernedOut,
        tags=["governance"],
    )
    def publish_object(
        object_type: ObjectType,
        object_id: str,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.PUBLISH))],
    ) -> GovernedOut:
        """Approved -> Published. Only now will the engine read it — execution
        gates on the READER (`executable()` refuses anything else), so nothing
        unapproved can run even if a route were mis-guarded."""
        return _governance_act(
            "publish",
            object_type,
            object_id,
            principal,
            metadata,
            audit,
            lambda obj: lifecycle.publish(
                obj, actor=principal.as_actor(), roles=frozenset(principal.roles)
            ),
        )

    @app.post(
        f"{API_PREFIX}/objects/{{object_type}}/{{object_id}}/retire",
        response_model=GovernedOut,
        tags=["governance"],
    )
    def retire_object(
        object_type: ObjectType,
        object_id: str,
        body: TransitionIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.RETIRE))],
    ) -> GovernedOut:
        """-> Retired. Feeds retire, never vanish; there is no DELETE anywhere
        on this API for a governed object, guarded or otherwise."""
        return _governance_act(
            "retire",
            object_type,
            object_id,
            principal,
            metadata,
            audit,
            lambda obj: lifecycle.retire(
                obj,
                actor=principal.as_actor(),
                roles=frozenset(principal.roles),
                comment=body.comment,
            ),
        )

    @app.get(f"{API_PREFIX}/work-queue", response_model=WorkQueueOut, tags=["governance"])
    def work_queue(
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> WorkQueueOut:
        """One view of everything awaiting this person, across every object
        type — the screen-shaped proof that there is ONE lifecycle. Feed-scoped
        objects the caller cannot reach are filtered where the list is built."""
        everything: list[GovernedObject] = []
        for object_type in ObjectType:
            for obj in metadata.list(object_type):
                if object_type is ObjectType.FEED and not principal.scopes.covers_feed(
                    obj.object_id
                ):
                    continue
                everything.append(obj)
        roles = frozenset(principal.roles)
        return WorkQueueOut(
            awaiting_my_review=[
                _governed_out(o)
                for o in lifecycle.awaiting_review_by(
                    tuple(everything), roles=roles, subject=principal.subject
                )
            ],
            my_submissions=[
                _governed_out(o)
                for o in lifecycle.submitted_by(tuple(everything), subject=principal.subject)
            ],
        )

    @app.get(f"{API_PREFIX}/audit", response_model=list[AuditOut], tags=["governance"])
    def read_audit(
        audit: Audit,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        object_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditOut]:
        """Readable by everyone who may view; writable by no one, ever.

        There is deliberately no DELETE and no PATCH on this path — not guarded,
        ABSENT. The port has no such verb either.
        """
        return [
            AuditOut(
                object_type=entry.object_type.value,
                object_id=entry.object_id,
                version=entry.version,
                action=entry.action,
                actor_subject=entry.actor.subject,
                actor_type=entry.actor_type,
                occurred_ts=entry.occurred_ts,
                detail=entry.detail,
            )
            for entry in audit.read(object_id=object_id, limit=limit)
        ]

    @app.get(f"{API_PREFIX}/users", response_model=list[PrincipalOut], tags=["admin"])
    def list_users(
        directory: Directory,
        _: Annotated[Principal, Depends(require(Action.MANAGE_USERS))],
    ) -> list[PrincipalOut]:
        """Administrators assign access. Note what they still cannot do — approve
        anything. The person who grants permissions being able to use them all is
        how segregation of duties dies."""
        return [_principal_out(person) for person in directory.directory()]

    @app.get(
        f"{API_PREFIX}/execution-plane/contracts",
        response_model=list[ContractOut],
        tags=["governance"],
    )
    def execution_plane_contracts(
        register: Plane,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> list[ContractOut]:
        """CF-V0-E1-01 on a screen.

        The unknowns are the point. A register that shows only what we know
        reads as complete while the unconfirmed facts live in somebody's head.
        """
        return [
            ContractOut(
                story_id=contract.story_id,
                reads=sorted(contract.reads),
                writes=sorted(contract.writes),
                unknowns=[
                    UnknownOut(question=u.question, owner=u.owner, blocks=u.blocks)
                    for u in contract.unknowns
                ],
            )
            for contract in sorted(register.contracts.values(), key=lambda c: c.story_id)
        ]

    @app.get(f"{API_PREFIX}/navigation", response_model=NavigationOut, tags=["identity"])
    def navigation(principal: CurrentPrincipal) -> NavigationOut:
        """Reachable by a user in no group — who correctly gets an empty nav
        and the "no access assigned" page, rather than a broken shell."""
        permitted = frozenset(a for a in Action if may(principal, a))
        return NavigationOut(
            active_wave=ACTIVE_WAVE,
            destinations=[
                DestinationOut(
                    key=d.key,
                    label=d.label,
                    route=d.route,
                    group=d.group.value,
                    answers=d.answers,
                    prominent=bool(d.prominent_for & principal.roles),
                )
                for d in for_roles(frozenset(principal.roles), permitted)
            ],
        )

    # ── operations ───────────────────────────────────────────────────────────

    @app.get(f"{API_PREFIX}/batches", response_model=list[BatchOut], tags=["operations"])
    def list_batches(
        feed_id: str,
        control: Control,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        limit: int = 50,
    ) -> list[BatchOut]:
        return [_batch_out(b) for b in control.list_batches(feed_id, limit)]

    @app.get(
        f"{API_PREFIX}/batches/{{batch_id}}/{{panel}}",
        response_model=RowsOut,
        tags=["operations"],
    )
    def batch_panel(
        batch_id: str,
        panel: BatchPanel,
        control: Control,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> RowsOut:
        """The one drawer, served by the SAME certified tools the agent calls.

        This is why a citation is a route: `recon:8842#DQ-002` opens this panel
        and renders the rows that citation cites. One projection, so a figure on
        a screen and a figure in an answer cannot disagree.
        """
        context = ToolContext(
            principal=principal,
            control=control,
            metadata=metadata,
            run_id=f"ui-{batch_id}",
            agent="ui",
        )
        return _rows_out(invoke(context, _PANEL_TOOLS[panel], {"batch_id": batch_id}))

    @app.get(f"{API_PREFIX}/tools/{{tool_name}}", response_model=RowsOut, tags=["ai"])
    def tool_rows(
        tool_name: str,
        request: Request,
        control: Control,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> RowsOut:
        """Any certified tool, called with its query params as arguments.

        The same mechanism `batch_panel` uses for the drawer, generalised: a
        citation whose destination is not a batch (a contract, a plan, a
        rule, a term) still opens on rows a certified tool produced, never a
        private query. Every Wave-0 tool is R0/read-only, so there is no
        write surface here to guard beyond `require(Action.VIEW)` itself.
        """
        if tool_name not in CATALOGUE:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such tool: {tool_name!r}")
        context = ToolContext(
            principal=principal,
            control=control,
            metadata=metadata,
            run_id=f"ui-{tool_name}",
            agent="ui",
        )
        if tool_name == "lookup_reference":
            # A rule_id or term is meant to resolve with no feed_id in hand
            # (CF-V0-E16-09) — the base index holds only the generic
            # glossary, so a specific feed's DQ rules are seeded in per call.
            context.reference.seed(all_dq_rule_entries(metadata))
        try:
            return _rows_out(invoke(context, tool_name, dict(request.query_params)))
        except ToolError as bad:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(bad)) from None

    # ── the intelligence plane ───────────────────────────────────────────────

    @app.get(f"{API_PREFIX}/tools", response_model=list[ToolOut], tags=["ai"])
    def list_tools(_: Annotated[Principal, Depends(require(Action.VIEW))]) -> list[ToolOut]:
        """What the agent may call, on a screen. A whitelist nobody can read is
        a whitelist nobody reviews."""
        return [
            ToolOut(
                name=spec.name,
                answers=spec.answers,
                reads=sorted(spec.reads),
                cites=[kind.value for kind in spec.cites],
                parameters=[p.name for p in spec.parameters],
                note=spec.note,
            )
            for spec in sorted(CATALOGUE.values(), key=lambda s: s.name)
        ]

    @app.post(f"{API_PREFIX}/ask", response_model=AskOut, tags=["ai"])
    def ask(
        body: AskIn,
        principal: Annotated[Principal, Depends(require(Action.ASK_AGENT))],
        control: Control,
        metadata: Store,
    ) -> AskOut:
        """Ask CINQFLOW. Read-only, cited, budgeted, audited.

        Note the permission: ASK_AGENT, which Read-Only users HAVE. Asking is
        reading, and an observability agent a Read-Only user cannot use is an
        agent that helps only the people who least need it.
        """
        run_id = f"ask-{uuid4().hex[:12]}"
        factory: AgentFactory | None = app.state.agent_factory
        if factory is None:
            return AskOut(
                claims=[],
                confidence="low",
                unanswered=["the llm pin is not fitted in this connection profile"],
                intent="declined",
                tools_called=[],
                trace=[],
                cost_usd="0",
                refused=True,
                refusal=(
                    "No model endpoint is configured. Everything else on this platform "
                    "works without one — this screen is the only thing that does not."
                ),
                run_id=run_id,
            )
        answer = factory(principal, control, metadata).ask(body.question, run_id=run_id)
        return AskOut(
            claims=[
                ClaimOut(
                    text=claim.text,
                    citation_ids=[str(c) for c in claim.citations],
                    routes=[c.route for c in claim.citations],
                )
                for claim in answer.claims
            ],
            confidence=answer.confidence,
            unanswered=list(answer.unanswered),
            intent=answer.intent.value,
            tools_called=list(answer.tools_called),
            trace=[TraceStepOut(node=node, duration_ms=ms) for node, ms in answer.trace],
            cost_usd=answer.cost_usd,
            refused=answer.refused,
            refusal=answer.refusal,
            run_id=run_id,
        )

    @app.get(f"{API_PREFIX}/agent-actions", response_model=list[AgentActionOut], tags=["ai"])
    def agent_actions(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        run_id: str | None = None,
        agent: str | None = None,
        limit: int = 200,
    ) -> list[AgentActionOut]:
        return [
            AgentActionOut(
                run_id=row.run_id,
                agent=row.agent,
                action=row.action,
                outcome=row.outcome.value,
                is_refusal=row.outcome.is_refusal,
                actor_subject=row.actor.subject,
                actor_type=row.actor_type,
                risk_class=row.risk_class,
                prompt_ref=row.prompt_ref,
                prompt_hash=row.prompt_hash,
                model=row.model,
                model_version=row.model_version,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                cost_usd=str(row.cost_usd),
                latency_ms=row.latency_ms,
                occurred_ts=row.occurred_ts,
                detail=row.detail,
            )
            for row in metadata.read_agent_actions(run_id=run_id, agent=agent, limit=limit)
        ]

    @app.get(f"{API_PREFIX}/llm-budget", response_model=BudgetOut, tags=["ai"])
    def llm_budget(
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        agent: str = AGENT,
    ) -> BudgetOut:
        """Cost against cap, refusals, and grounding — on a screen.

        "if it is not on a screen, it is not being governed."
        """
        rows = list(metadata.read_agent_actions(agent=agent, limit=10_000))
        today = datetime.now(UTC).date()
        todays = [r for r in rows if r.occurred_ts.date() == today]
        budget: Budget = app.state.llm_budget
        return BudgetOut(
            agent=agent,
            spent_today_usd=str(sum((r.cost_usd for r in todays), Decimal("0"))),
            daily_cap_usd=str(budget.per_agent_per_day_usd),
            per_run_cap_usd=str(budget.per_run_usd),
            runs_today=len({r.run_id for r in todays}),
            refusals_today=sum(1 for r in todays if r.outcome.is_refusal),
            grounded_claims=sum(1 for r in todays if r.action == "llm:large"),
            uncited_claims_blocked=sum(
                1 for r in todays if "no tool returned that citation" in r.detail
            ),
        )

    return app


# ── helpers ──────────────────────────────────────────────────────────────────


def _load(metadata: MetadataDbPort, feed_id: str, version: int | None = None) -> GovernedObject:
    try:
        return metadata.get(ObjectType.FEED, feed_id, version)
    except ObjectNotFoundError:
        # The same sentence a scope miss produces. Two sentences would be an
        # oracle for which feed ids are real.
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None


def _record_from(body: FeedIn) -> feed_registry.FeedRecord:
    try:
        fields = body.model_dump()
        # The envelope is not an engine field. It travels in the same body,
        # under its own key — see `feed.from_governed`.
        fields.pop("operations", None)
        return feed_registry.FeedRecord(**fields)
    except feed_registry.PatternSampleMismatchError as mismatch:
        # A pattern that does not match a real filename is refused BEFORE save,
        # with the side-by-side diff — incident #1 was a leading underscore
        # nobody could see in a regex.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(mismatch)) from None
    except feed_registry.FeedValidationError as invalid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(invalid)) from None


def _operations_body(body: FeedIn) -> dict[str, Any]:
    """Validate the envelope in CORE, then store what core accepted.

    Round-tripping through `FeedOperations` rather than storing the request
    body verbatim is what makes the refusals real: a timezone offset, a
    credentialled document link and an alert chain that does not escalate are
    all rejected here, at the boundary, rather than discovered when somebody
    reads the row.
    """
    if body.operations is None:
        return {}
    try:
        return operations.FeedOperations.from_body(body.operations.model_dump()).as_body()
    except (operations.OperationsValidationError, ValueError) as invalid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(invalid)) from None


def _readiness_out(obj: GovernedObject) -> ReadinessOut:
    ready = operations.readiness_of(obj)
    return ReadinessOut(
        feed_id=obj.object_id,
        is_ready=ready.is_ready,
        outstanding=len(ready.outstanding),
        items=[
            ChecklistItemOut(
                key=item.key,
                question=item.question,
                satisfied=item.satisfied,
                why_it_matters=item.why_it_matters,
                how_to_fix=item.how_to_fix,
            )
            for item in ready.items
        ],
        explanation=ready.explain(obj.object_id),
    )


def _feed_out(obj: GovernedObject) -> FeedOut:
    body: dict[str, Any] = obj.body
    citation = CitationId(kind=CitationKind.FEED, subject=obj.object_id, version=obj.version)
    return FeedOut(
        feed_id=obj.object_id,
        domain=str(body.get("domain", "")),
        source_system=str(body.get("source_system", "")),
        file_format=str(body.get("file_format", "")),
        landing_path=str(body.get("landing_path", "")),
        file_pattern=str(body.get("file_pattern", "")),
        schedule_cron=str(body.get("schedule_cron", "")),
        version=obj.version,
        lifecycle_state=obj.lifecycle_state.value,
        status=obj.lifecycle_state.status_word,
        citation_id=str(citation),
        route=citation.route,
        operations=OperationsModel(
            **operations.FeedOperations.from_body(body.get("operations")).as_body()
        ),
        # Sent with every feed, so the form's checklist and the lifecycle's
        # refusal are the same computation. A screen showing green while the
        # submit button returns 403 is the classic shape of a rule implemented
        # twice.
        readiness=_readiness_out(obj),
    )


def _source_out(obj: GovernedObject, feed_ids: tuple[str, ...] = ()) -> SourceOut:
    record = source_registry.SourceRecord.from_governed(obj)
    return SourceOut(
        source_id=record.source_id,
        name=record.name,
        kind=record.kind.value,
        endpoint_ref=record.endpoint_ref,
        line_of_business=list(record.line_of_business),
        states=list(record.states),
        owners=[
            OwnerModel(role=o.role.value, subject=o.subject, display_name=o.display_name)
            for o in record.owners
        ],
        counterparty_contact=record.counterparty_contact,
        notes=record.notes,
        version=obj.version,
        lifecycle_state=obj.lifecycle_state.value,
        status=obj.lifecycle_state.status_word,
        feed_ids=list(feed_ids),
    )


def _source_from(body: SourceIn) -> source_registry.SourceRecord:
    try:
        return source_registry.SourceRecord(
            source_id=body.source_id,
            name=body.name,
            kind=source_registry.SourceKind(body.kind),
            endpoint_ref=body.endpoint_ref,
            line_of_business=tuple(body.line_of_business),
            states=tuple(body.states),
            owners=tuple(
                operations.Owner(
                    role=operations.OwnerRole(o.role),
                    subject=o.subject,
                    display_name=o.display_name,
                )
                for o in body.owners
            ),
            counterparty_contact=body.counterparty_contact,
            notes=body.notes,
        )
    except (source_registry.SourceValidationError, ValueError) as invalid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(invalid)) from None


def _packet_for(metadata: MetadataDbPort, target: GovernedObject) -> ImpactPacket:
    """Impact from the WHOLE registry, every time. Computing it from a cached
    subset is how an approver ends up signing yesterday's blast radius.

    Module level rather than a closure inside `create_app`: CF-V1-E3-02's
    "referenced everywhere" view is the same computation as CF-V1-E11-02's
    approval packet, and two copies of it would be two answers to "what does
    this change touch".
    """
    everything: list[GovernedObject] = []
    for object_type in ObjectType:
        for obj in metadata.list(object_type):
            everything.extend(metadata.history(object_type, obj.object_id))
    return build_packet(
        target,
        tuple(everything),
        evidence=dict(target.body.get("evidence") or {}),
    )


# ── the mapping studio · CF-V1-E6-03 ─────────────────────────────────────────


def _contract_of(metadata: MetadataDbPort, feed_id: str) -> SchemaContract | None:
    """The feed's latest contract, or None if it has none yet.

    None rather than a raise: a BA may draft a mapping against a feed whose
    contract is still in review, and half a validation is better than a screen
    that refuses to load.
    """
    try:
        return contract_registry.from_governed(metadata.get(ObjectType.CONTRACT, feed_id))
    except (ObjectNotFoundError, ValueError):
        return None


def _mapping_from(feed_id: str, body: MappingIn, *, version: int) -> mapping_core.FeedMapping:
    return mapping_core.FeedMapping(
        feed_id=feed_id,
        version=version,
        contract_version=body.contract_version,
        lines=tuple(_mapping_line_from(raw) for raw in body.lines),
    )


def _mapping_line_from(raw: MappingLineModel) -> mapping_core.MappingLine:
    return mapping_core.MappingLine(
        target_entity=raw.target_entity,
        target_field=raw.target_field,
        source_columns=tuple(raw.source_columns),
        transform=_transform_from(raw.transform),
        null_policy=mapping_core.NullPolicy(raw.null_policy),
        default_value=raw.default_value,
        platform_supplied=raw.platform_supplied,
        unmapped_reason=raw.unmapped_reason,
        glossary_id=raw.glossary_id,
        notes=raw.notes,
        confidence=raw.confidence,
        citations=tuple(raw.citations),
    )


def _transform_from(raw: TransformModel) -> mapping_core.Transform:
    return mapping_core.Transform(
        kind=mapping_core.TransformKind(raw.kind),
        target_type=TypeName(raw.target_type) if raw.target_type else None,
        date_format=raw.date_format,
        separator=raw.separator,
        part=raw.part,
        lookup=tuple((pair[0], pair[1]) for pair in raw.lookup if len(pair) == 2),
        on_unlisted=mapping_core.UnlistedCode(raw.on_unlisted),
        cases=tuple(mapping_core.Case(when_in=tuple(c.when_in), then=c.then) for c in raw.cases),
        literal=raw.literal,
        default_value=raw.default_value,
    )


def _validate_mapping(
    metadata: MetadataDbPort, mapping: mapping_core.FeedMapping
) -> tuple[mapping_core.MappingFinding, ...]:
    """Both ends, whichever of them exists."""
    return mapping_core.validate(
        mapping,
        contract=_contract_of(metadata, mapping.feed_id),
        model=_canonical_of(metadata),
    )


def _next_mapping_version(metadata: MetadataDbPort, feed_id: str) -> int:
    history = list(metadata.history(ObjectType.MAPPING, feed_id))
    return max((obj.version for obj in history), default=0) + 1


def _transform_out(transform: mapping_core.Transform) -> TransformModel:
    return TransformModel(
        kind=transform.kind.value,
        target_type=transform.target_type.value if transform.target_type else None,
        date_format=transform.date_format,
        separator=transform.separator,
        part=transform.part,
        lookup=[[code, translated] for code, translated in transform.lookup],
        on_unlisted=transform.on_unlisted.value,
        cases=[CaseModel(when_in=list(c.when_in), then=c.then) for c in transform.cases],
        literal=transform.literal,
        default_value=transform.default_value,
        describe=transform.describe(),
    )


def _mapping_line_out(line: mapping_core.MappingLine) -> MappingLineModel:
    return MappingLineModel(
        target_entity=line.target_entity,
        target_field=line.target_field,
        source_columns=list(line.source_columns),
        transform=_transform_out(line.transform),
        null_policy=line.null_policy.value,
        default_value=line.default_value,
        platform_supplied=line.platform_supplied,
        unmapped_reason=line.unmapped_reason,
        glossary_id=line.glossary_id,
        notes=line.notes,
        confidence=line.confidence,
        citations=list(line.citations),
        status=line.status.value,
        describe=line.describe(),
    )


def _mapping_finding_out(finding: mapping_core.MappingFinding) -> MappingFindingOut:
    return MappingFindingOut(
        key=finding.key,
        address=finding.address,
        severity=finding.severity.value,
        blocks=finding.blocks,
        what=finding.what,
        why_it_matters=finding.why_it_matters,
        how_to_fix=finding.how_to_fix,
    )


def _mapping_out(metadata: MetadataDbPort, obj: GovernedObject) -> MappingOut:
    mapping = mapping_core.from_governed(obj)
    findings = _validate_mapping(metadata, mapping)
    mapped, total = mapping.coverage
    return MappingOut(
        feed_id=mapping.feed_id,
        version=obj.version,
        lifecycle_state=obj.lifecycle_state.value,
        status=obj.lifecycle_state.status_word,
        contract_version=mapping.contract_version,
        citation_id=str(mapping.citation),
        route=mapping.citation.route,
        mapped_count=mapped,
        total_count=total,
        unmapped_count=len(mapping.unmapped),
        lines=[_mapping_line_out(line) for line in mapping.lines],
        findings=[_mapping_finding_out(f) for f in findings],
        blocking_count=len(mapping_core.blocking(findings)),
    )


# ── AI source→target mapping · CF-V1-E6-02 ───────────────────────────────────


def _published_mappings(
    metadata: MetadataDbPort, *, exclude: str
) -> tuple[tuple[mapping_core.FeedMapping, ...], dict[str, str]]:
    """Every PUBLISHED mapping except this feed's own, with its approver.

    PUBLISHED only. A draft is somebody's unreviewed work, and offering it to
    a second feed as precedent would launder an unapproved decision into a
    place it would arrive wearing authority it has not earned.

    `exclude` keeps the feed under test out of the exemplar pool — its own
    prior mapping is handled separately, as the decision rather than as
    evidence, and mixing the two would make the eval's blind re-derivation a
    measurement of the platform reading its own answer key.
    """
    found: list[mapping_core.FeedMapping] = []
    approvers: dict[str, str] = {}
    for obj in metadata.list(ObjectType.MAPPING):
        if obj.object_id == exclude or not obj.is_executable:
            continue
        found.append(mapping_core.from_governed(obj))
        if obj.approved_by is not None:
            approvers[obj.object_id] = obj.approved_by.display_name or obj.approved_by.subject
    return tuple(found), approvers


def _own_published_mapping(
    metadata: MetadataDbPort, feed_id: str
) -> tuple[mapping_core.FeedMapping, ...]:
    """This feed's own published mapping, if it has one. The DECISION."""
    try:
        obj = metadata.get(ObjectType.MAPPING, feed_id)
    except ObjectNotFoundError:
        return ()
    return (mapping_core.from_governed(obj),) if obj.is_executable else ()


def _accepted_mapping_records(payload: dict[str, Any], body: ApproveProposalIn) -> dict[str, Any]:
    """The reviewer's version of the proposed lines.

    Absent fields keep the agent's value, so a reviewer redirecting one column
    does not restate the other forty — and a reviewer who changed nothing
    produces zero corrections, which is exactly what the eval counts.
    """
    decided = {d.source_column: d for d in body.mappings}
    records: list[dict[str, Any]] = []
    for record in payload.get("records", ()):
        accepted = dict(record)
        decision = decided.get(str(record.get("source_column")))
        if decision is not None:
            for attribute in ("target_entity", "target_field", "unmapped", "unmapped_reason"):
                value = getattr(decision, attribute)
                if value is not None:
                    accepted[attribute] = value
        records.append(accepted)
    return {**payload, "records": records}


def _mapping_body_from(records: tuple[dict[str, Any], ...], feed_id: str) -> dict[str, Any]:
    """Build a governed MAPPING body from the accepted records.

    Routed through `core.mapping.MappingLine` rather than assembled as a dict,
    so a reviewer who unmaps a column without giving a reason is refused by the
    same type that refuses it in the manual editor. One rule, one place.
    """
    lines: list[mapping_core.MappingLine] = []
    for record in records:
        source = str(record.get("source_column", ""))
        entity = str(record.get("target_entity") or "unassigned")
        field = str(record.get("target_field") or source)
        if record.get("unmapped"):
            lines.append(
                mapping_core.MappingLine(
                    target_entity=entity,
                    target_field=field,
                    unmapped_reason=str(record.get("unmapped_reason") or ""),
                    glossary_id=record.get("glossary_id"),
                    confidence=record.get("confidence"),
                )
            )
            continue
        lines.append(
            mapping_core.MappingLine(
                target_entity=entity,
                target_field=field,
                source_columns=(source,),
                glossary_id=record.get("glossary_id"),
                notes=str(record.get("rationale") or ""),
                confidence=record.get("confidence"),
            )
        )
    return mapping_core.mapping_body(mapping_core.FeedMapping(feed_id=feed_id, lines=tuple(lines)))


def _version_or(
    history: list[GovernedObject], wanted: int | None, *, default: int
) -> GovernedObject:
    chosen = wanted if wanted is not None else default
    for obj in history:
        if obj.version == chosen:
            return obj
    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        f"there is no version {chosen} — this object has "
        f"{', '.join(str(o.version) for o in history)}",
    )


def _refuse_silent_row_loss(
    metadata: MetadataDbPort, feed_id: str, accepts_loss: tuple[str, ...]
) -> None:
    """CF-V1-E6-04's gate, at the approval seam.

    Compares the version being approved against the currently PUBLISHED one —
    not against the immediately previous version, which may be a draft nobody
    ran. What matters is what changes for the pipeline, and the pipeline reads
    published metadata and nothing else.

    Silent on a first mapping: there is no published predecessor, so there is
    nothing a field can stop being.
    """
    history = list(metadata.history(ObjectType.MAPPING, feed_id))
    published = [obj for obj in history if obj.is_executable]
    pending = [obj for obj in history if obj.lifecycle_state is LifecycleState.PENDING_REVIEW]
    if not published or not pending:
        return
    live = max(published, key=lambda o: o.version)
    proposed = max(pending, key=lambda o: o.version)
    if proposed.version <= live.version:
        return
    refuse_unacknowledged_loss(
        mapping_versioning.compare(
            mapping_core.from_governed(live),
            mapping_core.from_governed(proposed),
            from_published=True,
        ),
        accepts_loss,
    )


def _mapping_diff_out(before: GovernedObject, after: GovernedObject) -> MappingDiffOut:
    diff = mapping_versioning.compare(
        mapping_core.from_governed(before),
        mapping_core.from_governed(after),
        from_published=before.is_executable,
    )
    return MappingDiffOut(
        feed_id=diff.feed_id,
        from_version=diff.from_version,
        to_version=diff.to_version,
        from_published=diff.from_published,
        lines=[
            MappingDiffLineOut(
                address=change.address,
                change=change.kind.value,
                before=change.before,
                after=change.after,
                loses_its_source=change.loses_its_source,
                explanation=change.explain(),
            )
            for change in diff.changed
        ],
        fields_losing_their_source=list(diff.fields_losing_their_source),
        summary=diff.summary(),
    )


def _accept_mapping_proposal(
    metadata: MetadataDbPort,
    proposal: Proposal,
    body: ApproveProposalIn,
    principal: Principal,
    audit: AuditLog,
) -> ProposalOut:
    """Accept a mapping suggestion, producing a DRAFT MAPPING.

    The same door a hand-typed mapping arrives at: `proposals.apply` makes the
    APPROVER the author, so they cannot then approve the mapping, and it
    travels CF-V1-E6-03's lifecycle to a steward exactly as one saved through
    the editor does.
    """
    feed_id = proposal.feed_id or ""
    accepted = _accepted_mapping_records(proposal.payload, body)
    corrections = proposals.diff_fields(
        proposal.payload,
        accepted,
        key="source_column",
        # `unmapped` is compared because a reviewer MAPPING a column the agent
        # declined, or declining one it mapped, is the most informative
        # correction the eval set can receive — and comparing only the target
        # would score both as "no change".
        fields=("target_entity", "target_field", "unmapped"),
    )
    records = tuple(r for r in accepted.get("records", ()) if isinstance(r, dict))
    try:
        decided = proposals.approve(
            proposal,
            approver=principal.as_actor(),
            comment=body.comment,
            corrections=corrections,
        )
        applied, draft = proposals.apply(
            decided,
            object_type=ObjectType.MAPPING,
            object_id=feed_id,
            body=_mapping_body_from(records, feed_id),
            version=_next_mapping_version(metadata, feed_id),
        )
    except (proposals.ProposalError, mapping_core.MappingError) as refused:
        audit.record(
            object_type=ObjectType.MAPPING,
            object_id=feed_id or proposal.proposal_id,
            action="refused:approve_proposal",
            actor=principal.as_actor(),
            detail=str(refused),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(refused)) from None

    metadata.save(draft)
    stored = metadata.record_proposal(applied)
    audit.record(
        object_type=ObjectType.MAPPING,
        object_id=draft.object_id,
        version=draft.version,
        action="applied_proposal",
        actor=principal.as_actor(),
        detail=f"{proposal.proposal_id} · {len(corrections)} correction(s)",
    )
    return _proposal_out(stored)


# ── NL rules and their preview · CF-V1-E7-01, CF-V1-E7-02 ────────────────────


def _published_rules(metadata: MetadataDbPort, feed_id: str) -> tuple[rules_core.RuleSpec, ...]:
    """This feed's PUBLISHED rules. House style, and the "already stated" check.

    Published only. A draft is somebody's unreviewed work, and treating it as
    already-decided would let an unapproved rule silence the agent about the
    sentence it came from.
    """
    try:
        obj = metadata.get(ObjectType.DQ_RULE, feed_id)
    except ObjectNotFoundError:
        return ()
    if not obj.is_executable:
        return ()
    try:
        return rules_core.rules_from_governed(obj)
    except rules_core.RuleError:
        return ()


def _sample_rows(
    metadata: MetadataDbPort,
    storage: StoragePort | None,
    feed_id: str,
    profile_id: str | None,
) -> tuple[dict[str, Any], ...]:
    """The rows a preview runs over, read from the profiled sample.

    From the SAME file the profile was taken of, so the counts a BA sees and
    the statistics they were shown describe one delivery. A preview over a
    different file would be a preview of a different question.
    """
    if storage is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "no storage pin is fitted on this deployment, so there is no sample to "
            "preview against. The rules themselves are unaffected.",
        )
    found = (
        [metadata.get_profile(profile_id, feed_id)]
        if profile_id
        else list(metadata.list_profiles(feed_id=feed_id, limit=1))
    )
    if not found:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"feed {feed_id!r} has no profiled sample — profile a delivery first, so the "
            "preview and the statistics describe the same file.",
        )
    record = found[0]
    try:
        content = storage.read_bytes(record.profile.source_key)
    except Exception:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "the profiled sample is no longer in landing, so there is nothing to preview "
            "against. Profiling a fresh delivery restores it.",
        ) from None
    parsed = parse(
        content,
        file_format=record.profile.structure.file_format,
        encoding=record.profile.structure.encoding,
    )
    return tuple(parsed.table.to_pylist())


def _sample_rows_or_none(
    metadata: MetadataDbPort,
    storage: StoragePort | None,
    feed_id: str,
    profile_id: str | None,
) -> tuple[dict[str, Any], ...] | None:
    """The sample, or None when there is not one.

    BEST EFFORT on the authoring path, deliberately. A feed with no profiled
    delivery yet — or one whose sample has moved out of landing — still
    produces usable rules, and the proposal says why the counts are missing.
    Refusing the whole proposal because a file moved would make the agent
    useless on exactly the feeds a BA is still setting up.

    The PREVIEW route raises instead, because there the sample IS the request.
    """
    try:
        return _sample_rows(metadata, storage, feed_id, profile_id)
    except HTTPException:
        return None


def _preview_pack_out(
    feed_id: str,
    rules: tuple[rules_core.RuleSpec, ...],
    rows: tuple[dict[str, Any], ...],
    contract: SchemaContract | None,
) -> RulePreviewPackOut:
    previews = rule_preview.preview_all(rules, rows, contract=contract)
    pack = rule_preview.evidence_pack(previews, sample_rows=len(rows))
    return RulePreviewPackOut(
        feed_id=feed_id,
        sample_rows=pack["sample_rows"],
        rules_previewed=pack["rules_previewed"],
        rules_not_previewable=pack["rules_not_previewable"],
        total_failures=pack["total_failures"],
        previews=[
            RulePreviewOut(
                rule_id=one.rule_id,
                stated=one.stated,
                explanation=one.explanation,
                tested=one.tested,
                passed=one.passed,
                failed=one.failed,
                skipped=one.skipped,
                failure_rate=one.failure_rate,
                failing_rows=[
                    FailingRowOut(row_number=row.row_number, values=dict(row.values))
                    for row in one.failing_rows
                ],
                masked_columns=list(one.masked_columns),
                not_previewable=one.not_previewable,
                summary=one.summary(),
            )
            for one in previews
        ],
    )


def _rule_record_fields(record: dict[str, Any]) -> dict[str, Any]:
    """One proposed rule, as the reviewer reads it. CF-V1-E7-01.

    `sql` and `pyspark` are rendered HERE, by the platform, from the stored
    check — never read from the payload, because the payload is what a model
    produced and the whole design is that a model produces no query.
    """
    stored = record.get("rule") or {}
    sql = pyspark = ""
    if stored:
        try:
            rebuilt = rules_core.rule_from_dict(stored)
            sql = rebuilt.sql(table="silver_raw")
            pyspark = rebuilt.pyspark()
        except rules_core.RuleError:  # pragma: no cover - stored rules are validated
            sql = pyspark = ""
    return {
        "stated": str(record.get("stated", "")),
        "unsupported": bool(record.get("unsupported", False)),
        "unsupported_reason": str(record.get("unsupported_reason", "")),
        "rule_id": record.get("rule_id"),
        "name": str(stored.get("name", "")),
        "explanation": str(record.get("explanation", "")),
        "check_kind": record.get("check_kind"),
        "column": record.get("column"),
        "dimension": stored.get("dimension"),
        "severity": record.get("severity"),
        "glossary_id": stored.get("glossary_id"),
        "confidence": record.get("confidence"),
        "settled_by": str(record.get("settled_by", "inference")),
        "rationale": str(stored.get("rationale", "")),
        "sql": sql,
        "pyspark": pyspark,
    }


def _canonical_of(metadata: MetadataDbPort) -> canonical.CanonicalModel:
    """Built per request from the spec and the CURRENT glossary.

    Not cached. The glossary is a governed object that a steward edits, and a
    canonical browser serving yesterday's vocabulary is the stale data
    dictionary this screen exists to replace.
    """
    return canonical.build(canonical.canonical_schemas(), _glossary_of(metadata))


def _canonical_field_out(field: canonical.CanonicalField) -> CanonicalFieldOut:
    return CanonicalFieldOut(
        name=field.name,
        entity=field.entity,
        domains=list(field.domains),
        definition=field.shown_definition,
        definition_missing=not field.is_defined,
        glossary_id=field.glossary_id,
        term=field.term,
        synonyms=list(field.synonyms),
        is_phi=field.is_phi,
        type=field.type.value if field.type else None,
        nullable=field.nullable,
        deployed=field.deployed,
    )


def _canonical_entity_out(
    entity: canonical.CanonicalEntity, *, with_fields: bool
) -> CanonicalEntityOut:
    defined, total = entity.coverage
    return CanonicalEntityOut(
        name=entity.name,
        domains=list(entity.domains),
        schema_name=entity.schema,
        deployed=entity.deployed,
        comment=entity.comment,
        field_count=total,
        defined_count=defined,
        phi_count=len(entity.phi_fields),
        fields=[_canonical_field_out(f) for f in entity.fields] if with_fields else [],
    )


def _suspension_out(state: suspension.Suspension) -> SuspensionOut:
    now = datetime.now(UTC)
    return SuspensionOut(
        feed_id=state.feed_id,
        is_paused=state.is_active_at(now),
        reason=state.reason,
        paused_by=state.paused_by.subject if state.paused_by else None,
        paused_ts=state.paused_ts,
        resumes_after=state.resumes_after,
        may_start_new_work=state.may_start_new_work(now),
        affects_work_already_running=state.affects_work_already_running,
        explanation=state.explain(now),
    )


def _references_out(metadata: MetadataDbPort, target: GovernedObject) -> ReferencesOut:
    """The reference graph, read for one object.

    Built from the WHOLE registry every time rather than from a cache: a
    "referenced everywhere" view computed from a subset is the same failure as
    a hand-maintained one, arriving by a route nobody thinks to check.
    """
    packet = _packet_for(metadata, target)
    return ReferencesOut(
        object_type=target.object_type.value,
        object_id=target.object_id,
        version=target.version,
        references=[
            ReferenceOut(
                object_type=touched.object_type.value,
                object_id=touched.object_id,
                version=touched.version,
                lifecycle_state=touched.lifecycle_state.value,
                via=touched.via,
            )
            for touched in (*packet.engineering_impact, *packet.business_impact)
        ],
        unknowns=[
            UnknownImpactOut(name=unknown.name, reason=unknown.reason)
            for unknown in packet.unknowns
        ],
    )


def _feeds_of_source(metadata: MetadataDbPort, source_id: str) -> tuple[str, ...]:
    """Which feeds name this source. Computed, never a maintained list."""
    return tuple(
        sorted(
            obj.object_id
            for obj in metadata.list(ObjectType.FEED)
            if str((obj.body.get("operations") or {}).get("source_id", "")) == source_id
        )
    )


def _glossary_of(metadata: MetadataDbPort) -> Glossary:
    """The glossary as the domain sees it, built from the registry.

    Every term is a governed object; this is the projection that turns the
    rows back into the value type the mapping and PHI agents reason with.
    """
    return Glossary(
        terms=tuple(
            GlossaryTerm.from_governed(obj) for obj in metadata.list(ObjectType.GLOSSARY_TERM)
        )
    )


def _glossary_states(metadata: MetadataDbPort) -> dict[str, tuple[str, int]]:
    return {
        obj.object_id: (obj.lifecycle_state.value, obj.version)
        for obj in metadata.list(ObjectType.GLOSSARY_TERM)
    }


def _glossary_out(term: GlossaryTerm, state: str, version: int) -> GlossaryTermOut:
    from cinqflow.core.model.governed import LifecycleState

    citation = CitationId(kind=CitationKind.TERM, subject=term.slug)
    return GlossaryTermOut(
        glossary_id=term.glossary_id,
        term=term.term,
        definition=term.definition,
        domain_category=term.domain_category,
        sub_category=term.sub_category,
        classification=term.classification,
        regulatory_reference=term.regulatory_reference,
        mapped_domains=list(term.mapped_domains),
        mapped_tables=list(term.mapped_tables),
        synonyms=list(term.synonyms),
        sensitivity=term.sensitivity,
        is_phi=term.is_phi,
        notes=term.notes,
        lifecycle_state=state,
        status=LifecycleState(state).status_word,
        version=version,
        citation_id=str(citation),
        route=citation.route,
    )


def _profile_out(record: FileProfileRecord, principal: Principal) -> FileProfileOut:
    """The profile on the wire, with values shown only to those who may edit.

        "Send sample data anywhere except storage the BA's role can access."
        — CF-V1-E5-01, a documented don't

    The BA working the sample needs the example values — judging a mapping
    without seeing what is in the column is guesswork. Everyone else gets the
    statistics with the values stripped. Because `FileProfile.fingerprint`
    covers the FACTS and not the values, both readers see the SAME
    `profile_id`: a steward reviewing the redacted view and the BA who ran it
    are provably looking at one piece of evidence.
    """
    profile = record.profile
    if not may(principal, Action.EDIT_FEED, feed_id=record.feed_id):
        profile = profile.without_values()
    structure = profile.structure
    return FileProfileOut(
        profile_id=profile.profile_id,
        profiler_version=profile.profiler_version,
        feed_id=record.feed_id,
        source_key=profile.source_key,
        source_fingerprint=profile.source_fingerprint,
        readable=profile.readable,
        would_load=profile.would_load,
        refusal=(
            None
            if profile.refusal is None
            else RefusalOut(
                reason=profile.refusal.reason.value,
                explanation=profile.refusal.explanation,
                ask_the_payer=profile.refusal.ask_the_payer,
            )
        ),
        structure=FileStructureOut(
            file_format=structure.file_format,
            encoding=structure.encoding,
            declared_encoding=structure.declared_encoding,
            byte_order_mark=structure.byte_order_mark,
            delimiter=structure.delimiter,
            quote_char=structure.quote_char,
            line_ending=structure.line_ending,
            column_count=structure.column_count,
            data_rows=structure.data_rows,
            bytes_total=structure.bytes_total,
            bytes_read=structure.bytes_read,
            sampled=structure.sampled,
        ),
        columns=[_column_profile_out(profile, column) for column in profile.columns],
        findings=[_finding_out(f) for f in profile.findings],
        blockers=[_finding_out(f) for f in profile.blockers],
        key_candidates=[
            KeyCandidateOut(
                columns=list(key.columns),
                distinct_count=key.distinct_count,
                populated_rows=key.populated_rows,
                null_rows=key.null_rows,
                duplicate_values=key.duplicate_values,
                is_unique=key.is_unique,
                examples=[[value, list(lines)] for value, lines in key.examples],
                values_redacted=key.values_redacted,
            )
            for key in profile.key_candidates
        ],
        key_search=KeySearchOut(
            single_columns_examined=profile.key_search.single_columns_examined,
            composite_width=profile.key_search.composite_width,
            pairs_examined=profile.key_search.pairs_examined,
            pairs_skipped=profile.key_search.pairs_skipped,
            rows_retained=profile.key_search.rows_retained,
            excluded_columns=list(profile.key_search.excluded_columns),
            note=profile.key_search.note,
        ),
        duplicate_rows=profile.duplicates.duplicate_rows,
        duplicate_groups=profile.duplicates.duplicate_groups,
        values_redacted=profile.values_redacted,
        profiled_by=record.profiled_by,
        profiled_ts=record.profiled_ts,
        citation_id=str(profile.citation),
        route=profile.citation.route,
    )


def _column_profile_out(profile: FileProfile, column: ColumnProfile) -> ColumnProfileOut:
    citation = profile.citation_for(column.name)
    return ColumnProfileOut(
        name=column.name,
        position=column.position,
        row_count=column.row_count,
        null_count=column.null_count,
        null_like_count=column.null_like_count,
        distinct_count=column.distinct_count,
        distinct_is_exact=column.distinct_is_exact,
        is_unique=column.is_unique,
        min_length=column.min_length,
        max_length=column.max_length,
        padded_count=column.padded_count,
        typed_cell_count=column.typed_cell_count,
        narrowest_type=column.narrowest_type.value if column.narrowest_type else None,
        type_candidates=[
            TypeCandidateOut(
                type=candidate.type.value,
                matched=candidate.matched,
                considered=candidate.considered,
                share=candidate.share,
            )
            for candidate in column.type_candidates
        ],
        date_formats=[
            DateFormatOut(label=fmt.label, matched=fmt.matched) for fmt in column.date_formats
        ],
        observed_precision=column.observed_precision,
        observed_scale=column.observed_scale,
        examples=list(column.examples),
        top_values=[[value, count] for value, count in column.top_values],
        min_value=column.min_value,
        max_value=column.max_value,
        values_redacted=column.values_redacted,
        citation_id=str(citation),
        route=citation.route,
    )


def _finding_out(finding: Finding) -> FindingOut:
    return FindingOut(
        quirk=finding.quirk.value,
        detail=finding.detail,
        occurrences=finding.occurrences,
        first_lines=list(finding.first_lines),
        columns=list(finding.columns),
        blocks_ingestion=finding.blocks_ingestion,
    )


def _phi_column_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_name": str(record.get("source_name", "")),
        "position": int(record.get("position", 0)),
        "is_phi": bool(record.get("is_phi", False)),
        "basis": str(record.get("basis", "precaution")),
        "phi_kind": record.get("phi_kind"),
        "code_set": record.get("code_set"),
        "confidence": float(record.get("confidence", 0.0)),
        "needs_steward_review": bool(record.get("needs_steward_review", False)),
        "glossary_id": record.get("glossary_id"),
        "rationale": str(record.get("rationale", "")),
        "citations": list(record.get("citations", ())),
    }


def _recall_out(proposal: Proposal, glossary: Glossary) -> PhiRecallOut:
    """Recompute the gate from a stored classification.

    Rebuilt from the payload rather than from a stored number, because a
    recall figure written at classification time cannot notice that a term was
    flagged PHI last Tuesday. The arithmetic is trivial and the staleness is
    the whole risk.
    """
    records = [r for r in proposal.payload.get("records", ()) if isinstance(r, dict)]
    expected = [r for r in records if glossary.is_phi_column(str(r.get("source_name", "")))]
    protected = [r for r in expected if r.get("is_phi")]
    missed = [str(r.get("source_name")) for r in expected if not r.get("is_phi")]
    over = [
        str(r.get("source_name"))
        for r in records
        if r.get("is_phi") and not glossary.is_phi_column(str(r.get("source_name", "")))
    ]
    return PhiRecallOut(
        protected=len(protected),
        expected=len(expected),
        passes=not missed,
        missed=missed,
        over_flagged=over,
        report=(
            f"{len(protected)}/{len(expected)} glossary-flagged columns protected"
            + (f" — MISSED: {', '.join(missed)}" if missed else " — gate holds")
            + f" · {len(over)} column(s) protected that the glossary does not flag "
            "(the safe direction, reported not gated)"
        ),
    )


def _masking_policy_out(proposal: Proposal) -> MaskingPolicyOut:
    records = [r for r in proposal.payload.get("records", ()) if isinstance(r, dict)]
    return MaskingPolicyOut(
        feed_id=proposal.feed_id or "",
        profile_id=str(proposal.payload.get("profile_id", "")),
        proposal_id=proposal.proposal_id,
        state=proposal.state.value,
        masked_columns=[str(r.get("source_name")) for r in records if r.get("is_phi")],
        unmasked_columns=[str(r.get("source_name")) for r in records if not r.get("is_phi")],
        pending_steward=[
            str(r.get("source_name")) for r in records if r.get("needs_steward_review")
        ],
    )


def _proposal_out(proposal: Proposal, *, model_called: bool = True) -> ProposalOut:
    records = proposal.payload.get("records", ())
    # ONE queue, two record shapes. Dispatched on the agent's name rather than
    # sniffed from the payload's keys: a payload that grows a `basis` field
    # would silently change how every proposal renders, and the agent is what
    # actually determines the shape.
    is_phi_agent = proposal.agent == PHI_AGENT
    is_mapping_agent = proposal.agent == MAPPING_AGENT
    is_rule_agent = proposal.agent == RULE_AGENT
    return ProposalOut(
        proposal_id=proposal.proposal_id,
        agent=proposal.agent,
        capability=proposal.capability,
        risk_class=proposal.risk_class.name,
        state=proposal.state.value,
        feed_id=proposal.feed_id,
        run_id=proposal.run_id,
        confidence=proposal.confidence,
        prompt_hash=proposal.prompt_hash,
        created_by=proposal.created_by.subject,
        created_ts=proposal.created_ts,
        decided_by=proposal.decided_by.subject if proposal.decided_by else None,
        decision_comment=proposal.decision_comment,
        decided_ts=proposal.decided_ts,
        applied_object_type=(
            proposal.applied_object_type.value if proposal.applied_object_type else None
        ),
        applied_object_id=proposal.applied_object_id,
        applied_version=proposal.applied_version,
        grounding_citations=[str(c) for c in proposal.grounding_citations],
        columns=(
            []
            if is_phi_agent or is_mapping_agent or is_rule_agent
            else [ProposedColumnOut(**_column_fields(r)) for r in records]
        ),
        phi_columns=(
            [PhiColumnOut(**_phi_column_fields(r)) for r in records] if is_phi_agent else []
        ),
        mapping_lines=(
            [ProposedMappingOut(**_mapping_record_fields(r)) for r in records]
            if is_mapping_agent
            else []
        ),
        rules=(
            [ProposedRuleOut(**_rule_record_fields(r)) for r in records] if is_rule_agent else []
        ),
        needs_steward_review=list(proposal.payload.get("needs_steward_review", ())),
        masked_columns=list(proposal.payload.get("masked_columns", ())),
        needs_input=list(proposal.payload.get("needs_input", ())),
        refusals=list(proposal.payload.get("refusals", ())),
        corrections=[
            CorrectionOut(
                field_path=c.field_path,
                proposed=c.proposed,
                accepted=c.accepted,
                is_addition=c.is_addition,
            )
            for c in proposal.corrections
        ],
        model_called=model_called,
    )


def _mapping_record_fields(record: dict[str, Any]) -> dict[str, Any]:
    """One proposed mapping line, as the reviewer reads it. CF-V1-E6-02."""
    return {
        "source_column": str(record.get("source_column", "")),
        "target_entity": str(record.get("target_entity", "")),
        "target_field": str(record.get("target_field", "")),
        "unmapped": bool(record.get("unmapped", False)),
        "unmapped_reason": str(record.get("unmapped_reason", "")),
        "glossary_id": record.get("glossary_id"),
        "confidence": float(record.get("confidence", 0.0)),
        "settled_by": str(record.get("settled_by", "inference")),
        "rationale": str(record.get("rationale", "")),
        "like_feed_id": record.get("like_feed_id"),
        "citations": list((record.get("line") or {}).get("citations", ())),
    }


def _column_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_name": str(record.get("source_name", "")),
        "position": int(record.get("position", 0)),
        "name": record.get("name"),
        "type": record.get("type"),
        "nullable": bool(record.get("nullable", True)),
        "is_phi": bool(record.get("is_phi", False)),
        "glossary_id": record.get("glossary_id"),
        "date_format": record.get("date_format"),
        "precision": record.get("precision"),
        "scale": record.get("scale"),
        "confidence": float(record.get("confidence", 0.0)),
        "settled_by": str(record.get("settled_by", "inference")),
        "needs_input": bool(record.get("needs_input", False)),
        "rationale": str(record.get("rationale", "")),
        "citations": list(record.get("citations", ())),
    }


def _apply_decisions(payload: dict[str, Any], body: ApproveProposalIn) -> dict[str, Any]:
    """The reviewer's version of the records.

    Absent fields keep the proposal's value, so changing one column's type does
    not require restating the other forty — and a reviewer who changed nothing
    produces zero corrections, which is exactly what the gate counts.
    """
    decided = {d.source_name: d for d in body.columns}
    records: list[dict[str, Any]] = []
    for record in payload.get("records", ()):
        accepted = dict(record)
        decision = decided.get(str(record.get("source_name")))
        if decision is not None:
            for attribute in ("name", "type", "nullable", "is_phi", "date_format"):
                value = getattr(decision, attribute)
                if value is not None:
                    accepted[attribute] = value
            # A column the human has now settled is no longer awaiting them.
            if accepted.get("name") and accepted.get("type"):
                accepted["needs_input"] = False
        records.append(accepted)
    return {**payload, "records": records}


def _refuse_downgrades_on_the_approve_path(
    corrections: tuple[proposals.Correction, ...],
) -> None:
    """Accepting a classification may raise a flag. It may never clear one.

    TWO ACTS, TWO DOORS, and the split is the point rather than an
    inconvenience. Accepting an agent's draft is AUTHORING, which a Business
    Analyst does (`EDIT_FEED`). Clearing a PHI flag is a STEWARD's decision
    with their name and their reason on it (`APPROVE`), and it goes through
    `/reclassify`. Folding the two into one route would mean either a BA could
    unprotect a column while accepting a contract, or a steward would need
    authoring rights they are deliberately not given.
    """
    cleared = _cleared_by_steward(corrections)
    if not cleared:
        return
    raise PhiDowngradeRefusedError(
        f"clearing the PHI flag on {', '.join(sorted(cleared))} is a data steward's "
        "decision, not part of accepting a classification. Accept it as it stands, or "
        "ask a steward to use the reclassify route — where their name and their reason "
        "are recorded. Masking everywhere reads these flags."
    )


def _cleared_by_steward(corrections: tuple[proposals.Correction, ...]) -> frozenset[str]:
    """Columns a reviewer explicitly unprotected, by name.

    Needed because "the classification says not PHI" and "a steward decided
    not PHI" look identical in the accepted records and must not be treated
    alike: the first may only ever RAISE the contract's flag, the second is
    the one act that may lower it.
    """
    return frozenset(
        c.field_path.removesuffix(".is_phi")
        for c in corrections
        if c.field_path.endswith(".is_phi") and c.accepted is False
    )


def _phi_contract_body(
    metadata: MetadataDbPort,
    feed_id: str,
    accepted: dict[str, Any],
    cleared: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Merge approved PHI flags onto the feed's existing contract columns.

    The flags attach to a CONTRACT, because that is the object the pipeline
    reads and the object masking is derived from — so this story's approval
    produces the next version of the same object CF-V1-E5-02's approval
    produced, rather than a parallel document that could disagree with it.

    Refuses when no contract exists yet, and says which story to run first.
    That ordering is real: a PHI flag is a property OF a contract column, and
    there is nowhere to put one before the columns are named.
    """
    try:
        current = metadata.get(ObjectType.CONTRACT, feed_id)
    except ObjectNotFoundError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"feed {feed_id!r} has no data contract yet, and a PHI flag is a property "
            "of a contract column. Approve a schema contract first — the classification "
            "stays in the queue and every flagged column stays masked meanwhile.",
        ) from None

    flags = {
        str(r.get("source_name")): r for r in accepted.get("records", ()) if isinstance(r, dict)
    }
    columns = []
    for column in current.body.get("columns", ()):
        source_name = str(column.get("source_name"))
        record = flags.get(source_name)
        if record is None:
            columns.append(column)
            continue
        columns.append(
            {
                **column,
                # RAISED OR KEPT — never lowered except by the one act that is
                # allowed to lower it. A classification and a contract are two
                # sources of the same flag, and combining them by OR is the
                # only rule that cannot lose one. `cleared` is the steward's
                # explicit decision, already checked for a named human and a
                # stated reason, and it is what breaks the OR.
                "is_phi": (
                    bool(record.get("is_phi"))
                    if source_name in cleared
                    else bool(column.get("is_phi")) or bool(record.get("is_phi"))
                ),
                "phi_kind": record.get("phi_kind"),
                "code_set": record.get("code_set"),
                "phi_basis": record.get("basis"),
            }
        )
    return {**current.body, "columns": columns}


def _contract_body(accepted: dict[str, Any], *, key_columns: tuple[str, ...]) -> dict[str, Any]:
    """The DRAFT contract's body — machine-enforceable, in `SchemaContract`'s shape.

    Columns still marked `needs_input` are DROPPED rather than typed as string.
    A contract must be enforceable, and a field nobody could type is not a
    field the engine can validate — it belongs on the "still to decide" list,
    which is where the wizard's readiness checklist will find it.
    """
    return {
        "key_columns": list(key_columns),
        "columns": [
            {
                "name": record.get("name"),
                "type": record.get("type"),
                # The key columns the approver declared are the ONLY NOT NULL
                # ones. A sample cannot establish that constraint, and a
                # guessed one quarantines real members — so it arrives with
                # the human's key declaration and nowhere else.
                "nullable": record.get("name") not in key_columns,
                "source_name": record.get("source_name"),
                "is_phi": bool(record.get("is_phi", False)),
                "precision": record.get("precision"),
                "scale": record.get("scale"),
                "date_formats": [record["date_format"]] if record.get("date_format") else [],
            }
            for record in accepted.get("records", ())
            if not record.get("needs_input") and record.get("name") and record.get("type")
        ],
        "undecided": [
            record.get("source_name")
            for record in accepted.get("records", ())
            if record.get("needs_input") or not (record.get("name") and record.get("type"))
        ],
    }


def _next_contract_version(metadata: MetadataDbPort, feed_id: str) -> int:
    history = metadata.history(ObjectType.CONTRACT, feed_id)
    return (max((o.version for o in history), default=0)) + 1


def _acceptance_out(acceptance: proposals.Acceptance) -> AcceptanceOut:
    return AcceptanceOut(
        total=acceptance.total,
        accepted=acceptance.accepted,
        corrected=acceptance.corrected,
        rate=acceptance.rate,
        deterministic_total=acceptance.deterministic_total,
        deterministic_corrected=acceptance.deterministic_corrected,
        inferred_total=acceptance.inferred_total,
        inferred_corrected=acceptance.inferred_corrected,
        inferred_rate=acceptance.inferred_rate,
        additions=acceptance.additions,
        report=acceptance.report(SCHEMA_ACCEPTANCE_GATE),
    )


def _touched_out(touched: Touched) -> TouchedOut:
    return TouchedOut(
        object_type=touched.object_type.value,
        object_id=touched.object_id,
        version=touched.version,
        lifecycle_state=touched.lifecycle_state.value,
        via=touched.via,
    )


def _packet_out(packet: ImpactPacket) -> ImpactPacketOut:
    return ImpactPacketOut(
        object_type=packet.object_type.value,
        object_id=packet.object_id,
        version=packet.version,
        lifecycle_state=packet.lifecycle_state.value,
        author_subject=packet.author_subject,
        diff=list(packet.diff),
        engineering_impact=[_touched_out(t) for t in packet.engineering_impact],
        business_impact=[_touched_out(t) for t in packet.business_impact],
        unknowns=[UnknownImpactOut(name=u.name, reason=u.reason) for u in packet.unknowns],
        evidence=packet.evidence,
        blocks_production=packet.blocks_production,
        is_empty=packet.is_empty,
    )


def _governed_out(obj: GovernedObject) -> GovernedOut:
    return GovernedOut(
        object_type=obj.object_type.value,
        object_id=obj.object_id,
        version=obj.version,
        lifecycle_state=obj.lifecycle_state.value,
        status=obj.lifecycle_state.status_word,
        created_by_subject=obj.created_by.subject,
        created_by_name=obj.created_by.display_name,
        created_ts=obj.created_ts,
        approved_by_subject=obj.approved_by.subject if obj.approved_by else None,
        approved_by_name=obj.approved_by.display_name if obj.approved_by else None,
        approved_ts=obj.approved_ts,
        body=obj.body,
    )


def _principal_out(principal: Principal) -> PrincipalOut:
    permitted = frozenset(a for a in Action if may(principal, a))
    return PrincipalOut(
        subject=principal.subject,
        display_name=principal.display_name,
        roles=sorted(role.value for role in principal.roles),
        has_access=principal.has_access,
        permitted_actions=sorted(a.value for a in permitted),
        # The persona home, ranked in core. ADR-0020's merge rule is a server
        # fact here rather than a branch in a page component.
        home_slots=[
            HomeSlotOut(key=slot.key.value, answers=slot.answers)
            for slot in home_for(frozenset(principal.roles), permitted)
        ],
    )


def _batch_out(batch: BatchControl) -> BatchOut:
    citation = CitationId(kind=CitationKind.BATCH, subject=batch.batch_id)
    return BatchOut(
        batch_id=batch.batch_id,
        feed_id=batch.feed_id,
        business_date=batch.business_date,
        state=batch.state.value,
        status=batch.state.status_word,
        started_ts=batch.started_ts,
        completed_ts=batch.completed_ts,
        citation_id=str(citation),
        route=citation.route,
    )


def _rows_out(result: ToolResult) -> RowsOut:
    return RowsOut(
        tool=result.tool,
        rows=[dict(row) for row in result.rows],
        citations=[str(c) for c in result.citations],
        row_count=result.row_count,
        out_of_scope=result.out_of_scope,
        marker=result.marker,
        note=result.note,
    )
