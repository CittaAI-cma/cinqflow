"""CF-V1-E7-04 — the governed queue, not a shrug.

    "Given a BA writes a cross-feed timing rule the generator scores at 62%,
     when she submits, then the rule lands in the technical review queue with
     both texts and the score; she sees 'Needs technical review — cross-feed
     timing logic', and nothing runs anywhere."
    "Given the reviewer determines the request needs genuinely custom
     engineering, when they mark it as such, then a linked engineering item is
     created carrying the full context, and the BA is told what happens next —
     the request never evaporates."
    — CF-V1-E7-04

Two tests carry the story. `test_every_below_threshold_candidate_is_routed`
is the measurable — 100% routed, zero silent publications — driven over
generated confidences rather than asserted on one example. And
`test_the_sentence_survives_every_exit` is the don't: intent is preserved
verbatim through every correction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, StatusWord
from cinqflow.core.rules import Check, CheckKind, RuleSpec
from cinqflow.core.rules.review import (
    NEEDS_TECHNICAL_REVIEW,
    Candidate,
    ReviewError,
    ReviewReason,
    ReviewState,
    awaiting_review,
    correct,
    corrections_for_eval,
    escalate,
    guard_publication,
    route,
    unrouted,
    withdraw,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
FLOOR = 0.80

BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Dana")
REVIEWER = Actor(
    subject="dev-platform@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Mei"
)
AGENT = Actor(subject="rule-authoring", actor_type=ActorType.AI)

CROSS_FEED = "Claims must arrive within 3 days of the enrollment file for the same month"
DOB = "Member date of birth cannot be in the future"

NOT_NULL = Check(kind=CheckKind.NOT_NULL, column="date_of_birth")


def _route(*candidates: Candidate):  # type: ignore[no-untyped-def]
    return route(candidates, feed_id=FEED, floor=FLOOR, now=NOW)


# ── the happy path ───────────────────────────────────────────────────────────
def test_a_sixty_two_percent_rule_lands_in_the_queue_with_both_texts() -> None:
    """The story's own example, figure for figure."""
    (review,) = _route(
        Candidate(
            stated=CROSS_FEED,
            confidence=0.62,
            machine_reading="Compares the claims file's arrival to another feed's.",
            unsupported_reason="cross-feed timing logic",
        )
    )
    assert review.reason is ReviewReason.UNSUPPORTED
    assert review.confidence == 0.62
    assert review.state is ReviewState.QUEUED
    assert review.state.status_word is StatusWord.NEEDS_REVIEW

    ba_text, machine_text = review.side_by_side()
    assert ba_text == CROSS_FEED
    assert machine_text == "Compares the claims file's arrival to another feed's."


def test_the_ba_is_told_in_plain_language_and_that_nothing_is_running() -> None:
    """Never a silent state change — and the half a BA actually wants is
    whether her half-written rule is quietly quarantining members."""
    (review,) = _route(Candidate(stated=CROSS_FEED, unsupported_reason="cross-feed timing"))
    told = review.explain_to_author()
    assert told.startswith(NEEDS_TECHNICAL_REVIEW)
    assert "Nothing runs anywhere" in told
    assert "confidence_floor" not in told
    assert "unsupported" not in told


def test_the_bas_first_text_is_shown_first() -> None:
    """A reviewer shown the machine's reading first anchors on it and then
    checks whether the sentence agrees — which reliably produces agreement."""
    (review,) = _route(Candidate(stated=DOB, confidence=0.4, check=NOT_NULL))
    assert review.side_by_side()[0] == DOB


# ── the measurable: 100% routed ──────────────────────────────────────────────
@pytest.mark.parametrize("hundredths", range(0, 101, 1))
def test_every_below_threshold_candidate_is_routed(hundredths: int) -> None:
    """THE MEASURABLE, over the whole confidence range rather than one example.

    "100% of below-threshold rules routed (zero silent publications), verified
    in testing."
    """
    confidence = hundredths / 100
    candidate = Candidate(stated=DOB, confidence=confidence, check=NOT_NULL)
    reviews = _route(candidate)
    assert unrouted([candidate], reviews, floor=FLOOR) == ()
    assert bool(reviews) is (confidence < FLOOR)


def test_unrouted_names_anything_that_slipped_through() -> None:
    """The verification is a function, not a claim — so the API can answer it
    too. A control only CI can see is a control nobody maintains."""
    low = Candidate(stated=DOB, confidence=0.1, check=NOT_NULL)
    assert unrouted([low], [], floor=FLOOR) == (DOB,)


def test_a_confident_supported_rule_is_not_routed() -> None:
    assert _route(Candidate(stated=DOB, confidence=0.96, check=NOT_NULL)) == ()


