"""CF-V1-E4-01 — the five-step wizard, whose checklist cannot lie.

    "1. Upload sample, 2. Approve schema, 3. Map fields, 4. Define and test
     rules, 5. Publish and schedule — with save-and-resume, mandatory checks,
     and a readiness checklist throughout."
    "Let a step be marked complete while its underlying object (schema,
     mapping, rules) is unapproved — the checklist reflects real states, not
     optimism."
    — CF-V1-E4-01, and its first don't

THE WIZARD HAS NO STATE OF ITS OWN. Not "its state is carefully kept in sync" —
it has none. `wizard()` is a pure function of the governed objects that exist
for a feed, and a step is complete because its object is APPROVED, never
because somebody clicked Next.

That is the whole of the don't, made structural. A wizard with a
`current_step` column is a wizard that can say "schema: complete" while the
contract sits in Draft, and it will say it exactly once — on the day somebody
requests changes on an approved contract and no code path remembers to walk
the pointer back. There is no pointer here to walk back.

SAVE-AND-RESUME IS THEN FREE, AND SO IS "DRAFTS SURVIVE FOR WEEKS". Nothing
expires because nothing is held: the draft objects in `registry.governed_object`
ARE the saved work, they are versioned and audited like everything else, and
`resume_at` is computed — the first step that is not complete. A BA returning
after three weeks resumes exactly where she left off because the platform never
recorded where that was; it looks.

WHAT A STEP CONTRIBUTES IS AN OBSTACLE LIST, NOT A BOOLEAN. "The wizard says
exactly what stands between her and done" is the story's own promise and the
reason `Obstacle` carries three strings and a citation: what is wrong, why it
matters, what to do, and the address that opens the thing itself.
`CitationId.route` is that one-click navigation — the same address space the
agent cites and the drawer opens (ADR-0020), so the wizard needed no link
scheme of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.mapping import LineStatus
from cinqflow.core.mapping import from_governed as mapping_from_governed
from cinqflow.core.model.governed import GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import StatusWord
from cinqflow.core.registry.canonical import CanonicalModel
from cinqflow.core.registry.operations import FeedOperations, Readiness, readiness


class OnboardingError(RuntimeError):
    """An onboarding that cannot proceed, with a reason a person can act on."""


# ── the five steps ───────────────────────────────────────────────────────────
@unique
class Step(StrEnum):
    """The journey, in business language. Five, in this order, and no others.

    The names are the BA's, not the platform's: she uploads a sample, she does
    not "create a profile"; she maps fields, she does not "author a mapping
    aggregate". The wizard is the platform's friendliest surface and the
    vocabulary is the largest part of that.
    """

    SAMPLE = "sample"
    SCHEMA = "schema"
    MAPPING = "mapping"
    RULES = "rules"
    PUBLISH = "publish"

    @property
    def ordinal(self) -> int:
        return list(Step).index(self) + 1

    @property
    def label(self) -> str:
        """What the step is called on screen.

        `label` rather than `title` because `Step` is a `StrEnum` and `str`
        already has a `title()`. Shadowing it would make `Step.SAMPLE.title`
        mean two things depending on whether it was called.
        """
        return {
            Step.SAMPLE: "Upload a sample file",
            Step.SCHEMA: "Approve the schema",
            Step.MAPPING: "Map the fields",
            Step.RULES: "Define and test the rules",
            Step.PUBLISH: "Publish and schedule",
        }[self]

    @property
    def object_type(self) -> ObjectType | None:
        """The governed object whose state decides this step. `None` for the
        two steps that are not one object: the sample is an observation, and
        publishing is the feed's own lifecycle."""
        return {
            Step.SCHEMA: ObjectType.CONTRACT,
            Step.MAPPING: ObjectType.MAPPING,
            Step.RULES: ObjectType.DQ_RULE,
            Step.PUBLISH: ObjectType.FEED,
        }.get(self)


