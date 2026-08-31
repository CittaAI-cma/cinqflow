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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import PlainTextResponse

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.api.audit import AuditLog
from cinqflow.api.deps import NOT_FOUND, CurrentPrincipal, Wiring, require
from cinqflow.api.schemas import (
    AcceptanceOut,
    AcknowledgeIncidentIn,
    ActionRecordOut,
    ActionRefusalOut,
    ActionRequestIn,
    ActionSurfaceOut,
    AgentActionOut,
    ApproveProposalIn,
    ArrivalRowOut,
    AskIn,
    AskOut,
    AttentionItemOut,
    AuditOut,
    AuthorRulesIn,
    BatchErrorOut,
    BatchOut,
    BatchViewOut,
    BlockerOut,
    BoardOut,
    BudgetOut,
    CanonicalEntityOut,
    CanonicalFieldOut,
    CanonicalModelOut,
    CaseModel,
    CertificationCheckOut,
    CertificationOut,
    ChapterOut,
    ChecklistItemOut,
    ClaimOut,
    CloneFeedIn,
    CloneOut,
    ColumnProfileOut,
    ConnectionCheckOut,
    ConsequenceOut,
    ContractOut,
    CorrectionOut,
    CorrectVarianceIn,
    CounterOut,
    DateFormatOut,
    DeliveryOut,
    DependencyPictureOut,
    DestinationOut,
    DetectPhiIn,
    DifferenceOut,
    DropExplanationOut,
    EvidencePackOut,
    ExampleOut,
    ExplainVarianceIn,
    FailingRowOut,
    FailureOut,
    FeedIn,
    FeedOut,
    FileProfileOut,
    FileStructureOut,
    FindingOut,
    FreshnessOut,
    GapOut,
    GlossaryTermOut,
    GovernedOut,
    GuideMatchOut,
    HomeSlotOut,
    ImpactPacketOut,
    IncidentOut,
    IncidentRowOut,
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
    NarrativeOut,
    NavigationOut,
    ObstacleOut,
    OperationsModel,
    OwnerModel,
    PauseFeedIn,
    PhiColumnOut,
    PhiRecallOut,
    PictureNodeOut,
    PolicyFindingOut,
    PreviewOut,
    PrincipalOut,
    PriorIncidentOut,
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
    ReleaseDecisionOut,
    ReliabilityComponentOut,
    ReliabilityOut,
    ResolveIncidentIn,
    ResumeFeedIn,
    ReviewQueueOut,
    RowsOut,
    RuleOutcomeOut,
    RulePolicyIn,
    RulePolicyOut,
    RulePolicySetOut,
    RulePreviewOut,
    RulePreviewPackOut,
    RunbookOut,
    SimilarFeedOut,
    SourceIn,
    SourceOut,
    StageViewOut,
    SuspensionEventOut,
    SuspensionOut,
    TechnicalReviewOut,
    ToolOut,
    TouchedOut,
    TraceStepOut,
    TransformModel,
    TransitionIn,
    TypeCandidateOut,
    UnknownImpactOut,
    UnknownOut,
    VarianceIn,
    VarianceOut,
    VersionDiffOut,
    WaiveVarianceIn,
    WeeklyAcceptanceOut,
    WizardOut,
    WizardStepOut,
    WorkQueueOut,
)
from cinqflow.core import certification as batch_certification
from cinqflow.core import delivery as delivery_core
from cinqflow.core import lifecycle, onboarding, proposals, reliability, scheduling
from cinqflow.core import mapping as mapping_core
from cinqflow.core import operations as ops_board
from cinqflow.core import rules as rules_core
from cinqflow.core import variance as variances_core
from cinqflow.core.agents.fingerprint_match.graph import AGENT as FINGERPRINT_MATCH_AGENT_NAME
from cinqflow.core.agents.mapping_suggestion.graph import AGENT as MAPPING_SUGGESTION_AGENT
from cinqflow.core.agents.phi_detection.graph import AGENT as PHI_DETECTION_AGENT
from cinqflow.core.agents.pipeline_insight.graph import AGENT
from cinqflow.core.agents.rule_authoring import graph as rule_authoring_graph
from cinqflow.core.agents.rule_authoring.graph import AGENT as RULE_AUTHORING_AGENT
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.citations import parse as parse_citation
from cinqflow.core.impact import ImpactPacket, ImpactUnknownError, Touched, build_packet
from cinqflow.core.intelligence import Budget
from cinqflow.core.landing import LandingOutcome
from cinqflow.core.mapping import versioning as mapping_versioning
from cinqflow.core.mapping.versioning import UnacknowledgedLossError, refuse_unacknowledged_loss
from cinqflow.core.model.governed import (
    GovernedObject,
    LifecycleState,
    LifecycleViolationError,
    ObjectType,
)
from cinqflow.core.model.profile import Profile
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.core.navigation import ACTIVE_WAVE, for_roles
from cinqflow.core.onboarding import evidence
from cinqflow.core.onboarding import release as onboarding_release
from cinqflow.core.operations import actions as ops_actions
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.core.operations import monitor as ops_monitor
from cinqflow.core.parsers import parse
from cinqflow.core.persona import home_for
from cinqflow.core.phi import Basis, ColumnClassification, PhiDowngradeRefusedError, reclassify
from cinqflow.core.profiling import ColumnProfile, FileProfile, Finding
from cinqflow.core.proposals import Proposal, ProposalState
from cinqflow.core.registry import canonical, suspension
from cinqflow.core.registry import clone as registry_clone
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry import operations as operations_registry
from cinqflow.core.registry import search as registry_search
from cinqflow.core.registry import source as source_registry
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.registry.execution_plane import ExecutionPlaneRegister
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.registry.wave0 import wave_0_register
from cinqflow.core.rules import policy as rule_policy
from cinqflow.core.rules import preview as rule_preview
from cinqflow.core.rules import review as rule_review
from cinqflow.core.schema_spec import TypeName
from cinqflow.core.security import Action, may
from cinqflow.core.tools import CATALOGUE, ToolError
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.agents.phi_detection import PhiDetectionAgent, RecallGateFailedError
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
from cinqflow.intelligence.agents.rule_authoring import RuleAuthoringAgent
from cinqflow.intelligence.agents.schema_inference import SchemaInferenceAgent
from cinqflow.intelligence.gateway import EmbeddingFailedError
from cinqflow.intelligence.tools import ToolContext, ToolResult, all_dq_rule_entries, invoke
from cinqflow.ports.authn import AuthnPort, Principal
from cinqflow.ports.connector import (
    AlreadyDeliveredError,
    ConnectorError,
    ConnectorPort,
)
from cinqflow.ports.control_tables import (
    BatchControl,
    BatchNotFoundError,
    ControlTableError,
    ControlTablesPort,
    schema_contract_evidence,
)
from cinqflow.ports.metadata_db import (
    ActionRecordRow,
    FileProfileRecord,
    MetadataDbPort,
    ObjectNotFoundError,
)
from cinqflow.ports.storage import StoragePort
from cinqflow.workers.delivery import DeliveryOutcome, DeliveryWorker
from cinqflow.workers.drift import propose_reprocess_for_newly_mapped_columns
from cinqflow.workers.incidents import priors_for, recovery_guides
from cinqflow.workers.knowledge import KnowledgeIngestWorker
from cinqflow.workers.ops import OpsVerifier
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

#: CF-V2-E12-04's, likewise — a drafted runbook, not a contract.
FINGERPRINT_MATCH_AGENT = FINGERPRINT_MATCH_AGENT_NAME

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

#: CF-V1-W1-26 · CF-V1-E16-07. Same reasoning, same lifetime as the agent
#: factories above — built per request off whichever metadata pin serves it —
#: even though `KnowledgeIngestWorker` is a pipeline stage (Archetype B), not
#: an agent: it still writes through `LlmGateway.embed` and `VectorPort
#: .index`, both request-scoped on the real plane for the same reason a
#: proposal-writing agent is. `None` (the default every route below checks
#: for) is what a deployment with no knowledge pin fitted looks like — the
#: incident closes and the runbook publishes exactly as they would otherwise;
#: only the side effect of either becoming retrievable knowledge does not
#: happen, the same "everything else works with no model" shape `ask`
#: degrades to.
KnowledgeIngestFactory = Callable[[MetadataDbPort], KnowledgeIngestWorker]


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
    BatchPanel.INPUTS: "list_batch_inputs",
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


def _connection_profile(request: Request) -> Profile | None:
    """The profile this deployment was started with. CF-V2-E12-03 reads it."""
    return request.app.state.profile  # type: ignore[no-any-return]


ConnectionProfile = Annotated["Profile | None", Depends(_connection_profile)]


