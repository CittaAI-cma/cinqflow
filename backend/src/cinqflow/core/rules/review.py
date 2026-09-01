"""CF-V1-E7-04 — the queue an uncertain rule lands in, and why it is a queue.

    "I want any rule whose generated logic falls below the confidence
     threshold, or that needs constructs the safe subset does not allow, to be
     routed automatically to a technical reviewer with the AI's interpretation
     shown beside the BA's original sentence, so that uncertain logic never
     reaches a pipeline silently — the fallback path is a governed queue, not a
     shrug."
    "Discard the BA's original sentence — intent is preserved verbatim through
     every correction."
    — CF-V1-E7-04, and its first don't

THE STORY'S OWN PHRASE IS THE SPECIFICATION: *a governed queue, not a shrug*.
`core.agents.rule_authoring` already DETECTS both conditions and emits a
`NeedsTechnicalReview` for each. What it cannot do — because an agent's output
is a proposal and nothing else — is give that detection somewhere to live, a
person to belong to, and an exit. This module is that somewhere.

ROUTING IS TOTAL, AND `unrouted` IS HOW WE KNOW. The measurable is "100% of
below-threshold rules routed (zero silent publications)", and a percentage
nobody computes is a percentage nobody meets. So `route` is paired with
`unrouted`, which returns everything that SHOULD have been routed and was not:
a property test drives generated confidences through both and asserts the
second is always empty. That is the difference between a claim and a gate.

THE SENTENCE IS IMMUTABLE AND THE LOGIC IS NOT. Every act here returns a new
review with `stated` untouched — correcting, escalating, withdrawing. The don't
says intent is preserved verbatim through every correction, and the way to make
that true is to have no code path that writes the field. A reviewer who decides
the AI misread the sentence must be able to see what was actually asked for,
and a platform that had helpfully tidied the wording would have deleted the
evidence of its own mistake.

THE SECOND TEXT IS THE MACHINE'S READING, NOT ITS SQL. "Both texts side by
side" means the BA's sentence and what the platform understood — which for an
UNSUPPORTED rule is "nothing, and here is why". A review that showed a
generated query beside a sentence would be asking a reviewer to diff a dialect
against English; showing the READ-BACK asks them the question they can actually
answer: is this what you meant?

THE REQUEST NEVER EVAPORATES. The exception in the story is the one that makes
this a governed queue rather than a triage bin: when a reviewer decides a
sentence needs real engineering, an `EngineeringItem` is created carrying the
full context and the review moves to ESCALATED — it does not close. A queue
whose items can leave without an outcome is a queue people stop trusting within
a month, and then they email the engineer directly, which is the workflow this
epic exists to end.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, StatusWord
from cinqflow.core.proposals import Correction
from cinqflow.core.rules import Check, RuleError, RuleSpec

#: What the BA is told when a rule is routed. The EXACT string, so the screen,
#: the test and the notification all mean the same thing by it.
NEEDS_TECHNICAL_REVIEW = "Needs technical review"


class ReviewError(RuleError):
    """A review act the queue does not permit."""


# ── why a rule is here ───────────────────────────────────────────────────────
@unique
class ReviewReason(StrEnum):
    """The four ways a sentence fails to become a runnable rule.

    Four rather than one, because each sends the reviewer somewhere different:
    a low-confidence answer needs checking, an unsupported construct needs
    engineering, an unknown column needs the BA, and an unreached model needs
    re-running. A single `needs_review` flag would send all four to the same
    person to work out which they were looking at.
    """

    #: The model answered and the PLATFORM did not believe it. The floor lives
    #: in `core.agents.rule_authoring.graph`, not in the prompt, because a
    #: model asked to self-censor at a number reports that number.
    BELOW_CONFIDENCE = "below_confidence"
    #: The safe subset cannot express it — a cross-feed timing rule, an
    #: aggregate, a join. The story's own example, and the honest answer.
    UNSUPPORTED = "unsupported"
    #: The sentence names a field this feed does not have. Routed rather than
    #: refused outright when near matches exist, because "did you mean
    #: `date_of_birth`?" is a question for a person, not a 400.
    UNKNOWN_COLUMN = "unknown_column"
    #: The model was never reached. A BROKEN RUN, not a decision — and
    #: distinguishing it matters, because "the agent declined" and "the agent
    #: never answered" look identical on a queue and mean opposite things.
    NOT_ATTEMPTED = "not_attempted"

    @property
    def is_for_engineering(self) -> bool:
        """Whether the likely exit is an engineering item rather than a fix."""
        return self is ReviewReason.UNSUPPORTED

    @property
    def in_plain_language(self) -> str:
        """What the BA reads. Never a silent state change, and never a code.

        "Explain to the BA in plain language why the rule needs review."
        """
        return {
            ReviewReason.BELOW_CONFIDENCE: (
                "The platform was not confident enough about how to turn this sentence into "
                "a check, so a technical reviewer will look at it with you. Nothing runs "
                "anywhere until they do."
            ),
            ReviewReason.UNSUPPORTED: (
                "This asks for something the rule vocabulary cannot express on its own — "
                "usually a comparison across feeds or a total across rows. A technical "
                "reviewer will tell you whether it can be built and how."
            ),
            ReviewReason.UNKNOWN_COLUMN: (
                "This sentence names a field this feed does not have. A technical reviewer "
                "will check whether it is spelled differently here or genuinely missing."
            ),
            ReviewReason.NOT_ATTEMPTED: (
                "The platform could not complete its reading of this sentence — that is a "
                "fault on our side, not a problem with what you wrote. It has been queued "
                "for a person to look at, and re-running usually resolves it."
            ),
        }[self]


@unique
class ReviewState(StrEnum):
    """QUEUED -> CORRECTED | ESCALATED | WITHDRAWN. Three exits, all recorded.

    There is no `CLOSED`. Every exit says what HAPPENED — the logic was
    written, the work was handed to engineering, or the author took the request
    back — because a queue whose items can end in "closed" is a queue where the
    honest answer and the tidy answer look the same.
    """

    QUEUED = "queued"
    #: A reviewer wrote the check. The rule exists now.
    CORRECTED = "corrected"
    #: Genuinely custom engineering. A linked item carries the context.
    ESCALATED = "escalated"
    #: The author withdrew the request. Recorded, not deleted.
    WITHDRAWN = "withdrawn"

    @property
    def is_open(self) -> bool:
        return self is ReviewState.QUEUED

    @property
    def status_word(self) -> StatusWord:
        """The seven words. There is no eighth."""
        return {
            ReviewState.QUEUED: StatusWord.NEEDS_REVIEW,
            ReviewState.CORRECTED: StatusWord.COMPLETED,
            ReviewState.ESCALATED: StatusWord.NEEDS_ATTENTION,
            ReviewState.WITHDRAWN: StatusWord.COMPLETED,
        }[self]


_EXITS: dict[ReviewState, frozenset[ReviewState]] = {
    ReviewState.QUEUED: frozenset(
        {ReviewState.CORRECTED, ReviewState.ESCALATED, ReviewState.WITHDRAWN}
    ),
    ReviewState.CORRECTED: frozenset(),
    ReviewState.ESCALATED: frozenset(),
    ReviewState.WITHDRAWN: frozenset(),
}


# ── the queue item ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EngineeringItem:
    """The exit for a sentence that needs real engineering.

        "Given the reviewer determines the request needs genuinely custom
         engineering, when they mark it as such, then a linked engineering item
         is created carrying the full context, and the BA is told what happens
         next — the request never evaporates."

    Carries the CONTEXT, not a pointer to it. An item that says "see review
    #412" is an item whose context disappears the day somebody archives the
    queue — and the whole reason this exists is that the request must survive
    its own queue.
    """

    item_id: str
    feed_id: str
    review_id: str
    #: The BA's sentence, verbatim. The thing engineering is actually being
    #: asked for.
    stated: str
    reason: ReviewReason
    machine_reading: str
    raised_by: Actor
    raised_ts: datetime
    context: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def title(self) -> str:
        return f"Rule needs engineering: {self.stated[:70]}"

    def explain_to_author(self) -> str:
        """What the BA is told. "What happens next", in their words."""
        return (
            f"Your rule — “{self.stated}” — needs a change to the platform itself, so it has "
            f"been raised with engineering as {self.item_id}. Your sentence is on the item "
            "exactly as you wrote it. Nothing about this feed changes in the meantime."
        )


@dataclass(frozen=True)
class TechnicalReview:
    """One uncertain rule, waiting for a person.

    `stated` is the BA's own sentence and NO ACT IN THIS MODULE WRITES IT.
    That is the don't made structural rather than remembered.
    """

    review_id: str
    feed_id: str
    #: The BA's own words. Verbatim, forever.
    stated: str
    reason: ReviewReason
    #: What the platform understood — the READ-BACK, or the reason there is
    #: none. The second of the two texts shown side by side.
    machine_reading: str
    confidence: float = 0.0
    created_ts: datetime | None = None
    state: ReviewState = ReviewState.QUEUED
    #: The agent's own attempt, where it made one. Kept so a reviewer can see
    #: what was nearly right rather than starting from the sentence again.
    attempted: Check | None = None
    #: Everything a reviewer needs that is not one of the two texts: the
    #: contract columns, the near matches, the sample preview if one ran.
    evidence: dict[str, Any] = field(default_factory=dict)
    reviewed_by: Actor | None = None
    reviewed_ts: datetime | None = None
    resolution_note: str = ""
    #: Set on CORRECTED. The check the reviewer wrote.
    corrected: Check | None = None
    #: Set on ESCALATED.
    engineering_item_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stated.strip():
            raise ReviewError(
                f"{self.review_id}: a review with no stated intent is a review of nothing. "
                "The BA's sentence is the whole subject."
            )

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.review_id)

    @property
    def is_open(self) -> bool:
        return self.state.is_open

    def side_by_side(self) -> tuple[str, str]:
        """The two texts, in the order the reviewer reads them.

        The BA's first. A reviewer shown the machine's reading first anchors on
        it and then checks whether the sentence agrees — which is the wrong
        question, and reliably produces agreement.
        """
        return self.stated, self.machine_reading

    def explain_to_author(self) -> str:
        """Plain language, and never a silent state change.

        Names the reason AND says that nothing is running, because the second
        half is what a BA actually wants to know: whether her half-written rule
        is quietly quarantining members while she waits.
        """
        return (
            f"{NEEDS_TECHNICAL_REVIEW} — {self.reason.in_plain_language} "
            f"Nothing runs anywhere until a reviewer has looked at it."
        )

    def summary(self) -> str:
        """The line on the reviewer's queue."""
        score = f" · confidence {self.confidence:.0%}" if self.confidence else ""
        return f"{self.feed_id}: “{self.stated}” — {self.reason.value}{score}"


