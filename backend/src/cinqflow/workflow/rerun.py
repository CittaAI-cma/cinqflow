"""Selective re-run (PR-3): the one function every re-run route and the
`/retry` alias call.

Re-running a step is enqueueing its topic again - with a fresh dedupe key, so
the queue's UNIQUE no longer swallows a legitimate repeat (the
`forward-flow-adoption.md §6.5` gap: promotion could never be re-queued), and
with a new ledger generation, so the history shows a second run rather than a
retry of the first. What is re-queued is exactly what the last generation was
asked to do - the queue message's own payload - so a preview re-run samples
the same batch with the same selector and a promotion re-run rebuilds the same
batch under the same version. Only when no message is on record (a step never
queued, or a pre-ledger run) is a minimal payload rebuilt from the scope.

Refused, with the reason (`RerunRefused` -> 409 at the API):

- a gate: a person decides once, and a rejected G1 is terminal;
- a step whose current generation is `pending` or `running` (`RERUNNABLE`,
  workflow/dag.py): it is already going to run;
- a step whose last message the queue will still retry on its own (`pending`
  or `claimed`, attempts below the maximum): re-queueing would run the same
  work twice for one failure. Once the message is `dead` - or `done`, because
  the handler recorded the failure and returned - a re-run is the only way on;
- an upload step the upload's own status makes impossible (re-profiling a file
  already approved at G1; re-landing one never approved) - the worker would
  refuse or hit an illegal transition, so the API says so first.

The capability check (`can_rerun_steps`) and the HTTP shape live in
`api/routers/steps.py`; nothing here knows about a request.
"""

from __future__ import annotations

from typing import Any

import psycopg

from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.dag import RERUNNABLE, StepDef, parse_feed_version_scope
from cinqflow.workflow.models import Upload
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import StepLedger, WorkflowStore


class RerunRefused(Exception):
    def __init__(self, message: str, *, status: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.hint = hint

    def detail(self) -> dict[str, Any]:
        """The API's 409 body: `message`, plus `status`/`hint` when they help."""
        out: dict[str, Any] = {"message": self.message}
        if self.status:
            out["status"] = self.status
        if self.hint:
            out["hint"] = self.hint
        return out


#: Which upload statuses each upload-scoped step can sensibly run from. Mirrors
#: what the workers themselves check (`profile_upload`, `interpret_upload`,
#: `land_bronze._RUNNABLE`) and `LEGAL_TRANSITIONS` - stated here so the refusal
#: happens at the request, with a reason, instead of as a worker exception.
_UPLOAD_STEP_STATUSES: dict[str, frozenset[UploadStatus]] = {
    "profile": frozenset(
        {
            UploadStatus.RECEIVED,
            UploadStatus.PROFILING,
            UploadStatus.PROFILED,
            UploadStatus.PROFILE_FAILED,
            UploadStatus.INTERPRETING,
            UploadStatus.INTERPRET_FAILED,
            UploadStatus.INTERPRETED,
        }
    ),
    "interpret": frozenset(
        {
            UploadStatus.PROFILED,
            UploadStatus.INTERPRETING,
            UploadStatus.INTERPRET_FAILED,
            UploadStatus.INTERPRETED,
        }
    ),
    # `LANDED` is the documented replay (states.py): a new batch from the
    # preserved original; Bronze is append-only, so the earlier one stays.
    "land": frozenset(
        {
            UploadStatus.APPROVED,
            UploadStatus.LANDING,
            UploadStatus.LAND_FAILED,
            UploadStatus.LANDED,
        }
    ),
}


def assert_upload_step_runnable(step: StepDef, upload: Upload) -> None:
    allowed = _UPLOAD_STEP_STATUSES.get(step.key)
    if allowed is not None and upload.status not in allowed:
        raise RerunRefused(
            f"'{step.key}' cannot run for an upload in status '{upload.status}'",
            status=str(upload.status),
            hint=f"'{step.key}' runs from: {', '.join(sorted(allowed))}",
        )


def default_payload(step: StepDef, scope_id: str, store: WorkflowStore) -> dict[str, Any]:
    """What to enqueue when no earlier message is on record. Minimal on purpose:
    every worker resolves the rest from the store."""
    if step.scope == "upload":
        return {"upload_id": scope_id}
    if step.key == "analyze":
        return {"batch_id": scope_id}
    if step.key == "preview":
        feed, version = parse_feed_version_scope(scope_id)
        return {"feed": feed, "version": version}
    # promote: the batch's own promotion run names the version; failing that,
    # the feed's latest approved mapping. Without either there is nothing an
    # approved decision authorised.
    promotion = store.get_run(scope_id, kind="promote_silver")
    if promotion is not None and promotion.mapping_version is not None:
        return {"feed": promotion.feed, "version": promotion.mapping_version, "batch_id": scope_id}
    landing = store.get_run(scope_id, kind="land_bronze")
    approved = store.latest_mapping_version(landing.feed, status="approved") if landing else None
    if landing is None or approved is None:
        raise RerunRefused(
            "this batch has never been promoted and its feed has no approved mapping",
            hint="approve a mapping version at G2 first",
        )
    return {"feed": landing.feed, "version": approved.version, "batch_id": scope_id}


def rerun_step(
    conn: psycopg.Connection,
    settings: Settings | None,
    *,
    step: StepDef,
    scope_id: str,
) -> dict[str, Any]:
    """Queue a new generation of `step` for `scope_id`. Does not commit."""
    s = settings or get_settings()
    if step.gate or step.topic is None:
        raise RerunRefused(
            f"'{step.key}' is a decision, not a step to re-run",
            hint="a person decides a gate once; a rejected G1 is terminal",
        )

    store = WorkflowStore(conn, s)
    ledger = StepLedger(conn, s)
    queue = Queue(conn, s)

    latest = ledger.latest(step.scope, scope_id, step.key)
    if latest is not None:
        if latest.state not in RERUNNABLE[step.key]:
            raise RerunRefused(
                f"'{step.key}' is {latest.state} - wait for it to finish", status=latest.state
            )
        if latest.message_id is not None:
            message = queue.state_of(latest.message_id)
            if message is not None and message["state"] in ("pending", "claimed"):
                raise RerunRefused(
                    f"the queue will retry '{step.key}' on its own "
                    f"(attempt {message['attempts']} of {queue.max_attempts})",
                    status=message["state"],
                    hint="re-run becomes available once the message is dead",
                )

    payload = None
    if latest is not None and latest.message_id is not None:
        payload = queue.payload_of(latest.message_id)
    if payload is None:
        payload = default_payload(step, scope_id, store)

    generation = latest.generation + 1 if latest is not None else 1
    message_id = queue.enqueue(
        step.topic, payload, dedupe_key=f"{step.topic}/rerun/{scope_id}/g{generation}"
    )
    if message_id is None:  # pragma: no cover - the generation makes the key unique
        raise RerunRefused(f"a re-run of '{step.key}' is already queued", status="pending")
    step_run = ledger.rerun(step.scope, scope_id, step.key, message_id=message_id)
    return {
        "scope_kind": step.scope,
        "scope_id": scope_id,
        "step": step.key,
        "generation": step_run.generation,
        "step_run_id": step_run.step_run_id,
        "queued": step.topic,
    }
