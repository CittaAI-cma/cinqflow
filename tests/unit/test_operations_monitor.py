"""CF-V2-E12-02 — the batch monitor, and the spreadsheet's last day.

    "Given batch #1244 failed overnight, when the operator opens it, then they
     see it failed at Bronze at 03:03 with error BH-AF-002, three errors of
     which two are consequences, zero rows written, and links to the error and
     incident detail."
    "Given a batch has been In Progress far beyond its usual duration, when the
     monitor evaluates it, then it is flagged 'stuck - 3x typical duration'
     rather than sitting quietly green."
    — CF-V2-E12-02

`test_batch_1244_reads_exactly_as_the_story_writes_it` is the acceptance
criterion, assembled from the client's own `BH-AF-002` cascade.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, Layer, StatusWord
from cinqflow.core.operations.monitor import (
    CASCADE_WINDOW,
    BatchFilter,
    SlaState,
    build_batch_view,
    search,
    separate_cascade,
    sla_state,
    typical_duration,
)
from cinqflow.ports.control_tables import BatchControl, ErrorRecord, StageStatus

pytestmark = pytest.mark.unit

FEED = "fidelis-downstate-roster"
BATCH = "1244"
DAY = "2026-08-30"
FAILED_AT = datetime(2026, 8, 30, 3, 3, tzinfo=UTC)
NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def batch(
    state: BatchState = BatchState.FAILED,
    *,
    batch_id: str = BATCH,
    feed_id: str = FEED,
    business_date: str = DAY,
    started: datetime = FAILED_AT,
    completed: datetime | None = None,
) -> BatchControl:
    return BatchControl(
        batch_id=batch_id,
        feed_id=feed_id,
        feed_version=1,
        business_date=business_date,
        state=state,
        started_ts=started,
        completed_ts=completed,
    )


def stage(
    layer: Layer,
    state: BatchState,
    *,
    records_in: int = 0,
    records_out: int = 0,
    quarantined: int = 0,
    drops: int = 0,
) -> StageStatus:
    return StageStatus(
        batch_id=BATCH,
        stage=layer,
        state=state,
        started_ts=FAILED_AT,
        completed_ts=FAILED_AT + timedelta(minutes=1),
        records_in=records_in,
        records_out=records_out,
        quarantined=quarantined,
        attributed_drops=drops,
    )


def error(
    digest: str,
    *,
    seconds: int = 0,
    layer: Layer = Layer.BRONZE,
    category: ErrorCategory = ErrorCategory.SYSTEM,
    rule_id: str | None = None,
    message: str = "",
) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=digest,
        batch_id=BATCH,
        stage=layer,
        category=category,
        message=message or f"error {digest}",
        occurred_ts=FAILED_AT + timedelta(seconds=seconds),
        rule_id=rule_id,
    )


# ── the story, figure for figure ─────────────────────────────────────────────
def test_batch_1244_reads_exactly_as_the_story_writes_it() -> None:
    """The client's own `BH-AF-002` cascade: a task fails on a missing
    parameter, and its two downstream tasks fail on the key it never wrote."""
    view = build_batch_view(
        batch(),
        stages=[
            stage(Layer.LANDING, BatchState.COMPLETED, records_in=10, records_out=10),
            stage(Layer.BRONZE, BatchState.FAILED, records_in=10, records_out=0, drops=10),
        ],
        errors=[
            error(
                "bh-af-002-root",
                rule_id="BH-AF-002",
                message=(
                    "evaluate_bronze_load: required key 'business_date' absent in XCom "
                    "from upstream validate_input"
                ),
            ),
            error("consequence-one", seconds=2),
            error("consequence-two", seconds=4),
        ],
        now=NOW,
    )
    assert view.failed_at is Layer.BRONZE
    assert view.started_ts.strftime("%H:%M") == "03:03"
    assert view.rows_written == 0
    assert len(view.cascade.all) == 3
    assert len(view.cascade.consequences) == 2
    assert view.cascade.first is not None
    assert view.cascade.first.rule_id == "BH-AF-002"
    assert "2 are consequences of the first" in view.cascade.explain()

    told = view.explain()
    assert "failed at bronze at 03:03" in told
    assert "BH-AF-002" in told
    assert "0 rows written" in told


def test_the_error_and_its_consequences_all_carry_links() -> None:
    """ "links to the error and incident detail" — via the platform's own
    address space, so no link scheme was needed."""
    view = build_batch_view(batch(), errors=[error("root"), error("fallout", seconds=1)], now=NOW)
    assert all(e.route for e in view.cascade.all)
    assert view.citation.route.startswith("/operations/control/batch/")


# ── cascade separation ───────────────────────────────────────────────────────
def test_a_distinct_rule_firing_is_a_second_finding_not_a_shadow() -> None:
    """Two DQ rules failing together are two things wrong, not one thing and
    its shadow."""
    cascade = separate_cascade(
        [error("first", rule_id="DQ-002"), error("second", seconds=1, rule_id="DQ-030")]
    )
    assert len(cascade.actionable) == 2
    assert cascade.consequences == ()


def test_an_earlier_layer_cannot_be_fallout_from_a_later_one() -> None:
    """Data does not travel backwards."""
    cascade = separate_cascade(
        [
            error("silver", layer=Layer.SILVER_RAW),
            error("bronze", seconds=1, layer=Layer.BRONZE),
        ]
    )
    assert len(cascade.actionable) == 2


def test_a_failure_outside_the_window_is_its_own_incident() -> None:
    """A wider window absorbs genuinely separate failures into somebody else's
    incident."""
    late = int(CASCADE_WINDOW.total_seconds()) + 60
    cascade = separate_cascade([error("first"), error("much-later", seconds=late)])
    assert len(cascade.actionable) == 2


def test_consequences_are_shown_and_not_hidden() -> None:
    """An operator who sees three errors on the batch and two on the incident
    will go looking for the missing one."""
    cascade = separate_cascade([error("root"), error("fallout", seconds=1)])
    assert len(cascade.all) == 2
    assert cascade.consequences[0].caused_by == "root"
    assert cascade.consequences[0].is_consequence


def test_a_batch_with_no_errors_says_so_plainly() -> None:
    assert "No errors were logged" in separate_cascade([]).explain()


def test_a_single_error_is_not_reported_as_a_cascade() -> None:
    assert "none is a consequence" in separate_cascade([error("only")]).explain()


# ── reconciliation, inline ───────────────────────────────────────────────────
def test_the_record_count_flow_is_visible_on_the_batch() -> None:
    """An operator asking "where did the rows go?" is one click into a monitor,
    and sending them to a separate page is how two views end up disagreeing."""
    view = build_batch_view(
        batch(BatchState.COMPLETED),
        stages=[
            stage(Layer.LANDING, BatchState.COMPLETED, records_in=10_000, records_out=10_000),
            stage(
                Layer.BRONZE,
                BatchState.COMPLETED,
                records_in=10_000,
                records_out=9_992,
                quarantined=8,
            ),
        ],
        now=NOW,
    )
    flow = view.flow()
    assert "landing: 10,000 in · 10,000 out" in flow[0]
    assert "8 quarantined" in flow[1]
    assert view.balances


def test_a_stage_that_does_not_balance_says_unexplained_on_its_own_line() -> None:
    """Incident #11 — 6.67M profiled against 13.76M rows, found by a person
    reading two screens."""
    view = build_batch_view(
        batch(BatchState.COMPLETED),
        stages=[stage(Layer.BRONZE, BatchState.COMPLETED, records_in=13_760, records_out=6_670)],
        now=NOW,
    )
    assert not view.balances
    assert "UNEXPLAINED" in view.flow()[0]
    assert view.stages[0].unexplained == 7_090


# ── the exception: stuck ─────────────────────────────────────────────────────
def _history(minutes: int, runs: int = 6) -> list[BatchControl]:
    return [
        batch(
            BatchState.COMPLETED,
            batch_id=f"h{i}",
            started=FAILED_AT - timedelta(days=i + 1),
            completed=FAILED_AT - timedelta(days=i + 1) + timedelta(minutes=minutes),
        )
        for i in range(runs)
    ]


def test_a_batch_far_beyond_its_usual_duration_does_not_sit_quietly_green() -> None:
    running = batch(BatchState.IN_PROGRESS, started=NOW - timedelta(minutes=40))
    view = build_batch_view(running, history=_history(10), now=NOW)
    assert view.sla is SlaState.STUCK
    assert view.status is StatusWord.NEEDS_ATTENTION
    assert view.typical_duration == timedelta(minutes=10)


def test_a_batch_inside_three_times_typical_is_on_time() -> None:
    running = batch(BatchState.IN_PROGRESS, started=NOW - timedelta(minutes=25))
    view = build_batch_view(running, history=_history(10), now=NOW)
    assert view.sla is SlaState.ON_TIME
    assert view.status is StatusWord.PROCESSING


def test_a_feed_with_too_little_history_is_unknown_not_stuck() -> None:
    """A board that cried stuck on every new feed would be muted inside a
    week."""
    running = batch(BatchState.IN_PROGRESS, started=NOW - timedelta(hours=9))
    view = build_batch_view(running, history=_history(10, runs=3), now=NOW)
    assert view.typical_duration is None
    assert view.sla is SlaState.UNKNOWN
    assert view.status is StatusWord.PROCESSING


def test_typical_duration_is_the_median_so_one_outage_does_not_move_it() -> None:
    """One 40-minute recovery drags a mean far enough that the next slow run
    looks fine — and the whole point is to notice the slow one."""
    history = _history(10, runs=6)
    history[0] = batch(
        BatchState.COMPLETED,
        batch_id="outage",
        started=FAILED_AT - timedelta(days=1),
        completed=FAILED_AT - timedelta(days=1) + timedelta(minutes=400),
    )
    assert typical_duration(history) == timedelta(minutes=10)


def test_only_completed_runs_count_toward_typical() -> None:
    """Including failed ones mixes "how long this takes" with "how long it took
    to give up"."""
    history = [*_history(10), batch(BatchState.FAILED, batch_id="f", completed=None)]
    assert typical_duration(history) == timedelta(minutes=10)


