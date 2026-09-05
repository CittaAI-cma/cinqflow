"""How the worker loop reads a handler's return dict into a ledger outcome.
Handlers already speak three ways (a recorded failure, a refusal, a result with
the id of what they made); this is the one place that vocabulary is decoded."""

from __future__ import annotations

from cinqflow.queue.worker import ledger_outcome
from cinqflow.workflow.dag import STEPS


def test_a_recorded_error_is_a_failed_step():
    state, artifact_type, artifact_id, error = ledger_outcome(
        STEPS["land"], {"upload_id": "u", "status": "land_failed", "error": "LandingFailure: x"}
    )
    assert (state, artifact_type, artifact_id) == ("failed", None, None)
    assert error == "LandingFailure: x"


def test_a_failed_status_without_error_text_is_still_failed():
    state, _, _, error = ledger_outcome(STEPS["profile"], {"status": "profile_failed"})
    assert state == "failed"
    assert error == "profile_failed"


def test_a_refusal_is_skipped_with_its_reason():
    state, _, _, error = ledger_outcome(
        STEPS["analyze"], {"batch_id": "b", "analysed": False, "reason": "run is failed"}
    )
    assert (state, error) == ("skipped", "run is failed")

    state, _, _, error = ledger_outcome(
        STEPS["land"], {"upload_id": "u", "status": "received", "landed": False}
    )
    assert state == "skipped"
    assert "received" in error


def test_success_carries_the_artifact_the_step_made():
    assert ledger_outcome(STEPS["profile"], {"profile_id": "p1", "status": "profiled"}) == (
        "done",
        "profile",
        "p1",
        None,
    )
    assert ledger_outcome(STEPS["interpret"], {"interpretation_id": "i1"}) == (
        "done",
        "interpretation",
        "i1",
        None,
    )
    assert ledger_outcome(STEPS["land"], {"landed": True, "batch_id": "b1"}) == (
        "done",
        "batch",
        "b1",
        None,
    )
    assert ledger_outcome(STEPS["analyze"], {"analysed": True, "proposal_id": "pr1"}) == (
        "done",
        "proposal",
        "pr1",
        None,
    )
    assert ledger_outcome(STEPS["preview"], {"previewed": True, "preview_id": "pv1"}) == (
        "done",
        "preview",
        "pv1",
        None,
    )
    assert ledger_outcome(STEPS["promote"], {"promoted": True, "batch_id": "b1"}) == (
        "done",
        "batch",
        "b1",
        None,
    )


def test_success_without_an_id_is_done_with_no_artifact():
    assert ledger_outcome(STEPS["land"], {"landed": True}) == ("done", None, None, None)


def test_a_proposal_that_failed_after_the_profile_stood_is_failed_not_done():
    # analyze_bronze keeps the deterministic Bronze profile and reports the AI
    # failure in `error`; the step is failed - the analyst has no proposal.
    state, _, _, error = ledger_outcome(
        STEPS["analyze"],
        {"batch_id": "b", "analysed": True, "proposal": None, "error": "TimeoutError: llm"},
    )
    assert state == "failed"
    assert error.startswith("TimeoutError")
