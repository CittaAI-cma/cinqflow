"""The universal HITL object — what every agent writes, and the only thing it writes.

    "Agents write ONLY to proposals.* · knowledge.* · ops.* · forecasts.* ·
     audit.agent_action — never to control.* or any data layer."
    "Agents propose; humans dispose. R4 is human-always and not configurable."
    — docs/architecture/plates/11-agent-runtime-and-the-risk-router.md

    intel_may_write: [proposals.*, knowledge.*, ops.*, forecasts.*,
                      audit.agent_action]
    — docs/architecture/plates/07-control-table-and-governed-object-model.md

ONE object for all four of Wave 1's R2 agents (schema inference, PHI detection,
mapping suggestion, NL->rule). Building a proposal type per agent would give
four review screens, four audit shapes and four places for "an agent applied
something" to become possible by accident.

THE SHAPE IS THE GUARANTEE. A proposal cannot become production state on its
own: `apply` returns a DRAFT governed object authored by THE HUMAN WHO APPROVED
IT, and that object then travels E11-01's lifecycle like anything else. So the
agent's output enters the world exactly where a hand-typed draft would, and
both universal negatives still bite — the approver of the proposal is the
author of the object, and therefore cannot approve the object.

CORRECTIONS ARE FUEL. The proposal keeps what the agent said; the applied
object keeps what the human decided. `corrections()` is the difference, and it
is what every eval set grows from — which is why the payload is never
overwritten by a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationId
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, RiskClass


class ProposalError(RuntimeError):
    """A proposal that would have crossed the line agents do not cross."""


class AgentDecisionError(ProposalError):
    """An agent tried to decide a proposal.

        "Agents propose; humans dispose."

    Raised rather than checked at a call site, because "the agent approved its
    own suggestion" is not a bug to find in review — it is the one outcome the
    whole HITL design exists to make unreachable.
    """


class NotAutomatableError(ProposalError):
    """An R4 proposal. Refused in code AND at the schema.

    `proposals.proposal` carries `CHECK risk_class IN ('R0','R1','R2','R3')`,
    so this is belt and braces on purpose: identity and PHI-consequential
    changes are human-always at any confidence, and a class that cannot be
    written cannot be automated by a later refactor either.
    """


@unique
class ProposalState(StrEnum):
    """DRAFT -> PENDING_REVIEW -> APPROVED|REJECTED -> APPLIED|FAILED.

    APPLIED and FAILED are separate terminals because "the human said yes and
    the write succeeded" and "the human said yes and the write did not" are
    different situations, and collapsing them loses the second one entirely.
    """

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"

    @property
    def is_open(self) -> bool:
        return self in {ProposalState.DRAFT, ProposalState.PENDING_REVIEW}

    @property
    def is_decided(self) -> bool:
        return self is not ProposalState.DRAFT and self is not ProposalState.PENDING_REVIEW


#: What a proposal may become next. Data, so the machine is readable and a new
#: state is a table edit rather than a rewritten conditional.
_TRANSITIONS: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.DRAFT: frozenset({ProposalState.PENDING_REVIEW, ProposalState.REJECTED}),
    ProposalState.PENDING_REVIEW: frozenset({ProposalState.APPROVED, ProposalState.REJECTED}),
    ProposalState.APPROVED: frozenset({ProposalState.APPLIED, ProposalState.FAILED}),
    ProposalState.REJECTED: frozenset(),
    ProposalState.APPLIED: frozenset(),
    ProposalState.FAILED: frozenset(),
}


@dataclass(frozen=True)
class Correction:
    """One thing a human changed about what the agent said.

        "corrections captured to eval set" — CF-V1-E5-02

    Both values are kept. A correction recording only the new value tells the
    eval set what is right and not what was wrong, and the second is the half
    that improves a prompt.
    """

    field_path: str
    proposed: Any
    accepted: Any

    @property
    def is_addition(self) -> bool:
        """The human supplied something the agent left as "needs your input".

        Counted separately from a correction that overwrote a wrong answer:
        an agent that declines to guess and a human fills the gap is the
        DESIGNED behaviour, not a miss.
        """
        return self.proposed is None


@dataclass(frozen=True)
class Proposal:
    """One agent suggestion, awaiting a person.

    `payload` is what the agent produced, verbatim and immutable through every
    transition — a decision never rewrites it. That is what makes the
    correction set recoverable a year later.
    """

    proposal_id: str
    agent: str
    capability: str
    risk_class: RiskClass
    run_id: str
    payload: dict[str, Any]
    created_by: Actor
    created_ts: datetime
    feed_id: str | None = None
    state: ProposalState = ProposalState.DRAFT
    confidence: float | None = None
    grounding_citations: tuple[CitationId, ...] = ()
    prompt_hash: str = ""
    decided_by: Actor | None = None
    decision_comment: str = ""
    decided_ts: datetime | None = None
    applied_object_type: ObjectType | None = None
    applied_object_id: str | None = None
    applied_version: int | None = None
    #: What the human changed. Empty on an untouched acceptance, which is the
    #: measurement CF-V1-E5-02's gate is stated in.
    corrections: tuple[Correction, ...] = ()

    def __post_init__(self) -> None:
        if not self.risk_class.automatable:
            raise NotAutomatableError(
                f"{self.agent} proposed at {self.risk_class.name} ({self.risk_class.label}). "
                "That class is human-always and not configurable at any confidence — there is "
                "no proposal to review because there is no automated path to review."
            )
        if self.created_by.actor_type is not ActorType.AI:
            raise ProposalError(
                f"{self.proposal_id} was created by a {self.created_by.actor_type.value}. "
                "A proposal is an AGENT's output; a person who wants a change makes a draft."
            )
        if not self.payload:
            raise ProposalError(
                f"{self.proposal_id} carries no payload — there is nothing to review"
            )

    @property
    def is_accepted_untouched(self) -> bool:
        """Approved with nothing changed. The unit the eval gate counts in."""
        decided = {ProposalState.APPROVED, ProposalState.APPLIED}
        return self.state in decided and not self.corrections


# ── the acts ─────────────────────────────────────────────────────────────────
def submit(proposal: Proposal, *, now: datetime | None = None) -> Proposal:
    """Draft -> Pending review. The agent's last act on its own suggestion."""
    return _move(proposal, ProposalState.PENDING_REVIEW, now=now)


