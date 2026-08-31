"""The governed object, and the ONE lifecycle every object type travels.

    "no object type may opt out of the lifecycle state machine"
    "the author of a change never approves it            # refused + logged"
    "nothing reaches Published without a named approver  # refused + logged"
    — docs/architecture/INVARIANTS.md, governance

ADR-0006 makes this one state machine, reused. The temptation in every registry
story is a private little status field — "draft/active" on the feed, something
slightly different on the contract — and that is exactly how a platform ends up
with four half-governed object types and one real one. So the lifecycle lives
here, once, and archetype A's recipe says it in as many words: reuse the
lifecycle engine, never a private state machine.

The two universal negatives are properties of THIS module, which is what makes
them structurally impossible rather than diligently avoided.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any, Self

from cinqflow.core.model.vocabulary import ActorType, StatusWord


@unique
class ObjectType(StrEnum):
    """Every governed object type. The registry and governance schemas.

    Adding a member means the new type inherits the whole lifecycle, the audit
    trail and both universal negatives — for free, and unavoidably.
    """

    SOURCE = "source"
    FEED = "feed"
    CONTRACT = "contract"
    MAPPING = "mapping"
    DQ_RULE = "dq_rule"
    GLOSSARY_TERM = "glossary_term"
    RUNBOOK = "runbook"
    RELEASE = "release"
    PROMPT = "prompt"
    EXECUTION_PLANE_CONTRACT = "execution_plane_contract"
    #: CF-V1-E16-04/E16-06. An uploaded document (a payer companion guide, a
    #: client spec) on its way into the knowledge plane. `body` carries its
    #: `ParsedDocument` (as plain data — see `core.knowledge.KnowledgeDocument`)
    #: plus the optional `feed_id` E16-06 tags an upload to. Published is the
    #: gate `core.knowledge.chunk_document` already enforces for every other
    #: source; this object earns that gate through the SAME steward-approval
    #: route glossary terms and runbooks already travel — no side door.
    KNOWLEDGE_DOCUMENT = "knowledge_document"


@unique
class LifecycleState(StrEnum):
    """DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED, with RETIRED and
    REJECTED as the exits.

    Only PUBLISHED is executable. The engine reads published metadata, which is
    what makes "no unapproved configuration reaches production" a property of
    the reader rather than a rule the writer is asked to respect.
    """

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    RETIRED = "retired"

    @property
    def is_executable(self) -> bool:
        return self is LifecycleState.PUBLISHED

    @property
    def status_word(self) -> StatusWord:
        """What a user sees. The richer machine stays internal."""
        return {
            LifecycleState.DRAFT: StatusWord.PROCESSING,
            LifecycleState.PENDING_REVIEW: StatusWord.NEEDS_REVIEW,
            LifecycleState.APPROVED: StatusWord.NEEDS_REVIEW,
            LifecycleState.PUBLISHED: StatusWord.COMPLETED,
            LifecycleState.REJECTED: StatusWord.NEEDS_ATTENTION,
            LifecycleState.RETIRED: StatusWord.COMPLETED,
        }[self]


# The only transitions that exist. Anything else is refused and logged.
TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.DRAFT: frozenset({LifecycleState.PENDING_REVIEW, LifecycleState.RETIRED}),
    LifecycleState.PENDING_REVIEW: frozenset(
        {LifecycleState.APPROVED, LifecycleState.REJECTED, LifecycleState.DRAFT}
    ),
    LifecycleState.APPROVED: frozenset({LifecycleState.PUBLISHED, LifecycleState.DRAFT}),
    LifecycleState.PUBLISHED: frozenset({LifecycleState.RETIRED, LifecycleState.DRAFT}),
    LifecycleState.REJECTED: frozenset({LifecycleState.DRAFT}),
    LifecycleState.RETIRED: frozenset(),
}


class LifecycleViolationError(RuntimeError):
    """An attempted state change the machine does not permit.

    Raised — never logged-and-continued. Every raise here is a documented
    "don't" with a negative test that makes the attempt and asserts both the
    refusal and the audit entry.
    """


class SelfApprovalError(LifecycleViolationError):
    """The author tried to approve their own change.

    Universal negative #1. Structurally impossible, and proven by a test that
    tries it.
    """


class UnnamedApproverError(LifecycleViolationError):
    """Publication attempted without a named approver.

    Universal negative #2. "Named" means a person, not a role and not a
    service account: an approval nobody's name is attached to is an approval
    nobody is accountable for.
    """


@dataclass(frozen=True)
class Actor:
    """Who did it. `type` is never inferred — an AI action that reads as human
    defeats the entire audit trail."""

    subject: str
    actor_type: ActorType
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("an actor without a subject is an anonymous action")


@dataclass(frozen=True)
class AuditEntry:
    """One append-only audit row.

    There is no `update` and no `delete` for these anywhere in the platform —
    not on the port, not in the schema, not for administrators.
    """

    object_type: ObjectType
    object_id: str
    version: int
    action: str
    actor: Actor
    occurred_ts: datetime
    from_state: LifecycleState | None = None
    to_state: LifecycleState | None = None
    detail: str = ""

    @property
    def actor_type(self) -> ActorType:
        return self.actor.actor_type


@dataclass(frozen=True)
class GovernedObject:
    """The common shape. Every registry object is one of these.

    `body` carries the type-specific payload; everything the LIFECYCLE needs is
    a first-class field, because governance that depends on reading inside an
    opaque blob is governance that will eventually be skipped.
    """

    object_type: ObjectType
    object_id: str
    version: int
    lifecycle_state: LifecycleState
    created_by: Actor
    created_ts: datetime
    body: dict[str, Any] = field(default_factory=dict)
    approved_by: Actor | None = None
    approved_ts: datetime | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("versions start at 1")
        if self.lifecycle_state is LifecycleState.PUBLISHED and self.approved_by is None:
            raise UnnamedApproverError(
                f"{self.object_type}:{self.object_id}@v{self.version} is Published with no "
                "named approver. Nothing reaches Published without one."
            )

    # ── the state machine, and the two universal negatives ───────────────────
    def transition_to(
        self, target: LifecycleState, *, actor: Actor, now: datetime | None = None
    ) -> tuple[Self, AuditEntry]:
        """Move to a new state, or refuse.

        Returns the new object AND its audit entry together, deliberately: a
        state change that could be persisted without its audit row is a state
        change that eventually will be.
        """
        occurred = now or datetime.now(UTC)
        allowed = TRANSITIONS[self.lifecycle_state]
        if target not in allowed:
            permitted = ", ".join(sorted(s.value for s in allowed)) or "nothing (terminal)"
            raise LifecycleViolationError(
                f"{self.object_type}:{self.object_id} cannot go "
                f"{self.lifecycle_state.value} -> {target.value}. Permitted: {permitted}."
            )

        approver = self.approved_by
        approved_ts = self.approved_ts

        if target is LifecycleState.APPROVED:
            if actor.subject == self.created_by.subject:
                raise SelfApprovalError(
                    f"{actor.subject} authored {self.object_type}:{self.object_id}@v{self.version} "
                    "and may not approve it. The author of a change never approves it."
                )
            if actor.actor_type is not ActorType.HUMAN:
                raise UnnamedApproverError(
                    f"{actor.subject} is a {actor.actor_type.value} actor. Agents propose; "
                    "humans dispose — an approver is a named person."
                )
            approver, approved_ts = actor, occurred

        if target is LifecycleState.PUBLISHED and approver is None:
            raise UnnamedApproverError(
                f"{self.object_type}:{self.object_id}@v{self.version} has no named approver."
            )

        moved = replace(self, lifecycle_state=target, approved_by=approver, approved_ts=approved_ts)
        entry = AuditEntry(
            object_type=self.object_type,
            object_id=self.object_id,
            version=self.version,
            action=f"transition:{target.value}",
            actor=actor,
            occurred_ts=occurred,
            from_state=self.lifecycle_state,
            to_state=target,
        )
        return moved, entry

    def new_version(
        self, body: dict[str, Any], *, actor: Actor, now: datetime | None = None
    ) -> Self:
        """Amend by creating the NEXT version in Draft. Never edit in place.

        A published object stays exactly as it was approved; the amendment is a
        new draft that must earn its own approval. That is what makes
        "promoted configuration is byte-identical to what was approved" true.
        """
        return replace(
            self,
            version=self.version + 1,
            lifecycle_state=LifecycleState.DRAFT,
            body=body,
            created_by=actor,
            created_ts=now or datetime.now(UTC),
            approved_by=None,
            approved_ts=None,
        )

    @property
    def is_executable(self) -> bool:
        """The engine reads only this. Unapproved configuration cannot run
        because the reader will not read it."""
        return self.lifecycle_state.is_executable
