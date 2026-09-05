"""Consumer loop. Refuses unknown topics before claiming anything.

Also the one place a worker step is opened and closed in the step ledger
(`workflow/dag.py`, `StepLedger`): `running` is committed before the handler
runs, and afterwards the handler's return dict is read into `done` (with the
artifact it made), `failed` (a failure it caught and recorded) or `skipped`
(a refusal - the scope was not in a runnable state). A handler that raises
leaves a `failed` step with the exception text *before* `Queue.claim` records
the message failure and re-raises. Handlers stay thin: none of them carries
ledger code (structure.md boundary 5).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg

from cinqflow.db import connect
from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings, get_settings
from cinqflow.workers import (
    analyze_bronze,
    interpret_upload,
    land_bronze,
    profile_upload,
    promote_silver,
    reject_upload,
    run_preview,
)
from cinqflow.workflow.dag import (
    StepDef,
    StepState,
    downstream_gate,
    scope_id_for,
    step_for_topic,
)
from cinqflow.workflow.store import StepLedger

log = logging.getLogger(__name__)

Handler = Callable[[psycopg.Connection, dict], dict]


def handlers(settings: Settings) -> dict[str, Handler]:
    """Every topic the platform knows how to run."""
    return {
        profile_upload.TOPIC: lambda conn, payload: profile_upload.handle(conn, payload, settings),
        interpret_upload.TOPIC: lambda conn, payload: interpret_upload.handle(
            conn, payload, settings
        ),
        land_bronze.TOPIC: lambda conn, payload: land_bronze.handle(conn, payload, settings),
        reject_upload.TOPIC: lambda conn, payload: reject_upload.handle(conn, payload, settings),
        analyze_bronze.TOPIC: lambda conn, payload: analyze_bronze.handle(conn, payload, settings),
        run_preview.TOPIC: lambda conn, payload: run_preview.handle(conn, payload, settings),
        promote_silver.TOPIC: lambda conn, payload: promote_silver.handle(conn, payload, settings),
    }


class NoHandler(Exception):
    pass


def run_once(conn: psycopg.Connection, settings: Settings | None = None) -> dict | None:
    """Claim and process at most one message. Returns its result, or None."""
    s = settings or get_settings()
    registry = handlers(s)
    queue = Queue(conn, s)
    with queue.claim(list(registry)) as message:
        if message is None:
            return None
        handler = registry.get(message.topic)
        if handler is None:  # pragma: no cover - claim() filtered by topic
            raise NoHandler(message.topic)
        log.info("handling %s (attempt %s)", message.topic, message.attempts)
        step = step_for_topic(message.topic)
        if step is None:  # housekeeping (upload.reject): not a step of the run
            return handler(conn, message.payload)
        return _run_step(conn, s, step, message.message_id, message.payload, handler)


#: What a finished handler produced, per step: (artifact_type, key in the result).
_ARTIFACT: dict[str, tuple[str, str]] = {
    "profile": ("profile", "profile_id"),
    "interpret": ("interpretation", "interpretation_id"),
    "land": ("batch", "batch_id"),
    "analyze": ("proposal", "proposal_id"),
    "preview": ("preview", "preview_id"),
    "promote": ("batch", "batch_id"),
}

#: A handler that looked and declined says so with one of these set to False.
_REFUSALS: tuple[str, ...] = ("landed", "analysed", "previewed", "promoted")


def ledger_outcome(
    step: StepDef, result: dict
) -> tuple[StepState, str | None, str | None, str | None]:
    """(state, artifact_type, artifact_id, error) read off a handler's return.

    Handlers already speak three ways: a failure they caught and recorded
    (`error`, or a `*_failed` status), a refusal to act on a scope that is not
    in a runnable state (`<verb>: False`, usually with a `reason`), or success
    carrying the id of what they made. Nothing here reads the database."""
    if result.get("error"):
        return "failed", None, None, str(result["error"])
    status = str(result.get("status") or "")
    if status.endswith("_failed"):
        return "failed", None, None, status
    for verb in _REFUSALS:
        if result.get(verb) is False:
            reason = result.get("reason") or (
                f"refused in status {status}" if status else f"{verb} = false"
            )
            return "skipped", None, None, str(reason)
    artifact_type, key = _ARTIFACT.get(step.key, (None, None))
    artifact_id = result.get(key) if key else None
    if artifact_id is None:
        return "done", None, None, None
    return "done", artifact_type, str(artifact_id), None


def _run_step(
    conn: psycopg.Connection,
    s: Settings,
    step: StepDef,
    message_id: str,
    payload: dict,
    handler: Handler,
) -> dict:
    """The ledger bookkeeping around one handler call (module docstring)."""
    ledger = StepLedger(conn, s)
    scope_id = scope_id_for(step, payload)
    step_run = ledger.start(step.scope, scope_id, step.key, message_id=message_id)
    # Durable before the work begins: a poll sees `running` at once, and a
    # handler's own rollback on its failure path cannot take this with it.
    conn.commit()
    try:
        result = handler(conn, payload)
    except Exception as exc:  # noqa: BLE001 - recorded on the step, then re-raised
        conn.rollback()
        ledger.fail(step_run.step_run_id, f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise
    state, artifact_type, artifact_id, error = ledger_outcome(step, result)
    if state == "done":
        ledger.finish(step_run.step_run_id, artifact_type=artifact_type, artifact_id=artifact_id)
        gate = downstream_gate(step)
        if gate is not None:
            ledger.open_gate(gate.scope, scope_id, gate.key)
    elif state == "failed":
        ledger.fail(step_run.step_run_id, error or state)
    else:
        ledger.skip(step_run.step_run_id, error or "refused")
    # Committed together with the handler's own final writes by `Queue.claim`.
    return result


def drain(conn: psycopg.Connection, settings: Settings | None = None, limit: int = 100) -> int:
    """Process until the queue is empty. Used by tests and by `cinqflow work --once`."""
    processed = 0
    while processed < limit:
        if run_once(conn, settings) is None:
            break
        processed += 1
    return processed


def serve(poll_seconds: float = 1.0) -> None:  # pragma: no cover - long running
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("worker up; topics=%s", ", ".join(handlers(s)))
    while True:
        try:
            with connect(s) as conn:
                if drain(conn, s) == 0:
                    time.sleep(poll_seconds)
        except KeyboardInterrupt:
            log.info("worker down")
            return
        except Exception:
            log.exception("worker loop error; backing off")
            time.sleep(poll_seconds * 5)


if __name__ == "__main__":  # pragma: no cover
    serve()
