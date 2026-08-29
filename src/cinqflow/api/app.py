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
    AgentActionOut,
    AskIn,
    AskOut,
    AuditOut,
    BatchOut,
    BudgetOut,
    ClaimOut,
    ContractOut,
    DestinationOut,
    FeedIn,
    FeedOut,
    NavigationOut,
    PrincipalOut,
    RowsOut,
    ToolOut,
    TraceStepOut,
    UnknownOut,
)
from cinqflow.core.agents.pipeline_insight.graph import AGENT
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.intelligence import Budget
from cinqflow.core.model.governed import GovernedObject, ObjectType
from cinqflow.core.navigation import ACTIVE_WAVE, for_roles
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry.execution_plane import ExecutionPlaneRegister
from cinqflow.core.registry.wave0 import wave_0_register
from cinqflow.core.security import Action, may
from cinqflow.core.tools import CATALOGUE
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
from cinqflow.intelligence.tools import ToolContext, ToolResult, invoke
from cinqflow.ports.authn import AuthnPort, Principal
from cinqflow.ports.control_tables import BatchControl, ControlTablesPort
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError

API_PREFIX = "/api"

#: Build an agent for ONE caller. Never a shared agent — a shared agent is a
#: shared scope, and the tool context is where the caller's RBAC lives.
AgentFactory = Callable[[Principal, "ControlTablesPort", MetadataDbPort], PipelineInsightAgent]


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
    agent_factory: AgentFactory | None = None,
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
    app.state.agent_factory = agent_factory
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
    ) -> list[FeedOut]:
        """Scope is applied while the list is BUILT, never to a finished response.

        "Apply a scope filter to results rather than to the query"
        — INVARIANTS.md, a documented don't
        """
        return [
            _feed_out(obj)
            for obj in metadata.list(ObjectType.FEED)
            if principal.scopes.covers_feed(obj.object_id)
        ]

    @app.get(f"{API_PREFIX}/feeds/{{feed_id}}", response_model=FeedOut, tags=["intake"])
    def get_feed(
        feed_id: str,
        metadata: Store,
        _: Annotated[Principal, Depends(require(Action.VIEW))],
    ) -> FeedOut:
        return _feed_out(_load(metadata, feed_id))

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
        amended = current.new_version(
            record.as_governed(author=principal.as_actor()).body, actor=principal.as_actor()
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

    # ── governance ───────────────────────────────────────────────────────────

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
            principal=principal, control=control, metadata=metadata,
            run_id=f"ui-{batch_id}", agent="ui",
        )
        return _rows_out(invoke(context, _PANEL_TOOLS[panel], {"batch_id": batch_id}))

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
                claims=[], confidence="low",
                unanswered=["the llm pin is not fitted in this connection profile"],
                intent="declined", tools_called=[], trace=[], cost_usd="0",
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


def _load(metadata: MetadataDbPort, feed_id: str) -> GovernedObject:
    try:
        return metadata.get(ObjectType.FEED, feed_id)
    except ObjectNotFoundError:
        # The same sentence a scope miss produces. Two sentences would be an
        # oracle for which feed ids are real.
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None


def _record_from(body: FeedIn) -> feed_registry.FeedRecord:
    try:
        return feed_registry.FeedRecord(**body.model_dump())
    except feed_registry.PatternSampleMismatchError as mismatch:
        # A pattern that does not match a real filename is refused BEFORE save,
        # with the side-by-side diff — incident #1 was a leading underscore
        # nobody could see in a regex.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(mismatch)) from None
    except feed_registry.FeedValidationError as invalid:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(invalid)) from None


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
    )


def _principal_out(principal: Principal) -> PrincipalOut:
    return PrincipalOut(
        subject=principal.subject,
        display_name=principal.display_name,
        roles=sorted(role.value for role in principal.roles),
        has_access=principal.has_access,
        permitted_actions=sorted(a.value for a in Action if may(principal, a)),
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
