"""Topic batch.land_bronze - runs only after an analyst approved at G1."""

from __future__ import annotations

import logging

import psycopg

from cinqflow.engine.runner import LandingFailure, PipelineRunner
from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings, get_settings
from cinqflow.workers import analyze_bronze
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "batch.land_bronze"

_RUNNABLE = (UploadStatus.APPROVED, UploadStatus.LANDING, UploadStatus.LAND_FAILED)


def handle(conn: psycopg.Connection, payload: dict, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    upload_id = payload["upload_id"]
    upload = WorkflowStore(conn, s).get_upload(upload_id)

    if upload.status not in _RUNNABLE:
        # Approval is the only thing that authorises a plane write.
        log.warning("refusing to land %s in status %s", upload_id, upload.status)
        return {"upload_id": upload_id, "status": upload.status, "landed": False}

    try:
        outcome = PipelineRunner(conn, s).land_bronze(upload)
    except LandingFailure as exc:
        return {"upload_id": upload_id, "status": UploadStatus.LAND_FAILED, "error": str(exc)}

    # Bronze intelligence follows a clean landing automatically.
    Queue(conn, s).enqueue(
        analyze_bronze.TOPIC,
        {"batch_id": outcome.batch_id},
        dedupe_key=f"{analyze_bronze.TOPIC}/{outcome.batch_id}",
    )

    return {
        "upload_id": upload_id,
        "status": UploadStatus.LANDED,
        "batch_id": outcome.batch_id,
        "bronze_table": outcome.bronze_table,
        "counts": outcome.counts.model_dump(),
        "landed": True,
    }