# ── the four reasons, and why they are four ──────────────────────────────────
def test_an_unreached_model_is_a_broken_run_not_a_low_score() -> None:
    """ "The agent declined" and "the agent never answered" look identical on a
    queue and mean opposite things."""
    (review,) = _route(Candidate(stated=DOB, not_attempted=True))
    assert review.reason is ReviewReason.NOT_ATTEMPTED
    assert review.evidence["run_was_broken"] is True
    assert "fault on our side" in review.reason.in_plain_language


def test_a_confident_refusal_is_unsupported_and_not_a_confidence_problem() -> None:
    """A model that says *I cannot express this* at 0.95 is telling the truth
    confidently. Reporting that as a confidence problem sends the reviewer
    looking for a better prompt."""
    (review,) = _route(
        Candidate(stated=CROSS_FEED, confidence=0.95, unsupported_reason="needs a join")
    )
    assert review.reason is ReviewReason.UNSUPPORTED
    assert review.reason.is_for_engineering


def test_an_unknown_column_is_routed_rather_than_refused_outright() -> None:
    """ "Did you mean date_of_birth?" is a question for a person, not a 400."""
    (review,) = _route(Candidate(stated=DOB, confidence=0.9, unknown_column="dob_dt"))
    assert review.reason is ReviewReason.UNKNOWN_COLUMN
    assert review.evidence["unknown_column"] == "dob_dt"


def test_the_second_text_is_never_blank() -> None:
    """A blank right-hand column reads as a rendering bug, and a reviewer
    assumes the screen is broken rather than that we had nothing to say."""
    for candidate in (
        Candidate(stated=DOB, not_attempted=True),
        Candidate(stated=DOB, unsupported_reason=""),
        Candidate(stated=DOB, confidence=0.2, unknown_column="dob_dt"),
    ):
        (review,) = _route(candidate)
        assert review.machine_reading.strip()


# ── idempotency ──────────────────────────────────────────────────────────────
def test_rerunning_the_agent_does_not_manufacture_a_second_review() -> None:
    """Content-addressed, like `error_id_hash`. Idempotency at the item level
    is what makes re-running safe enough that people actually re-run."""
    first = _route(Candidate(stated=CROSS_FEED, unsupported_reason="x"))
    second = _route(Candidate(stated=CROSS_FEED, unsupported_reason="x"))
    assert first[0].review_id == second[0].review_id


# ── the don't ────────────────────────────────────────────────────────────────
def test_the_sentence_survives_every_exit() -> None:
    """THE DON'T, ASSERTED ON ALL THREE EXITS.

    "Discard the BA's original sentence — intent is preserved verbatim through
    every correction."
    """
    (queued,) = _route(Candidate(stated=CROSS_FEED, confidence=0.62, unsupported_reason="x"))

    corrected, spec = correct(
        queued, NOT_NULL, reviewer=REVIEWER, note="It meant DOB, not arrival.", now=NOW
    )
    escalated, item = escalate(queued, reviewer=REVIEWER, note="Needs a cross-feed join.", now=NOW)
    withdrawn = withdraw(queued, actor=BA, note="I will ask differently.", now=NOW)

    assert corrected.stated == CROSS_FEED
    assert escalated.stated == CROSS_FEED
    assert withdrawn.stated == CROSS_FEED
    assert item.stated == CROSS_FEED
    # And the rule that eventually RUNS still carries her words, not the
    # reviewer's note.
    assert spec.stated == CROSS_FEED
    assert spec.rationale == "It meant DOB, not arrival."


def test_a_corrected_rule_reports_no_model_confidence() -> None:
    """A human-authored check is not a model that scored perfectly."""
    (queued,) = _route(Candidate(stated=DOB, confidence=0.3, check=NOT_NULL))
    _, spec = correct(queued, NOT_NULL, reviewer=REVIEWER, note="Right column.", now=NOW)
    assert spec.confidence is None


# ── the exception: the request never evaporates ──────────────────────────────
def test_escalation_creates_a_linked_item_carrying_the_context() -> None:
    (queued,) = _route(
        Candidate(stated=CROSS_FEED, confidence=0.62, unsupported_reason="cross-feed timing")
    )
    escalated, item = escalate(queued, reviewer=REVIEWER, note="Needs a cross-feed join.", now=NOW)
    assert escalated.state is ReviewState.ESCALATED
    assert escalated.engineering_item_id == item.item_id
    assert item.review_id == queued.review_id
    # The CONTEXT, not a pointer to it.
    assert item.context["unsupported_reason"] == "cross-feed timing"
    assert item.context["confidence_floor"] == FLOOR
    assert "raised with engineering" in item.explain_to_author()