@unique
class StepState(StrEnum):
    """Where a step actually is. Derived from a lifecycle state, never stored.

    Five, because the four lifecycle states a BA cares about collapse into
    different ACTIONS: a draft needs her, a review needs somebody else, a
    rejection needs her again but differently, and a locked step needs the one
    before it. `AWAITING_APPROVAL` is deliberately not `COMPLETE` — that
    conflation is precisely the don't this module exists to make impossible.
    """

    #: Nothing exists yet, and the step before it is not done.
    LOCKED = "locked"
    #: Nothing exists yet, and she may start.
    NOT_STARTED = "not_started"
    #: A draft exists. Her move.
    IN_PROGRESS = "in_progress"
    #: Submitted. Somebody else's move — and NOT complete.
    AWAITING_APPROVAL = "awaiting_approval"
    #: Approved or published. Genuinely done.
    COMPLETE = "complete"
    #: Rejected, or something upstream invalidated it. Her move, with a reason.
    BLOCKED = "blocked"

    @property
    def is_complete(self) -> bool:
        return self is StepState.COMPLETE

    @property
    def status_word(self) -> StatusWord:
        """The seven words. There is no eighth."""
        return {
            StepState.LOCKED: StatusWord.EXPECTED,
            StepState.NOT_STARTED: StatusWord.EXPECTED,
            StepState.IN_PROGRESS: StatusWord.PROCESSING,
            StepState.AWAITING_APPROVAL: StatusWord.NEEDS_REVIEW,
            StepState.COMPLETE: StatusWord.COMPLETED,
            StepState.BLOCKED: StatusWord.NEEDS_ATTENTION,
        }[self]


_FROM_LIFECYCLE: dict[LifecycleState, StepState] = {
    LifecycleState.DRAFT: StepState.IN_PROGRESS,
    LifecycleState.PENDING_REVIEW: StepState.AWAITING_APPROVAL,
    LifecycleState.APPROVED: StepState.COMPLETE,
    LifecycleState.PUBLISHED: StepState.COMPLETE,
    LifecycleState.REJECTED: StepState.BLOCKED,
    LifecycleState.RETIRED: StepState.BLOCKED,
}


# ── what stands between her and done ─────────────────────────────────────────
@dataclass(frozen=True)
class Obstacle:
    """One thing in the way, and the address that opens it.

    THREE STRINGS AND A CITATION, for the reason `ChecklistItem` has three: an
    obstacle that only names a field gets a placeholder typed into it. The
    citation is what makes "one-click navigation back to them" true — it is the
    platform's address space (ADR-0020), so the wizard needed no link scheme of
    its own and a blocker opens the same drawer the agent's citation does.
    """

    key: str
    what: str
    why_it_matters: str
    how_to_fix: str
    citation: CitationId | None = None
    #: An obstacle that stops the step being complete. An advisory one is
    #: reported and does not gate — the honest-gaps list of CF-V1-E4-02 is made
    #: of these, and marking them blocking would make a documented no-map
    #: decision unrepresentable.
    blocking: bool = True

    @property
    def route(self) -> str:
        """Where the one click goes. Empty when there is nothing to open yet —
        which is itself the answer for "you have not uploaded a sample"."""
        return self.citation.route if self.citation else ""


@dataclass(frozen=True)
class StepStatus:
    """One step of the five, as it actually is."""

    step: Step
    state: StepState
    obstacles: tuple[Obstacle, ...] = ()
    #: The object this step is about, at the version the state was read from.
    citation: CitationId | None = None
    version: int | None = None

    @property
    def blocking(self) -> tuple[Obstacle, ...]:
        return tuple(o for o in self.obstacles if o.blocking)

    @property
    def advisory(self) -> tuple[Obstacle, ...]:
        return tuple(o for o in self.obstacles if not o.blocking)

    @property
    def is_complete(self) -> bool:
        return self.state.is_complete and not self.blocking


