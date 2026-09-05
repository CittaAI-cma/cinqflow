"""Topic upload.interpret - the AI step. Reasons over the persisted profile."""

from __future__ import annotations

import logging

import psycopg

from cinqflow.intelligence.runtime import AgentRuntime
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import Provenance
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "upload.interpret"


def handle(
    conn: psycopg.Connection,
    payload: dict,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    s = settings or get_settings()
    store = WorkflowStore(conn, s)
    upload_id = payload["upload_id"]
    upload = store.get_upload(upload_id)
    profile = store.get_profile(upload_id)
    if profile is None:
        raise RuntimeError(f"no profile for upload {upload_id}")

    if upload.status in (UploadStatus.PROFILED, UploadStatus.INTERPRET_FAILED):
        store.set_status(upload_id, UploadStatus.INTERPRETING)
    store.start_interpretation_run(upload_id=upload_id, profile_id=profile.profile_id)
    # Durable before the graph starts: a poll must see `interpreting` and a
    # freshly-started run immediately, not only once the whole job finishes.
    conn.commit()

    def on_step(node: str) -> None:
        store.record_interpretation_step(upload_id=upload_id, node=node)
        conn.commit()

    try:
        result = (runtime or AgentRuntime(settings=s)).run(
            "interpret_file",
            facts=profile.facts,
            source_system=upload.source_system,
            feed=upload.feed,
            domain=upload.domain,
            on_step=on_step,
        )
    except Exception as exc:  # noqa: BLE001 - persisted as a retryable state
        conn.rollback()
        WorkflowStore(conn, s).set_status(
            upload_id, UploadStatus.INTERPRET_FAILED, error=f"{type(exc).__name__}: {exc}"
        )
        WorkflowStore(conn, s).finish_interpretation_run(
            upload_id=upload_id, status="failed", error=f"{type(exc).__name__}: {exc}"
        )
        conn.commit()
        log.warning("interpretation failed for %s: %s", upload_id, exc)
        return {
            "upload_id": upload_id,
            "status": UploadStatus.INTERPRET_FAILED,
            "error": f"{type(exc).__name__}: {exc}",
        }

    interpretation = store.put_interpretation(
        upload_id=upload_id,
        profile_id=profile.profile_id,
        provenance=Provenance(
            prompt=result["prompt"], model=result["model"], knowledge=result["knowledge"]
        ),
        content=result["content"],
    )
    store.set_status(upload_id, UploadStatus.INTERPRETED)
    store.finish_interpretation_run(upload_id=upload_id, status="completed")
    log.info(
        "interpreted %s: %s claims, %s signals (%s)",
        upload_id,
        len(interpretation.content.claims),
        len(interpretation.content.signals),
        interpretation.content.headline,
    )
    return {
        "upload_id": upload_id,
        "interpretation_id": interpretation.interpretation_id,
        "status": UploadStatus.INTERPRETED,
    }
