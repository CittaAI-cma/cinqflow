"""Topic upload.reject - moves a rejected original out of `incoming`.

Storage is only ever touched by the engine, so even this one-line move runs here
rather than in the request handler.
"""

from __future__ import annotations

import logging

import psycopg

from cinqflow.engine.runner import PipelineRunner
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "upload.reject"


def handle(conn: psycopg.Connection, payload: dict, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    upload_id = payload["upload_id"]
    upload = WorkflowStore(conn, s).get_upload(upload_id)

    if upload.status != UploadStatus.REJECTED:
        log.warning("upload %s is %s, not rejected", upload_id, upload.status)
        return {"upload_id": upload_id, "status": upload.status, "moved": False}

    if not upload.landing_key.split("/")[3] == "incoming":
        return {"upload_id": upload_id, "status": upload.status, "moved": False}

    new_key = PipelineRunner(conn, s).reject(upload)
    return {"upload_id": upload_id, "status": upload.status, "landing_key": new_key, "moved": True}
