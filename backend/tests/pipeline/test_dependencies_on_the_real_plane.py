"""CF-V1-E8-03's dependency gate, where it actually has to hold: at the batch
seam, on the real control tables.

    "Given enrollment fails at Silver Raw, when the claims schedule fires, then
     claims hold in Waiting-on-Upstream with the blocking batch linked … and
     everything resumes when enrollment recovers."
    "Start a dependent run when its upstream is failed or on hold — no
     overrides outside the governed exception flow."
    — CF-V1-E8-03

`tests/unit/test_scheduling_dependencies.py` proves the semantics. This proves
that the RUNNER honours them, and — the test that matters — that the decision
computed from real `batch_control` rows changes on its own when the upstream
recovers. A pure-function test cannot show that, because the thing being
claimed is about rows.

Every write rolls back (the `plane` fixture), so the suite leaves nothing
behind.
"""

from __future__ import annotations

import pytest

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.core.registry.golden_fidelis import CONTRACT, DQ_002, FEED, PLAN, landing_key
from cinqflow.core.registry.golden_fidelis import roster_csv as _roster
from cinqflow.core.scheduling import (
    DependencyGraph,
    DependencyHoldError,
    HoldReason,
    decide,
    notice_for,
    recovered,
)
from cinqflow.ports.control_tables import BatchControl
from cinqflow.workers.pipeline import PipelineRunner

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

KEY = landing_key("2026-08-01")
PERIOD = "2026-08-01"
UPSTREAM = "cms-reference-data"
GRAPH = DependencyGraph(upstreams={FEED.feed_id: (UPSTREAM,)})


@pytest.fixture
def bundle(plane: Connection):  # type: ignore[no-untyped-def]
    storage = MemFsStorage()
    control = PostgresControlTables(plane)
    runner = PipelineRunner(
        storage=storage,
        control=control,
        compute=PostgresCompute(plane),
        source_system="fidelis",
    )
    return runner, storage, control


def _file(storage: MemFsStorage):  # type: ignore[no-untyped-def]
    storage.place(KEY, _roster())
    return next(f for f in storage.list_files("enrollments/") if f.key == KEY)


def _run(bundle, **overrides):  # type: ignore[no-untyped-def]
    runner, storage, _ = bundle
    return runner.run(
        _file(storage),
        feed=FEED,
        feed_version=1,
        contract=CONTRACT,
        rules=(DQ_002,),
        plan=PLAN,
        business_date=PERIOD,
        **overrides,
    )


def _upstream(control: PostgresControlTables, state: BatchState) -> BatchControl:
    """Write a real upstream batch row, then read the ruling off the table."""
    from datetime import UTC, datetime

    batch = BatchControl(
        batch_id=f"{UPSTREAM}-{PERIOD}",
        feed_id=UPSTREAM,
        feed_version=1,
        business_date=PERIOD,
        state=BatchState.RECEIVED,
        started_ts=datetime.now(UTC),
    )
    control.open_batch(batch)
    control.update_batch_state(batch.batch_id, state)
    return control.get_batch(batch.batch_id)


def _ruling(control: PostgresControlTables):  # type: ignore[no-untyped-def]
    rows = list(control.list_batches(UPSTREAM)) + list(control.list_batches(FEED.feed_id))
    return decide(feed_id=FEED.feed_id, business_date=PERIOD, graph=GRAPH, batches=rows)


def test_a_held_run_opens_no_batch(bundle, plane: Connection) -> None:  # type: ignore[no-untyped-def]
    """Refused at the same seam the pause is refused at, and before anything is
    read, registered or moved."""
    _, _, control = bundle
    _upstream(control, BatchState.FAILED)
    before = plane.fetch_one(
        "SELECT count(*) FROM control.batch_control WHERE feed_id = %s", (FEED.feed_id,)
    )[0]

    with pytest.raises(DependencyHoldError, match="Nothing downstream runs"):
        _run(bundle, release=_ruling(control))

    after = plane.fetch_one(
        "SELECT count(*) FROM control.batch_control WHERE feed_id = %s", (FEED.feed_id,)
    )[0]
    assert after == before, "a held feed must not open a batch"


def test_the_hold_names_the_blocking_batch_from_the_real_table(bundle) -> None:  # type: ignore[no-untyped-def]
    _, _, control = bundle
    upstream = _upstream(control, BatchState.FAILED)
    ruling = _ruling(control)

    assert ruling.blockers[0].reason is HoldReason.UPSTREAM_FAILED
    assert ruling.blockers[0].batch_id == upstream.batch_id
    assert ruling.batch_state is BatchState.BLOCKED

    notice = notice_for(ruling)
    assert notice is not None and notice.blocking_batch_ids == (upstream.batch_id,)


def test_everything_resumes_when_the_upstream_recovers(bundle) -> None:  # type: ignore[no-untyped-def]
    """THE CLAIM, ON REAL ROWS.

    Nobody cleared a flag between these two rulings. The upstream's row changed
    and the answer changed with it — which is what makes "release held work
    automatically" a property of the design rather than a background job.
    """
    _, _, control = bundle
    upstream = _upstream(control, BatchState.FAILED)
    held = _ruling(control)
    assert not held.may_run

    control.update_batch_state(upstream.batch_id, BatchState.COMPLETED)
    released = _ruling(control)

    assert released.may_run
    assert recovered(held, released)

    outcome = _run(bundle, release=released)
    assert outcome.state is BatchState.COMPLETED
    assert Layer.SILVER_RAW in outcome.stages_completed


def test_a_resume_is_never_held_by_an_upstream_that_broke_afterwards(bundle) -> None:  # type: ignore[no-untyped-def]
    """Holding an in-flight batch would strand it halfway through the spine
    with no way to finish or fail it."""
    _, _, control = bundle
    _upstream(control, BatchState.COMPLETED)
    first = _run(bundle, release=_ruling(control))
    assert first.state is BatchState.COMPLETED

    upstream = control.get_batch(f"{UPSTREAM}-{PERIOD}")
    control.update_batch_state(upstream.batch_id, BatchState.FAILED)
    held = _ruling(control)
    assert not held.may_run

    resumed = _run(
        bundle,
        release=held,
        resume_from=Layer.SILVER_RAW,
        batch_id=first.batch_id,
    )
    assert resumed.batch_id == first.batch_id
