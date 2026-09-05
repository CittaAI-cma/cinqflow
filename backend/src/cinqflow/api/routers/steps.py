"""The workflow declaration, the step ledger, and selective re-run.

`GET /api/workflow` is the declaration itself (`workflow/dag.py`), so the
frontend derives its step list from the backend instead of keeping a copy.
`GET /api/steps` is the ledger, filtered - `?state=failed` is the platform
persona's "needs attention" list; `?scope_kind=&scope_id=` is one object's
history across generations.

The three `.../steps/{step_key}/rerun` routes (PR-3) are the only writes here:
one per scope, all `require_capability("can_rerun_steps")`, all the same
function underneath (`workflow/rerun.py`). A step belongs to exactly one scope,
and the route says which - re-running `analyze` under `/api/uploads/...` is a
409 that names the right route, not a guess.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException

from cinqflow.api.deps import make_get_current_user, require_capability
from cinqflow.auth.models import CurrentUser
from cinqflow.settings import Settings
from cinqflow.workflow.dag import SCOPES, STEP_STATES, STEPS, StepDef, as_dicts, feed_version_scope
from cinqflow.workflow.rerun import RerunRefused, assert_upload_step_runnable, rerun_step
from cinqflow.workflow.store import StepLedger, UnknownUpload, WorkflowStore

MAX_LIMIT = 500

_ROUTE_FOR_SCOPE = {
    "upload": "/api/uploads/{upload_id}/steps/{step}/rerun",
    "batch": "/api/batches/{batch_id}/steps/{step}/rerun",
    "feed_version": "/api/feeds/{feed}/mapping-versions/{version}/steps/{step}/rerun",
}


def _step_in_scope(step_key: str, scope: str) -> StepDef:
    step = STEPS.get(step_key)
    if step is None:
        raise HTTPException(404, detail=f"unknown step: {step_key}")
    if step.scope != scope:
        raise HTTPException(
            409,
            detail={
                "message": f"'{step_key}' is a {step.scope}-scoped step, not {scope}",
                "hint": "POST " + _ROUTE_FOR_SCOPE[step.scope].replace("{step}", step_key),
            },
        )
    return step


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    router = APIRouter()
    get_current_user = make_get_current_user(s, get_conn)
    require_rerun = require_capability("can_rerun_steps", get_current_user)

    @router.get("/api/workflow")
    def get_workflow() -> dict:
        return {"steps": as_dicts()}

    @router.get("/api/steps")
    def list_steps(
        state: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        limit: int = 50,
        conn=Depends(get_conn),
    ) -> dict:
        if state is not None and state not in STEP_STATES:
            raise HTTPException(
                422,
                detail={"message": f"'{state}' is not a step state", "allowed": list(STEP_STATES)},
            )
        if (scope_kind is None) != (scope_id is None):
            raise HTTPException(
                422, detail={"message": "scope_kind and scope_id go together; give both or neither"}
            )
        if scope_kind is not None and scope_kind not in SCOPES:
            raise HTTPException(
                422, detail={"message": f"'{scope_kind}' is not a scope", "allowed": list(SCOPES)}
            )
        limit = max(1, min(limit, MAX_LIMIT))

        ledger = StepLedger(conn, s)
        if scope_kind is not None and scope_id is not None:
            steps = ledger.list_for(scope_kind, scope_id)
            if state is not None:
                steps = [step for step in steps if step.state == state]
            steps = steps[:limit]
        elif state is not None:
            steps = ledger.list_by_state(state, limit=limit)
        else:
            steps = ledger.list_recent(limit=limit)
        return {"steps": [step.model_dump(mode="json") for step in steps]}

    # ------------------------------------------------------------ re-run (PR-3)
    def _rerun(conn, step: StepDef, scope_id: str) -> dict:
        try:
            result = rerun_step(conn, s, step=step, scope_id=scope_id)
        except RerunRefused as exc:
            raise HTTPException(409, detail=exc.detail()) from None
        conn.commit()
        return result

    @router.post("/api/uploads/{upload_id}/steps/{step_key}/rerun", status_code=202)
    def rerun_upload_step(
        upload_id: str,
        step_key: str,
        conn=Depends(get_conn),
        _user: CurrentUser = Depends(require_rerun),
    ) -> dict:
        step = _step_in_scope(step_key, "upload")
        try:
            upload = WorkflowStore(conn, s).get_upload(upload_id)
        except UnknownUpload:
            raise HTTPException(404, detail=f"unknown upload: {upload_id}") from None
        try:
            assert_upload_step_runnable(step, upload)
        except RerunRefused as exc:
            raise HTTPException(409, detail=exc.detail()) from None
        return _rerun(conn, step, upload_id)

    @router.post("/api/batches/{batch_id}/steps/{step_key}/rerun", status_code=202)
    def rerun_batch_step(
        batch_id: str,
        step_key: str,
        conn=Depends(get_conn),
        _user: CurrentUser = Depends(require_rerun),
    ) -> dict:
        step = _step_in_scope(step_key, "batch")
        if WorkflowStore(conn, s).get_run(batch_id) is None:
            raise HTTPException(404, detail=f"unknown batch: {batch_id}")
        return _rerun(conn, step, batch_id)

    @router.post(
        "/api/feeds/{feed}/mapping-versions/{version}/steps/{step_key}/rerun", status_code=202
    )
    def rerun_mapping_version_step(
        feed: str,
        version: int,
        step_key: str,
        conn=Depends(get_conn),
        _user: CurrentUser = Depends(require_rerun),
    ) -> dict:
        step = _step_in_scope(step_key, "feed_version")
        if WorkflowStore(conn, s).get_mapping_version(feed, version) is None:
            raise HTTPException(404, detail=f"unknown mapping version: {feed} v{version}")
        return _rerun(conn, step, feed_version_scope(feed, version))

    return router