def _move(review: TechnicalReview, to: ReviewState) -> None:
    if to not in _EXITS[review.state]:
        allowed = ", ".join(sorted(s.value for s in _EXITS[review.state])) or "nowhere"
        raise ReviewError(
            f"{review.review_id}: {review.state.value} -> {to.value} is not a legal move "
            f"(from {review.state.value} you may go to {allowed})."
        )


def _require_human(actor: Actor, act: str) -> None:
    if actor.actor_type is not ActorType.HUMAN:
        raise ReviewError(
            f"a {actor.actor_type.value} actor tried to {act} a technical review. This queue "
            "exists BECAUSE the agent was not sure — letting it resolve its own uncertainty "
            "would be the silent auto-apply the story refuses."
        )


# ── routing: total, and provably so ──────────────────────────────────────────
@dataclass(frozen=True)
class Candidate:
    """One thing the agent produced, as far as routing is concerned.

    A structural read-model rather than an import of
    `intelligence.agents.rule_authoring`: `core/` cannot depend on the
    intelligence layer (the chip's layering runs the other way), and the two
    shapes the agent emits — an authored rule with a confidence, and a
    `NeedsTechnicalReview` — reduce to the same four fields here.
    """

    stated: str
    confidence: float = 0.0
    #: The check the agent produced, if it produced one.
    check: Check | None = None
    machine_reading: str = ""
    unsupported_reason: str = ""
    unknown_column: str | None = None
    #: The model was never reached for this sentence.
    not_attempted: bool = False

    def reason(self, *, floor: float) -> ReviewReason | None:
        """Why this needs a person, or None if it does not.

        ORDER MATTERS, and it is the order of certainty. "The model never
        answered" outranks "the answer scored low", because a run that did not
        happen has no score to be low; and "unsupported" outranks confidence
        because a model that says *I cannot express this* at 0.95 is telling
        the truth confidently, and reporting that as a confidence problem would
        send the reviewer looking for a better prompt.
        """
        if self.not_attempted:
            return ReviewReason.NOT_ATTEMPTED
        if self.unsupported_reason.strip():
            return ReviewReason.UNSUPPORTED
        if self.unknown_column:
            return ReviewReason.UNKNOWN_COLUMN
        if self.check is None:
            return ReviewReason.UNSUPPORTED
        if self.confidence < floor:
            return ReviewReason.BELOW_CONFIDENCE
        return None


