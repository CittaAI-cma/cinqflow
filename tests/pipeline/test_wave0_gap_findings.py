"""Wave-0 validation audit (2026-08-29) — Postgres-plane gaps, as executable tests.

Same convention as tests/audit/test_wave0_gap_findings.py: each test states
the REQUIRED behaviour and is marked xfail(strict=True) because the platform
does not deliver it today. Fixing the defect flips the test to XPASS, which
strict mode fails, forcing the marker's removal.
"""

from __future__ import annotations

import pytest

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.landing import LandingOutcome
from cinqflow.core.model.vocabulary import BatchState
from cinqflow.workers.pipeline import PipelineRunner
from tests.pipeline.test_golden_roster import CONTRACT, DQ_002, FEED, KEY, PLAN, _roster

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

wave0_gap = pytest.mark.xfail(strict=True, reason="confirmed Wave-0 gap — remove when fixed")


@pytest.fixture
def bundle(plane: Connection) -> tuple[PipelineRunner, MemFsStorage, PostgresControlTables]:
    storage = MemFsStorage()
    control = PostgresControlTables(plane)
    compute = PostgresCompute(plane)
    return (
        PipelineRunner(storage=storage, control=control, compute=compute, source_system="fidelis"),
        storage,
        control,
    )


def _run(bundle, **overrides):
    runner, storage, _ = bundle
    storage.place(KEY, _roster())
    file = next(f for f in storage.list_files("enrollments/") if f.key == KEY)
    return runner.run(
        file,
        feed=FEED,
        feed_version=1,
        contract=CONTRACT,
        rules=(DQ_002,),
        plan=PLAN,
        business_date="2026-08-01",
        **overrides,
    )


# ── FIXED · the input registry now learns which batch a file fed ────────────
#
# Files are registered with batch_id=None before the batch opens; nothing used
# to back-fill it after open_batch. workers/pipeline.py now calls the new
# `link_input_to_batch` verb right after open_batch.
def test_an_accepted_files_registry_entry_is_linked_to_its_batch(bundle) -> None:
    _runner, storage, control = bundle
    fingerprint = None
    outcome = _run(bundle)
    assert outcome.decision.outcome is LandingOutcome.ACCEPTED
    assert outcome.batch_id is not None

    # The file was moved after processing; find its registry row by fingerprint.
    for entry_fingerprint in [storage.fingerprint(f.key) for f in storage.list_files("")]:
        entry = control.find_input_by_fingerprint(entry_fingerprint)
        if entry is not None:
            fingerprint = entry_fingerprint
            break
    assert fingerprint is not None, "the accepted file must have a registry entry"

    entry = control.find_input_by_fingerprint(fingerprint)
    assert entry is not None
    assert entry.batch_id == outcome.batch_id, (
        f"input_registry.batch_id is {entry.batch_id!r} for the file that fed batch "
        f"{outcome.batch_id} — file→batch lineage is unanswerable from the registry"
    )


# ── FIXED · an unexpected crash no longer strands the batch IN_PROGRESS ──────
#
# run() used to convert only _StageFailureError into a FAILED batch with an
# error row. workers/pipeline.py now also catches any other Exception mid-run,
# records a SYSTEM_ERROR row, marks the batch FAILED, and re-raises — the
# crash still propagates, but the batch reaches a terminal state first.
def test_an_unexpected_crash_does_not_strand_the_batch_in_progress(
    bundle, plane: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, _, _control = bundle

    def boom(**_kwargs) -> None:
        raise RuntimeError("simulated infrastructure crash after Silver Raw load")

    monkeypatch.setattr(runner._compute, "record_recon_history", boom)

    # run() returns a terminal RunOutcome rather than propagating: a real run
    # commits inside ONE transaction (pg_control.commit), so an exception
    # escaping run() would roll back the very FAILED-state and error rows
    # this test checks for.
    outcome = _run(bundle)
    assert outcome.state is BatchState.FAILED
    assert "simulated infrastructure crash" in (outcome.failure or "")

    row = plane.fetch_one(
        "SELECT batch_id, state FROM control.batch_control ORDER BY started_ts DESC LIMIT 1"
    )
    assert row is not None, "the batch was opened before the crash"
    batch_id, state = row
    assert BatchState(state) is not BatchState.IN_PROGRESS, (
        f"batch {batch_id} is stranded IN_PROGRESS after a mid-run crash — no error row, "
        "no terminal state, invisible to reconciliation and to the agent"
    )
