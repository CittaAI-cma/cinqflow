"""Read-only views over the workflow declaration and the step ledger.

`GET /api/workflow` is the declaration itself (`workflow/dag.py`), so the
frontend derives its step list from the backend instead of keeping a copy.
`GET /api/steps` is the ledger, filtered - `?state=failed` is the platform
persona's "needs attention" list (PR-4 renders it); `?scope_kind=&scope_id=`
is one object's history across generations. Nothing here changes state:
re-running a step is PR-3's endpoint, capability-gated there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException

from cinqflow.settings import Settings
from cinqflow.workflow.dag import SCOPES, STEP_STATES, as_dicts
from cinqflow.workflow.store import StepLedger

MAX_LIMIT = 500


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    router = APIRouter()

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

    return router
