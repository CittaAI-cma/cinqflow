"""CF-V1-E3-04's pause, where it actually has to hold: at the batch seam.

    "pause stops new work, in-flight finishes safely"

`tests/unit/test_feed_suspension.py` proves the semantics. This proves them
against the real runner and the real control tables, because the claim is
about what happens to a BATCH — and a batch is a database row, not a value
object.

The second test is the one that matters. It is easy to write a pause that
stops everything, and a pause that abandoned a batch mid-flight would leave
half-loaded bronze rows and a reconciliation that cannot balance.

Every write rolls back (the `plane` fixture), so the suite leaves nothing
behind and needs no cleanup code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer

# The golden Fidelis fixture, which the Wave-0 pipeline already proves
# byte-exact — so this suite adds a pause to a run that is known to work.
from cinqflow.core.registry.golden_fidelis import CONTRACT, DQ_002, FEED, PLAN, landing_key
from cinqflow.core.registry.golden_fidelis import roster_csv as _roster
from cinqflow.core.registry.suspension import SuspensionAction, pause, resume
from cinqflow.workers.pipeline import FeedPausedError, PipelineRunner

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

KEY = landing_key("2026-08-01")
NOW = datetime.now(UTC)
SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")


@pytest.fixture
def bundle(plane: Connection):  # type: ignore[no-untyped-def]
    storage = MemFsStorage()
    control = PostgresControlTables(plane)
    compute = PostgresCompute(plane)
    runner = PipelineRunner(
        storage=storage, control=control, compute=compute, source_system="fidelis"
    )
    return runner, storage, control


def _file(storage: MemFsStorage, key: str = KEY):  # type: ignore[no-untyped-def]
    storage.place(key, _roster())
    return next(f for f in storage.list_files("enrollments/") if f.key == key)


def _run(bundle, **overrides):  # type: ignore[no-untyped-def]
    runner, storage, _ = bundle
    return runner.run(
        _file(storage),
        feed=FEED,
        feed_version=1,
        contract=CONTRACT,
        rules=(DQ_002,),
        plan=PLAN,
        business_date="2026-08-01",
        **overrides,
    )


def test_a_paused_feed_opens_no_batch(bundle, plane: Connection) -> None:  # type: ignore[no-untyped-def]
    """Refused BEFORE anything is read, registered or moved."""
    _, _, control = bundle
    before = plane.fetch_one("SELECT count(*) FROM control.batch_control")[0]

    suspension = _paused()
    with pytest.raises(FeedPausedError, match="No new batch will start"):
        _run(bundle, suspension=suspension)

    after = plane.fetch_one("SELECT count(*) FROM control.batch_control")[0]
    assert after == before, "a paused feed must not open a batch"
    _ = control


def test_a_paused_feeds_file_is_left_where_it_is(bundle) -> None:  # type: ignore[no-untyped-def]
    """Not moved to `processed`, not moved to `rejected`.

    Moving it and declining to load it would LOSE the delivery, which is the
    opposite of what a pause is for: resuming has to pick the file up on the
    next run.
    """
    runner, storage, _ = bundle
    _file(storage)
    with pytest.raises(FeedPausedError):
        _run(bundle, suspension=_paused())
    assert any(f.key == KEY for f in storage.list_files("enrollments/")), (
        "the file moved — a paused delivery would have been lost"
    )
    _ = runner


def test_the_same_file_runs_once_the_pause_is_lifted(bundle) -> None:  # type: ignore[no-untyped-def]
    """The delivery was not lost, so resuming loads it."""
    with pytest.raises(FeedPausedError):
        _run(bundle, suspension=_paused())

    outcome = _run(bundle, suspension=_resumed())
    assert outcome.state is BatchState.COMPLETED


def test_a_batch_already_running_finishes_while_the_feed_is_paused(
    bundle, plane: Connection
) -> None:  # type: ignore[no-untyped-def]
    """THE HALF THAT IS EASY TO GET WRONG.

    A batch is opened, the feed is paused mid-flight, and the batch is then
    resumed from Silver Raw. It must complete: a pause that abandoned work in
    progress would leave half-loaded bronze rows and a reconciliation that
    cannot balance.
    """
    runner, storage, _ = bundle
    first = _run(bundle)
    assert first.state is BatchState.COMPLETED

    # Paused now, with a batch id in hand — exactly the shape of an operator
    # pausing a feed while a run is under way. The file has been moved to
    # `processed` by the first run, so it is found by name rather than by its
    # landing key.
    file = next(
        f for f in storage.list_files("enrollments/") if f.key.endswith(KEY.rsplit("/", 1)[-1])
    )
    resumed = runner.run(
        file,
        feed=FEED,
        feed_version=1,
        contract=CONTRACT,
        rules=(DQ_002,),
        plan=PLAN,
        business_date="2026-08-01",
        resume_from=Layer.SILVER_RAW,
        batch_id=first.batch_id,
        suspension=_paused(),
    )
    assert resumed.state is BatchState.COMPLETED, "in-flight work must finish"
    assert (
        plane.fetch_one(
            "SELECT state FROM control.batch_control WHERE batch_id = %s", (first.batch_id,)
        )[0]
        == BatchState.COMPLETED.value
    )


def test_a_timed_pause_lets_work_start_again_by_itself(bundle) -> None:  # type: ignore[no-untyped-def]
    """No background job lifts it — the expiry is computed, so a job whose
    failure would silently extend an outage does not exist."""
    lapsed = _paused(resumes_after=NOW - timedelta(seconds=1), paused_at=NOW - timedelta(hours=2))
    outcome = _run(bundle, suspension=lapsed)
    assert outcome.state is BatchState.COMPLETED


# ── the ledger, on the real table ────────────────────────────────────────────


def test_the_suspension_ledger_is_append_only_on_the_real_plane(plane: Connection) -> None:
    """Two rows, and the newest wins. Resuming appends rather than deletes, so
    "was this paused on the 3rd?" survives the resume."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    feed_id = FEED.feed_id

    store.record_suspension(
        pause(feed_id, actor=SAM, reason="payer migration", now=NOW - timedelta(hours=2))
    )
    assert store.current_suspension(feed_id).is_paused is True

    store.record_suspension(resume(feed_id, actor=SAM, now=NOW - timedelta(hours=1)))
    assert store.current_suspension(feed_id).is_paused is False

    ledger = store.list_suspensions(feed_id=feed_id)
    assert [event.action for event in ledger] == [
        SuspensionAction.RESUMED,
        SuspensionAction.PAUSED,
    ]
    assert ledger[1].reason == "payer migration"
    assert ledger[1].actor.subject == SAM.subject


def test_one_feeds_pause_is_invisible_to_another_on_the_real_plane(plane: Connection) -> None:
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    store.record_suspension(pause("other-feed", actor=SAM, reason="migration", now=NOW))
    assert store.current_suspension(FEED.feed_id).is_paused is False


# ── helpers ──────────────────────────────────────────────────────────────────


def _paused(*, resumes_after: datetime | None = None, paused_at: datetime | None = None):  # type: ignore[no-untyped-def]
    from cinqflow.core.registry.suspension import current

    return current(
        FEED.feed_id,
        (
            pause(
                FEED.feed_id,
                actor=SAM,
                reason="Fidelis are re-cutting the extract after a plan merge",
                now=paused_at or NOW,
                resumes_after=resumes_after,
            ),
        ),
    )


def _resumed():  # type: ignore[no-untyped-def]
    from cinqflow.core.registry.suspension import current

    return current(
        FEED.feed_id,
        (
            pause(FEED.feed_id, actor=SAM, reason="migration", now=NOW - timedelta(hours=2)),
            resume(FEED.feed_id, actor=SAM, now=NOW - timedelta(hours=1)),
        ),
    )
