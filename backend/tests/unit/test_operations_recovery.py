"""CF-V2-E8-04 — the four recoveries, and the proof each one cannot double-load.

    "Allow a recovery that would double-load data — every path proves
     idempotency before executing."
    — the documented don't with the sharpest teeth in Wave 2

The tests are grouped by the way the recovery goes wrong, because that is what
`prove_idempotent` enumerates: a second copy wearing a new batch id, a replay
re-appending to an append-only layer, and a backdate silently doubling a period
somebody already loaded.
"""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.core.model.vocabulary import Layer
from cinqflow.core.operations.actions import DoubleLoadError, OpsAction
from cinqflow.core.operations.recovery import (
    ReplayMode,
    backdate,
    reprocess_batch,
    reprocess_failed_only,
    restart_from,
)

pytestmark = pytest.mark.unit

BATCH = "1244"
FEED = "fidelis-downstate-roster"


# ── the happy path the story is written around ───────────────────────────────
def test_exactly_the_quarantined_records_re_enter_and_the_batch_id_is_unchanged() -> None:
    """ "full reruns of 22-million-row batches to fix 200 records is the waste
    we are ending"."""
    plan = reprocess_failed_only(
        batch_id=BATCH, feed_id=FEED, record_keys=[f"M-{n}" for n in range(214)]
    )
    plan.prove_idempotent()

    assert plan.row_count == 214
    assert plan.start_stage is Layer.SILVER_RAW
    # IN_PLACE, so the ledger shows ONE episode rather than two batches —
    # which is what "the batch's ledger shows the whole episode" means.
    assert plan.mode is ReplayMode.IN_PLACE
    assert plan.batch_id == BATCH


def test_reprocessing_nothing_is_refused_rather_than_quietly_rerunning_everything() -> None:
    with pytest.raises(DoubleLoadError, match="say what you mean"):
        reprocess_failed_only(batch_id=BATCH, feed_id=FEED, record_keys=[])


def test_the_preview_states_the_scope_and_the_cost_before_the_button() -> None:
    plan = reprocess_failed_only(
        batch_id=BATCH, feed_id=FEED, record_keys=[f"M-{n}" for n in range(214)]
    )
    text = plan.preview().explain()
    assert "214 rows re-enter" in text
    assert "quarantined records only" in text
    assert "an approval identifier is required" in text


# ── condition 1 · a second copy wearing a different batch id ─────────────────
def test_a_reprocess_names_the_batch_it_supersedes() -> None:
    """A new batch id keeps both versions in the ledger, which is what makes
    "what did this look like before the fix" answerable four months later."""
    plan = reprocess_batch(batch_id=BATCH, feed_id=FEED, rows=9_992, new_batch_id="1301")
    plan.prove_idempotent()
    assert plan.mode is ReplayMode.SUPERSEDING
    assert plan.supersedes == BATCH


# ── condition 2 · re-appending to an append-only layer ───────────────────────
def test_an_in_place_replay_starting_at_bronze_is_refused_before_executing() -> None:
    """Bronze is append-only and the database would refuse it. Refuse it here,
    where the message tells the operator what to do instead."""
    with pytest.raises(DoubleLoadError, match="append-only"):
        restart_from(batch_id=BATCH, feed_id=FEED, stage=Layer.BRONZE, rows=10).prove_idempotent()


def test_landing_is_not_a_restart_point() -> None:
    with pytest.raises(DoubleLoadError, match="fingerprint refusal"):
        restart_from(batch_id=BATCH, feed_id=FEED, stage=Layer.LANDING, rows=10)


def test_a_restart_from_silver_raw_is_permitted() -> None:
    plan = restart_from(batch_id=BATCH, feed_id=FEED, stage=Layer.SILVER_RAW, rows=9_992)
    plan.prove_idempotent()
    assert plan.mode is ReplayMode.IN_PLACE
    assert Layer.LANDING not in plan.stages


# ── condition 3 · the backdate, and the period that was never loaded ─────────
def test_a_backdate_over_an_existing_period_demands_an_explicit_decision() -> None:
    """ "shows precisely which existing batches overlap and requires an explicit
    supersede decision — no accidental double-count"."""
    plan = backdate(
        feed_id=FEED,
        business_date=date(2026, 7, 1),
        new_batch_id="1301",
        rows=100,
        overlapping=[BATCH],
    )
    with pytest.raises(DoubleLoadError, match="explicit supersede"):
        plan.prove_idempotent()
    assert plan.preview().requires_supersede_decision


def test_an_acknowledged_supersede_is_permitted() -> None:
    backdate(
        feed_id=FEED,
        business_date=date(2026, 7, 1),
        new_batch_id="1301",
        rows=100,
        overlapping=[BATCH],
        supersede_acknowledged=True,
    ).prove_idempotent()


def test_a_backdate_onto_a_period_nobody_ever_loaded_is_permitted() -> None:
    """THE REGRESSION FOR A REAL DEFECT.

    A backdate is SUPERSEDING by nature, but a period that was never processed
    has nothing to supersede. Refusing it as "superseding but names no batch"
    made a legitimate recovery unreachable — and an operator with no button
    goes back to the manual control-row surgery this story exists to end.
    """
    plan = backdate(feed_id=FEED, business_date=date(2026, 7, 1), new_batch_id="1301", rows=100)
    plan.prove_idempotent()

    assert plan.overlapping_batches == ()
    assert plan.supersedes == ""
    assert not plan.replaces_a_period
    assert not plan.preview().requires_supersede_decision


def test_a_reprocess_always_replaces_a_period_even_with_no_overlap_list() -> None:
    """The exemption is BACKDATE-shaped and does not leak.

    A reprocess names the batch it replaces by definition, so a reprocess with
    an empty `supersedes` is still the duplicate-roster incident.
    """
    plan = reprocess_batch(batch_id="", feed_id=FEED, rows=10, new_batch_id="1301")
    assert plan.replaces_a_period
    with pytest.raises(DoubleLoadError, match="second copy"):
        plan.prove_idempotent()


# ── the era this ends ────────────────────────────────────────────────────────
def test_every_recovery_action_reloads_data_and_so_must_prove_itself() -> None:
    for plan in (
        restart_from(batch_id=BATCH, feed_id=FEED, stage=Layer.SILVER_RAW, rows=1),
        reprocess_batch(batch_id=BATCH, feed_id=FEED, rows=1, new_batch_id="1301"),
        reprocess_failed_only(batch_id=BATCH, feed_id=FEED, record_keys=["M-1"]),
        backdate(feed_id=FEED, business_date=date(2026, 7, 1), new_batch_id="1301", rows=1),
    ):
        assert plan.action.reloads_data
        assert plan.action in set(OpsAction)