@dataclass(frozen=True)
class Wizard:
    """The single readiness view.

    "Show a single readiness view: what is complete, what is missing, what
     needs engineering review." — CF-V1-E4-01
    """

    feed_id: str
    steps: tuple[StepStatus, ...] = ()
    #: CF-V1-E3-02's operational envelope, computed by the same function the
    #: registry form calls. Carried here rather than restated so a BA sees ONE
    #: checklist, not a wizard's and a registry's disagreeing about ready.
    operations: Readiness = field(default_factory=lambda: Readiness(feed_id=""))

    def status(self, step: Step) -> StepStatus:
        for candidate in self.steps:
            if candidate.step is step:
                return candidate
        raise OnboardingError(f"{step.value} is not one of this wizard's steps")

    @property
    def resume_at(self) -> Step:
        """Where she picks up. The first step that is not complete.

        COMPUTED, so a return after three weeks lands correctly without the
        platform ever having recorded where she was.
        """
        for status in self.steps:
            if not status.is_complete:
                return status.step
        return Step.PUBLISH

    @property
    def completed(self) -> tuple[Step, ...]:
        return tuple(s.step for s in self.steps if s.is_complete)

    @property
    def outstanding(self) -> tuple[Obstacle, ...]:
        """Everything blocking, across all five steps, in journey order."""
        return tuple(o for status in self.steps for o in status.blocking)

    @property
    def gaps(self) -> tuple[Obstacle, ...]:
        """Everything worth saying that does not block. The honest-gaps list."""
        return tuple(o for status in self.steps for o in status.advisory)

    @property
    def is_publishable(self) -> bool:
        """Step 5 unlocks only when everything upstream is genuinely approved."""
        return not self.outstanding and self.operations.is_ready

    def explain(self) -> str:
        """The whole readiness view as text — what the wizard says out loud."""
        if self.is_publishable:
            return f"{self.feed_id} is ready to publish. Every step is approved."
        lines = [f"{self.feed_id} — {len(self.completed)} of {len(self.steps)} steps complete."]
        for status in self.steps:
            lines.append(
                f"  {status.step.ordinal}. {status.step.label}: {status.state.status_word.value}"
            )
            for obstacle in status.blocking:
                lines.append(f"       - {obstacle.what}")
                lines.append(f"         To fix: {obstacle.how_to_fix}")
        return "\n".join(lines)


# ── computing it ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OnboardingInputs:
    """Everything the wizard reads. Gathered by the caller, never fetched here.

    `core/` performs no I/O, so the API route loads the objects and hands them
    over. Passing the GOVERNED OBJECTS themselves rather than a pre-digested
    summary is the point: a summary is a place where "approved" can be computed
    once, wrongly, and the wizard would then faithfully display it.
    """

    feed_id: str
    #: Every version of every governed object for this feed. The wizard reads
    #: the latest of each type; the history is what the narrative view uses.
    objects: tuple[GovernedObject, ...] = ()
    #: CF-V1-E5-01 profile ids. A sample exists when one of these does.
    sample_profile_ids: tuple[str, ...] = ()
    #: The target model, for "is this canonical field required?". Optional —
    #: a BA drafting before the model is loaded must still see her checklist.
    model: CanonicalModel | None = None
    #: CF-V1-E4-02's pack, when one has been produced. Its staleness is a
    #: PUBLISH obstacle; see `core.onboarding.evidence`.
    evidence_fingerprint: str | None = None
    #: The fingerprint the current configuration would produce. When these two
    #: differ, the evidence is stale.
    configuration_fingerprint: str | None = None
    #: CF-V1-E3-02's envelope, so one checklist covers operations too.
    operations: FeedOperations | None = None


def latest(objects: Sequence[GovernedObject], object_type: ObjectType) -> GovernedObject | None:
    """The newest version of one object type.

    Newest by VERSION, not by timestamp: two objects saved in the same second
    are ordered by the number that governance actually uses, and a clock skew
    between two writers must never change which contract the wizard reads.
    """
    candidates = [o for o in objects if o.object_type is object_type]
    return max(candidates, key=lambda o: o.version) if candidates else None