def approve(
    proposal: Proposal,
    *,
    approver: Actor,
    comment: str = "",
    corrections: tuple[Correction, ...] = (),
    now: datetime | None = None,
) -> Proposal:
    """A person accepts the suggestion, possibly having changed it.

    `corrections` travel WITH the approval rather than being written
    afterwards: an approval recorded now and its corrections recorded later is
    an eval set that silently under-counts every crash in between.
    """
    _require_human(approver, "approve")
    moved = _move(proposal, ProposalState.APPROVED, now=now)
    return replace(
        moved,
        decided_by=approver,
        decision_comment=comment,
        decided_ts=moved.decided_ts,
        corrections=corrections,
    )


def reject(
    proposal: Proposal, *, approver: Actor, comment: str, now: datetime | None = None
) -> Proposal:
    """A person declines. A comment is REQUIRED.

    A rejection with no reason teaches the eval set nothing and teaches the
    next reviewer less — it is the most informative event an agent can
    produce, and discarding the reason wastes it.
    """
    _require_human(approver, "reject")
    if not comment.strip():
        raise ProposalError(
            "a rejection needs a reason: it is the most informative thing an agent's "
            "output can produce, and an unexplained refusal teaches nobody anything"
        )
    moved = _move(proposal, ProposalState.REJECTED, now=now)
    return replace(moved, decided_by=approver, decision_comment=comment)


