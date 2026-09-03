"""Batches, bronze/quarantine rows, and lineage."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException

from cinqflow.dataplane.contract import bronze_table, quarantine_table
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.settings import Settings
from cinqflow.workflow.models import BatchDetail, mask_facts, mask_row
from cinqflow.workflow.store import WorkflowStore


def build_router(
    settings: Settings, get_conn: Callable[[], Iterator]
) -> APIRouter:
    s = settings
    router = APIRouter()

    @router.get("/api/batches")
    def list_batches(limit: int = 50, conn=Depends(get_conn)) -> dict:
        runs = WorkflowStore(conn, s).list_runs(limit=limit)
        return {"batches": [r.model_dump(mode="json") for r in runs]}

    @router.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str, conn=Depends(get_conn)) -> dict:
        store = WorkflowStore(conn, s)
        run = store.get_run(batch_id)
        if run is None:
            raise HTTPException(404, detail=f"unknown batch: {batch_id}")
        bronze_profile = store.get_bronze_profile(batch_id)
        if bronze_profile is not None:
            bronze_profile = bronze_profile.model_copy(
                update={"facts": mask_facts(bronze_profile.facts)}
            )

        detail = BatchDetail(
            run=run,
            lineage=store.get_lineage(batch_id),
            approvals=store.list_approvals(run.upload_id),
            upload=store.get_upload(run.upload_id),
            bronze_profile=bronze_profile,
            proposal=store.get_proposal(batch_id),
        )
        return detail.model_dump(mode="json")

    @router.get("/api/batches/{batch_id}/progress")
    def get_batch_progress(
        batch_id: str, kind: str | None = None, conn=Depends(get_conn)
    ) -> dict:
        """A lightweight poll target for the landing and promotion screens: just
        the run, none of `GET /api/batches/{id}`'s lineage, approvals, upload,
        bronze profile or proposal - a one-time detail view needs those; a poll
        firing every 1500ms does not. `kind` disambiguates when both a
        `land_bronze` and a `promote_silver` run share this batch_id; omitted,
        it resolves the same way `GET /api/batches/{id}` already does."""
        run = WorkflowStore(conn, s).get_run(batch_id, kind=kind)
        if run is None:
            raise HTTPException(404, detail=f"unknown batch: {batch_id}")
        return run.model_dump(mode="json")

    @router.get("/api/batches/{batch_id}/bronze-profile")
    def get_bronze_profile(batch_id: str, conn=Depends(get_conn)) -> dict:
        """Deterministic facts about what landed. Computed by code, no model."""
        profile = WorkflowStore(conn, s).get_bronze_profile(batch_id)
        if profile is None:
            raise HTTPException(404, detail=f"no bronze profile for batch: {batch_id}")
        payload = profile.model_copy(update={"facts": mask_facts(profile.facts)})
        return {
            **payload.model_dump(mode="json"),
            "is_sample": profile.is_sample,
        }

    @router.get("/api/batches/{batch_id}/proposal")
    def get_proposal(batch_id: str, conn=Depends(get_conn)) -> dict:
        """The AI mapping proposal. Advisory only until Stage 4 gives the analyst
        an editable version to own."""
        proposal = WorkflowStore(conn, s).get_proposal(batch_id)
        if proposal is None:
            raise HTTPException(404, detail=f"no proposal for batch: {batch_id}")
        return {
            **proposal.model_dump(mode="json"),
            "counts": proposal.content.counts,
            "authoritative": False,
        }

    @router.get("/api/batches/{batch_id}/rows")
    def get_batch_rows(
        batch_id: str, limit: int = 50, offset: int = 0, conn=Depends(get_conn)
    ) -> dict:
        """Bronze rows for one batch, PHI-masked using the upload's own profile."""
        store = WorkflowStore(conn, s)
        run = store.get_run(batch_id)
        if run is None:
            raise HTTPException(404, detail=f"unknown batch: {batch_id}")

        table = bronze_table(run.feed)
        plane = PostgresDataPlane(conn)
        if not plane.table_exists(table):
            return {"batch_id": batch_id, "table": table.qualified, "rows": [], "total": 0}

        profile = store.get_profile(run.upload_id)
        phi = set(profile.facts.phi_candidates) if profile else set()
        rows = plane.read_rows(table, batch_id, limit=min(limit, 200), offset=offset)
        return {
            "batch_id": batch_id,
            "table": table.qualified,
            "total": plane.count_rows(table, batch_id),
            "phi_masked": sorted(phi),
            "rows": [
                {
                    "row_number": row["row_number"],
                    "record_hash": row["record_hash"],
                    "raw_row": mask_row(row["raw_row"], phi),
                }
                for row in rows
            ],
        }

    @router.get("/api/batches/{batch_id}/quarantine")
    def get_batch_quarantine(
        batch_id: str, limit: int = 50, offset: int = 0, conn=Depends(get_conn)
    ) -> dict:
        """Rows the approved mapping refused, with the rule that refused them.

        PHI-masked exactly as the Bronze rows are: a quarantined row exists to be
        understood, and understanding why a rule fired does not require the value
        it fired on. Read-only - a fix is a new mapping version, and re-promoting
        the batch re-drives these rows through it.
        """
        store = WorkflowStore(conn, s)
        run = store.get_run(batch_id)
        if run is None:
            raise HTTPException(404, detail=f"unknown batch: {batch_id}")

        table = quarantine_table(run.feed, schema=s.silver_schema)
        plane = PostgresDataPlane(conn)
        if not plane.table_exists(table):
            return {
                "batch_id": batch_id,
                "table": table.qualified,
                "total": 0,
                "by_outcome": {},
                "by_rule": {},
                "rows": [],
            }

        profile = store.get_profile(run.upload_id)
        phi = set(profile.facts.phi_candidates) if profile else set()
        rows = plane.read_quarantine(table, batch_id, limit=min(limit, 200), offset=offset)

        by_rule: dict[str, int] = {}
        for row in rows:
            for reason in row["reasons"]:
                key = f"{reason.get('source')}:{reason.get('rule')}"
                by_rule[key] = by_rule.get(key, 0) + 1

        return {
            "batch_id": batch_id,
            "table": table.qualified,
            "total": plane.count_rows(table, batch_id),
            "by_outcome": plane.count_by(table, batch_id, "outcome"),
            # Over the returned page only; `by_outcome` and `total` cover the batch.
            "by_rule": dict(sorted(by_rule.items())),
            "phi_masked": sorted(phi),
            "rows": [
                {
                    "row_number": row["row_number"],
                    "mapping_version": row["mapping_version"],
                    "outcome": row["outcome"],
                    "reasons": row["reasons"],
                    "raw_row": mask_row(row["raw_row"], phi),
                }
                for row in rows
            ],
        }

    @router.get("/api/lineage/{batch_id}")
    def get_lineage(batch_id: str, conn=Depends(get_conn)) -> dict:
        store = WorkflowStore(conn, s)
        lineage = store.get_lineage(batch_id)
        if lineage is None:
            raise HTTPException(404, detail=f"unknown batch: {batch_id}")
        runs = store.list_batch_runs(batch_id)
        landing = next((r for r in runs if r.kind == "land_bronze"), None)
        promotion = next((r for r in runs if r.kind == "promote_silver"), None)
        approvals = store.list_approvals(lineage.upload_id)
        return {
            "chain": {
                "upload_id": lineage.upload_id,
                "fingerprint": lineage.fingerprint,
                "landing_key": lineage.landing_key,
                "batch_id": lineage.batch_id,
                "bronze_table": lineage.bronze_table,
                "mapping": (
                    {"feed": landing.feed if landing else None, "version": lineage.mapping_version}
                    if lineage.mapping_version is not None
                    else None
                ),
                "mapping_version": lineage.mapping_version,
                "silver_table": lineage.silver_table,
                "silver_tables": lineage.silver_tables,
            },
            "run": landing.model_dump(mode="json") if landing else None,
            "runs": [r.model_dump(mode="json") for r in runs],
            "promotion": promotion.model_dump(mode="json") if promotion else None,
            "approvals": [a.model_dump(mode="json") for a in approvals],
            "gates": {
                gate: next(
                    (a.model_dump(mode="json") for a in approvals if a.gate == gate),
                    None,
                )
                for gate in ("G1", "G2")
            },
        }

    return router