def wizard(inputs: OnboardingInputs) -> Wizard:
    """The whole readiness view, computed. Pure.

    Steps are evaluated in order and each one LOCKS the next until it is
    complete — "step 5 unlocks only when everything upstream is genuinely
    approved" — with one deliberate exception: a step whose own object already
    exists is never shown as LOCKED, because telling a BA that the mapping she
    has been editing for a week is locked would be false, and the useful thing
    to tell her is what is wrong with the contract instead.
    """
    objects = tuple(inputs.objects)
    steps: list[StepStatus] = []
    upstream_complete = True

    for step in Step:
        status = _status(step, inputs, objects, upstream_complete=upstream_complete)
        steps.append(status)
        upstream_complete = upstream_complete and status.is_complete

    operations = (
        readiness(inputs.feed_id, inputs.operations)
        if inputs.operations is not None
        else Readiness(feed_id=inputs.feed_id)
    )
    return Wizard(feed_id=inputs.feed_id, steps=tuple(steps), operations=operations)


def _status(
    step: Step,
    inputs: OnboardingInputs,
    objects: tuple[GovernedObject, ...],
    *,
    upstream_complete: bool,
) -> StepStatus:
    if step is Step.SAMPLE:
        return _sample_status(inputs)

    obj = latest(objects, step.object_type) if step.object_type else None
    if obj is None:
        return StepStatus(
            step=step,
            state=StepState.NOT_STARTED if upstream_complete else StepState.LOCKED,
            obstacles=(_not_started(step, upstream_complete=upstream_complete),),
        )

    state = _FROM_LIFECYCLE[obj.lifecycle_state]
    citation = _cite(_CITATION_KIND[step], obj)
    obstacles = list(_state_obstacles(step, obj, state, citation))
    if step is Step.MAPPING:
        obstacles.extend(_mapping_obstacles(obj, inputs.model, citation))
    if step is Step.PUBLISH:
        obstacles.extend(_publish_obstacles(inputs, objects))
    return StepStatus(
        step=step,
        state=state,
        obstacles=tuple(obstacles),
        citation=citation,
        version=obj.version,
    )


_CITATION_KIND: dict[Step, CitationKind] = {
    Step.SCHEMA: CitationKind.CONTRACT,
    Step.MAPPING: CitationKind.MAPPING,
    Step.RULES: CitationKind.RULE,
    Step.PUBLISH: CitationKind.FEED,
}


def _cite(kind: CitationKind, obj: GovernedObject) -> CitationId:
    """Address one step's object.

    The VERSION is attached only where the citation vocabulary carries one.
    `CitationKind.RULE` is an unversioned kind — a Wave-0 decision this module
    is not the place to revisit — so a rule set is addressed by its feed and
    its version travels in `StepStatus.version` instead. Building the citation
    unconditionally raises, which is the citation module correctly refusing a
    category error rather than a bug to work around.
    """
    return CitationId(
        kind=kind,
        subject=obj.object_id,
        version=obj.version if kind.versioned else None,
    )


def _sample_status(inputs: OnboardingInputs) -> StepStatus:
    """Step 1. A sample is an OBSERVATION, not a governed object — it has no
    lifecycle and nothing approves it, so its step is complete the moment one
    exists."""
    if inputs.sample_profile_ids:
        return StepStatus(
            step=Step.SAMPLE,
            state=StepState.COMPLETE,
            citation=CitationId(kind=CitationKind.PROFILE, subject=inputs.sample_profile_ids[-1]),
        )
    return StepStatus(
        step=Step.SAMPLE,
        state=StepState.NOT_STARTED,
        obstacles=(
            Obstacle(
                key="no_sample",
                what="No sample file has been uploaded yet.",
                why_it_matters=(
                    "Everything after this reads the sample: the schema is inferred from "
                    "it, the mapping is checked against it, and the rules are tested on it. "
                    "Without one there is nothing to be right or wrong about."
                ),
                how_to_fix="Upload a representative file the payer has actually sent.",
            ),
        ),
    )


