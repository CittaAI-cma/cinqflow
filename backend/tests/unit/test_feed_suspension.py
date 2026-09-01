"""CF-V1-E3-04 — pausing a feed, on the axis it belongs to.

The suite's first section is about a decision rather than a behaviour: PAUSED
is not a lifecycle state, and the tests assert the consequences of that choice
rather than restating it. A paused feed stays PUBLISHED, so "which version was
live in March" still answers; resuming needs no approver, so an operator at 3am
is not looking for a steward.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.suspension import (
    Suspension,
    SuspensionAction,
    SuspensionError,
    current,
    pause,
    resume,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=2)

SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")
ROBOT = Actor(subject="scheduler", actor_type=ActorType.SYSTEM)
FEED = "fidelis-downstate-roster"


# ── pausing is not a lifecycle transition ────────────────────────────────────


def test_paused_is_not_a_lifecycle_state() -> None:
    """THE DECISION, asserted where somebody would come to undo it.

    The story says Draft -> Active -> Paused -> Retired and the obvious
    reading is to add PAUSED to `LifecycleState`. ADR-0006 says there is one
    state machine; adding to it would make un-pausing an approval, so a feed
    could not be restarted at 3am without finding a steward.
    """
    assert "paused" not in {state.value for state in LifecycleState}
    assert "PAUSED" not in LifecycleState.__members__


def test_a_pause_needs_a_reason() -> None:
    """Somebody will find this paused next week and have to decide whether to
    lift it. An unexplained pause is one nobody dares touch."""
    with pytest.raises(SuspensionError, match="needs a reason"):
        pause(FEED, actor=SAM, reason="   ", now=NOW)


def test_resuming_needs_no_reason() -> None:
    """Deliberately asymmetric. Requiring a justification to START a feed
    again would make the safe direction the expensive one — and the row still
    records who did it, which is what accountability actually needs."""
    event = resume(FEED, actor=SAM, now=NOW)
    assert event.action is SuspensionAction.RESUMED
    assert event.reason == ""


def test_no_machine_may_pause_or_resume_a_feed() -> None:
    """Refused on the ACTOR TYPE. A scheduler that could pause a feed on a
    heuristic would be an agent taking production action."""
    with pytest.raises(SuspensionError, match="system actor"):
        pause(FEED, actor=ROBOT, reason="looks quiet", now=NOW)
    with pytest.raises(SuspensionError, match="system actor"):
        resume(FEED, actor=ROBOT, now=NOW)


def test_a_pause_that_has_already_expired_is_refused() -> None:
    with pytest.raises(SuspensionError, match="pauses nothing"):
        pause(FEED, actor=SAM, reason="migration", now=NOW, resumes_after=NOW - timedelta(hours=1))


# ── the ledger ───────────────────────────────────────────────────────────────


def test_a_feed_that_was_never_paused_is_an_ordinary_answer() -> None:
    """Not a missing one — `current` is total over an empty ledger."""
    state = current(FEED, ())
    assert state == Suspension(feed_id=FEED)
    assert state.may_start_new_work(NOW)


def test_the_newest_event_wins() -> None:
    events = (
        pause(FEED, actor=SAM, reason="payer migration", now=NOW),
        resume(FEED, actor=SAM, now=NOW + timedelta(hours=4)),
    )
    assert current(FEED, events).is_paused is False


def test_resuming_writes_a_row_rather_than_deleting_one() -> None:
    """A feed that was paused for six days and a feed that was never paused
    must not look identical afterwards."""
    events = (
        pause(FEED, actor=SAM, reason="payer migration", now=NOW),
        resume(FEED, actor=SAM, now=NOW + timedelta(days=6)),
    )
    assert len(events) == 2
    assert [e.action for e in events] == [SuspensionAction.PAUSED, SuspensionAction.RESUMED]
    # And the history still answers "was this paused on the 3rd?"
    assert current(FEED, events[:1]).is_paused is True


def test_one_feeds_pause_does_not_touch_another() -> None:
    events = (pause("other-feed", actor=SAM, reason="migration", now=NOW),)
    assert current(FEED, events).is_paused is False


# ── stops new work, finishes what is running ─────────────────────────────────


def test_a_paused_feed_starts_no_new_work() -> None:
    state = current(FEED, (pause(FEED, actor=SAM, reason="payer migration", now=NOW),))
    assert not state.may_start_new_work(NOW)
    assert "No new batch will start" in state.explain(NOW)


def test_a_pause_does_not_reach_work_already_running() -> None:
    """The half of the criterion that is easy to get wrong. A pause that
    killed in-flight batches would leave half-loaded silver tables and a
    reconciliation that cannot balance."""
    state = current(FEED, (pause(FEED, actor=SAM, reason="migration", now=NOW),))
    assert state.affects_work_already_running is False
    assert "does not abandon work in progress" in state.explain(NOW)


def test_a_timed_pause_lifts_itself() -> None:
    """Computed, not run by a background job — so a feed paused until Monday
    resumes on Monday even if nothing was running over the weekend to notice.
    A job that has to run for a pause to end is a job whose failure silently
    extends an outage."""
    state = current(
        FEED,
        (pause(FEED, actor=SAM, reason="payer migration", now=NOW, resumes_after=LATER),),
    )
    assert not state.may_start_new_work(NOW)
    assert state.may_start_new_work(LATER)
    assert state.may_start_new_work(LATER + timedelta(days=30))


def test_an_open_ended_pause_says_so() -> None:
    """The incumbent's longest outage was a feed paused "for an hour" and
    unpaused eleven days later by somebody looking for something else."""
    state = current(FEED, (pause(FEED, actor=SAM, reason="migration", now=NOW),))
    assert "with no end date" in state.explain(NOW)


def test_the_explanation_names_the_person_and_the_reason() -> None:
    state = current(FEED, (pause(FEED, actor=SAM, reason="payer migration", now=NOW),))
    explanation = state.explain(NOW)
    assert "Sam Okafor" in explanation
    assert "payer migration" in explanation
