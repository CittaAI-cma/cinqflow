"""The step ledger against a real Postgres: generations, attempts, gates, and
the worker loop's bookkeeping around a handler that raises or refuses."""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.queue.queue import Queue
from cinqflow.queue.worker import run_once
from cinqflow.workers import interpret_upload, land_bronze
from cinqflow.workflow.store import StepLedger, UnknownUpload, WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

UPLOAD = "6f0c0000-0000-0000-0000-00000000aaaa"


@pytest.fixture
def ledger(conn, settings) -> StepLedger:
    return StepLedger(conn, settings)


# ------------------------------------------------------------ generations


def test_queued_then_start_is_one_generation(ledger):
    queued = ledger.queued(
        "upload", UPLOAD, "profile", message_id="9e7a0000-0000-0000-0000-000000000001"
    )
    assert (queued.state, queued.generation, queued.attempts) == ("pending", 1, 0)
    assert queued.started_ts is None

    started = ledger.start("upload", UPLOAD, "profile")
    assert started.step_run_id == queued.step_run_id
    assert (started.state, started.generation, started.attempts) == ("running", 1, 1)
    assert started.message_id == "9e7a0000-0000-0000-0000-000000000001"  # kept, not cleared
    assert started.started_ts is not None


def test_start_without_a_queued_row_still_opens_generation_one(ledger):
    started = ledger.start("upload", UPLOAD, "profile")
    assert (started.state, started.generation, started.attempts) == ("running", 1, 1)


def test_a_retry_after_failure_is_the_same_generation_with_another_attempt(ledger):
    first = ledger.start("upload", UPLOAD, "profile")
    failed = ledger.fail(first.step_run_id, "ParseError: bad bytes")
    assert failed.state == "failed"
    assert failed.error == "ParseError: bad bytes"
    assert failed.finished_ts is not None

    again = ledger.start("upload", UPLOAD, "profile")
    assert again.step_run_id == first.step_run_id
    assert (again.state, again.generation, again.attempts) == ("running", 1, 2)
    assert again.error is None and again.finished_ts is None


def test_running_a_finished_step_again_is_a_new_generation(ledger):
    first = ledger.start("upload", UPLOAD, "land")
    done = ledger.finish(first.step_run_id, artifact_type="batch", artifact_id="b1")
    assert (done.state, done.artifact_type, done.artifact_id) == ("done", "batch", "b1")

    replay = ledger.queued("upload", UPLOAD, "land")
    assert (replay.generation, replay.state) == (2, "pending")
    started = ledger.start("upload", UPLOAD, "land")
    assert started.step_run_id == replay.step_run_id
    assert started.generation == 2

    history = ledger.list_for("upload", UPLOAD)
    assert [(r.step_key, r.generation, r.state) for r in history] == [
        ("land", 1, "done"),
        ("land", 2, "running"),
    ]
    assert ledger.latest("upload", UPLOAD, "land").generation == 2


def test_skipping_marks_an_unreached_step_and_leaves_a_done_one_alone(ledger):
    skipped = ledger.skip_step("upload", UPLOAD, "land", "rejected at G1")
    assert (skipped.state, skipped.error, skipped.generation) == ("skipped", "rejected at G1", 1)

    done = ledger.finish(ledger.start("upload", UPLOAD, "profile").step_run_id)
    assert ledger.skip_step("upload", UPLOAD, "profile", "x").step_run_id == done.step_run_id
    assert ledger.latest("upload", UPLOAD, "profile").state == "done"


# ------------------------------------------------------------------- gates


def test_a_gate_opens_once_and_is_decided_once(ledger):
    opened = ledger.open_gate("upload", UPLOAD, "gate_g1")
    assert opened is not None
    assert (opened.state, opened.attempts) == ("running", 0)
    assert ledger.open_gate("upload", UPLOAD, "gate_g1") is None  # already open

    decided = ledger.decide(
        "upload",
        UPLOAD,
        "gate_g1",
        approved=True,
        approval_id="ap-1",
        approver="lead@x.org",
        note=None,
    )
    assert decided.step_run_id == opened.step_run_id
    assert (decided.state, decided.artifact_type, decided.artifact_id) == (
        "done",
        "approval",
        "ap-1",
    )
    assert decided.finished_ts is not None