def apply(
    proposal: Proposal,
    *,
    object_type: ObjectType,
    object_id: str,
    body: dict[str, Any],
    version: int = 1,
    now: datetime | None = None,
) -> tuple[Proposal, GovernedObject]:
    """Turn an approved proposal into a DRAFT governed object.

    Three things this deliberately does NOT do, each of which would be the
    shortest route to "an agent changed production":

      • it does not publish — the object arrives DRAFT and travels E11-01's
        lifecycle exactly as a hand-typed one would;
      • it does not author the object as the agent — the APPROVER is the
        author, so the universal negative bites and they cannot then approve
        the object they just accepted;
      • it does not read the payload. `body` is what the human accepted, which
        may differ from what the agent proposed, and taking the payload here
        would silently discard every correction.
    """
    if proposal.state is not ProposalState.APPROVED:
        raise ProposalError(
            f"{proposal.proposal_id} is {proposal.state.value}; only an approved proposal "
            "may be applied. Applying an undecided one is an agent writing production state."
        )
    if proposal.decided_by is None:  # pragma: no cover - approve() guarantees this
        raise ProposalError(f"{proposal.proposal_id} is approved by nobody")

    stamp = now or datetime.now(UTC)
    applied = replace(
        _move(proposal, ProposalState.APPLIED, now=stamp),
        applied_object_type=object_type,
        applied_object_id=object_id,
        applied_version=version,
    )
    obj = GovernedObject(
        object_type=object_type,
        object_id=object_id,
        version=version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=proposal.decided_by,
        created_ts=stamp,
        body=body,
    )
    return applied, obj


def fail(proposal: Proposal, *, detail: str, now: datetime | None = None) -> Proposal:
    """The human said yes and the write did not happen. Recorded, not silent."""
    moved = _move(proposal, ProposalState.FAILED, now=now)
    note = f"{moved.decision_comment} | apply failed: {detail}".strip(" |")
    return replace(moved, decision_comment=note)


# ── internals ────────────────────────────────────────────────────────────────
def _move(proposal: Proposal, to: ProposalState, *, now: datetime | None = None) -> Proposal:
    allowed = _TRANSITIONS[proposal.state]
    if to not in allowed:
        raise ProposalError(
            f"{proposal.proposal_id}: {proposal.state.value} -> {to.value} is not a legal "
            f"move (from {proposal.state.value} you may go to "
            f"{', '.join(sorted(s.value for s in allowed)) or 'nowhere — it is terminal'})"
        )
    return replace(proposal, state=to, decided_ts=now or datetime.now(UTC))


def _require_human(actor: Actor, act: str) -> None:
    if actor.actor_type is not ActorType.HUMAN:
        raise AgentDecisionError(
            f"a {actor.actor_type.value} actor tried to {act} a proposal. Agents propose; "
            "humans dispose — at every confidence, in every environment."
        )


# ── the correction set ───────────────────────────────────────────────────────
def diff_fields(
    proposed: dict[str, Any], accepted: dict[str, Any], *, key: str, fields: tuple[str, ...]
) -> tuple[Correction, ...]:
    """Compare two lists of records by `key`, field by field.

    Generic on purpose: schema inference compares columns, mapping compares
    mapping lines, and rule generation compares rules. One comparison means one
    definition of "accepted without correction", and therefore one number the
    four eval gates are all stated in.
    """
    proposed_by = {str(r.get(key)): r for r in proposed.get("records", ())}
    accepted_by = {str(r.get(key)): r for r in accepted.get("records", ())}
    found: list[Correction] = []

    for name in sorted(proposed_by.keys() | accepted_by.keys()):
        before = proposed_by.get(name)
        after = accepted_by.get(name)
        if before is None:
            found.append(Correction(field_path=name, proposed=None, accepted=after))
            continue
        if after is None:
            found.append(Correction(field_path=name, proposed=before, accepted=None))
            continue
        for attribute in fields:
            if before.get(attribute) != after.get(attribute):
                found.append(
                    Correction(
                        field_path=f"{name}.{attribute}",
                        proposed=before.get(attribute),
                        accepted=after.get(attribute),
                    )
                )
    return tuple(found)