def _review_id(feed_id: str, stated: str) -> str:
    """Content-addressed, so re-running the agent on the same sentence does not
    manufacture a second review of it.

    The same discipline `ErrorRecord.error_id_hash` uses, and for the same
    reason: idempotency at the item level is what makes re-running safe enough
    that people actually re-run.
    """
    digest = hashlib.sha256(f"{feed_id}|{stated.strip()}".encode()).hexdigest()[:16]
    return f"TR-{digest}"


def route(
    candidates: Sequence[Candidate],
    *,
    feed_id: str,
    floor: float,
    now: datetime | None = None,
) -> tuple[TechnicalReview, ...]:
    """Every candidate that cannot become a runnable rule, as a queue item.

    Returns reviews ONLY — the rules that passed are the caller's to keep. A
    function that returned both would invite a call site that took the first
    element and dropped the second, which is exactly the silent publication
    this story exists to prevent.
    """
    stamp = now or datetime.now(UTC)
    reviews: list[TechnicalReview] = []
    for candidate in candidates:
        reason = candidate.reason(floor=floor)
        if reason is None:
            continue
        reviews.append(
            TechnicalReview(
                review_id=_review_id(feed_id, candidate.stated),
                feed_id=feed_id,
                stated=candidate.stated,
                reason=reason,
                machine_reading=_reading(candidate, reason),
                confidence=candidate.confidence,
                created_ts=stamp,
                attempted=candidate.check,
                evidence=_evidence(candidate, floor=floor),
            )
        )
    return tuple(reviews)


