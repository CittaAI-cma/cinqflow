"""Requests to extend the governed canonical model. Never applied automatically -
accepting one is a steward's signal to hand-edit the YAML; nothing here writes it."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Body, Depends, HTTPException

from cinqflow.settings import Settings
from cinqflow.workflow.store import (
    CanonicalFieldProposalAlreadyDecided,
    UnknownCanonicalFieldProposal,
    WorkflowStore,
)

DECISIONS = {"accepted", "rejected"}


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    router = APIRouter()

    @router.post("/api/canonical-proposals", status_code=201)
    def create_canonical_proposal(
        domain: str = Body(...),
        entity: str = Body(...),
        field_name: str = Body(...),
        type: str = Body(...),
        reason: str = Body(...),
        concept: str | None = Body(None),
        evidence: list[str] = Body(default_factory=list),
        requested_by: str = Body("analyst@cinqcare.com"),
        source_batch_id: str | None = Body(None),
        source_upload_id: str | None = Body(None),
        conn=Depends(get_conn),
    ) -> dict:
        store = WorkflowStore(conn, s)
        created = store.create_canonical_field_proposal(
            domain=domain,
            entity=entity,
            field_name=field_name,
            type=type,
            reason=reason,
            concept=concept,
            evidence=evidence,
            requested_by=requested_by,
            source_batch_id=source_batch_id,
            source_upload_id=source_upload_id,
        )
        conn.commit()
        return created.model_dump(mode="json")

    @router.get("/api/canonical-proposals")
    def list_canonical_proposals(
        domain: str | None = None, status: str | None = None, conn=Depends(get_conn)
    ) -> dict:
        proposals = WorkflowStore(conn, s).list_canonical_field_proposals(
            domain=domain, status=status
        )
        return {"proposals": [p.model_dump(mode="json") for p in proposals]}

    @router.get("/api/canonical-proposals/{proposal_id}")
    def get_canonical_proposal(proposal_id: str, conn=Depends(get_conn)) -> dict:
        proposal = WorkflowStore(conn, s).get_canonical_field_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(404, detail=f"unknown canonical field proposal: {proposal_id}")
        return proposal.model_dump(mode="json")

    @router.post("/api/canonical-proposals/{proposal_id}/decide", status_code=202)
    def decide_canonical_proposal(
        proposal_id: str,
        decision: str = Body(...),
        decided_by: str = Body(...),
        note: str | None = Body(None),
        conn=Depends(get_conn),
    ) -> dict:
        if decision not in DECISIONS:
            raise HTTPException(
                422,
                detail={
                    "message": f"'{decision}' is not a valid decision",
                    "allowed": sorted(DECISIONS),
                },
            )
        store = WorkflowStore(conn, s)
        try:
            decided = store.decide_canonical_field_proposal(
                proposal_id=proposal_id, decision=decision, decided_by=decided_by, note=note
            )
        except UnknownCanonicalFieldProposal:
            raise HTTPException(
                404, detail=f"unknown canonical field proposal: {proposal_id}"
            ) from None
        except CanonicalFieldProposalAlreadyDecided as exc:
            raise HTTPException(
                409,
                detail={"message": f"already {exc.status}", "proposal_id": exc.proposal_id},
            ) from None
        conn.commit()
        return decided.model_dump(mode="json")

    return router