def test_a_finished_batch_is_never_stuck() -> None:
    """Labelling a completed batch stuck after the fact tells an operator
    nothing they can act on."""
    done = batch(BatchState.COMPLETED, completed=FAILED_AT + timedelta(hours=9))
    assert sla_state(done, typical=timedelta(minutes=10), now=NOW) is SlaState.ON_TIME


# ── the first failing stage ──────────────────────────────────────────────────
def test_the_named_stage_is_the_first_one_that_failed() -> None:
    """Naming the furthest stage recorded sends an operator to a layer the data
    never reached."""
    view = build_batch_view(
        batch(),
        stages=[
            stage(Layer.BRONZE, BatchState.FAILED),
            stage(Layer.SILVER_RAW, BatchState.FAILED),
        ],
        now=NOW,
    )
    assert view.failed_at is Layer.BRONZE


# ── history is a filter, not a project ───────────────────────────────────────
def test_all_failed_fidelis_batches_in_july_is_one_filter() -> None:
    views = [
        build_batch_view(
            batch(BatchState.FAILED, batch_id="jul-1", business_date="2026-07-04"), now=NOW
        ),
        build_batch_view(
            batch(BatchState.COMPLETED, batch_id="jul-2", business_date="2026-07-11"), now=NOW
        ),
        build_batch_view(
            batch(BatchState.FAILED, batch_id="aug-1", business_date="2026-08-02"), now=NOW
        ),
        build_batch_view(
            batch(BatchState.FAILED, batch_id="other", feed_id="uhc", business_date="2026-07-20"),
            now=NOW,
        ),
    ]
    criteria = BatchFilter(
        feed_id=FEED,
        states=frozenset({BatchState.FAILED}),
        from_business_date="2026-07-01",
        to_business_date="2026-07-31",
    )
    assert [v.batch_id for v in search(views, criteria)] == ["jul-1"]
    assert "failed" in criteria.describe()


def test_an_empty_filter_matches_everything() -> None:
    views = [build_batch_view(batch(), now=NOW)]
    assert len(search(views, BatchFilter())) == 1
    assert BatchFilter().describe() == "everything"


def test_results_are_newest_first() -> None:
    views = [
        build_batch_view(batch(batch_id="old", business_date="2026-07-01"), now=NOW),
        build_batch_view(batch(batch_id="new", business_date="2026-08-01"), now=NOW),
    ]
    assert [v.batch_id for v in search(views, BatchFilter())] == ["new", "old"]


# ── the don't ────────────────────────────────────────────────────────────────
def test_this_module_offers_no_mutation() -> None:
    """ "Offer any mutation from this screen except through the governed action
    surface." A "quick retry" added here would be the one path around all of
    its approvals — so the module's own surface is asserted."""
    import cinqflow.core.operations.monitor as module

    forbidden = {"retry", "pause", "resume", "acknowledge", "assign", "execute", "cancel"}
    exported = {name for name in dir(module) if not name.startswith("_")}
    assert not (exported & forbidden)