@dataclass(frozen=True)
class Acceptance:
    """The eval gate's arithmetic, with the model's share separated out.

        "eval red until >= 90% fields accepted without correction on feeds with
         existing human schemas"
        — CF-V1-E5-02

    `deterministic` and `inferred` are reported apart because a contract whose
    columns were mostly settled by counting is not evidence that a model is
    good at inference. The GATE is on the whole contract — that is what a BA
    accepts — and the split is what stops the number being read as a claim
    about the model.
    """

    total: int = 0
    corrected: int = 0
    deterministic_total: int = 0
    deterministic_corrected: int = 0
    inferred_total: int = 0
    inferred_corrected: int = 0
    additions: int = 0

    @property
    def accepted(self) -> int:
        return self.total - self.corrected

    @property
    def rate(self) -> float:
        return self.accepted / self.total if self.total else 0.0

    @property
    def inferred_rate(self) -> float:
        """The model's own hit rate. Never the headline, always reported."""
        if not self.inferred_total:
            return 0.0
        return (self.inferred_total - self.inferred_corrected) / self.inferred_total

    def passes(self, threshold: float) -> bool:
        """A zero-field measurement is a FAILURE, not a vacuous pass.

        An eval that returns 100% because it graded nothing is the most
        dangerous green there is.
        """
        return self.total > 0 and self.rate >= threshold

    def report(self, threshold: float) -> str:
        return (
            f"{self.accepted}/{self.total} fields accepted without correction "
            f"({self.rate:.1%}, gate {threshold:.0%}) — "
            f"deterministic {self.deterministic_total - self.deterministic_corrected}"
            f"/{self.deterministic_total}, "
            f"inferred {self.inferred_total - self.inferred_corrected}/{self.inferred_total} "
            f"({self.inferred_rate:.1%}); {self.additions} field(s) the agent declined to guess"
        )


def measure(proposal: Proposal, *, deterministic_keys: frozenset[str] = frozenset()) -> Acceptance:
    """Grade one decided proposal.

    The unit is a RECORD — one contract column — and a record counts as
    corrected if the human changed anything about it. Counting attributes
    instead would make the gate depend on how many attributes a record happens
    to carry, so a schema that gained a `precision` field would look better at
    no improvement in accuracy.

    ADDITIONS ARE NOT MISSES. A column the agent declined to type — "needs your
    input" — that a human then filled in is the designed behaviour, so it is
    counted and reported separately rather than scored against the model.
    """
    key = str(proposal.payload.get("key", "name"))
    records = proposal.payload.get("records", ())
    names = {str(r.get(key)) for r in records}
    total = len(names)
    if not total:
        return Acceptance()

    corrected_names = {
        c.field_path.split(".", 1)[0] for c in proposal.corrections if not c.is_addition
    } & names
    additions = sum(1 for c in proposal.corrections if c.is_addition)

    deterministic = names & deterministic_keys
    deterministic_corrected = corrected_names & deterministic

    return Acceptance(
        total=total,
        corrected=len(corrected_names),
        deterministic_total=len(deterministic),
        deterministic_corrected=len(deterministic_corrected),
        inferred_total=total - len(deterministic),
        inferred_corrected=len(corrected_names) - len(deterministic_corrected),
        additions=additions,
    )


# ── persistence shape ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ProposalBody:
    """Helper for the JSONB round trip. Kept beside the type it serialises so
    a new field cannot be added to one without the other."""

    @staticmethod
    def to_dict(proposal: Proposal) -> dict[str, Any]:
        return {
            "payload": proposal.payload,
            "grounding_citations": [str(c) for c in proposal.grounding_citations],
            "corrections": [
                {"field_path": c.field_path, "proposed": c.proposed, "accepted": c.accepted}
                for c in proposal.corrections
            ],
        }

    @staticmethod
    def corrections_from(raw: dict[str, Any]) -> tuple[Correction, ...]:
        return tuple(
            Correction(
                field_path=c["field_path"], proposed=c.get("proposed"), accepted=c.get("accepted")
            )
            for c in raw.get("corrections", ())
        )