def _reading(candidate: Candidate, reason: ReviewReason) -> str:
    """The second text. Never empty — "nothing, and here is why" is a reading.

    A blank right-hand column reads as a rendering bug, and a reviewer looking
    at one assumes the screen is broken rather than that the platform had
    nothing to say.
    """
    if candidate.machine_reading.strip():
        return candidate.machine_reading.strip()
    if candidate.check is not None:
        return candidate.check.explain()
    match reason:
        case ReviewReason.UNSUPPORTED:
            return (
                candidate.unsupported_reason.strip()
                or "The platform could not express this in the rule vocabulary."
            )
        case ReviewReason.UNKNOWN_COLUMN:
            return (
                f"The platform read this as a check on {candidate.unknown_column!r}, which "
                "this feed does not have."
            )
        case ReviewReason.NOT_ATTEMPTED:
            return "The platform did not complete a reading of this sentence."
        case _:
            return "The platform produced no check it was willing to stand behind."


def _evidence(candidate: Candidate, *, floor: float) -> dict[str, Any]:
    bundle: dict[str, Any] = {"confidence_floor": floor}
    if candidate.confidence:
        bundle["confidence"] = candidate.confidence
    if candidate.unknown_column:
        bundle["unknown_column"] = candidate.unknown_column
    if candidate.unsupported_reason.strip():
        bundle["unsupported_reason"] = candidate.unsupported_reason.strip()
    if candidate.not_attempted:
        bundle["run_was_broken"] = True
    return bundle


