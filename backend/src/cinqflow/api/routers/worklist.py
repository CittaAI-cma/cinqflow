"""The two homes' data: what is waiting on an analyst, and what needs the
platform team's attention.

`GET /api/worklist` - the Data Analyst home. Without it the register computes
the same thing client-side by pulling every upload and filtering in the browser
- correct, but O(uploads) instead of O(1), and it can't see mapping versions at
all. One cheap call instead. PR-4 adds `counts`, `waiting_since` per item (from
the ledger's gate rows: the moment the gate opened), and the recent uploads.

`GET /api/attention` - the Data Platform home (PR-4). Failed steps from the
ledger (gates excluded: a rejection is a decision, `StepDef.gate`), steps in
flight, the messages the queue gave up on, queue depth, and each feed's latest
upload with the adverse ones first. Every figure is read from what exists; no
new tables.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends

from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings
from cinqflow.workflow.dag import STEPS, WORKFLOW, feed_version_scope, parse_feed_version_scope
from cinqflow.workflow.models import StepRun
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import StepLedger, UnknownUpload, WorkflowStore

MAX_LIMIT = 200

#: Upload statuses that read as "this run stopped badly" on the platform home.
ADVERSE_STATUSES = frozenset(
    {
        UploadStatus.REJECTED,
        UploadStatus.PROFILE_FAILED,
        UploadStatus.INTERPRET_FAILED,
        UploadStatus.LAND_FAILED,
    }
)


def _iso(value: Any) -> str | None:
    """Same rendering as pydantic's JSON mode (`...Z`), so a timestamp added by
    hand compares equal to one on a dumped model."""
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _step_context(step: StepRun, store: WorkflowStore) -> dict[str, Any]:
    """Where a ledger row belongs, in the words a person navigates by: feed,
    file, and the route that shows it. Resolved per scope; an object that no
    longer exists (deleted upload) leaves the fields null rather than failing
    the whole list."""
    context: dict[str, Any] = {
        "feed": None,
        "filename": None,
        "upload_id": None,
        "batch_id": None,
        "href": None,
    }
    if step.scope_kind == "upload":
        try:
            upload = store.get_upload(step.scope_id)
        except UnknownUpload:
            return context
        context.update(
            feed=upload.feed,
            filename=upload.filename,
            upload_id=upload.upload_id,
            href=f"/uploads/{upload.upload_id}",
        )
    elif step.scope_kind == "batch":
        run = store.get_run(step.scope_id)
        if run is None:
            return context
        context.update(
            feed=run.feed,
            upload_id=run.upload_id,
            batch_id=run.batch_id,
            href=f"/batches/{run.batch_id}",
        )
        try:
            context["filename"] = store.get_upload(run.upload_id).filename
        except UnknownUpload:
            pass
    else:
        try:
            feed, version = parse_feed_version_scope(step.scope_id)
        except ValueError:
            return context
        context.update(feed=feed, href=f"/mapping/{feed}?v={version}")
    return context


def _with_context(steps: list[StepRun], store: WorkflowStore) -> list[dict[str, Any]]:
    out = []
    for step in steps:
        definition = STEPS.get(step.step_key)
        out.append(
            {
                **step.model_dump(mode="json"),
                "label": definition.label if definition else step.step_key,
                **_step_context(step, store),
            }
        )
    return out


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
        version, same as today. `waiting_since` is when the gate opened in the
        ledger (the step before it finished); for a run from before the ledger
        it falls back to the artifact's own timestamp.
        """
        store = WorkflowStore(conn, s)
        ledger = StepLedger(conn, s)
        uploads_at_g1 = store.list_uploads_by_status(["interpreted"])
        versions_at_g2 = store.list_mapping_versions_by_status("previewed")

        def waiting_since_upload(upload) -> str:
            gate = ledger.latest("upload", upload.upload_id, "gate_g1")
            return _iso(gate.started_ts or gate.queued_ts) if gate else _iso(upload.created_ts)

        def waiting_since_version(version) -> str:
            gate = ledger.latest(
                "feed_version", feed_version_scope(version.feed, version.version), "gate_g2"
            )
            if gate:
                return _iso(gate.started_ts or gate.queued_ts)
            return _iso(version.updated_ts or version.created_ts)

        return {
            "counts": {
                "waiting_at_g1": len(uploads_at_g1),
                "approvable_at_g2": len(versions_at_g2),
            },
            "uploads_at_g1": [
                {**u.model_dump(mode="json"), "waiting_since": waiting_since_upload(u)}
                for u in uploads_at_g1
            ],
            "mapping_versions_at_g2": [
                # `origin`/`editable` are computed properties, not model fields, so
                # `model_dump()` alone drops them - added here to match the shape
                # `GET .../mapping-versions` already returns, so a caller doesn't
                # get a differently-shaped mapping version depending on which
                # endpoint it came from.
                {
                    **v.model_dump(mode="json"),
                    "origin": v.origin,
                    "editable": v.editable,
                    "waiting_since": waiting_since_version(v),
                }
                for v in versions_at_g2
            ],
            "recent_uploads": [u.model_dump(mode="json") for u in store.list_uploads(limit=8)],
        }

    @router.get("/api/attention")
    def get_attention(limit: int = 50, conn=Depends(get_conn)) -> dict:
        """The Data Platform home. Failed worker steps (a rejected gate is a
        decision, not a failure - excluded by `StepDef.gate`), steps queued or
        running, dead-letter messages, queue depth, and each feed's latest
        upload, adverse first."""
        limit = max(1, min(limit, MAX_LIMIT))
        store = WorkflowStore(conn, s)
        ledger = StepLedger(conn, s)
        queue = Queue(conn, s)

        failed = [
            step
            for step in ledger.list_by_state("failed", limit=limit * 2)
            if not (STEPS.get(step.step_key) and STEPS[step.step_key].gate)
        ][:limit]
        in_flight = [
            step
            for step in ledger.list_by_state("running", limit=limit)
            + ledger.list_by_state("pending", limit=limit)
            if not (STEPS.get(step.step_key) and STEPS[step.step_key].gate)
        ]

        latest_by_feed: dict[str, Any] = {}
        for upload in store.list_uploads(limit=500):  # newest first
            latest_by_feed.setdefault(upload.feed, upload)
        feeds = [
            {
                "feed": upload.feed,
                "upload_id": upload.upload_id,
                "filename": upload.filename,
                "status": upload.status,
                "error": upload.error,
                "created_ts": _iso(upload.created_ts),
                "adverse": upload.status in ADVERSE_STATUSES,
            }
            for upload in latest_by_feed.values()
        ]
        # Newest first, then the adverse feeds ahead of the healthy ones (a stable
        # sort keeps the newest-first order within each group).
        feeds.sort(key=lambda f: f["created_ts"] or "", reverse=True)
        feeds.sort(key=lambda f: not f["adverse"])

        topics = [step.topic for step in WORKFLOW if step.topic] + ["upload.reject"]
        return {
            "failed_steps": _with_context(failed, store),
            "in_flight_steps": _with_context(in_flight, store),
            "dead_messages": queue.list_dead(limit),
            "queue_depth": {
                "pending_total": queue.depth(),
                **{topic: queue.depth(topic) for topic in topics},
            },
            "feeds": feeds,
        }

    return router
