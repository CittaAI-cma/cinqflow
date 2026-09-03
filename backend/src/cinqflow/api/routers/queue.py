"""Queue depth, for operators."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends

from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings
from cinqflow.workers import analyze_bronze, land_bronze, promote_silver, reject_upload, run_preview


def build_router(
    settings: Settings, get_conn: Callable[[], Iterator]
) -> APIRouter:
    s = settings
    router = APIRouter()

    @router.get("/api/queue/depth")
    def queue_depth(conn=Depends(get_conn)) -> dict:
        q = Queue(conn, s)
        return {
            "pending_total": q.depth(),
            "upload_profile": q.depth("upload.profile"),
            "upload_interpret": q.depth("upload.interpret"),
            "batch_land_bronze": q.depth(land_bronze.TOPIC),
            "upload_reject": q.depth(reject_upload.TOPIC),
            "bronze_analyze": q.depth(analyze_bronze.TOPIC),
            "mapping_preview": q.depth(run_preview.TOPIC),
            "mapping_promote": q.depth(promote_silver.TOPIC),
        }

    return router