def test_a_rejection_is_an_adverse_end_to_the_gate_with_the_decision_on_record(ledger):
    # No open_gate first: the decision itself creates the row when a gate was
    # never opened (an upload interpreted before this ledger existed).
    rejected = ledger.decide(
        "upload",
        UPLOAD,
        "gate_g1",
        approved=False,
        approval_id="ap-2",
        approver="lead@x.org",
        note="Wrong business date.",
    )
    assert rejected.state == "failed"
    assert rejected.error == "rejected by lead@x.org: Wrong business date."
    assert rejected.artifact_id == "ap-2"


# ---------------------------------------------------------------- listing


def test_list_for_orders_by_workflow_step_then_generation(ledger):
    ledger.finish(ledger.start("upload", UPLOAD, "interpret").step_run_id)
    ledger.finish(ledger.start("upload", UPLOAD, "profile").step_run_id)
    ledger.open_gate("upload", UPLOAD, "gate_g1")
    assert [r.step_key for r in ledger.list_for("upload", UPLOAD)] == [
        "profile",
        "interpret",
        "gate_g1",
    ]


def test_list_by_state_finds_failures_across_scopes(ledger):
    ledger.fail(ledger.start("upload", UPLOAD, "profile").step_run_id, "boom")
    ledger.fail(ledger.start("batch", "b9", "analyze").step_run_id, "llm down")
    ledger.finish(ledger.start("batch", "b9", "promote").step_run_id)

    failed = ledger.list_by_state("failed")
    assert sorted((r.scope_kind, r.step_key) for r in failed) == [
        ("batch", "analyze"),
        ("upload", "profile"),
    ]
    assert len(ledger.list_recent(limit=10)) == 3
    assert len(ledger.list_recent(limit=2)) == 2


def test_purge_removes_a_scope_history(ledger):
    ledger.finish(ledger.start("upload", UPLOAD, "profile").step_run_id)
    ledger.finish(ledger.start("batch", "b1", "analyze").step_run_id)
    assert ledger.purge("upload", [UPLOAD]) == 1
    assert ledger.purge("batch", ["b1", "b2"]) == 1
    assert ledger.list_for("upload", UPLOAD) == []
    assert ledger.purge("batch", []) == 0


# ------------------------------------------------- the worker loop's bookkeeping


def _message_state(conn, settings, message_id: str) -> tuple[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT state, attempts FROM {settings.queue_schema}.message WHERE message_id = %s",
            (message_id,),
        )
        row = cur.fetchone()
    return row["state"], row["attempts"]


def test_a_handler_that_raises_leaves_a_failed_step_with_the_error(conn, settings, ledger):
    """The generic failure path: nothing in `interpret_upload` catches
    `UnknownUpload`, so before this ledger the only trace was
    `queue.message.last_error`. Now the step itself says what happened."""
    message_id = Queue(conn, settings).enqueue(
        interpret_upload.TOPIC, {"upload_id": UPLOAD}, dedupe_key="t/interpret/ghost"
    )
    conn.commit()

    with pytest.raises(UnknownUpload):
        run_once(conn, settings)

    step = ledger.latest("upload", UPLOAD, "interpret")
    assert step.state == "failed"
    assert step.error.startswith("UnknownUpload")
    assert step.attempts == 1
    assert step.message_id == message_id
    # The queue's own retry accounting is untouched by the ledger write.
    assert _message_state(conn, settings, message_id) == ("pending", 1)


def test_a_handler_that_refuses_leaves_a_skipped_step_with_the_reason(conn, settings, ledger):
    store = WorkflowStore(conn, settings)
    upload = store.create_upload(
        fingerprint="sha256-" + "0" * 64,
        filename="x.csv",
        file_type="csv",
        size_bytes=3,
        uploader="t",
        source_system="s",
        feed="f",
        domain="d",
        business_date=date(2026, 1, 1),
        landing_key="d/s/f/incoming/2026-01-01/x.csv",
    )
    Queue(conn, settings).enqueue(
        land_bronze.TOPIC, {"upload_id": upload.upload_id}, dedupe_key="t/land/received"
    )
    conn.commit()

    result = run_once(conn, settings)
    assert result["landed"] is False  # `received` is not a landable status

    step = ledger.latest("upload", upload.upload_id, "land")
    assert step.state == "skipped"
    assert "received" in step.error
    assert step.finished_ts is not None