def create_app(
    *,
    authn: AuthnPort,
    metadata_db: MetadataDbPort,
    plane_register: ExecutionPlaneRegister | None = None,
    control_tables: ControlTablesPort | None = None,
    storage: StoragePort | None = None,
    connector: ConnectorPort | None = None,
    agent_factory: AgentFactory | None = None,
    schema_inference_factory: SchemaInferenceFactory | None = None,
    phi_detection_factory: PhiDetectionFactory | None = None,
    mapping_suggestion_factory: MappingSuggestionFactory | None = None,
    rule_authoring_factory: RuleAuthoringFactory | None = None,
    knowledge_ingest_factory: KnowledgeIngestFactory | None = None,
    budget: Budget | None = None,
    profile: Profile | None = None,
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
    # CF-V2-E12-03 needs to know whether an approval identifier is required,
    # and Law 3 says that difference lives in the connection profile. Held on
    # app.state beside the other wiring rather than sniffed, so a deployment
    # that forgot to pass one is DEVELOPMENT explicitly rather than by
    # accident — see `_environment_of`.
    app.state.profile = profile
    app.state.control_tables = control_tables or MemStoreControlTables()
    # No default. Unlike control tables, there is no in-memory landing zone
    # that would be honest here: a profiler pointed at an empty store would
    # answer "file not found" for every real sample.
    app.state.storage = storage
    # CF-V1-E3-05. No default either, and for a stronger reason: a deployment
    # with no connector CANNOT accept a delivery, and answering "not
    # configured" is the truth. Defaulting to something that accepted uploads
    # into memory would mean a BA uploads a file, sees it land, and finds
    # nothing there after a restart.
    app.state.connector = connector
    app.state.agent_factory = agent_factory
    # None leaves the inference route answering "not configured" rather than
    # 500ing — the deterministic profile and the manual editor still work,
    # which is exactly what a deployment with no model endpoint should offer.
    app.state.schema_inference_factory = schema_inference_factory
    app.state.phi_detection_factory = phi_detection_factory
    app.state.mapping_suggestion_factory = mapping_suggestion_factory
    app.state.rule_authoring_factory = rule_authoring_factory
    # CF-V1-W1-26. See `KnowledgeIngestFactory`'s own comment for why `None`
    # is a valid, honest deployment shape rather than a misconfiguration.
    app.state.knowledge_ingest_factory = knowledge_ingest_factory
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
        readiness = {
            obj.object_id: operations_registry.readiness_of(obj).is_ready for obj in visible
        }
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
    # OPERATIONAL, not governance. `PAUSE_FEED` rather than `APPROVE`, because
    # stopping and starting a feed is an operator's decision — and requiring
    # an approver to lift a pause would mean finding a steward at 3am to turn
    # the tap back on. (Wave 0 gated this on RUN_PIPELINE because PAUSE_FEED
    # did not exist yet; CF-V2-E12-03 gave the lever its own name so the
    # scoped matrix can grant stopping and running separately.)

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
        principal: Annotated[Principal, Depends(require(Action.PAUSE_FEED))],
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
        principal: Annotated[Principal, Depends(require(Action.PAUSE_FEED))],
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

    # ── CF-V1-E3-05 · deliveries ─────────────────────────────────────────
    #
    # THE DOOR. Plate 09 named seven connectors from Wave 0 and none of them
    # had a pin, so the platform could read a landing zone it had no way to
    # fill — and the wizard's first step said "Upload a sample file" beside no
    # way to upload one. This is that step, and it is a CONNECTOR rather than a
    # route that writes bytes: an uploaded file and an SFTP-fetched one are
    # indistinguishable by the time landing controls see them.

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/deliveries",
        response_model=DeliveryOut,
        tags=["delivery"],
        status_code=status.HTTP_201_CREATED,
    )
    async def deliver_file(
        feed_id: str,
        request: Request,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.EDIT_FEED))],
        audit: Audit,
        file: Annotated[UploadFile, File(description="The payer's file.")],
        business_date: Annotated[str, Form()],
        checksum: Annotated[str | None, Form()] = None,
        declared_row_count: Annotated[int | None, Form()] = None,
    ) -> DeliveryOut:
        """Land a file, register it, and profile it. CF-V1-E3-05.

        EDIT_FEED, not VIEW: a delivery changes what the platform holds, and a
        reader who could deliver could put content into the estate.

        200-with-a-rejection rather than a 4xx for a file landing controls
        decline. The request succeeded — the bytes arrived, the row exists, the
        file is parked where somebody can look at it. Returning 400 would say
        the caller made a mistake, when what happened is that the PAYER sent
        something the feed does not expect, and that is a finding to act on
        rather than an error to retry.
        """
        worker = _delivery_worker(request, metadata)
        if worker is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no connector pin is fitted on this deployment, so there is nowhere for a "
                "delivery to land. Fit one in the connection profile — `connector: "
                "{ adapter: upload }` at rung 0.5.",
            )
        governed = _load(metadata, feed_id)
        record = feed_registry.from_governed(governed)
        content = await file.read()
        try:
            outcome = worker.deliver(
                content,
                filename=file.filename or "",
                feed=record,
                feed_version=governed.version,
                business_date=business_date,
                delivered_by=principal.subject,
                manifest=delivery_core.Manifest(
                    checksum=checksum, declared_row_count=declared_row_count
                ),
            )
        except delivery_core.DeliveryError as refused:
            # A path for a filename, a checksum that does not match, a business
            # date that is not one. None of these is a file arriving.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None
        except AlreadyDeliveredError as refused:
            raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from None
        except ConnectorError as failure:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(failure)) from None

        audit.record(
            object_type=ObjectType.FEED,
            object_id=feed_id,
            action="deliver_file",
            actor=principal.as_actor(),
            detail=(
                f"{outcome.delivery.file.filename} -> {outcome.decision.outcome.value}"
                f" ({outcome.delivery.fingerprint})"
            ),
        )
        return _delivery_out(outcome, principal)

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/deliveries/source",
        response_model=ConnectionCheckOut,
        tags=["delivery"],
    )
    def delivery_source(
        feed_id: str,
        request: Request,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ConnectionCheckOut:
        """Can the delivery source be reached at all?

        Asked separately from listing, so an expired credential reports as an
        expired credential rather than as an empty directory.
        """
        connector = getattr(request.app.state, "connector", None)
        if connector is None:
            return ConnectionCheckOut(
                reachable=False,
                source="none",
                detail="no connector pin is fitted on this deployment",
            )
        check = connector.connect()
        return ConnectionCheckOut(
            reachable=check.reachable, source=check.source, detail=check.detail
        )

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

    # ── CF-V1-E7-03 · a tested sentence becomes executable policy ────────

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/rule-policies",
        response_model=RulePolicySetOut,
        tags=["rules"],
    )
    def read_rule_policies(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> RulePolicySetOut:
        """This feed's rule configuration, and what stands between it and an
        approval.

        The LADDER travels with it. What a consequence means is a product fact
        (`Consequence.in_plain_language`), and a copy of those sentences in a
        dropdown is a copy that drifts from what the engine does.
        """
        try:
            obj = metadata.get(ObjectType.DQ_RULE, feed_id)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has no rules yet — write some first",
            ) from None
        return _policy_set_out(metadata, obj)

    @app.put(
        f"{API_PREFIX}/feeds/{{feed_id}}/rule-policies",
        response_model=RulePolicySetOut,
        tags=["rules"],
    )
    def configure_rule_policies(
        feed_id: str,
        body: list[RulePolicyIn],
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.CONFIGURE_RULE_POLICY))],
    ) -> RulePolicySetOut:
        """Configure where each rule runs, what happens on failure, and when.

        SAVE IS PERMISSIVE AND APPROVAL IS NOT — the same split CF-V1-E3-02
        made for the feed envelope. Somebody configuring eleven rules over an
        afternoon must be able to keep nine of them; what is refused is asking
        another person to approve a set that cannot run. So the shape errors (a
        layer the engine does not reach, a window that closes before it opens)
        refuse HERE, because those are configurations that could never be
        right, and the missing-evidence and unpaged-stop gates refuse at
        APPROVE.

        Amends by writing the NEXT version, never in place: a published rule
        set stays exactly as it was approved.

        WHO MAY CONFIGURE, AND WHY IT IS NOT ONLY THE STEWARD. The story reads
        "the steward sets Silver Raw / Quarantine / threshold 1% and approves",
        and taken literally that is one person authoring a change and then
        signing it — which `GovernedObject.transition_to` refuses, for every
        object type, by design. The refusal is right and the story's sentence
        is a summary of an outcome rather than a specification of two acts.

        So this route is open to whoever authors the rule set (BA, engineer)
        AND to the steward, and the law is left exactly where it is: a steward
        who configures a threshold becomes the author of that version and
        needs a second approver. That is the correct outcome — a person who
        changes what a failing row costs should not be the only one who read
        the change — and there is a test that makes the attempt.
        """
        try:
            current = metadata.get(ObjectType.DQ_RULE, feed_id)
        except ObjectNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has no rules yet — write some first",
            ) from None

        known = {rule.rule_id for rule in rules_core.rules_from_governed(current)}
        try:
            policies = [_policy_in(entry, known) for entry in body]
        except rule_policy.PolicyError as refused:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from None

        amended = current.new_version(
            rule_policy.with_policies(current.body, policies),
            actor=principal.as_actor(),
        )
        stored = metadata.save(amended)
        audit.record(
            object_type=ObjectType.DQ_RULE,
            object_id=feed_id,
            action="configure:rule_policies",
            actor=principal.as_actor(),
            detail="; ".join(policy.describe() for policy in policies),
        )
        return _policy_set_out(metadata, stored)

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

        if proposal.agent == FINGERPRINT_MATCH_AGENT:
            # A fingerprint-match proposal produces a DRAFT RUNBOOK — a
            # drafted incident-recovery guide, not a contract. Its payload
            # carries title/steps/remedy/signatures
            # (`DraftedGuide.as_payload`), none of which is a contract column,
            # so routed out here for the same reason mapping is: a different
            # object type, keyed differently, with nothing below that applies.
            return _accept_runbook_proposal(metadata, proposal, body, principal, audit)

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

    @app.get(
        f"{API_PREFIX}/agents/{{agent}}/acceptance",
        response_model=list[WeeklyAcceptanceOut],
        tags=["intelligence"],
    )
    def agent_weekly_acceptance(
        agent: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        limit: int = 200,
    ) -> list[WeeklyAcceptanceOut]:
        """The health metric the route above's own docstring already named:
        the acceptance rate THIS agent earned, per ISO week, summed across
        every proposal a person has decided — CF-V1-E6-02's missing half.

        Reads APPROVED and APPLIED proposals, the same pair
        `Proposal.is_accepted_untouched` reads — a rejected proposal was
        never accepted, and a pending one has not been graded yet.

        `deterministic_keys` is left at `measure()`'s default (empty) here on
        purpose: which `settled_by` value counts as deterministic differs by
        agent and payload shape, which is exactly why the single-proposal
        route above computes it per agent rather than generically. Repeating
        that per-agent mapping here would put agent-specific business logic
        inside a function this route exists to keep agent-agnostic — and the
        number reported here is the OVERALL rate, which does not depend on
        the deterministic/inferred split.

        Not hard-coded to one agent beyond the path parameter: any agent name
        that has ever written a proposal works, including one this build has
        never heard of, because the aggregation reads only what `Proposal`
        and `Acceptance` already carry.
        """
        decided = [
            p
            for state in (ProposalState.APPROVED, ProposalState.APPLIED)
            for p in metadata.list_proposals(agent=agent, state=state, limit=limit)
        ]
        measured = [(p, proposals.measure(p)) for p in decided]
        return [_weekly_acceptance_out(week) for week in proposals.weekly_acceptance(measured)]

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

        # CF-V1-E7-03 adds a SIXTH, for rule sets only: no rule publishes
        # untested, and a rule that can stop production must page a human.
        # Checked here rather than inside `lifecycle.approve` for the reason
        # the mapping gate is — it needs the stored evidence, which the
        # lifecycle engine has no store to read — and BEFORE the act, so
        # nothing is persisted by a refusal.
        if object_type is ObjectType.DQ_RULE:
            try:
                _refuse_unapprovable_rules(metadata, object_id)
            except rule_policy.PolicyError as refused:
                audit.record(
                    object_type=ObjectType.DQ_RULE,
                    object_id=object_id,
                    action="refused:rule_policy",
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

    # ── CF-V1-E4-01/02/03 · the onboarding journey ───────────────────────
    #
    # Three GETs and one POST. Every one of them COMPUTES its answer from the
    # governed objects on each request — there is no onboarding row, no
    # `current_step`, and therefore nothing that can disagree with the
    # lifecycle it is describing.

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/onboarding",
        response_model=WizardOut,
        tags=["onboarding"],
    )
    def onboarding_wizard(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> WizardOut:
        """CF-V1-E4-01 — the single readiness view.

        Scope-checked like every other feed route: a BA who cannot see this
        feed gets 404 rather than a checklist that reveals which objects exist
        for it. The obstacle list is as informative as the feed itself, so
        leaking it would leak the feed.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return _wizard_out(_wizard_for(metadata, feed_id))

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/evidence",
        response_model=EvidencePackOut,
        tags=["onboarding"],
    )
    def onboarding_evidence(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> EvidencePackOut:
        """CF-V1-E4-02 — the generated pack, with its document.

        404 when no test has been run, rather than an empty pack: an empty pack
        rendered as a document is evidence that says nothing while looking like
        evidence, and somebody would attach it to an approval.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        pack = _stored_pack(metadata, feed_id)
        if pack is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"feed {feed_id!r} has no end-to-end test result yet. Run the test on your "
                "sample file; the pack is generated from it.",
            )
        return _pack_out(pack)

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/narrative",
        response_model=NarrativeOut,
        tags=["onboarding"],
    )
    def onboarding_narrative(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> NarrativeOut:
        """CF-V1-E4-03 — who drafted, tested, approved, published, and when.

        Built from the AUDIT LEDGER across every object type for this feed, not
        from the feed's own rows: the contract's approval and the mapping's
        rejection are chapters of the same story, and a narrative that read
        only feed events would omit most of it.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        # ONE call, because every object in a feed's onboarding shares the
        # feed's id: the contract, the mapping and the rule set are all stored
        # under `object_id = feed_id`. So the whole story is already one query,
        # and filtering by type would be the thing that dropped chapters.
        entries = list(metadata.read_audit(object_id=feed_id, limit=500))
        chapters = onboarding_release.narrative(entries)
        return NarrativeOut(
            feed_id=feed_id,
            chapters=[
                ChapterOut(
                    occurred_ts=chapter.occurred_ts,
                    who=chapter.who,
                    what=chapter.what,
                    object_type=chapter.object_type.value,
                    detail=chapter.detail,
                )
                for chapter in chapters
            ],
            story=onboarding_release.render_narrative(chapters),
        )

    @app.post(
        f"{API_PREFIX}/feeds/{{feed_id}}/onboarding/submit",
        response_model=GovernedOut,
        tags=["onboarding"],
    )
    def submit_onboarding(
        feed_id: str,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.SUBMIT_FOR_REVIEW))],
    ) -> GovernedOut:
        """CF-V1-E4-03 — step 5, and the two refusals that make it mean
        something.

        A red checklist and stale evidence both refuse HERE, before the
        lifecycle is touched, and both leave an audit row. The staleness check
        is the wave's exit criterion: a mapping edited after the test blocks
        submission, mechanically, because the fingerprint moved.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        try:
            feed = metadata.get(ObjectType.FEED, feed_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None

        pack = _stored_pack(metadata, feed_id)
        if pack is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "the end-to-end sample test has not been run. Both approvers read the "
                "evidence pack; without one they would be signing for a configuration "
                "nobody has watched run.",
            )
        try:
            moved, entry, _ = onboarding_release.submit_for_release(
                feed,
                view=_wizard_for(metadata, feed_id),
                pack=pack,
                configuration=_configuration_fingerprint(metadata, feed_id),
                actor=principal.as_actor(),
            )
        except onboarding_release.ReleaseError as refused:
            audit.record(
                object_type=ObjectType.FEED,
                object_id=feed_id,
                action="refused:submit_onboarding",
                actor=principal.as_actor(),
                detail=str(refused),
            )
            raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from None
        return _governed_out(metadata.record_transition(moved, entry))

    # ── CF-V1-E8-03 · dependencies and holds ─────────────────────────────────

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/dependencies",
        response_model=DependencyPictureOut,
        tags=["operations"],
    )
    def feed_dependencies(
        feed_id: str,
        metadata: Store,
        control: Control,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        business_date: str | None = None,
    ) -> DependencyPictureOut:
        """CF-V1-E8-03 — the dependency picture, so a hold is self-explanatory.

        Recomputed on every request, from the control rows as they are now.
        That is not a performance choice: a stored hold is one somebody has to
        clear, and held work nobody cleared is indistinguishable from work that
        was never scheduled.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        graph = scheduling.DependencyGraph.from_feeds(metadata.list(ObjectType.FEED))
        period = business_date or _latest_period(control, feed_id, graph)
        batches = _batches_for(control, feed_id, graph)
        drawn = scheduling.picture(
            feed_id=feed_id,
            business_date=period,
            graph=graph,
            batches=batches,
            stages={b.batch_id: list(control.get_stages(b.batch_id)) for b in batches},
            suspended=_suspended_feeds(metadata, graph),
        )
        return _picture_out(drawn)

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
        control: Control,
        profile: ConnectionProfile,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.PUBLISH))],
    ) -> GovernedOut:
        """Approved -> Published. Only now will the engine read it — execution
        gates on the READER (`executable()` refuses anything else), so nothing
        unapproved can run even if a route were mis-guarded.

        CF-V1-W1-26 · CF-V1-E16-07, RUNBOOK ONLY: a published guide's steps
        become knowledge — see `_embed_published_runbook`'s own docstring for
        why it runs AFTER the transition below rather than as a gate beside
        the MAPPING/DQ_RULE ones on `approve_object`, and for the atomic
        supersede this triggers when the guide_id has a prior published
        version.

        CF-V1-W1-28: the transition above is ALREADY DURABLE by the time the
        embed runs — `_embed_published_runbook` catches its own
        `EmbeddingFailedError` rather than letting it reach here, so a
        publish this route already committed never turns into a 500 the
        caller cannot safely retry (`lifecycle.publish` refuses a non-
        Approved object, and Published is exactly that). Its warning, if
        any, rides on `result` rather than being swallowed.

        CF-V1-E6-04 (W1-34, F5 RE-SCOPED), MAPPING ONLY: the same AFTER-THE-
        TRANSITION posture, for a different side effect — see
        `_reprocess_candidates_after_mapping_publish`'s own docstring for why
        a mapping publish that newly covers a once-ungoverned column
        surfaces a reprocess CANDIDATE rather than ever running one itself.
        """
        result = _governance_act(
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
        if object_type is ObjectType.RUNBOOK:
            warning = _embed_published_runbook(
                metadata, object_id, principal, app.state.knowledge_ingest_factory, audit
            )
            if warning is not None:
                result = result.model_copy(update={"warnings": [warning]})
        if object_type is ObjectType.MAPPING:
            warning = _reprocess_candidates_after_mapping_publish(
                metadata, control, profile, object_id, principal, audit
            )
            if warning is not None:
                result = result.model_copy(update={"warnings": [*result.warnings, warning]})
        return result

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

    # ── CF-V2-E12-01 · the operations home board ─────────────────────────

    @app.get(f"{API_PREFIX}/operations/board", response_model=BoardOut, tags=["operations"])
    def operations_board(
        control: Control,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        business_date: str | None = None,
        domain: str | None = None,
    ) -> BoardOut:
        """CF-V2-E12-01 — the morning question, answered from the control tables.

        SCOPED, like every other feed surface: an operator sees the feeds their
        scopes cover and no others. The counters are recomputed over what they
        can see rather than filtered from a global total, because a header that
        says twelve while the body lists two is a header people quote in
        stand-up.

        An unreachable plane returns the last numbers WITH a staleness banner
        rather than zeros — zeros read as "nothing expected today", which on a
        morning when the plane is down is the most dangerous thing this screen
        could say.
        """
        day = business_date or datetime.now(UTC).date().isoformat()
        try:
            board = _board_for(metadata, control, principal, day)
        except ControlTableError:
            board = ops_board.stale_board(_EMPTY_BOARD(day), now=datetime.now(UTC))
        if domain:
            board = board.in_domain(domain)
        return _board_out(board)

    # ── CF-V2-E12-02 · the batch and stage monitor ───────────────────────

    @app.get(
        f"{API_PREFIX}/operations/batches/{{batch_id}}",
        response_model=BatchViewOut,
        tags=["operations"],
    )
    def batch_monitor(
        batch_id: str,
        control: Control,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> BatchViewOut:
        """CF-V2-E12-02 — where exactly is this batch, and what happened.

        ONE response carrying stages, errors and the cascade, because "two
        clicks" means the second click has to find everything already here.
        """
        return _batch_view_out(_batch_view(control, batch_id, principal))

    # ── CF-V2-E12-03 · the governed action surface ───────────────────────

    @app.get(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/actions",
        response_model=ActionSurfaceOut,
        tags=["operations"],
    )
    def action_surface(
        batch_id: str,
        control: Control,
        metadata: Store,
        profile: ConnectionProfile,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ActionSurfaceOut:
        """CF-V2-E12-03 — exactly the actions the surface would permit.

        A console that draws a button and then refuses it teaches people that
        refusals are noise, so this and the POST read the same matrix — BOTH
        matrices: the allowed-state table and PERMISSION_FOR. A batch in the
        right state still offers nothing to a caller whose role cannot act on
        it, which is how a Read-Only analyst gets the same screen with no
        buttons rather than buttons that bounce.
        """
        batch = _batch_or_404(control, batch_id, principal)
        environment = _environment_of(profile)
        paused = metadata.current_suspension(batch.feed_id).is_active_at(datetime.now(UTC))
        available = tuple(
            action
            for action in ops_actions.offered(batch_state=batch.state, feed_paused=paused)
            if may(principal, ops_actions.PERMISSION_FOR[action], feed_id=batch.feed_id)
        )
        return ActionSurfaceOut(
            target=batch_id,
            offered=[action.value for action in available],
            previews=[
                _preview_out(
                    ops_actions.preview(
                        ops_actions.ActionRequest(
                            action=action, target=batch_id, actor=principal.as_actor()
                        ),
                        environment=environment,
                    )
                )
                for action in available
            ],
            environment=environment.value,
        )

    @app.post(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/actions",
        response_model=ActionRecordOut,
        tags=["operations"],
    )
    def act_on_batch(
        batch_id: str,
        body: ActionRequestIn,
        control: Control,
        metadata: Store,
        audit: Audit,
        profile: ConnectionProfile,
        # ACKNOWLEDGE is the DOOR, not the gate: the weakest operations
        # permission, so nobody who cannot even acknowledge reaches the
        # handler. The action actually requested is gated inside on
        # PERMISSION_FOR — a retry still costs RETRY_BATCH, a reprocess
        # REPROCESS — because one route serves the whole enum and the
        # permission differs per action.
        principal: Annotated[Principal, Depends(require(Action.ACKNOWLEDGE))],
    ) -> ActionRecordOut:
        """CF-V2-E12-03 — see, decide, act, all audited, in one place.

        The record returned is REQUESTED, never complete: 'retry requested' is
        not 'retry succeeded', and the outcome is written by whatever observed
        it. A screen that showed a tick here would be the console this story
        exists to replace.

        Every refusal leaves a row — the guardrail says the system refuses,
        notifies a human and RECORDS the refusal, and a surface that logged its
        successes and swallowed its refusals is one where "I clicked retry and
        nothing happened" is unanswerable.
        """
        batch = _batch_or_404(control, batch_id, principal)
        try:
            action = ops_actions.OpsAction(body.action)
        except ValueError:
            offered_now = ", ".join(a.value for a in ops_actions.OpsAction)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{body.action!r} is not an operations action. This surface offers "
                f"{offered_now} — and nothing free-form.",
            ) from None

        # The gate the door could not check: this SPECIFIC action's permission.
        # Refused with a row, like every other refusal on this surface — an
        # operator reading the audit must find "I clicked reprocess and it
        # bounced" as easily as "the reprocess ran".
        decision = may(principal, ops_actions.PERMISSION_FOR[action], feed_id=batch.feed_id)
        if not decision:
            audit.record(
                object_type=ObjectType.FEED,
                object_id=batch.feed_id,
                action=f"refused:{action.value}",
                actor=principal.as_actor(),
                detail=decision.reason,
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)

        suspension = metadata.current_suspension(batch.feed_id)
        now = datetime.now(UTC)
        request = ops_actions.ActionRequest(
            action=action,
            target=batch_id,
            actor=principal.as_actor(),
            reason=body.reason,
            approval_identifier=body.approval_identifier,
            assignee=body.assignee,
            note=body.note,
            resume_from=Layer(body.resume_from) if body.resume_from else None,
        )
        try:
            ops_actions.authorize(
                request,
                environment=_environment_of(profile),
                batch_state=batch.state,
                feed_paused=suspension.is_active_at(now),
                paused_reason=suspension.reason,
                pause_citation=CitationId(kind=CitationKind.FEED, subject=batch.feed_id),
                now=now,
            )
        except ops_actions.RefusedError as refused:
            # A refusal is a ROW in the ledger, not only an audit line — six
            # weeks later, "why did nobody retry the batch that was failing
            # all night" is answered from the same history as the retries.
            metadata.record_action_event(
                ActionRecordRow(
                    record_id=str(uuid4()),
                    feed_id=batch.feed_id,
                    record=ops_actions.refused_action(request, refused.refusal, now=now),
                )
            )
            audit.record(
                object_type=ObjectType.FEED,
                object_id=batch.feed_id,
                action=f"refused:{refused.refusal.action.value}",
                actor=principal.as_actor(),
                detail=refused.refusal.explain(),
            )
            raise HTTPException(status.HTTP_409_CONFLICT, refused.refusal.explain()) from None

        record = ops_actions.request_action(request, now=now)
        row = metadata.record_action_event(
            ActionRecordRow(record_id=str(uuid4()), feed_id=batch.feed_id, record=record)
        )
        audit.record(
            object_type=ObjectType.FEED,
            object_id=batch.feed_id,
            action=f"operations:{action.value}",
            actor=principal.as_actor(),
            detail=record.explain(),
        )
        if not action.mutates_production:
            # Bookkeeping's only effect IS the row, so verification has
            # nothing to wait for — an acknowledgement left REQUESTED would
            # teach operators that the surface never finishes anything.
            # Pipeline actions stay strictly two-phase: the engine runs, and
            # the verifier re-reads the control tables afterwards.
            row = OpsVerifier(control=control, metadata=metadata).verify_record(
                row.record_id, now=now
            )
        return _action_record_out(row.record, record_id=row.record_id)

    @app.get(
        f"{API_PREFIX}/operations/actions/{{record_id}}",
        response_model=ActionRecordOut,
        tags=["operations"],
    )
    def action_record(
        record_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ActionRecordOut:
        """CF-V2-E12-03 — the round trip the story is about.

        The POST answered REQUESTED; this is where the screen polls until a
        worker has re-read the control tables and the phase became VERIFIED or
        FAILED. A record still REQUESTED here is rendered as exactly that —
        never as a tick.
        """
        try:
            row = metadata.get_action_record(record_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        if not principal.scopes.covers_feed(row.feed_id):
            # The usual shape: out of scope must be indistinguishable from
            # not found, or the denial leaks which records exist.
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return _action_record_out(row.record, record_id=row.record_id)

    @app.get(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/action-history",
        response_model=list[ActionRecordOut],
        tags=["operations"],
    )
    def action_history(
        batch_id: str,
        control: Control,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> list[ActionRecordOut]:
        """Everything anyone did — or was refused — on this batch, newest
        first. The monitor stays action-free; history is not an affordance."""
        batch = _batch_or_404(control, batch_id, principal)
        return [
            _action_record_out(row.record, record_id=row.record_id)
            for row in metadata.list_action_records(batch_id=batch.batch_id)
        ]

    # ── CF-V2-E12-04 · fingerprinting and guide matching ─────────────────

    @app.get(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/incident",
        response_model=IncidentOut,
        tags=["operations"],
    )
    def batch_incident(
        batch_id: str,
        control: Control,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> IncidentOut:
        """CF-V2-E12-04 — the incident, computed.

        NO MODEL IS CALLED on this route. The signature is a deterministic
        normalisation of the errors the engine already logged and the match is
        an exact lookup, so the answer is the same on every machine and the
        precision gate measures the normalisation rather than a model's mood.
        """
        batch = _batch_or_404(control, batch_id, principal)
        return _incident_out(_incident_for(control, metadata, batch))

    @app.get(
        f"{API_PREFIX}/operations/incidents",
        response_model=list[IncidentRowOut],
        tags=["operations"],
    )
    def list_incidents(
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
        state: str | None = None,
    ) -> list[IncidentRowOut]:
        """CF-V2-E12-04 — open incidents, newest first, from the ledger.

        The LEDGER's rows, not recomputed evidence: a list that re-derived
        every incident's cascade would touch the error log once per row, and
        the operator scanning it needs states and assignments, not bundles.
        The per-batch route serves the full evidence.
        """
        wanted: fingerprinting.IncidentState | None = None
        if state is not None:
            try:
                wanted = fingerprinting.IncidentState(state)
            except ValueError:
                states = ", ".join(s.value for s in fingerprinting.IncidentState)
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{state!r} is not an incident state. This surface offers {states}.",
                ) from None
        rows = metadata.list_incident_events(state=wanted)
        return [
            IncidentRowOut(
                incident_id=event.incident_id,
                batch_id=event.batch_id,
                feed_id=event.feed_id,
                state=event.state.value,
                signature=event.signature,
                assigned_to=event.assigned_to,
                opened_ts=event.opened_ts,
                resolved_ts=event.resolved_ts,
            )
            for event in rows
            if principal.scopes.covers_feed(event.feed_id)
        ]

    @app.get(
        f"{API_PREFIX}/operations/runbooks/{{guide_id}}",
        response_model=RunbookOut,
        tags=["operations"],
    )
    def get_runbook(
        guide_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
        version: int | None = None,
    ) -> RunbookOut:
        """A `runbook:<id>` citation's destination (CF-V1-W1-25) —
        `RecoveryGuide.citation` and `GuideMatch.citations` point here, and so
        does every per-step citation `workers.knowledge.chunk_runbook`
        produces for the knowledge plane."""
        try:
            obj = metadata.get(ObjectType.RUNBOOK, guide_id, version)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        return _runbook_out(obj)

    def _move_incident(
        incident_id: str,
        control: ControlTablesPort,
        metadata: MetadataDbPort,
        principal: Principal,
        audit: AuditLog,
        move: Callable[[fingerprinting.Incident], fingerprinting.Incident],
        action_name: str,
    ) -> IncidentOut:
        """One transition: evidence recomputed, decision applied, event
        appended, audited. The state machine itself refuses illegal moves —
        this function only carries its answer to the wire."""
        try:
            held = metadata.get_incident_event(incident_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        if not principal.scopes.covers_feed(held.feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        batch = control.get_batch(held.batch_id)
        incident = _incident_for(control, metadata, batch)
        try:
            moved = move(incident)
        except fingerprinting.IncidentTransitionError as refused:
            raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from None
        metadata.record_incident_event(
            fingerprinting.event_for(
                moved, actor_subject=principal.subject, occurred_ts=datetime.now(UTC)
            )
        )
        audit.record(
            object_type=ObjectType.FEED,
            object_id=held.feed_id,
            action=f"incident:{action_name}",
            actor=principal.as_actor(),
            detail=f"{incident_id} -> {moved.state.value}",
        )
        # CF-V1-W1-26 · CF-V1-E16-07 — the hook: a closed incident's narrative
        # becomes knowledge. Checked EXPLICITLY on `.embeddable` rather than
        # assumed from `action_name == "close"` — `Incident.embeddable`
        # already IS the gate (`core.operations.fingerprint`'s own docstring;
        # `.narrative()` refuses anything not CLOSED), and asking it here
        # instead of re-deriving "was this a close" keeps exactly one place
        # that knows what "embeddable" means. Acknowledge and resolve reach
        # this same line and never trip it — only `close()` can produce a
        # CLOSED incident. `close()` is a ONE-WAY transition (the state
        # machine refuses OPEN/ACKNOWLEDGED/RESOLVED -> CLOSED a second time,
        # raising `IncidentTransitionError` above before `moved` even exists)
        # so a second close attempt never reaches this line at all — the
        # pipeline fires AT MOST once per incident, structurally, not by a
        # guard added here.
        #
        # CF-V1-W1-28: `record_incident_event`/`audit.record` above have
        # ALREADY made the CLOSED transition durable — `Incident.close()` is
        # one-way, so there is no legal retry of THIS call if the embed below
        # throws uncaught. `EmbeddingFailedError` (the one type
        # `LlmGateway.embed` raises for a budget refusal or transport
        # failure) is therefore caught here, audited the same way
        # `_governance_act` audits a caught refusal, and turned into a
        # warning on the response instead of an unhandled 500 that would
        # lie about whether the close took. Anything else is a real bug and
        # still surfaces.
        warning: str | None = None
        if moved.embeddable:
            factory: KnowledgeIngestFactory | None = app.state.knowledge_ingest_factory
            if factory is not None:
                try:
                    factory(metadata).ingest_incident(
                        moved,
                        run_id=f"knowledge-incident-{incident_id}-{uuid4().hex[:8]}",
                        caller=principal.as_actor(),
                    )
                except EmbeddingFailedError as failure:
                    warning = f"embed-on-close failed: {failure}"
                    audit.record(
                        object_type=ObjectType.FEED,
                        object_id=held.feed_id,
                        action="embed_failed:incident_close",
                        actor=principal.as_actor(),
                        detail=f"{incident_id}: {warning}",
                    )
        return _incident_out(moved, warnings=(warning,) if warning is not None else ())

    @app.post(
        f"{API_PREFIX}/operations/incidents/{{incident_id}}/acknowledge",
        response_model=IncidentOut,
        tags=["operations"],
    )
    def acknowledge_incident(
        incident_id: str,
        body: AcknowledgeIncidentIn,
        control: Control,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.ACKNOWLEDGE))],
    ) -> IncidentOut:
        """ "I have seen this." Names the caller; optionally assigns."""
        return _move_incident(
            incident_id,
            control,
            metadata,
            principal,
            audit,
            lambda incident: incident.acknowledge(
                by=principal.subject, assigned_to=body.assigned_to
            ),
            "acknowledge",
        )

    @app.post(
        f"{API_PREFIX}/operations/incidents/{{incident_id}}/resolve",
        response_model=IncidentOut,
        tags=["operations"],
    )
    def resolve_incident(
        incident_id: str,
        body: ResolveIncidentIn,
        control: Control,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.ACKNOWLEDGE))],
    ) -> IncidentOut:
        """The resolution text is what CF-V2-E16-07 embeds on close — the core
        refuses an empty one, because it becomes the narrative the next
        matching incident retrieves."""
        return _move_incident(
            incident_id,
            control,
            metadata,
            principal,
            audit,
            lambda incident: incident.resolve(resolution=body.resolution, at=datetime.now(UTC)),
            "resolve",
        )

    @app.post(
        f"{API_PREFIX}/operations/incidents/{{incident_id}}/close",
        response_model=IncidentOut,
        tags=["operations"],
    )
    def close_incident(
        incident_id: str,
        control: Control,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.ACKNOWLEDGE))],
    ) -> IncidentOut:
        """Closing is what makes the narrative embeddable — "only a closed one
        teaches." The machine refuses to close anything unresolved."""
        return _move_incident(
            incident_id,
            control,
            metadata,
            principal,
            audit,
            lambda incident: incident.close(),
            "close",
        )

    # ── CF-V2-E12-05 · the reliability score ──────────────────────────────

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/reliability",
        response_model=ReliabilityOut,
        tags=["operations"],
    )
    def feed_reliability(
        feed_id: str,
        control: Control,
        metadata: Store,
        profile: ConnectionProfile,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ReliabilityOut:
        """CF-V2-E12-05 — 'can I trust this feed?', decomposable on click.

        Every observation is a control-plane query, never a curated number —
        the same discipline as the board's counters. A signal the plane
        cannot measure yet (identity, until Wave 3) is UNMEASURED, not zero:
        it lowers the score's confidence, never the score.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        _load(metadata, feed_id)  # 404 before an empty score for a feed that is not real
        score = reliability.score_for(
            feed_id=feed_id,
            as_of=datetime.now(UTC).date(),
            observations=_reliability_observations(control, feed_id),
            weights=_weights_of(profile),
            bands=_bands_of(profile),
        )
        return ReliabilityOut(
            feed_id=score.feed_id,
            as_of=score.as_of,
            overall=score.overall,
            band=score.band.value,
            confidence=score.confidence,
            components=[
                ReliabilityComponentOut(
                    signal=component.signal.value,
                    value=component.value,
                    weight=component.weight,
                    evidence=component.evidence,
                    sample_size=component.sample_size,
                    measured=component.measured,
                )
                for component in score.components
            ],
            citation=str(score.citation),
        )

    # ── CF-V2-E13-03 · variance investigation, approval and waiver ────────

    @app.post(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/variances",
        response_model=VarianceOut,
        tags=["operations"],
    )
    def open_variance(
        batch_id: str,
        body: VarianceIn,
        control: Control,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.ACKNOWLEDGE))],
    ) -> VarianceOut:
        """A discrepancy, written down by whoever found it. The platform
        computes the delta and the criticality — the finder declares the
        numbers, never the verdict."""
        batch = _batch_or_404(control, batch_id, principal)
        try:
            kind = variances_core.VarianceKind(body.kind)
        except ValueError:
            kinds = ", ".join(k.value for k in variances_core.VarianceKind)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{body.kind!r} is not a variance kind. This surface offers {kinds}.",
            ) from None
        now = datetime.now(UTC)
        opened = variances_core.Variance(
            variance_id=str(uuid4()),
            batch_id=batch_id,
            feed_id=batch.feed_id,
            kind=kind,
            expected=body.expected,
            actual=body.actual,
            tolerance=body.tolerance,
            opened_by=principal.subject,
            opened_ts=now,
        )
        metadata.record_variance_event(opened, actor_subject=principal.subject, occurred_ts=now)
        audit.record(
            object_type=ObjectType.FEED,
            object_id=batch.feed_id,
            action="variance:opened",
            actor=principal.as_actor(),
            detail=f"{opened.variance_id} · {kind.value} · delta {opened.delta}",
        )
        return _variance_out(opened)

    def _decide_variance(
        variance_id: str,
        metadata: MetadataDbPort,
        principal: Principal,
        audit: AuditLog,
        decide: Callable[[variances_core.Variance], variances_core.Variance],
        action_name: str,
    ) -> VarianceOut:
        """One decision: the core's own refusals carried to the wire, the
        result a new ledger row, everything audited — including the refusal,
        because 'no state change without the required approver' is only
        provable from rows."""
        try:
            held = metadata.get_variance(variance_id)
        except ObjectNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
        if not principal.scopes.covers_feed(held.feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        try:
            decided = decide(held)
        except variances_core.VarianceError as refused:
            audit.record(
                object_type=ObjectType.FEED,
                object_id=held.feed_id,
                action=f"refused:variance_{action_name}",
                actor=principal.as_actor(),
                detail=str(refused),
            )
            raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from None
        now = datetime.now(UTC)
        metadata.record_variance_event(decided, actor_subject=principal.subject, occurred_ts=now)
        audit.record(
            object_type=ObjectType.FEED,
            object_id=held.feed_id,
            action=f"variance:{action_name}",
            actor=principal.as_actor(),
            detail=f"{variance_id} -> {decided.outcome.value}",
        )
        return _variance_out(decided)

    @app.post(
        f"{API_PREFIX}/variances/{{variance_id}}/waive",
        response_model=VarianceOut,
        tags=["operations"],
    )
    def waive_variance(
        variance_id: str,
        body: WaiveVarianceIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.WAIVE_VARIANCE))],
    ) -> VarianceOut:
        """Steward only, and the type does the rest: a critical variance
        cannot be waived AT ALL, a blank (or 'n/a') reason is refused, the
        author cannot waive their own finding, and the expiry is capped."""
        granted = datetime.now(UTC).date()
        waiver = variances_core.Waiver(
            waived_by=principal.subject,
            reason=body.reason,
            granted_on=granted,
            expires_on=body.expires_on or variances_core.default_expiry(granted),
        )
        return _decide_variance(
            variance_id,
            metadata,
            principal,
            audit,
            lambda variance: variance.waive(waiver),
            "waive",
        )

    @app.post(
        f"{API_PREFIX}/variances/{{variance_id}}/correct",
        response_model=VarianceOut,
        tags=["operations"],
    )
    def correct_variance(
        variance_id: str,
        body: CorrectVarianceIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.ACKNOWLEDGE))],
    ) -> VarianceOut:
        """The $48,000 path: fixed via reprocess, closed with a note that says
        what was corrected — publication proceeds because the variance is
        CORRECTED, not because anyone forgave it."""
        return _decide_variance(
            variance_id,
            metadata,
            principal,
            audit,
            lambda variance: variance.correct(by=principal.subject, note=body.note),
            "correct",
        )

    @app.post(
        f"{API_PREFIX}/variances/{{variance_id}}/approve-with-explanation",
        response_model=VarianceOut,
        tags=["operations"],
    )
    def approve_variance(
        variance_id: str,
        body: ExplainVarianceIn,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.WAIVE_VARIANCE))],
    ) -> VarianceOut:
        """An explanation is not a fix — the core refuses it for a critical
        variance, and this route just carries the sentence."""
        return _decide_variance(
            variance_id,
            metadata,
            principal,
            audit,
            lambda variance: variance.approve_with_explanation(
                by=principal.subject, explanation=body.explanation
            ),
            "approve_with_explanation",
        )

    # ── CF-V2-E13-04 · certification derives; there is no button ──────────

    @app.get(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/certification",
        response_model=CertificationOut,
        tags=["operations"],
    )
    def batch_certification_view(
        batch_id: str,
        control: Control,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> CertificationOut:
        """Computed ON THIS READ from retained history — recon rows, rule
        results, drift, the SLA cycle and the variance ledger. There is no
        route that sets a certification status; the absence is the
        acceptance criterion, and a test holds it over the OpenAPI document.
        """
        batch = _batch_or_404(control, batch_id, principal)
        verdict = batch_certification.certify(
            batch_id=batch_id,
            feed_id=batch.feed_id,
            checks=_certification_checks(control, batch),
            variances=metadata.list_variances(batch_id=batch_id),
            now=datetime.now(UTC),
        )
        return CertificationOut(
            batch_id=verdict.batch_id,
            feed_id=verdict.feed_id,
            verdict=verdict.verdict.value,
            publishable=verdict.publishable,
            derived_ts=verdict.derived_ts,
            checks=[
                CertificationCheckOut(
                    kind=check.kind.value,
                    passed=check.passed,
                    completed=check.completed,
                    evidence=check.evidence,
                )
                for check in verdict.checks
            ],
            variances=[_variance_out(v) for v in verdict.variances],
        )

    @app.get(
        f"{API_PREFIX}/operations/batches/{{batch_id}}/certification/export",
        tags=["operations"],
    )
    def export_certification(
        batch_id: str,
        control: Control,
        metadata: Store,
        audit: Audit,
        principal: Annotated[Principal, Depends(require(Action.CERTIFY_EXPORT))],
    ) -> PlainTextResponse:
        """The evidence document, plain text on purpose: byte-comparable, so
        'identical to the day it was certified' is a string equality. The
        verdict derives from retained history, so re-deriving it from that
        history returns the same bytes — evidence never degrades."""
        batch = _batch_or_404(control, batch_id, principal)
        verdict = batch_certification.certify(
            batch_id=batch_id,
            feed_id=batch.feed_id,
            checks=_certification_checks(control, batch),
            variances=metadata.list_variances(batch_id=batch_id),
            now=datetime.now(UTC),
        )
        audit.record(
            object_type=ObjectType.FEED,
            object_id=batch.feed_id,
            action="certification:exported",
            actor=principal.as_actor(),
            detail=f"{batch_id} · {verdict.verdict.value}",
        )
        return PlainTextResponse(batch_certification.evidence_document(verdict))

    # ── CF-V1-E7-04 · the technical review queue ─────────────────────────

    @app.get(
        f"{API_PREFIX}/feeds/{{feed_id}}/rule-reviews",
        response_model=ReviewQueueOut,
        tags=["rules"],
    )
    def rule_reviews(
        feed_id: str,
        metadata: Store,
        principal: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> ReviewQueueOut:
        """CF-V1-E7-04 — uncertain logic, routed, with the measurable attached.

        `unrouted` must always be empty, and it is served rather than left to
        CI because a control only CI can see is a control nobody maintains.
        """
        if not principal.scopes.covers_feed(feed_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        candidates = _review_candidates(metadata, feed_id)
        reviews = rule_review.route(
            candidates, feed_id=feed_id, floor=rule_authoring_graph.CONFIDENCE_FLOOR
        )
        return ReviewQueueOut(
            reviews=[_review_out(review) for review in reviews],
            open_count=len(rule_review.awaiting_review(reviews)),
            unrouted=list(
                rule_review.unrouted(
                    candidates, reviews, floor=rule_authoring_graph.CONFIDENCE_FLOOR
                )
            ),
        )

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


def _delivery_worker(request: Request, metadata: MetadataDbPort) -> DeliveryWorker | None:
    """The worker, or None when this deployment cannot accept a delivery.

    None rather than a stub, for the same reason `_profiler` returns None: a
    stub that accepted uploads into nowhere would let a BA upload a file, watch
    it land, and find nothing there afterwards.
    """
    connector = getattr(request.app.state, "connector", None)
    storage = request.app.state.storage
    if connector is None or storage is None:
        return None
    return DeliveryWorker(
        connector=connector,
        storage=storage,
        control=request.app.state.control_tables,
        metadata=metadata,
    )


def _delivery_out(outcome: DeliveryOutcome, principal: Principal) -> DeliveryOut:
    """The receipt, in the words of the person who pressed Upload."""
    delivery = outcome.delivery
    citation = parse_citation(outcome.citation)
    return DeliveryOut(
        outcome=outcome.decision.outcome.value,
        headline=outcome.headline(),
        reason=outcome.decision.reason,
        check_name=outcome.decision.check_name,
        feed_id=delivery.feed_id,
        filename=delivery.file.filename,
        key=delivery.file.key,
        landed_key=outcome.landed_key or delivery.file.key,
        size_bytes=delivery.file.size_bytes,
        fingerprint=delivery.fingerprint,
        business_date=delivery.business_date,
        delivered_by=outcome.requested_by or delivery.delivered_by,
        source=outcome.source,
        citation_id=outcome.citation,
        route=citation.route,
        profile_id=outcome.profile_id,
        profile=None,
        next_step=_next_step(outcome),
    )


def _next_step(outcome: DeliveryOutcome) -> str:
    """What to do now, in the BA's words rather than the platform's.

    Every branch here is a landing outcome, and each one has a genuinely
    different next action — which is why this is not one sentence with the
    outcome interpolated into it.
    """
    if outcome.accepted:
        return (
            "Approve the schema next. The columns were profiled by computation, so the "
            "types and counts on that screen are measured rather than guessed."
        )
    outcome_value = outcome.decision.outcome
    if outcome_value is LandingOutcome.SKIPPED:
        return (
            "This exact content has already been processed, so nothing was loaded twice. "
            "Ask the batch it arrived in what happened to it."
        )
    if outcome_value is LandingOutcome.UNEXPECTED:
        return (
            "The file is parked, not lost. Either the payer sent something new, or this "
            "feed's file-name pattern needs to admit it — check the pattern before "
            "changing it, because widening one to fit a stray file is how the next "
            "wrong file gets in."
        )
    return (
        "The file is in the rejected folder with its reason. Send the reason to the "
        "payer rather than re-uploading — the same bytes will be rejected again."
    )


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
        return operations_registry.FeedOperations.from_body(body.operations.model_dump()).as_body()
    except (operations_registry.OperationsValidationError, ValueError) as invalid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(invalid)) from None


def _readiness_out(obj: GovernedObject) -> ReadinessOut:
    ready = operations_registry.readiness_of(obj)
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
            **operations_registry.FeedOperations.from_body(body.get("operations")).as_body()
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
                operations_registry.Owner(
                    role=operations_registry.OwnerRole(o.role),
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
    """This feed's own published mapping, if it has one. The DECISION.

    W1-36: NOT `metadata.get(ObjectType.MAPPING, feed_id)` — `version=None`
    means the highest version NUMBER, regardless of lifecycle state, so a
    DRAFT/PENDING_REVIEW/APPROVED version sitting on top of an
    already-PUBLISHED one (exactly what starting to edit a mapping, or
    accepting a mapping-suggestion proposal, produces) would shadow the
    published version and make this look like a feed with no mapping at
    all. Walk the full history and take the highest version that is
    ACTUALLY executable — the same pattern `_refuse_silent_row_loss` below
    uses to find "the currently PUBLISHED one".
    """
    published = [obj for obj in metadata.history(ObjectType.MAPPING, feed_id) if obj.is_executable]
    if not published:
        return ()
    return (mapping_core.from_governed(max(published, key=lambda o: o.version)),)


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


def _runbook_out(obj: GovernedObject) -> RunbookOut:
    """The same fields `_runbook_body_from` writes, read back — `get_runbook`
    (CF-V1-W1-25) is the one place this codebase renders a runbook on its
    own, so this is the one place that reads its body back out."""
    body = obj.body
    return RunbookOut(
        guide_id=obj.object_id,
        title=str(body.get("title") or obj.object_id),
        steps=[str(step) for step in body.get("steps", ())],
        signatures=[str(sig) for sig in body.get("signatures", ())],
        remedy=str(body["remedy"]) if body.get("remedy") else None,
        is_transient=bool(body.get("is_transient", False)),
        version=obj.version,
        lifecycle_state=obj.lifecycle_state.value,
        status=obj.lifecycle_state.status_word,
    )


def _runbook_record(payload: dict[str, Any]) -> dict[str, Any]:
    """The one record a fingerprint-match payload carries.

    `DraftedGuide.as_payload` always writes exactly one — this agent drafts
    one guide per novel incident, never a batch of them — so there is no
    "which record" ambiguity the way a contract's or a mapping's many-column
    payload has.
    """
    records = tuple(r for r in payload.get("records", ()) if isinstance(r, dict))
    return records[0] if records else {}


def _runbook_body_from(record: dict[str, Any]) -> dict[str, Any]:
    """A governed RUNBOOK body — the same fields `workers.incidents
    .recovery_guides` reads back, and `core.operations.fingerprint
    .RecoveryGuide` carries. `guide_id` travels in the body too, alongside
    being the object's own id, so the body is a complete, self-describing
    record of what was published rather than one that depends on its own
    key to be read.

    `feed_id` (CF-V1-W1-26) is the one field with no `RecoveryGuide` counterpart
    — it never reaches the matcher, only `workers.incidents.recovery_guides`'s
    own staleness check and `core.knowledge.chunk_runbook`'s scope tags read
    it. `DraftedGuide.as_payload` is the only producer of this record today,
    and it always names the originating incident's feed; a hand-authored
    runbook with no incident behind it simply carries `None` here, which
    reads as "linked to no feed" — never stale, never scoped."""
    return {
        "guide_id": record.get("guide_id"),
        "title": record.get("title"),
        "signatures": list(record.get("signatures", ())),
        "steps": list(record.get("steps", ())),
        "remedy": record.get("remedy"),
        "is_transient": bool(record.get("is_transient", False)),
        "feed_id": record.get("feed_id"),
    }


def _next_runbook_version(metadata: MetadataDbPort, guide_id: str) -> int:
    history = metadata.history(ObjectType.RUNBOOK, guide_id)
    return max((obj.version for obj in history), default=0) + 1


def _embed_published_runbook(
    metadata: MetadataDbPort,
    object_id: str,
    principal: Principal,
    factory: KnowledgeIngestFactory | None,
    audit: AuditLog,
) -> str | None:
    """CF-V1-W1-26 · CF-V1-E16-07 — a runbook publish is what makes its steps
    knowledge (ADR-0007: "Only Published governed objects embed").

    Module-level, not nested beside `_governance_act`, for the same reason
    `_runbook_body_from` and its neighbours already are: this is called from
    `publish_object` AFTER `_governance_act` has already persisted the
    transition, never as a gate that could block it — an embedding failure
    is not a reason to refuse a publish the lifecycle engine already approved
    routing for, the same distinction W1-24 drew between a `complete()`
    failure (the feature degrades to a manual path) and an `embed()` one
    (nothing downstream is waiting on it synchronously). `factory=None`
    degrades silently: the publish already succeeded, and the only thing
    that does not happen is this side effect — exactly what `ask` does when
    no LLM pin is fitted, except there is no caller here to hand a refusal to.

    ATOMIC SUPERSEDE. `_prior_published_version` finds the guide_id's most
    recent OTHER Published version, if one exists, and hands it to
    `KnowledgeIngestWorker.ingest_runbook` as `supersedes` — which is what
    turns this into ONE `VectorPort.supersede` call rather than a plain
    `index()` that would leave the prior version's chunks stranded under
    their own (different) content-addressed ids forever.

    CF-V1-W1-28: the paragraph above said the publish "already succeeded"
    while the code let a bare `EmbeddingFailedError` — `LlmGateway.embed`'s
    own type for a budget refusal or a transport failure, see its docstring —
    reach ASGI and turn into an unhandled 500. By then `metadata.get` above
    already reads back `lifecycle_state="published"`, so the 500 was a LIE
    about what happened and a TRAP for whoever retried it: `lifecycle.publish`
    only accepts an Approved object, and this one no longer is. Caught here,
    audited (`action="embed_failed:publish"`, mirroring `_governance_act`'s
    own `refused:{act}` audit-on-catch pattern), and returned as a warning
    string for `publish_object` to carry on the response — never hidden, and
    never allowed to make a durable success look like a failure at the door.
    Anything OTHER than `EmbeddingFailedError` is a real bug in the embed path
    and is left to propagate; only the documented degrade mode degrades.
    """
    if factory is None:
        return None
    published = metadata.get(ObjectType.RUNBOOK, object_id)
    prior = _prior_published_version(metadata, object_id, before=published.version)
    try:
        factory(metadata).ingest_runbook(
            published,
            run_id=f"knowledge-runbook-{object_id}-v{published.version}-{uuid4().hex[:8]}",
            caller=principal.as_actor(),
            supersedes=prior,
        )
    except EmbeddingFailedError as failure:
        detail = f"embed-on-publish failed: {failure}"
        audit.record(
            object_type=ObjectType.RUNBOOK,
            object_id=object_id,
            version=published.version,
            action="embed_failed:publish",
            actor=principal.as_actor(),
            detail=detail,
        )
        return detail
    return None


def _prior_published_version(
    metadata: MetadataDbPort, object_id: str, *, before: int
) -> GovernedObject | None:
    """The most recent PUBLISHED version older than the one just published —
    the one whose chunks an atomic supersede must retire. `None` on a guide's
    first publish, which is exactly what `KnowledgeIngestWorker.ingest_runbook
    `'s own `supersedes=None` default means: nothing to retire.

    A version's `lifecycle_state` never changes once transitioned away from
    Draft except by another transition on THAT SAME version
    (`record_transition`'s "state and approver columns only, never the
    body") — so an older version this finds as PUBLISHED really is still
    sitting there Published, never silently downgraded by a later version's
    own publish. That is exactly the state `chunk_runbook` needs to accept it.
    """
    candidates = [
        obj
        for obj in metadata.history(ObjectType.RUNBOOK, object_id)
        if obj.version < before and obj.lifecycle_state is LifecycleState.PUBLISHED
    ]
    return max(candidates, key=lambda obj: obj.version, default=None)


def _reprocess_candidates_after_mapping_publish(
    metadata: MetadataDbPort,
    control: ControlTablesPort,
    profile: Profile | None,
    object_id: str,
    principal: Principal,
    audit: AuditLog,
) -> str | None:
    """CF-V1-E6-04 (W1-34, F5 RE-SCOPED) — a mapping publish that newly covers
    a column some past batch's own `control.schema_drift` rows already call
    `unmapped_column` surfaces a reprocess CANDIDATE for that batch.

    `workers.drift.propose_reprocess_for_newly_mapped_columns` does the real
    work — reads which batch(es) actually saw the finding, builds a real
    `reprocess_batch` `RecoveryPlan` per batch, and asks the SAME `authorize`
    gate a human's own retry answers to, as the SYSTEM actor it is. See its
    own docstring for why that gate — not a check written here — is what
    keeps this from ever auto-running: the candidate lands REFUSED
    (`RefusalReason.NOT_A_HUMAN`) in the SAME `ops.action_record` ledger
    CF-V2-E12-03 built, and a steward's own `POST .../actions` is still the
    only door that runs one.

    RUNS AFTER `_governance_act` HAS ALREADY COMMITTED THE PUBLISH — the same
    posture `_embed_published_runbook` takes toward its own side effect: a
    `ControlTableError` here (the one anticipated failure — a control-tables
    read going wrong) degrades to a warning on the response rather than
    turning an already-durable publish into a 500 the caller cannot safely
    retry. Anything else is a real bug in this trigger and is left to
    propagate, the same rule `_embed_published_runbook` states for its own
    `EmbeddingFailedError`.
    """
    published = metadata.get(ObjectType.MAPPING, object_id)
    try:
        created = propose_reprocess_for_newly_mapped_columns(
            metadata,
            control,
            feed_id=object_id,
            mapping=mapping_core.from_governed(published),
            environment=_environment_of(profile),
        )
    except ControlTableError as failure:
        detail = f"reprocess candidates not surfaced: {failure}"
        audit.record(
            object_type=ObjectType.MAPPING,
            object_id=object_id,
            version=published.version,
            action="reprocess_candidates_failed:publish",
            actor=principal.as_actor(),
            detail=detail,
        )
        return detail
    if not created:
        return None
    batches = ", ".join(sorted({row.record.target for row in created}))
    return (
        f"reprocess candidate(s) surfaced for batch(es) {batches} — a steward's own "
        "operations action confirms one, this publish never runs one"
    )


def _accept_runbook_proposal(
    metadata: MetadataDbPort,
    proposal: Proposal,
    body: ApproveProposalIn,
    principal: Principal,
    audit: AuditLog,
) -> ProposalOut:
    """Accept a drafted recovery guide, producing a DRAFT RUNBOOK.

    A runbook proposal has no per-column corrections to apply: it is one
    title/steps/remedy record, not a set of records a reviewer redirects
    field by field, and `ApproveProposalIn.columns`/`.mappings` describe
    contract fields and mapping lines respectively — neither shape fits a
    guide. Rather than force one of those correction models onto a payload it
    was never built to describe, this door offers exactly what the payload
    supports: accept the draft as written, with a comment. A reviewer who
    wants the guide to say something different edits it after acceptance,
    the same as they would a hand-typed draft.

    Keyed by `guide_id`, not by feed — a guide answers one FINGERPRINT, and
    the same fingerprint can recur on any feed, so tying it to the feed the
    first incident happened to land on would make a genuinely known failure
    on a second feed look novel again.
    """
    record = _runbook_record(proposal.payload)
    guide_id = str(record.get("guide_id") or "")
    try:
        decided = proposals.approve(
            proposal,
            approver=principal.as_actor(),
            comment=body.comment,
            corrections=(),
        )
        applied, draft = proposals.apply(
            decided,
            object_type=ObjectType.RUNBOOK,
            object_id=guide_id,
            body=_runbook_body_from(record),
            version=_next_runbook_version(metadata, guide_id),
        )
    except proposals.ProposalError as refused:
        audit.record(
            object_type=ObjectType.RUNBOOK,
            object_id=guide_id or proposal.proposal_id,
            action="refused:approve_proposal",
            actor=principal.as_actor(),
            detail=str(refused),
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(refused)) from None

    metadata.save(draft)
    stored = metadata.record_proposal(applied)
    audit.record(
        object_type=ObjectType.RUNBOOK,
        object_id=draft.object_id,
        version=draft.version,
        action="applied_proposal",
        actor=principal.as_actor(),
        detail=f"{proposal.proposal_id} · accepted as drafted",
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
    is_fingerprint_agent = proposal.agent == FINGERPRINT_MATCH_AGENT
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
            if is_phi_agent or is_mapping_agent or is_rule_agent or is_fingerprint_agent
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


def _weekly_acceptance_out(weekly: proposals.WeeklyAcceptance) -> WeeklyAcceptanceOut:
    return WeeklyAcceptanceOut(
        agent=weekly.agent,
        week=weekly.week_label,
        proposal_count=weekly.proposal_count,
        acceptance=_acceptance_out(weekly.acceptance),
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


# ── CF-V1-E4-01/02/03 · onboarding helpers ───────────────────────────────────
#
# Every one of these ASSEMBLES what `core.onboarding` needs and then asks it.
# None of them decides anything: "is this step complete?" is answered in core,
# once, by the same function the wizard's own tests exercise — which is what
# stops the screen showing green while submit returns 409.

#: Where CF-V1-E4-02's pack is kept between the test run and the approval.
#:
#: On the FEED's governed body rather than in a table of its own. The pack is
#: not a governed object — nothing approves it, and it has no lifecycle — but
#: it must version WITH the feed, because "which evidence did this version
#: publish on?" is the question the whole staleness gate exists to answer. A
#: separate table would answer it with a join and a timestamp; a body key
#: answers it by construction, since a new feed version carries the body it was
#: created with.
EVIDENCE_KEY = "evidence_pack"


def _wizard_for(metadata: MetadataDbPort, feed_id: str) -> onboarding.Wizard:
    """Gather every governed object for one feed and compute the checklist."""
    objects = tuple(
        obj
        for object_type in (
            ObjectType.FEED,
            ObjectType.CONTRACT,
            ObjectType.MAPPING,
            ObjectType.DQ_RULE,
        )
        for obj in metadata.history(object_type, feed_id)
    )
    feed = next(
        (o for o in objects if o.object_type is ObjectType.FEED),
        None,
    )
    pack = _stored_pack(metadata, feed_id)
    return onboarding.wizard(
        onboarding.OnboardingInputs(
            feed_id=feed_id,
            objects=objects,
            sample_profile_ids=tuple(
                record.profile_id for record in metadata.list_profiles(feed_id=feed_id)
            ),
            model=_canonical_of(metadata),
            evidence_fingerprint=pack.fingerprint if pack else None,
            configuration_fingerprint=_configuration_fingerprint(metadata, feed_id),
            operations=(
                operations_registry.FeedOperations.from_body(feed.body.get("operations"))
                if feed
                else None
            ),
        )
    )


def _configuration_objects(metadata: MetadataDbPort, feed_id: str) -> tuple[GovernedObject, ...]:
    """The three objects a run consumes, at their LATEST versions.

    Latest rather than published: a BA tests her draft, and fingerprinting the
    published set would compare the evidence against a configuration she is not
    submitting.
    """
    found: list[GovernedObject] = []
    for object_type in (ObjectType.CONTRACT, ObjectType.MAPPING, ObjectType.DQ_RULE):
        history = list(metadata.history(object_type, feed_id))
        if history:
            found.append(max(history, key=lambda o: o.version))
    return tuple(found)


def _configuration_fingerprint(metadata: MetadataDbPort, feed_id: str) -> str:
    return evidence.configuration_fingerprint(_configuration_objects(metadata, feed_id))


def _stored_pack(metadata: MetadataDbPort, feed_id: str) -> evidence.EvidencePack | None:
    """Read the pack off the feed's body, or None when no test has been run."""
    try:
        feed = metadata.get(ObjectType.FEED, feed_id)
    except ObjectNotFoundError:
        return None
    raw = feed.body.get(EVIDENCE_KEY)
    return _pack_from_body(raw) if isinstance(raw, dict) and raw else None


def _pack_from_body(raw: dict[str, Any]) -> evidence.EvidencePack:
    """Rebuild the pack from JSONB.

    Only the fields the API and the gates read. The MARKDOWN is re-rendered
    from them rather than stored: a stored document and the numbers beside it
    are two copies of one fact, and the day they disagree is the day somebody
    approves the document.
    """
    failure = raw.get("failure")
    return evidence.EvidencePack(
        feed_id=str(raw.get("feed_id", "")),
        fingerprint=str(raw.get("fingerprint", "")),
        produced_ts=_ts(raw.get("produced_ts")),
        rows_in=int(raw.get("rows_in", 0)),
        rows_loaded=int(raw.get("rows_loaded", 0)),
        rows_quarantined=int(raw.get("rows_quarantined", 0)),
        drops=tuple(
            evidence.DropExplanation(
                rule_id=str(d.get("rule_id", "")),
                reason=str(d.get("reason", "")),
                record_count=int(d.get("record_count", 0)),
                columns=tuple(d.get("columns", ())),
            )
            for d in raw.get("drops", ())
        ),
        examples=tuple(
            evidence.Example(
                row_number=int(e.get("row_number", 0)),
                before=dict(e.get("before", {})),
                after=dict(e.get("after", {})),
            )
            for e in raw.get("examples", ())
        ),
        rules=tuple(
            evidence.RuleOutcome(
                rule_id=str(r.get("rule_id", "")),
                name=str(r.get("name", "")),
                tested=int(r.get("tested", 0)),
                flagged=int(r.get("flagged", 0)),
                quarantined=bool(r.get("quarantined", False)),
            )
            for r in raw.get("rules", ())
        ),
        gaps=tuple(
            evidence.Gap(
                key=str(g.get("key", "")),
                what=str(g.get("what", "")),
                why_it_is_acceptable=str(g.get("why_it_is_acceptable", "")),
            )
            for g in raw.get("gaps", ())
        ),
        failure=(
            evidence.Failure(
                step=str(failure.get("step", "")),
                explanation=str(failure.get("explanation", "")),
                citation=parse_citation(str(failure["citation"]))
                if failure.get("citation")
                else None,
            )
            if isinstance(failure, dict) and failure
            else None
        ),
        balanced=bool(raw.get("balanced", True)),
        sample_filename=str(raw.get("sample_filename", "")),
    )


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now(UTC)


def _obstacle_out(obstacle: onboarding.Obstacle) -> ObstacleOut:
    return ObstacleOut(
        key=obstacle.key,
        what=obstacle.what,
        why_it_matters=obstacle.why_it_matters,
        how_to_fix=obstacle.how_to_fix,
        citation=str(obstacle.citation) if obstacle.citation else None,
        route=obstacle.route,
        blocking=obstacle.blocking,
    )


def _wizard_out(view: onboarding.Wizard) -> WizardOut:
    return WizardOut(
        feed_id=view.feed_id,
        steps=[
            WizardStepOut(
                step=status.step.value,
                ordinal=status.step.ordinal,
                label=status.step.label,
                state=status.state.value,
                status=status.state.status_word,
                is_complete=status.is_complete,
                version=status.version,
                citation=str(status.citation) if status.citation else None,
                obstacles=[_obstacle_out(o) for o in status.obstacles],
            )
            for status in view.steps
        ],
        resume_at=view.resume_at.value,
        is_publishable=view.is_publishable,
        outstanding=[_obstacle_out(o) for o in view.outstanding],
        gaps=[_obstacle_out(o) for o in view.gaps],
        operations_outstanding=[item.question for item in view.operations.outstanding],
        explanation=view.explain(),
    )


def _pack_out(pack: evidence.EvidencePack) -> EvidencePackOut:
    return EvidencePackOut(
        feed_id=pack.feed_id,
        fingerprint=pack.fingerprint,
        produced_ts=pack.produced_ts,
        rows_in=pack.rows_in,
        rows_loaded=pack.rows_loaded,
        rows_quarantined=pack.rows_quarantined,
        balanced=pack.balanced,
        accounts_for_every_row=pack.accounts_for_every_row,
        partial=pack.partial,
        summary=pack.summary(),
        sample_filename=pack.sample_filename,
        drops=[
            DropExplanationOut(
                rule_id=drop.rule_id,
                reason=drop.reason,
                record_count=drop.record_count,
                columns=list(drop.columns),
            )
            for drop in pack.drops
        ],
        rules=[
            RuleOutcomeOut(
                rule_id=rule.rule_id,
                name=rule.name,
                tested=rule.tested,
                flagged=rule.flagged,
                hit_rate=rule.hit_rate,
                quarantined=rule.quarantined,
            )
            for rule in pack.rules
        ],
        examples=[
            ExampleOut(row_number=e.row_number, before=e.before, after=e.after)
            for e in pack.examples
        ],
        gaps=[
            GapOut(
                key=gap.key,
                what=gap.what,
                why_it_is_acceptable=gap.why_it_is_acceptable,
                citation=str(gap.citation) if gap.citation else None,
            )
            for gap in pack.gaps
        ],
        failure=(
            FailureOut(
                step=pack.failure.step,
                explanation=pack.failure.explanation,
                citation=str(pack.failure.citation) if pack.failure.citation else None,
                route=pack.failure.route,
            )
            if pack.failure
            else None
        ),
        markdown=pack.render_markdown(),
    )


# ── CF-V1-E8-03 · dependency helpers ─────────────────────────────────────────
def _related_feeds(feed_id: str, graph: scheduling.DependencyGraph) -> tuple[str, ...]:
    """The subject and everything it transitively waits for.

    Upstreams only. The blast radius is computed from the GRAPH and needs no
    batches, so pulling downstream feeds' control rows would be reading rows to
    display nothing.
    """
    reached = [feed_id]
    frontier = [feed_id]
    while frontier:
        current = frontier.pop(0)
        for parent in graph.upstream_of(current):
            if parent not in reached:
                reached.append(parent)
                frontier.append(parent)
    return tuple(reached)


def _batches_for(
    control: ControlTablesPort, feed_id: str, graph: scheduling.DependencyGraph
) -> list[BatchControl]:
    return [
        batch for name in _related_feeds(feed_id, graph) for batch in control.list_batches(name)
    ]


def _latest_period(
    control: ControlTablesPort, feed_id: str, graph: scheduling.DependencyGraph
) -> str:
    """The period to explain when the caller did not name one.

    The newest business date ANY feed in the picture has a batch for — not the
    subject's own newest, because the interesting case is precisely the one
    where the subject has no batch BECAUSE it is held, and defaulting to its
    own history would explain last month instead of the hold.
    """
    periods = sorted({batch.business_date for batch in _batches_for(control, feed_id, graph)})
    return periods[-1] if periods else datetime.now(UTC).date().isoformat()


def _suspended_feeds(metadata: MetadataDbPort, graph: scheduling.DependencyGraph) -> frozenset[str]:
    """Which feeds in the graph are paused right now. CF-V1-E3-04's axis,
    folded from the ledger by the pin that owns it."""
    now = datetime.now(UTC)
    return frozenset(
        feed_id
        for feed_id in graph.order()
        if metadata.current_suspension(feed_id).is_active_at(now)
    )


def _decision_out(decision: scheduling.ReleaseDecision) -> ReleaseDecisionOut:
    return ReleaseDecisionOut(
        feed_id=decision.feed_id,
        business_date=decision.business_date,
        may_run=decision.may_run,
        batch_state=decision.batch_state.value,
        status=decision.status_word,
        blockers=[
            BlockerOut(
                reason=blocker.reason.value,
                feed_id=blocker.feed_id,
                business_date=blocker.business_date,
                batch_id=blocker.batch_id,
                state=blocker.state.value if blocker.state else None,
                layer=blocker.layer.value if blocker.layer else None,
                chain=list(blocker.chain),
                is_root=blocker.is_root,
                explanation=blocker.explain(),
            )
            for blocker in decision.blockers
        ],
        chain=list(decision.chain),
        needs_notification=decision.needs_notification,
        explanation=decision.explain(),
    )


def _picture_out(drawn: scheduling.DependencyPicture) -> DependencyPictureOut:
    return DependencyPictureOut(
        subject=drawn.subject,
        business_date=drawn.business_date,
        nodes=[
            PictureNodeOut(
                feed_id=node.feed_id,
                business_date=node.business_date,
                batch_id=node.batch_id,
                state=node.state.value if node.state else None,
                status=node.status_word,
                is_subject=node.is_subject,
                is_root_cause=node.is_root_cause,
            )
            for node in drawn.nodes
        ],
        edges=[[parent, child] for parent, child in drawn.edges],
        blast_radius=list(drawn.blast_radius),
        decision=_decision_out(drawn.decision) if drawn.decision else None,
        is_self_explanatory=drawn.is_self_explanatory,
    )


# ── CF-V1-E7-03 · rule policy helpers ────────────────────────────────────────
def _policy_in(entry: RulePolicyIn, known: set[str]) -> rule_policy.RulePolicy:
    """One posted policy, validated against the vocabulary AND the rule set.

    A policy for a rule this feed does not have is refused rather than stored:
    it would sit in the body forever, never match a spec, and read on the feed
    profile as protection that does not exist.
    """
    if entry.rule_id not in known:
        raise rule_policy.PolicyError(
            f"{entry.rule_id!r} is not a rule of this feed. A policy for a rule that does "
            "not exist would read on the feed profile as protection that is not there."
        )
    try:
        layer = Layer(entry.layer)
    except ValueError:
        runnable = ", ".join(layer.value for layer in rule_policy.RUNNABLE_LAYERS)
        raise rule_policy.PolicyError(
            f"{entry.layer!r} is not a layer. Rules run at {runnable}."
        ) from None
    try:
        consequence = rule_policy.Consequence(entry.on_failure)
    except ValueError:
        ladder = " -> ".join(c.value for c in rule_policy.Consequence)
        raise rule_policy.PolicyError(
            f"{entry.on_failure!r} is not one of the six outcomes. The ladder is {ladder}."
        ) from None

    return rule_policy.RulePolicy(
        rule_id=entry.rule_id,
        layer=layer,
        on_failure=consequence,
        threshold_percent=entry.threshold_percent,
        execution_order=entry.execution_order,
        effective_from=entry.effective_from,
        effective_to=entry.effective_to,
        alert_recipient=entry.alert_recipient,
        owner=entry.owner,
        rationale=entry.rationale,
    )


def _policy_out(policy: rule_policy.RulePolicy) -> RulePolicyOut:
    return RulePolicyOut(
        rule_id=policy.rule_id,
        layer=policy.layer.value,
        on_failure=policy.on_failure.value,
        threshold_percent=policy.threshold_percent,
        execution_order=policy.execution_order,
        effective_from=policy.effective_from,
        effective_to=policy.effective_to,
        alert_recipient=policy.alert_recipient,
        owner=policy.owner,
        rationale=policy.rationale,
        describes=policy.describe(),
    )


def _rule_evidence(obj: GovernedObject) -> dict[str, Any] | None:
    """CF-V1-E7-02's saved preview, from the rule set's own body.

    Stored beside the rules it is evidence about, for the same reason the
    onboarding pack lives on the feed: it must version WITH them, so "which
    evidence did this rule set approve on?" needs no join and no timestamp.
    """
    stored = obj.body.get(RULE_EVIDENCE_KEY)
    return stored if isinstance(stored, dict) and stored else None


#: Where CF-V1-E7-02's saved preview lives on the DQ_RULE body.
RULE_EVIDENCE_KEY = "test_evidence"


def _policy_set_out(metadata: MetadataDbPort, obj: GovernedObject) -> RulePolicySetOut:
    policies = rule_policy.policies_from_body(obj.body)
    findings = rule_policy.findings_for(policies, evidence=_rule_evidence(obj))
    history = list(metadata.history(ObjectType.DQ_RULE, obj.object_id))
    previous = [o for o in history if o.version == obj.version - 1]
    softened = (
        rule_policy.refuse_silent_softening(
            rule_policy.policies_from_body(previous[0].body), policies
        )
        if previous
        else ()
    )
    return RulePolicySetOut(
        feed_id=obj.object_id,
        version=obj.version,
        lifecycle_state=obj.lifecycle_state.value,
        policies=[_policy_out(policy) for policy in policies],
        findings=[
            PolicyFindingOut(
                key=finding.key,
                rule_id=finding.rule_id,
                what=finding.what,
                why_it_matters=finding.why_it_matters,
                how_to_fix=finding.how_to_fix,
                blocks=finding.blocks,
            )
            for finding in findings
        ],
        is_approvable=bool(policies) and not rule_policy.blocking(findings),
        softened=list(softened),
        ladder=[
            ConsequenceOut(
                value=rung.value,
                rank=rung.rank,
                changes_the_batch=rung.changes_the_batch,
                needs_a_person=rung.needs_a_person,
                in_plain_language=rung.in_plain_language,
            )
            for rung in rule_policy.Consequence
        ],
    )


def _refuse_unapprovable_rules(metadata: MetadataDbPort, feed_id: str) -> None:
    """CF-V1-E7-03's two gates, at the approval seam.

    A rule set with NO policies configured is allowed through. That is
    deliberate and it is not a hole: a spec with no policy does not run —
    `rule_policy.runnable_at` will not return it — so approving the sentences
    before configuring where they run is a legitimate order of work, and
    refusing it would force a steward to configure eleven rules before anyone
    had agreed the eleven sentences were right.
    """
    try:
        obj = metadata.get(ObjectType.DQ_RULE, feed_id)
    except ObjectNotFoundError:  # pragma: no cover - the act itself would 404
        return
    policies = rule_policy.policies_from_body(obj.body)
    if not policies:
        return
    rule_policy.refuse_unapprovable(policies, evidence=_rule_evidence(obj))


# ── CF-V2-E12-01/02/03/04 · operations helpers ───────────────────────────────
#
# Every one of these ASSEMBLES what `core.operations` needs and then asks it.
# None decides anything: "is this feed missing?", "is this batch stuck?", "may
# this action run?" are answered in core, once, by the same functions the unit
# tests exercise — which is what stops a screen and a gate disagreeing.

#: Where the environment comes from. Law 3 puts all environment difference in
#: the connection profile, so this reads the profile's rung rather than
#: sniffing anything: rungs 3 and 4 are the client's own tenancy, where real
#: member data can exist and a change record is required.
_PRODUCTION_RUNGS = frozenset({3, 4})


def _environment_of(profile: Profile | None) -> ops_actions.Environment:
    """Development or production, read from the connection profile.

    NO PROFILE MEANS DEVELOPMENT, and that is safe in exactly one direction:
    a missing profile can only ever REMOVE the approval-identifier
    requirement, never add one — so the failure mode is a development
    deployment asking for a change record it did not need, not a production
    one skipping it.

    Which is why this is a wired dependency and not a `getattr` on whatever
    object happened to be in scope: a lookup that silently returned None in
    production would disable the requirement precisely where it exists.
    `cinqflow serve` passes the profile it was started with, and
    `test_production_is_read_from_the_profile_not_guessed` fails if this stops
    reading it.
    """
    rung = getattr(profile, "rung", None)
    if rung in _PRODUCTION_RUNGS:
        return ops_actions.Environment.PRODUCTION
    return ops_actions.Environment.DEVELOPMENT


def _EMPTY_BOARD(day: str) -> ops_board.Board:  # noqa: N802 - a named constant-like builder
    """The board shown when nothing could be read.

    Its counters still name their source, because a zero with no provenance is
    exactly the figure this screen exists to abolish.
    """
    return ops_board.build_board(
        business_date=day, expectations=[], batches=[], now=datetime.now(UTC)
    )


def _visible_feeds(metadata: MetadataDbPort, principal: Principal) -> tuple[GovernedObject, ...]:
    return tuple(
        obj for obj in metadata.list(ObjectType.FEED) if principal.scopes.covers_feed(obj.object_id)
    )


def _board_for(
    metadata: MetadataDbPort,
    control: ControlTablesPort,
    principal: Principal,
    business_date: str,
) -> ops_board.Board:
    """Assemble today's expectations and ask core what the morning looks like."""
    now = datetime.now(UTC)
    feeds = _visible_feeds(metadata, principal)
    expectations: list[ops_board.Expectation] = []
    batches: list[BatchControl] = []

    for feed in feeds:
        envelope = operations_registry.FeedOperations.from_body(feed.body.get("operations"))
        service_level = envelope.service_level
        if service_level is None:
            # A feed with no declared SLA is not expected at a time, so it
            # cannot be late. Counting it as expected would put a row on the
            # board that can never leave it.
            continue
        expectations.append(
            ops_board.Expectation.from_service_level(
                feed_id=feed.object_id,
                domain=str(feed.body.get("domain") or "—"),
                business_date=business_date,
                service_level=service_level,
                due_ts=_due_ts(business_date, service_level, now),
            )
        )
        batches.extend(control.list_batches(feed.object_id, 20))

    return ops_board.build_board(
        business_date=business_date,
        expectations=expectations,
        batches=batches,
        graph=scheduling.DependencyGraph.from_feeds(feeds),
        now=now,
    )


def _due_ts(business_date: str, service_level: Any, now: datetime) -> datetime:
    """When today's delivery was due, in UTC.

    The offset is resolved from the IANA name with the standard library's own
    timezone database — which is exactly where `core.ops_board.resolve_due`
    says this belongs, because `core/` performs no I/O and the offset for a
    given date is a fact about that date rather than about the string.
    """
    from zoneinfo import ZoneInfo

    try:
        day = datetime.fromisoformat(business_date).replace(tzinfo=UTC)
    except ValueError:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        zone = ZoneInfo(service_level.timezone)
        offset = day.replace(tzinfo=zone).utcoffset() or timedelta(0)
    except Exception:
        offset = timedelta(0)
    return ops_board.resolve_due(
        business_day=day,
        expected_by_local_time=service_level.expected_by_local_time,
        offset=offset,
    )


def _counter_out(counter: ops_board.Counter) -> CounterOut:
    return CounterOut(label=counter.label, value=counter.value, derived_from=counter.derived_from)


def _board_out(board: ops_board.Board) -> BoardOut:
    expected, received, missing, at_risk = board.totals
    return BoardOut(
        business_date=board.business_date,
        expected=expected,
        received=received,
        missing=missing,
        at_risk=at_risk,
        counters=[_counter_out(counter) for counter in board.counters],
        rows=[
            ArrivalRowOut(
                feed_id=row.feed_id,
                domain=row.domain,
                business_date=row.business_date,
                condition=row.condition.value,
                status=row.status,
                due_ts=row.due_ts,
                minutes_late=row.minutes_late,
                batch_id=row.batch_id,
                headline=row.headline(),
                citation=str(row.citation),
                route=row.citation.route,
            )
            for row in board.rows
        ],
        attention=[
            AttentionItemOut(
                feed_id=item.feed_id,
                domain=item.domain,
                headline=item.headline,
                impact=item.impact,
                why=item.why,
                status=item.status,
                citation=str(item.citation),
                route=item.route,
            )
            for item in board.attention
        ],
        domains=list(ops_board.domains(board)),
        freshness=FreshnessOut(
            as_of=board.freshness.as_of,
            may_be_stale=board.freshness.may_be_stale,
            banner=board.freshness.banner(datetime.now(UTC)),
        ),
        explanation=board.explain(),
    )


def _batch_or_404(control: ControlTablesPort, batch_id: str, principal: Principal) -> BatchControl:
    try:
        batch = control.get_batch(batch_id)
    except (BatchNotFoundError, ControlTableError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    if not principal.scopes.covers_feed(batch.feed_id):
        # Deliberately the same shape as "not found". An out-of-scope batch
        # must not be distinguishable from one that does not exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return batch


def _batch_view(
    control: ControlTablesPort, batch_id: str, principal: Principal
) -> ops_monitor.BatchView:
    batch = _batch_or_404(control, batch_id, principal)
    return ops_monitor.build_batch_view(
        batch,
        stages=list(control.get_stages(batch_id)),
        errors=list(control.list_errors(batch_id=batch_id)),
        history=list(control.list_batches(batch.feed_id, 50)),
        now=datetime.now(UTC),
    )


def _error_out(error: ops_monitor.ErrorView) -> BatchErrorOut:
    return BatchErrorOut(
        error_id_hash=error.error_id_hash,
        stage=error.stage.value,
        category=error.category.value,
        message=error.message,
        occurred_ts=error.occurred_ts,
        rule_id=error.rule_id,
        is_consequence=error.is_consequence,
        caused_by=error.caused_by,
        citation=str(error.citation),
        route=error.route,
    )


def _batch_view_out(view: ops_monitor.BatchView) -> BatchViewOut:
    return BatchViewOut(
        batch_id=view.batch_id,
        feed_id=view.feed_id,
        business_date=view.business_date,
        state=view.state.value,
        status=view.status,
        started_ts=view.started_ts,
        completed_ts=view.completed_ts,
        failed_at=view.failed_at.value if view.failed_at else None,
        rows_written=view.rows_written,
        balances=view.balances,
        sla=view.sla.value,
        typical_duration_seconds=(
            int(view.typical_duration.total_seconds()) if view.typical_duration else None
        ),
        stages=[
            StageViewOut(
                layer=stage.layer.value,
                state=stage.state.value,
                status=stage.status,
                started_ts=stage.started_ts,
                completed_ts=stage.completed_ts,
                duration_seconds=(int(stage.duration.total_seconds()) if stage.duration else None),
                records_in=stage.records_in,
                records_out=stage.records_out,
                quarantined=stage.quarantined,
                attributed_drops=stage.attributed_drops,
                unexplained=stage.unexplained,
                balances=stage.balances,
                flow=stage.flow(),
            )
            for stage in view.stages
        ],
        errors=[_error_out(error) for error in view.cascade.all],
        cascade_summary=view.cascade.explain(),
        flow=list(view.flow()),
        explanation=view.explain(),
        citation=str(view.citation),
        route=view.citation.route,
    )


def _preview_out(shown: ops_actions.Preview) -> PreviewOut:
    return PreviewOut(
        action=shown.action.value,
        target=shown.target,
        what_will_happen=shown.what_will_happen,
        scope_records=shown.scope_records,
        scope_stages=[layer.value for layer in shown.scope_stages],
        estimated_minutes=shown.estimated_minutes,
        requires_approval_identifier=shown.requires_approval_identifier,
        explanation=shown.explain(),
    )


def _action_record_out(record: ops_actions.ActionRecord, *, record_id: str = "") -> ActionRecordOut:
    return ActionRecordOut(
        record_id=record_id,
        action=record.action.value,
        target=record.target,
        actor_subject=record.actor.subject,
        requested_ts=record.requested_ts,
        phase=record.phase.value,
        status=record.status,
        is_complete=record.is_complete,
        reason=record.reason,
        approval_identifier=record.approval_identifier,
        verified_ts=record.verified_ts,
        outcome=record.outcome,
        explanation=record.explain(),
    )


def _action_refusal_out(refusal: ops_actions.Refusal) -> ActionRefusalOut:
    return ActionRefusalOut(
        action=refusal.action.value,
        reason=refusal.reason.value,
        detail=refusal.detail,
        target=refusal.target,
        notifies=refusal.notifies,
        route=refusal.route,
    )


def _weights_of(profile: Profile | None) -> reliability.Weights:
    """The profile's weighting, or core's defaults — never a third place."""
    if profile is None or not profile.reliability:
        return reliability.Weights()
    weights = profile.reliability.get("weights", {})
    return reliability.Weights(**{str(k): float(v) for k, v in weights.items()})


def _bands_of(profile: Profile | None) -> reliability.Bands:
    if profile is None or not profile.reliability:
        return reliability.Bands()
    bands = profile.reliability.get("bands", {})
    return reliability.Bands(**{str(k): float(v) for k, v in bands.items()})


def _reliability_observations(
    control: ControlTablesPort, feed_id: str
) -> dict[reliability.Signal, tuple[float, str, int]]:
    """Six signals, each a control-plane query — 'no hand-maintained figures
    anywhere'. A signal with nothing to read stays OUT of the map, so it
    becomes an unmeasured component rather than a zero."""
    observations: dict[reliability.Signal, tuple[float, str, int]] = {}

    rule_history = control.rule_result_history(feed_id, limit=200)
    evaluated = sum(r.evaluated for r in rule_history)
    failed = sum(r.failed for r in rule_history)
    if evaluated > 0:
        observations[reliability.Signal.DQ] = (
            round(100.0 * (evaluated - failed) / evaluated, 1),
            f"{failed:,} of {evaluated:,} evaluations failed across {len(rule_history)} rule runs",
            len(rule_history),
        )

    cycles = control.sla_history(feed_id, days=90)
    if cycles:
        on_time = sum(1 for c in cycles if c.sla_status == "On-Time")
        observations[reliability.Signal.SLA] = (
            round(100.0 * on_time / len(cycles), 1),
            f"{on_time} of {len(cycles)} cycles on time over 90 days",
            len(cycles),
        )

    batches = control.list_batches(feed_id, 30)
    recons = [r for b in batches for r in control.get_reconciliation(b.batch_id)]
    if recons:
        balanced = sum(1 for r in recons if r.balances)
        observations[reliability.Signal.RECONCILIATION] = (
            round(100.0 * balanced / len(recons), 1),
            f"{balanced} of {len(recons)} stage reconciliations balanced",
            len(recons),
        )

    terminal = [b for b in batches if b.state in {BatchState.COMPLETED, BatchState.FAILED}]
    if terminal:
        completed = sum(1 for b in terminal if b.state is BatchState.COMPLETED)
        observations[reliability.Signal.PIPELINE] = (
            round(100.0 * completed / len(terminal), 1),
            f"{completed} of {len(terminal)} recent batches completed",
            len(terminal),
        )

    if batches:
        drifted = sum(
            1 for b in batches if any(d.blocked_batch for d in control.get_schema_drift(b.batch_id))
        )
        observations[reliability.Signal.SCHEMA] = (
            round(100.0 * (len(batches) - drifted) / len(batches), 1),
            f"{drifted} of {len(batches)} recent batches blocked by schema drift",
            len(batches),
        )

    # IDENTITY is deliberately absent until Wave 3 — unmeasured, never zero.
    return observations


def _variance_out(variance: variances_core.Variance) -> VarianceOut:
    waiver = variance.waiver
    return VarianceOut(
        variance_id=variance.variance_id,
        batch_id=variance.batch_id,
        feed_id=variance.feed_id,
        kind=variance.kind.value,
        expected=variance.expected,
        actual=variance.actual,
        delta=variance.delta,
        tolerance=variance.tolerance,
        critical=variance.critical,
        outcome=variance.outcome.value,
        opened_by=variance.opened_by,
        opened_ts=variance.opened_ts,
        explanation=variance.explanation,
        waived_by=waiver.waived_by if waiver else "",
        waiver_reason=waiver.reason if waiver else "",
        waiver_expires_on=waiver.expires_on if waiver else None,
        citation=str(variance.citation),
    )


def _certification_checks(
    control: ControlTablesPort, batch: BatchControl
) -> tuple[batch_certification.Check, ...]:
    """Every check is a control-plane read — the same discipline as the
    board's counters and the reliability score. A check with nothing recorded
    yet is INCOMPLETE (the verdict goes PENDING), never assumed passed; the
    SLA check is OMITTED when no cycle was owed, because rule 1 only demands
    the mandatory set and a window nobody promised cannot be missed."""
    checks: list[batch_certification.Check] = []
    recons = control.get_reconciliation(batch.batch_id)
    balanced = all(r.balances for r in recons)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.BALANCE,
            passed=bool(recons) and balanced,
            completed=bool(recons),
            evidence=(
                f"rows_in == rows_out + quarantined + attributed_drops on {len(recons)} stage(s)"
                if recons
                else "no reconciliation recorded yet"
            ),
            citation=CitationId(kind=CitationKind.RECON, subject=batch.batch_id),
        )
    )
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.RECONCILIATION,
            passed=bool(recons) and all(r.unexplained == 0 for r in recons),
            completed=bool(recons),
            evidence=(
                f"{sum(r.unexplained for r in recons)} unexplained rows across "
                f"{len(recons)} stage(s)"
                if recons
                else "no reconciliation recorded yet"
            ),
            citation=CitationId(kind=CitationKind.RECON, subject=batch.batch_id),
        )
    )
    total_drops = sum(entry.record_count for recon in recons for entry in recon.drop_ledger)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.DROP_LEDGER,
            passed=bool(recons)
            and all(
                entry.rule_id not in {"other", "unknown", ""}
                for recon in recons
                for entry in recon.drop_ledger
            ),
            completed=bool(recons),
            evidence=f"{total_drops} excluded row(s), every one attributed to a rule",
            citation=CitationId(kind=CitationKind.RECON, subject=batch.batch_id),
        )
    )
    results = control.rule_results(batch.batch_id)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.DQ_RULES,
            passed=bool(results),
            completed=bool(results),
            evidence=(
                f"{len(results)} rule(s) recorded a verdict; "
                f"{sum(r.failed for r in results)} row(s) flagged, all attributed"
                if results
                else "no rule verdicts recorded — silence is not a pass"
            ),
            citation=CitationId(kind=CitationKind.BATCH, subject=batch.batch_id),
        )
    )
    drift = control.get_schema_drift(batch.batch_id)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.SCHEMA_CONTRACT,
            passed=not any(d.blocked_batch for d in drift),
            completed=True,
            evidence=schema_contract_evidence(drift),
            citation=CitationId(kind=CitationKind.BATCH, subject=batch.batch_id),
        )
    )
    owed = next(
        (
            cycle
            for cycle in control.sla_history(batch.feed_id, days=90)
            if cycle.batch_id == batch.batch_id
        ),
        None,
    )
    if owed is not None:
        checks.append(
            batch_certification.Check(
                kind=batch_certification.CheckKind.SLA_WINDOW,
                passed=owed.sla_status == "On-Time",
                completed=True,
                evidence=f"cycle {owed.cycle_date.isoformat()} was {owed.sla_status}",
                citation=CitationId(kind=CitationKind.FEED, subject=batch.feed_id),
            )
        )
    return tuple(checks)


