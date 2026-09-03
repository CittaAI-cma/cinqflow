"""What's waiting on the analyst, across every feed.

Without this the register computes the same thing client-side by pulling
every upload and filtering in the browser - correct, but O(uploads) instead
of O(1), and it can't see mapping versions at all. One cheap call instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends

from cinqflow.settings import Settings
from cinqflow.workflow.store import WorkflowStore


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    router = APIRouter()

    @router.get("/api/worklist")
    def get_worklist(conn=Depends(get_conn)) -> dict:
        """Two lists: uploads sitting at G1, and mapping versions sitting at G2.

        `mapping_versions_at_g2` is every `previewed` version, not the stricter
        `approvable` set - approvable also requires the preview to still
        describe the version's *current* spec, which is only knowable by
        fetching each version's preview individually (`stale_reason` on
        `GET .../mapping-versions/{v}/preview`). This list is the honest cheap
        answer; a caller that needs the strict count still pays for it per
        version, same as today.
        """
        store = WorkflowStore(conn, s)
        uploads_at_g1 = store.list_uploads_by_status(["interpreted"])
        versions_at_g2 = store.list_mapping_versions_by_status("previewed")
        return {
            "uploads_at_g1": [u.model_dump(mode="json") for u in uploads_at_g1],
            "mapping_versions_at_g2": [
                # `origin`/`editable` are computed properties, not model fields, so
                # `model_dump()` alone drops them - added here to match the shape
                # `GET .../mapping-versions` already returns, so a caller doesn't
                # get a differently-shaped mapping version depending on which
                # endpoint it came from.
                {**v.model_dump(mode="json"), "origin": v.origin, "editable": v.editable}
                for v in versions_at_g2
            ],
        }

    return router
