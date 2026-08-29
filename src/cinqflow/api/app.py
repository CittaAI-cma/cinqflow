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
    BatchOut,
    BudgetOut,
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
    ReadinessOut,
    ReclassifyIn,
    ReferenceOut,
    ReferencesOut,
    RefusalOut,
    RejectProposalIn,
    ResumeFeedIn,
    RowsOut,
    SimilarFeedOut,
    SourceIn,
    SourceOut,
    SuspensionEventOut,
    SuspensionOut,
    ToolOut,
    TouchedOut,
    TraceStepOut,
    TransitionIn,
    TypeCandidateOut,
    UnknownImpactOut,
    UnknownOut,
    VersionDiffOut,
    WorkQueueOut,
)
from cinqflow.core import lifecycle, proposals
from cinqflow.core.agents.phi_detection.graph import AGENT as PHI_DETECTION_AGENT
from cinqflow.core.agents.pipeline_insight.graph import AGENT
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.impact import ImpactPacket, ImpactUnknownError, Touched, build_packet
from cinqflow.core.intelligence import Budget
from cinqflow.core.model.governed import (
    GovernedObject,
    LifecycleViolationError,
    ObjectType,
)
from cinqflow.core.navigation import ACTIVE_WAVE, for_roles
from cinqflow.core.persona import home_for
from cinqflow.core.phi import Basis, ColumnClassification, PhiDowngradeRefusedError, reclassify
from cinqflow.core.profiling import ColumnProfile, FileProfile, Finding
from cinqflow.core.proposals import Proposal, ProposalState
from cinqflow.core.registry import clone as registry_clone
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry import operations, suspension
from cinqflow.core.registry import search as registry_search
from cinqflow.core.registry import source as source_registry
from cinqflow.core.registry.execution_plane import ExecutionPlaneRegister
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.registry.wave0 import wave_0_register
from cinqflow.core.security import Action, may
from cinqflow.core.tools import CATALOGUE, ToolError
from cinqflow.intelligence.agents.phi_detection import PhiDetectionAgent, RecallGateFailedError
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
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

#: Build an agent for ONE caller. Never a shared agent — a shared agent is a
#: shared scope, and the tool context is where the caller's RBAC lives.
AgentFactory = Callable[[Principal, "ControlTablesPort", MetadataDbPort], PipelineInsightAgent]

#: Built per REQUEST rather than per app: the agent writes a proposal through
#: whichever metadata pin the request is served by, which on the real plane is
#: a per-request transaction.
SchemaInferenceFactory = Callable[[MetadataDbPort], SchemaInferenceAgent]

#: Same reasoning, same lifetime. CF-V1-E5-03.
PhiDetectionFactory = Callable[[MetadataDbPort], PhiDetectionAgent]


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
        this handler existed."""
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
        columns=([] if is_phi_agent else [ProposedColumnOut(**_column_fields(r)) for r in records]),
        phi_columns=(
            [PhiColumnOut(**_phi_column_fields(r)) for r in records] if is_phi_agent else []
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