def _not_started(step: Step, *, upstream_complete: bool) -> Obstacle:
    if not upstream_complete:
        return Obstacle(
            key=f"{step.value}_locked",
            what=f"{step.label} has not started.",
            why_it_matters=(
                "The step before it is not approved yet, and this one reads its result."
            ),
            how_to_fix=f"Finish step {step.ordinal - 1} first.",
        )
    return Obstacle(
        key=f"{step.value}_not_started",
        what=f"{step.label} has not started.",
        why_it_matters="It is the next thing standing between this feed and production.",
        how_to_fix=f"Open step {step.ordinal} and begin.",
    )


def _state_obstacles(
    step: Step, obj: GovernedObject, state: StepState, citation: CitationId
) -> tuple[Obstacle, ...]:
    """The obstacle a step's own LIFECYCLE state implies.

    `AWAITING_APPROVAL` produces one deliberately. A step in review is not
    complete, and a wizard that showed it as merely "pending" with nothing in
    the outstanding list would let a BA reach step 5 believing she was done.
    """
    match state:
        case StepState.IN_PROGRESS:
            return (
                Obstacle(
                    key=f"{step.value}_draft",
                    what=f"{step.label}: the draft has not been submitted.",
                    why_it_matters=(
                        "Nothing unapproved reaches production, so a draft protects "
                        "nobody and blocks everything after it."
                    ),
                    how_to_fix="Submit it for review when you are happy with it.",
                    citation=citation,
                ),
            )
        case StepState.AWAITING_APPROVAL:
            return (
                Obstacle(
                    key=f"{step.value}_in_review",
                    what=f"{step.label}: submitted, waiting for a reviewer.",
                    why_it_matters=(
                        "It is approved when somebody signs it, not when it is sent. "
                        "This step is not complete until then."
                    ),
                    how_to_fix=("Nothing for you to do. The reviewer sees it on their work queue."),
                    citation=citation,
                ),
            )
        case StepState.BLOCKED:
            note = (
                "It was rejected, and the reviewer's comment says why."
                if obj.lifecycle_state is LifecycleState.REJECTED
                else "It has been retired."
            )
            return (
                Obstacle(
                    key=f"{step.value}_blocked",
                    what=f"{step.label}: {note}",
                    why_it_matters="Nothing downstream can proceed on a rejected object.",
                    how_to_fix="Open it, make the change asked for, and resubmit.",
                    citation=citation,
                ),
            )
        case _:
            return ()


def _mapping_obstacles(
    obj: GovernedObject, model: CanonicalModel | None, citation: CitationId
) -> tuple[Obstacle, ...]:
    """The story's own exception, and the line the client's workbooks force.

        "Given her mapping contains two UNMAPPED fields, when she opens step 5,
         then publishing is blocked with the two fields named and one-click
         navigation back to them."

    A REQUIRED canonical field with no source BLOCKS. An optional one is an
    advisory gap and is reported honestly rather than refused — because the
    client's own `NO MAP Fields` sheet has a `Reason` column, and a wizard that
    refused every documented no-map would make their existing, deliberate,
    reviewed decisions unrepresentable.

    Each obstacle carries the mapping's citation with the field as its
    FRAGMENT, so "one-click navigation back to them" opens the line and not
    just the mapping.
    """
    mapping = mapping_from_governed(obj)
    obstacles: list[Obstacle] = []
    for line in mapping.unmapped:
        required = _is_required(line.target_entity, line.target_field, model)
        obstacles.append(
            Obstacle(
                key=f"unmapped:{line.address}",
                what=(
                    f"{line.address} has no source"
                    + (f" — {line.unmapped_reason}" if line.unmapped_reason else "")
                ),
                why_it_matters=(
                    "The canonical model says this field is required, so every record "
                    "would arrive without it and every consumer downstream would read "
                    "the gap as missing data rather than as an unfinished mapping."
                    if required
                    else (
                        "It is optional, so nothing breaks — but a reviewer should see "
                        "it was a decision rather than an omission."
                    )
                ),
                how_to_fix=(
                    "Map it to a source column, or have a steward agree the field stays "
                    "empty for this feed and record why."
                    if required
                    else "Nothing, unless the reason above is wrong."
                ),
                citation=CitationId(
                    kind=CitationKind.MAPPING,
                    subject=citation.subject,
                    version=citation.version,
                    fragment=_fragment(line.target_field),
                ),
                blocking=required,
            )
        )
    return tuple(obstacles)