def _incident_for(
    control: ControlTablesPort, metadata: MetadataDbPort, batch: BatchControl
) -> fingerprinting.Incident:
    """The whole incident: evidence recomputed, decisions from the ledger.

    The computed half is deterministic over control.error_log and the
    published runbooks; the ledger holds only what people did. `hydrate`
    folds the two, so acknowledging an incident never has to store — and can
    never contradict — its evidence.
    """
    errors = tuple(control.list_errors(batch_id=batch.batch_id))
    incident = fingerprinting.fingerprint_batch(
        batch_id=batch.batch_id,
        feed_id=batch.feed_id,
        errors=errors,
        guides=recovery_guides(metadata),
        history=priors_for(control, feed_id=batch.feed_id, batch_id=batch.batch_id, errors=errors),
        now=datetime.now(UTC),
    )
    try:
        held = metadata.get_incident_event(incident.incident_id)
    except ObjectNotFoundError:
        return incident
    return fingerprinting.hydrate(incident, held)


def _incident_out(
    incident: fingerprinting.Incident, *, warnings: tuple[str, ...] = ()
) -> IncidentOut:
    """`warnings` (CF-V1-W1-28) is CloneOut's own pattern: "unapproved
    inherited parts marked" there, "the knowledge-embed side effect did not
    happen" here — a fact a steward should know that is not part of the
    incident's own state, so it does not belong mixed into `resolution` or
    any other substantive field."""
    match = incident.match
    return IncidentOut(
        incident_id=incident.incident_id,
        batch_id=incident.batch_id,
        feed_id=incident.feed_id,
        opened_ts=incident.opened_ts,
        kind=incident.kind.value,
        status=incident.status,
        state=incident.state.value,
        acknowledged_by=incident.acknowledged_by,
        assigned_to=incident.assigned_to,
        resolution=incident.resolution,
        resolved_ts=incident.resolved_ts,
        signature=incident.signature,
        root_cause=_error_out(incident.root_cause) if incident.root_cause else None,
        consequences=[_error_out(e) for e in incident.cascade.consequences],
        match=(
            GuideMatchOut(
                guide_id=match.guide.guide_id,
                title=match.guide.title,
                steps=list(match.guide.steps),
                signature=match.signature,
                matched_errors=list(match.matched_errors),
                occurrences=match.occurrences,
                mean_fix_minutes=match.mean_fix_minutes,
                remedy=match.guide.remedy.value if match.guide.remedy else None,
                stale=match.guide.stale,
                priors=[
                    PriorIncidentOut(
                        incident_id=prior.incident_id,
                        occurred_ts=prior.occurred_ts,
                        fix_minutes=prior.fix_minutes,
                        batch_id=prior.batch_id,
                        citation=str(prior.citation),
                    )
                    for prior in match.priors
                ],
                citations=[str(c) for c in match.citations],
                explanation=match.explain(),
            )
            if match
            else None
        ),
        proposed_remedy=(incident.proposed_remedy.value if incident.proposed_remedy else None),
        evidence_bundle=incident.evidence_bundle(),
        explanation=incident.explain(),
        citation=str(incident.citation),
        route=incident.citation.route,
        warnings=list(warnings),
    )


