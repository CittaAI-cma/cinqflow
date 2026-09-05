"""Topic upload.profile - deterministic. No model is involved anywhere here."""

from __future__ import annotations

import logging

import psycopg

from cinqflow.dataplane.filestore import FileStore
from cinqflow.engine import profiler
from cinqflow.engine.parsers import ParseError, parse
from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import StepLedger, WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "upload.profile"


def handle(conn: psycopg.Connection, payload: dict, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    store = WorkflowStore(conn, s)
    upload_id = payload["upload_id"]
    upload = store.get_upload(upload_id)

    if upload.status in (UploadStatus.RECEIVED, UploadStatus.PROFILE_FAILED):
        store.set_status(upload_id, UploadStatus.PROFILING)
        # Durable before parsing starts (mirrors interpret_upload.handle): the
        # failure path below rolls the transaction back, and an uncommitted
        # PROFILING here would roll back to PROFILE_FAILED with it - making the
        # failure handler's own PROFILE_FAILED write an illegal
        # PROFILE_FAILED -> PROFILE_FAILED transition on a second attempt
        # (masked on a first attempt only because RECEIVED -> PROFILE_FAILED is
        # legal).
        conn.commit()

    try:
        content = FileStore(s).read_bytes(upload.landing_key)
        parsed = parse(content, upload.file_type)
        facts = profiler.profile(parsed, s)
    except (ParseError, FileNotFoundError, OSError) as exc:
        conn.rollback()
        WorkflowStore(conn, s).set_status(
            upload_id, UploadStatus.PROFILE_FAILED, error=f"{type(exc).__name__}: {exc}"
        )
        conn.commit()
        log.warning("profiling failed for %s: %s", upload_id, exc)
        return {
            "upload_id": upload_id,
            "status": UploadStatus.PROFILE_FAILED,
            "error": f"{type(exc).__name__}: {exc}",
        }

    profile = store.put_profile(
        profile_id=profiler.profile_id(facts),
        upload_id=upload_id,
        profiler_version=profiler.PROFILER_VERSION,
        facts=facts,
    )
    store.set_status(upload_id, UploadStatus.PROFILED)

    message_id = Queue(conn, s).enqueue(
        "upload.interpret",
        {"upload_id": upload_id, "profile_id": profile.profile_id},
        dedupe_key=f"upload.interpret/{upload_id}/{profile.profile_id}",
    )
    if message_id:
        StepLedger(conn, s).queued("upload", upload_id, "interpret", message_id=message_id)
    log.info("profiled %s: %s rows, %s columns", upload_id, facts.row_count, len(facts.columns))
    return {
        "upload_id": upload_id,
        "profile_id": profile.profile_id,
        "status": UploadStatus.PROFILED,
    }