def unrouted(
    candidates: Sequence[Candidate],
    reviews: Sequence[TechnicalReview],
    *,
    floor: float,
) -> tuple[str, ...]:
    """Everything that SHOULD have been routed and was not.

        "100% of below-threshold rules routed (zero silent publications),
         verified in testing"

    THIS is the verification. A percentage nobody computes is a percentage
    nobody meets, so the measurable is a function that must return empty — and
    a property test drives generated confidences through `route` and asserts it
    does. Written as its own function rather than inside a test so that the API
    can answer it too: a control only CI can see is a control nobody maintains.
    """
    queued = {review.stated for review in reviews}
    return tuple(
        candidate.stated
        for candidate in candidates
        if candidate.reason(floor=floor) is not None and candidate.stated not in queued
    )


def guard_publication(specs: Sequence[RuleSpec], reviews: Sequence[TechnicalReview]) -> None:
    """Refuse to publish a rule set while any of its rules is still in review.

    The other half of "nothing runs anywhere". Routing keeps an uncertain rule
    out of the proposal; this keeps a rule that was LATER questioned out of
    production — because a reviewer who opens a review on a rule already
    approved has said something, and the publication path has to hear it.
    """
    open_sentences = {r.stated.strip() for r in reviews if r.is_open}
    blocked = sorted({s.rule_id for s in specs if s.stated.strip() in open_sentences})
    if blocked:
        raise ReviewError(
            f"{', '.join(blocked)} is in technical review and cannot be published. "
            "Uncertain logic never reaches a pipeline silently — resolve the review first."
        )


# ── the three exits ──────────────────────────────────────────────────────────
def correct(
    review: TechnicalReview,
    check: Check,
    *,
    reviewer: Actor,
    note: str,
    rule_id: str | None = None,
    now: datetime | None = None,
) -> tuple[TechnicalReview, RuleSpec]:
    """The reviewer writes the logic in place, and a real rule comes out.

        "Give the reviewer both texts side by side plus the evidence, and let
         them correct the logic in place."

    The returned `RuleSpec` carries the BA'S SENTENCE, not the reviewer's note.
    That is the don't: intent is preserved verbatim through every correction,
    so the rule that eventually runs is still answerable to the person who
    asked for it — and a year later, "why does this rule exist?" opens her
    words rather than an engineer's paraphrase of them.
    """
    _require_human(reviewer, "correct")
    _move(review, ReviewState.CORRECTED)
    if not note.strip():
        raise ReviewError(
            f"{review.review_id}: a correction must say what was wrong. The evaluation set "
            "learns from the reason, not from the new answer — and an unexplained "
            "correction teaches the next run nothing."
        )
    stamp = now or datetime.now(UTC)
    moved = replace(
        review,
        state=ReviewState.CORRECTED,
        reviewed_by=reviewer,
        reviewed_ts=stamp,
        resolution_note=note.strip(),
        corrected=check,
    )
    spec = RuleSpec(
        rule_id=rule_id or f"{review.feed_id}-{review.review_id}",
        name=_titled(review.stated),
        # VERBATIM. Not `note`, not a regenerated sentence.
        stated=review.stated,
        check=check,
        # The reviewer wrote this by hand, so there is no model confidence to
        # report. Leaving it None rather than 1.0 keeps the eval honest: a
        # human-authored check is not a model that scored perfectly.
        confidence=None,
        rationale=note.strip(),
        citations=(str(review.citation),),
    )
    return moved, spec