def test_an_escalated_review_stays_visible_rather_than_closing() -> None:
    """A queue whose items can leave without an outcome is one people stop
    trusting, and then they email the engineer directly."""
    (queued,) = _route(Candidate(stated=CROSS_FEED, unsupported_reason="x"))
    escalated, _ = escalate(queued, reviewer=REVIEWER, note="Engineering.", now=NOW)
    assert escalated.state.status_word is StatusWord.NEEDS_ATTENTION


# ── the guardrails ───────────────────────────────────────────────────────────
def test_the_agent_cannot_resolve_its_own_uncertainty() -> None:
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    with pytest.raises(ReviewError) as refused:
        correct(queued, NOT_NULL, reviewer=AGENT, note="I am sure now.", now=NOW)
    assert "silent auto-apply" in str(refused.value)


def test_every_exit_must_say_why() -> None:
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    for act in (
        lambda: correct(queued, NOT_NULL, reviewer=REVIEWER, note="  ", now=NOW),
        lambda: escalate(queued, reviewer=REVIEWER, note="", now=NOW),
        lambda: withdraw(queued, actor=BA, note="", now=NOW),
    ):
        with pytest.raises(ReviewError):
            act()


def test_a_resolved_review_cannot_be_resolved_again() -> None:
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    corrected, _ = correct(queued, NOT_NULL, reviewer=REVIEWER, note="Fixed.", now=NOW)
    with pytest.raises(ReviewError) as refused:
        escalate(corrected, reviewer=REVIEWER, note="Actually engineering.", now=NOW)
    assert "not a legal move" in str(refused.value)


def test_a_rule_in_review_cannot_be_published() -> None:
    """The other half of "nothing runs anywhere": a reviewer who questions an
    already-approved rule has said something, and publication must hear it."""
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    spec = RuleSpec(rule_id="DQ-026", name="DOB", stated=DOB, check=NOT_NULL)
    with pytest.raises(ReviewError) as refused:
        guard_publication([spec], [queued])
    assert "cannot be published" in str(refused.value)


def test_a_resolved_review_no_longer_blocks_publication() -> None:
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    corrected, spec = correct(queued, NOT_NULL, reviewer=REVIEWER, note="Fixed.", now=NOW)
    guard_publication([spec], [corrected])


# ── corrections are fuel ─────────────────────────────────────────────────────
def test_a_correction_records_what_was_wrong_as_well_as_what_is_right() -> None:
    """The eval set learns from the reason, not from the new answer."""
    attempted = Check(kind=CheckKind.NOT_NULL, column="dob_dt")
    (queued,) = _route(Candidate(stated=DOB, confidence=0.3, check=attempted))
    corrected, _ = correct(queued, NOT_NULL, reviewer=REVIEWER, note="Wrong column.", now=NOW)

    (fuel,) = corrections_for_eval([corrected])
    assert fuel.field_path == DOB
    assert fuel.proposed["column"] == "dob_dt"
    assert fuel.accepted["column"] == "date_of_birth"


def test_an_escalation_teaches_the_prompt_not_to_try() -> None:
    """`accepted=None` records that no check was the right answer — which a
    missing entry would not."""
    (queued,) = _route(Candidate(stated=CROSS_FEED, confidence=0.62, unsupported_reason="x"))
    escalated, _ = escalate(queued, reviewer=REVIEWER, note="Engineering.", now=NOW)
    (fuel,) = corrections_for_eval([escalated])
    assert fuel.accepted is None
    assert fuel.is_addition is False or fuel.proposed is None


def test_an_open_review_contributes_nothing_yet() -> None:
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    assert corrections_for_eval([queued]) == ()


# ── the queue ────────────────────────────────────────────────────────────────
def test_the_queue_is_oldest_first_not_lowest_confidence_first() -> None:
    """A queue ordered by score buries the 79% answers behind the 20% ones
    forever — and the 79% answers clear in a minute."""
    early = route(
        [Candidate(stated="first sentence", confidence=0.79, check=NOT_NULL)],
        feed_id=FEED,
        floor=FLOOR,
        now=NOW,
    )[0]
    late = route(
        [Candidate(stated="second sentence", confidence=0.05, check=NOT_NULL)],
        feed_id=FEED,
        floor=FLOOR,
        now=datetime(2026, 8, 30, 13, 0, tzinfo=UTC),
    )[0]
    assert [r.stated for r in awaiting_review([late, early])] == [
        "first sentence",
        "second sentence",
    ]


def test_a_resolved_review_leaves_the_queue() -> None:
    (queued,) = _route(Candidate(stated=DOB, confidence=0.2, check=NOT_NULL))
    corrected, _ = correct(queued, NOT_NULL, reviewer=REVIEWER, note="Fixed.", now=NOW)
    assert awaiting_review([corrected]) == ()