def _is_required(entity_name: str, field_name: str, model: CanonicalModel | None) -> bool:
    """Required means the canonical model says NOT NULL.

    Unknown means NOT required, deliberately. A model that has not been loaded,
    or a field it has not been told about, must not manufacture a blocker: the
    BA would be refused for a reason nobody could act on, which is how a
    platform teaches people to route around it.
    """
    if model is None:
        return False
    entity = model.entity(entity_name)
    target = entity.field(field_name) if entity else None
    return bool(target and target.nullable is False)


def _fragment(name: str) -> str:
    """A citation fragment is `[A-Za-z0-9][A-Za-z0-9._-]*`. Field names from a
    canonical model already satisfy it; anything else is trimmed rather than
    raising, because a link that cannot be built must not break a checklist."""
    cleaned = "".join(c for c in name if c.isalnum() or c in "._-")
    return cleaned if cleaned[:1].isalnum() else f"f{cleaned}"


def _publish_obstacles(
    inputs: OnboardingInputs, objects: tuple[GovernedObject, ...]
) -> tuple[Obstacle, ...]:
    """Step 5's own gates: stale evidence, and unmapped required fields raised
    to the step a BA is standing on.

    The mapping's blockers are REPEATED here on purpose. The story says
    publishing is blocked "when she opens step 5" — she is looking at step 5,
    and an obstacle listed only under step 3 is one she has to go and find.
    """
    obstacles: list[Obstacle] = []
    stale = (
        inputs.evidence_fingerprint is not None
        and inputs.configuration_fingerprint is not None
        and inputs.evidence_fingerprint != inputs.configuration_fingerprint
    )
    if inputs.evidence_fingerprint is None:
        obstacles.append(
            Obstacle(
                key="no_evidence",
                what="The end-to-end sample test has not been run.",
                why_it_matters=(
                    "Both approvers read the evidence pack. Without one they would be "
                    "signing for a configuration nobody has watched run."
                ),
                how_to_fix="Run the end-to-end test on your sample file.",
                citation=CitationId(kind=CitationKind.FEED, subject=inputs.feed_id),
            )
        )
    elif stale:
        obstacles.append(
            Obstacle(
                key="stale_evidence",
                what="The configuration changed after the last end-to-end test.",
                why_it_matters=(
                    "The pack describes a configuration that is no longer the one being "
                    "approved. Publishing on it would mean the evidence and the thing it "
                    "vouches for are different objects."
                ),
                how_to_fix="Run the end-to-end test once more; it takes minutes.",
                citation=CitationId(kind=CitationKind.FEED, subject=inputs.feed_id),
            )
        )

    mapping = latest(objects, ObjectType.MAPPING)
    if mapping is not None:
        citation = CitationId(
            kind=CitationKind.MAPPING, subject=mapping.object_id, version=mapping.version
        )
        obstacles.extend(
            o for o in _mapping_obstacles(mapping, inputs.model, citation) if o.blocking
        )
    return tuple(obstacles)


def unmapped_fields(mapping: GovernedObject) -> tuple[str, ...]:
    """The addresses of every unmapped target field. Named, because the story
    says "the two fields named" and a count is not a name."""
    return tuple(
        line.address
        for line in mapping_from_governed(mapping).lines
        if line.status is LineStatus.UNMAPPED
    )