def escalate(
    review: TechnicalReview,
    *,
    reviewer: Actor,
    note: str,
    item_id: str | None = None,
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[TechnicalReview, EngineeringItem]:
    """Genuinely custom engineering. The request survives its own queue."""
    _require_human(reviewer, "escalate")
    _move(review, ReviewState.ESCALATED)
    if not note.strip():
        raise ReviewError(
            f"{review.review_id}: escalation must say what engineering is being asked for. "
            "An item that carries only the original sentence puts the reviewer's whole "
            "analysis in a conversation nobody can find later."
        )
    stamp = now or datetime.now(UTC)
    raised = item_id or f"ENG-{review.review_id[3:]}"
    moved = replace(
        review,
        state=ReviewState.ESCALATED,
        reviewed_by=reviewer,
        reviewed_ts=stamp,
        resolution_note=note.strip(),
        engineering_item_id=raised,
    )
    item = EngineeringItem(
        item_id=raised,
        feed_id=review.feed_id,
        review_id=review.review_id,
        stated=review.stated,
        reason=review.reason,
        machine_reading=review.machine_reading,
        raised_by=reviewer,
        raised_ts=stamp,
        context={**review.evidence, **(context or {})},
        note=note.strip(),
    )
    return moved, item


def withdraw(
    review: TechnicalReview, *, actor: Actor, note: str, now: datetime | None = None
) -> TechnicalReview:
    """The author takes the request back. Recorded, never deleted.

    A withdrawn review still holds the sentence somebody typed and the reason
    the platform could not build it — which is exactly the material the next
    prompt version is tuned against.
    """
    _require_human(actor, "withdraw")
    _move(review, ReviewState.WITHDRAWN)
    if not note.strip():
        raise ReviewError(f"{review.review_id}: say why it is being withdrawn")
    return replace(
        review,
        state=ReviewState.WITHDRAWN,
        reviewed_by=actor,
        reviewed_ts=now or datetime.now(UTC),
        resolution_note=note.strip(),
    )


# ── corrections are fuel ─────────────────────────────────────────────────────
def corrections_for_eval(reviews: Sequence[TechnicalReview]) -> tuple[Correction, ...]:
    """Every reviewed correction, as the evaluation set's own type.

        "Feed every reviewed correction back into the evaluation set."

    `core.proposals.Correction` rather than a shape of its own, deliberately:
    schema inference, mapping and rule authoring all grade in the same unit, so
    the four Wave-1 eval gates are stated in one arithmetic. A fifth correction
    type here would have made this queue's learning invisible to all of it.

    An ESCALATED review contributes one too, and it is the most informative
    kind: `accepted=None` records that no check was the right answer — which
    teaches a prompt not to try, where a missing entry teaches it nothing.
    """
    found: list[Correction] = []
    for review in reviews:
        if review.state is ReviewState.CORRECTED and review.corrected is not None:
            found.append(
                Correction(
                    field_path=review.stated,
                    proposed=_check_summary(review.attempted),
                    accepted=_check_summary(review.corrected),
                )
            )
        elif review.state is ReviewState.ESCALATED:
            found.append(
                Correction(
                    field_path=review.stated,
                    proposed=_check_summary(review.attempted),
                    accepted=None,
                )
            )
    return tuple(found)


def _check_summary(check: Check | None) -> dict[str, Any] | None:
    """What a correction records about a check.

    The KIND and the column, not the whole object. A correction set is read by
    a person tuning a prompt, and burying the one field that changed inside a
    twelve-key dump is how a fuel source becomes a log file.
    """
    if check is None:
        return None
    return {"kind": check.kind.value, "column": check.column, "reads": check.explain()}


def _titled(sentence: str) -> str:
    words = sentence.strip().rstrip(".").split()
    short = " ".join(words[:8])
    return short[:1].upper() + short[1:] if short else "Rule"


# ── the queue itself ─────────────────────────────────────────────────────────
def awaiting_review(
    reviews: Sequence[TechnicalReview], *, feed_id: str | None = None
) -> tuple[TechnicalReview, ...]:
    """The reviewer's queue: open items, oldest first.

    Oldest first rather than lowest-confidence first. A queue ordered by score
    quietly buries the 79% answers behind the 20% ones forever, and the 79%
    answers are the ones a reviewer can actually clear in a minute.
    """
    return tuple(
        sorted(
            (
                review
                for review in reviews
                if review.is_open and (feed_id is None or review.feed_id == feed_id)
            ),
            key=lambda r: (r.created_ts or datetime.min.replace(tzinfo=UTC), r.review_id),
        )
    )
