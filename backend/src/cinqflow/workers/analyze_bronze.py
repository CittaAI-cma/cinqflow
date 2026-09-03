"""Topic bronze.analyze - deterministic Bronze profile, then an AI mapping proposal.

The order is the point: code establishes the facts about what landed, and the model
reasons only over those facts plus governed knowledge.
"""

from __future__ import annotations

import logging

import psycopg

from cinqflow.dataplane.contract import bronze_table
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine import profiler
from cinqflow.engine.bronze_profiler import profile_batch
from cinqflow.intelligence.runtime import AgentRuntime
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import Provenance
from cinqflow.workflow.states import RunState
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "bronze.analyze"


def handle(
    conn: psycopg.Connection,
    payload: dict,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    s = settings or get_settings()
    store = WorkflowStore(conn, s)
    batch_id = payload["batch_id"]

    run = store.get_run(batch_id)
    if run is None:
        raise RuntimeError(f"unknown batch {batch_id}")
    if run.state != RunState.COMPLETED:
        # Analysing a batch that did not land cleanly would describe partial data.
        log.warning("refusing to analyse batch %s in state %s", batch_id, run.state)
        return {"batch_id": batch_id, "analysed": False, "reason": f"run is {run.state}"}

    upload = store.get_upload(run.upload_id)
    table = bronze_table(run.feed)
    plane = PostgresDataPlane(conn)

    # 1. Deterministic: what actually landed. The upload profile supplies the source
    # column order, which JSONB storage cannot preserve on its own.
    upload_profile = store.get_profile(run.upload_id)
    result = profile_batch(
        plane,
        table,
        batch_id,
        settings=s,
        column_order=[c.name for c in upload_profile.facts.columns] if upload_profile else None,
    )
    bronze_profile = store.put_bronze_profile(
        profile_id=result.profile_id,
        batch_id=batch_id,
        bronze_table=table.qualified,
        profiler_version=profiler.PROFILER_VERSION,
        rows_in_batch=result.rows_in_batch,
        rows_profiled=result.rows_profiled,
        facts=result.facts,
    )
    conn.commit()

    # 2. AI: propose mappings over those facts and the governed target model.
    try:
        outcome = (runtime or AgentRuntime(settings=s)).run(
            "recommend_mapping",
            facts=result.facts,
            source_system=upload.source_system,
            feed=upload.feed,
            domain=_domain_for(upload.domain),
        )
    except Exception as exc:  # noqa: BLE001 - the profile stands regardless
        conn.rollback()
        log.warning("mapping proposal failed for batch %s: %s", batch_id, exc)
        return {
            "batch_id": batch_id,
            "analysed": True,
            "bronze_profile_id": bronze_profile.profile_id,
            "proposal": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    proposal = store.put_proposal(
        batch_id=batch_id,
        upload_id=run.upload_id,
        feed=run.feed,
        domain=_domain_for(upload.domain),
        bronze_profile_id=bronze_profile.profile_id,
        status=outcome["status"],
        provenance=Provenance(
            prompt=outcome["prompt"], model=outcome["model"], knowledge=outcome["knowledge"]
        ),
        content=outcome["content"],
    )

    counts = proposal.content.counts
    log.info(
        "analysed batch %s: %s columns profiled, proposal %s (%s)",
        batch_id,
        len(result.facts.columns),
        proposal.status,
        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )
    return {
        "batch_id": batch_id,
        "analysed": True,
        "bronze_profile_id": bronze_profile.profile_id,
        "proposal_id": proposal.proposal_id,
        "proposal_status": proposal.status,
        "counts": counts,
    }


def _domain_for(upload_domain: str) -> str:
    """Uploads are registered under a plural landing domain (`enrollments`); the
    canonical model is named in the singular (`enrollment`)."""
    return upload_domain[:-1] if upload_domain.endswith("s") else upload_domain
