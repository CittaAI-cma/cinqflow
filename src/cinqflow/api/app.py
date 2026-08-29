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

from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status

from cinqflow.api.audit import AuditLog
from cinqflow.api.deps import NOT_FOUND, CurrentPrincipal, Wiring, require
from cinqflow.api.schemas import (
    AuditOut,
    ContractOut,
    FeedIn,
    FeedOut,
    PrincipalOut,
    UnknownOut,
)
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import GovernedObject, ObjectType
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry.execution_plane import ExecutionPlaneRegister
from cinqflow.core.registry.wave0 import wave_0_register
from cinqflow.core.security import Action, may
from cinqflow.ports.authn import AuthnPort, Principal
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError

API_PREFIX = "/api"


# Resolved from app.state, at MODULE level. A dependency defined inside
# `create_app` is invisible to `get_type_hints`, which resolves annotations
# against module globals — FastAPI would silently read it as a query parameter.
def _store(request: Request) -> MetadataDbPort:
    return request.app.state.metadata_db  # type: ignore[no-any-return]


def _audit(request: Request) -> AuditLog:
    return request.app.state.wiring.audit  # type: ignore[no-any-return]


def _plane(request: Request) -> ExecutionPlaneRegister:
    return request.app.state.plane_register  # type: ignore[no-any-return]


def _authn(request: Request) -> AuthnPort:
    return request.app.state.wiring.authn  # type: ignore[no-any-return]


Store = Annotated[MetadataDbPort, Depends(_store)]
Audit = Annotated[AuditLog, Depends(_audit)]
Plane = Annotated[ExecutionPlaneRegister, Depends(_plane)]
Directory = Annotated[AuthnPort, Depends(_authn)]


def create_app(
    *,
    authn: AuthnPort,
    metadata_db: MetadataDbPort,
    plane_register: ExecutionPlaneRegister | None = None,
) -> FastAPI:
    app = FastAPI(
        title="CINQFLOW",
        version="0.1.0",
        summary="Wave 0 — Landing to Silver Raw, and the platform explaining itself.",
    )
    app.state.wiring = Wiring(authn=authn, audit=AuditLog(metadata_db))
    app.state.metadata_db = metadata_db
    app.state.plane_register = plane_register or wave_0_register()

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