# ── CF-V1-E7-04 · the technical review queue ─────────────────────────────────
def _review_candidates(metadata: MetadataDbPort, feed_id: str) -> tuple[rule_review.Candidate, ...]:
    """The agent's latest rule-authoring output, as routing candidates.

    Read from the PROPOSAL rather than from a queue table: the agent already
    writes every authored rule and every `NeedsTechnicalReview` into
    `proposals.proposal`, and a second store would be a second answer to "what
    did the agent say" — with the queue and the proposal free to disagree about
    a rule nobody has looked at yet.
    """
    proposals = [
        proposal
        for proposal in metadata.list_proposals(feed_id=feed_id)
        if proposal.agent == rule_authoring_graph.AGENT
    ]
    if not proposals:
        return ()
    latest = max(proposals, key=lambda p: p.created_ts)
    found: list[rule_review.Candidate] = []
    for record in latest.payload.get("records", ()):
        if not isinstance(record, dict):
            continue
        raw_rule = record.get("rule")
        found.append(
            rule_review.Candidate(
                stated=str(record.get("stated", "")),
                confidence=float(record.get("confidence") or 0.0),
                check=(
                    rules_core.check_from_dict(raw_rule["check"])
                    if isinstance(raw_rule, dict) and isinstance(raw_rule.get("check"), dict)
                    else None
                ),
                machine_reading=str(record.get("explanation", "")),
                unsupported_reason=str(record.get("unsupported_reason") or ""),
            )
        )
    return tuple(candidate for candidate in found if candidate.stated.strip())


def _review_out(review: rule_review.TechnicalReview) -> TechnicalReviewOut:
    return TechnicalReviewOut(
        review_id=review.review_id,
        feed_id=review.feed_id,
        stated=review.stated,
        machine_reading=review.machine_reading,
        reason=review.reason.value,
        explained_to_author=review.explain_to_author(),
        confidence=review.confidence,
        state=review.state.value,
        status=review.state.status_word,
        created_ts=review.created_ts,
        evidence=dict(review.evidence),
        reviewed_by=review.reviewed_by.subject if review.reviewed_by else None,
        resolution_note=review.resolution_note,
        engineering_item_id=review.engineering_item_id,
        citation=str(review.citation),
    )
