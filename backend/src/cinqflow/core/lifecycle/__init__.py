"""CF-V1-E11-01 — one lifecycle engine, and its approval routing as DATA.

    approval_routing:
      mapping|dq_rule -> data_steward
      config|contract -> platform_engineer
      publication     -> business_approver + technical_approver
      identity_R4     -> data_steward_always
    — docs/architecture/plates/14-governed-object-lifecycle-and-the-release-path.md

The state machine itself lives in `core/model/governed.py` and is not repeated
here — this module answers the OTHER half of E11-01: given an object type, WHO
may review it, and what does each governance act look like as a (moved object,
audit entry) pair.

Everything here is pure. Persistence is the metadata_db pin's
`record_transition`, called by the API — core computes the transition, the pin
stores it, and neither can be reached without the other's discipline: the
core refuses illegal moves, the pin refuses to store a state without its row.

Routing is a TABLE, not code, so CF-V4-E2-02 widens it and the completeness
test (`every ObjectType is routed`) keeps a new object type from quietly
shipping without the guardrails the first ten have.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cinqflow.core.impact import ImpactPacket, refuse_if_unknown
from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    LifecycleViolationError,
    ObjectType,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.registry.operations import ActivationBlockedError, readiness_of


class ApprovalRoutingError(LifecycleViolationError):
    """The caller holds APPROVE, but not for THIS object type.

    The permission matrix says what a role may attempt; this router says where.
    A steward approving a schema contract is exactly as refused — and exactly
    as logged — as a Read-Only user approving anything.
    """


@dataclass(frozen=True)
class Route:
    """Who reviews, and who releases, one object type."""

    reviewers: frozenset[Role]
    publishers: frozenset[Role]


_STEWARDED = Route(
    reviewers=frozenset({Role.DATA_STEWARD}),
    publishers=frozenset({Role.DATA_STEWARD}),
)
# Config and contracts route to the PLATFORM engineer — plate 14's technical
# approver, deliberately not the engineer who authors and operates. Publication
# additionally admits the business approver: CF-V1-E4-03's dual signature
# (business AND technical, both recorded on the evidence pack) arrives with the
# release packet; this table names who may hold the pen at all.
_ENGINEERED = Route(
    reviewers=frozenset({Role.PLATFORM_ENGINEER}),
    publishers=frozenset({Role.PLATFORM_ENGINEER, Role.BUSINESS_APPROVER}),
)

APPROVAL_ROUTING: dict[ObjectType, Route] = {
    ObjectType.SOURCE: _ENGINEERED,
    ObjectType.FEED: _ENGINEERED,
    ObjectType.CONTRACT: _ENGINEERED,
    ObjectType.PROMPT: _ENGINEERED,
    ObjectType.EXECUTION_PLANE_CONTRACT: _ENGINEERED,
    #: Same route as CONTRACT: a schema-shaped, platform-wide object whose
    #: DEPLOYMENT is a platform-engineering act. The steward's part is
    #: upstream of this — deciding each workbook discrepancy before the
    #: model is even submitted — not a second gate on the same act.
    ObjectType.ODS_MODEL: _ENGINEERED,
    ObjectType.MAPPING: _STEWARDED,
    ObjectType.DQ_RULE: _STEWARDED,
    ObjectType.GLOSSARY_TERM: _STEWARDED,
    ObjectType.RUNBOOK: _STEWARDED,
    ObjectType.KNOWLEDGE_DOCUMENT: _STEWARDED,
    #: CF-V3-E10-03's own persona: "As a Data Steward, I want Silver ODS
    #: publication per batch to pass a final gate." The steward who
    #: reviews a mapping's dedup precedence is the same one who signs off
    #: on the batch that ran it — no second, engineering-flavoured lane.
    ObjectType.ODS_BATCH_CERTIFICATION: _STEWARDED,
    ObjectType.RELEASE: Route(
        reviewers=frozenset({Role.PLATFORM_ENGINEER, Role.BUSINESS_APPROVER}),
        publishers=frozenset({Role.PLATFORM_ENGINEER, Role.BUSINESS_APPROVER}),
    ),
}


def _routed(obj: GovernedObject, roles: frozenset[Role], *, publishing: bool) -> None:
    route = APPROVAL_ROUTING[obj.object_type]
    allowed = route.publishers if publishing else route.reviewers
    if not (roles & allowed):
        act = "publish or retire" if publishing else "review"
        expected = ", ".join(sorted(r.value for r in allowed))
        held = ", ".join(sorted(r.value for r in roles)) or "no role"
        raise ApprovalRoutingError(
            f"{obj.object_type.value} objects {act} through {expected}; the caller holds "
            f"{held}. The permission matrix says what a role may attempt — this router "
            "says where."
        )


# ── the governance acts, each returning (moved object, its audit entry) ──────


def submit(
    obj: GovernedObject, *, actor: Actor, comment: str = "", now: datetime | None = None
) -> tuple[GovernedObject, AuditEntry]:
    """Draft -> In Review (and Rejected -> Draft -> In Review is two acts,
    deliberately: the resubmission is visible in the trail, not elided).

    CF-V1-E3-02 adds the READINESS gate here rather than at save. A
    half-gathered feed must SAVE — an analyst waiting three days for a payer's
    SLA needs somewhere to keep what they have — and what is refused is asking
    somebody to review a feed nobody could operate. Validation-at-save teaches
    people to type placeholder values into required fields, and a registry
    full of `owner@example.com` is worse than one with visible gaps.

    Placed in the ENGINE rather than in the route so that every path to
    submission is gated: `readiness_of` is total over `ObjectType`, so later
    stories widen the checklist by teaching that function about their type
    rather than by adding a second guard somewhere else.
    """
    ready = readiness_of(obj)
    if not ready.is_ready:
        raise ActivationBlockedError(obj.object_id, ready)
    moved, entry = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=actor, now=now)
    return moved, _with_comment(entry, comment)


def request_changes(
    obj: GovernedObject,
    *,
    actor: Actor,
    roles: frozenset[Role],
    comment: str,
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry]:
    """In Review -> Draft, with the reviewer's comment REQUIRED.

    "support request-changes with comments, and re-submission that preserves
    the conversation" — the conversation IS the audit trail on this object,
    and a request-changes with nothing said is a rejection the author cannot
    act on.
    """
    if not comment.strip():
        raise LifecycleViolationError(
            "request-changes requires a comment — a change request with nothing said "
            "is a rejection the author cannot act on"
        )
    _routed(obj, roles, publishing=False)
    moved, entry = obj.transition_to(LifecycleState.DRAFT, actor=actor, now=now)
    return moved, _with_comment(entry, comment)


def approve(
    obj: GovernedObject,
    *,
    actor: Actor,
    roles: frozenset[Role],
    comment: str = "",
    packet: ImpactPacket | None = None,
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry]:
    """In Review -> Approved. Four gates, and the ORDER is chosen for the
    message the approver gets, since every one of them refuses without
    persisting anything:

    1. routing — is this the caller's lane at all;
    2. the core's two universal negatives (author never approves own; approver
       is a named human), which live in `transition_to` and are NOT re-checked
       here, because a second copy of a guarantee is where the first drifts.
       They come before the softer gates deliberately: "supply a rationale" is
       the wrong thing to tell someone whose real problem is that they wrote
       the change themselves;
    3. the packet — CF-V1-E11-02: a hole in the impact blocks the signature;
    4. the rationale — required, and it becomes part of the audit record.

    `packet` is optional in the signature and supplied by every real caller —
    a test may approve a bare object, but the API route always builds one.
    """
    _routed(obj, roles, publishing=False)
    moved, entry = obj.transition_to(LifecycleState.APPROVED, actor=actor, now=now)
    if packet is not None:
        refuse_if_unknown(packet)
    if not comment.strip():
        raise LifecycleViolationError(
            "an approval must state its rationale — it becomes part of the audit record, "
            "and an approval nobody explained is the rubber stamp this platform refuses "
            "to make available"
        )
    return moved, _with_comment(entry, comment)


def reject(
    obj: GovernedObject,
    *,
    actor: Actor,
    roles: frozenset[Role],
    comment: str,
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry]:
    """In Review -> Rejected. A terminal 'no' (the author may redraft), and
    like request-changes it must say why."""
    if not comment.strip():
        raise LifecycleViolationError("a rejection must say why — the author has to act on it")
    _routed(obj, roles, publishing=False)
    moved, entry = obj.transition_to(LifecycleState.REJECTED, actor=actor, now=now)
    return moved, _with_comment(entry, comment)


def publish(
    obj: GovernedObject,
    *,
    actor: Actor,
    roles: frozenset[Role],
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry]:
    """Approved -> Published. Only now is the object executable — the engine
    reads Published metadata and nothing else."""
    _routed(obj, roles, publishing=True)
    return obj.transition_to(LifecycleState.PUBLISHED, actor=actor, now=now)


def retire(
    obj: GovernedObject,
    *,
    actor: Actor,
    roles: frozenset[Role],
    comment: str = "",
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry]:
    """-> Retired. History preserved; feeds retire, never vanish."""
    _routed(obj, roles, publishing=True)
    moved, entry = obj.transition_to(LifecycleState.RETIRED, actor=actor, now=now)
    return moved, _with_comment(entry, comment)


# ── the Work Queue — one view of everything awaiting a given person ──────────


def awaiting_review_by(
    objects: tuple[GovernedObject, ...], *, roles: frozenset[Role], subject: str
) -> tuple[GovernedObject, ...]:
    """Everything In Review that routes to one of the caller's roles — minus
    what the caller authored, which they could not approve anyway. The queue
    never offers work the engine would refuse."""
    return tuple(
        obj
        for obj in objects
        if obj.lifecycle_state is LifecycleState.PENDING_REVIEW
        and (roles & APPROVAL_ROUTING[obj.object_type].reviewers)
        and obj.created_by.subject != subject
    )


def submitted_by(
    objects: tuple[GovernedObject, ...], *, subject: str
) -> tuple[GovernedObject, ...]:
    """The caller's own objects still in flight — what they are waiting ON."""
    in_flight = {LifecycleState.DRAFT, LifecycleState.PENDING_REVIEW, LifecycleState.REJECTED}
    return tuple(
        obj
        for obj in objects
        if obj.created_by.subject == subject and obj.lifecycle_state in in_flight
    )


def _with_comment(entry: AuditEntry, comment: str) -> AuditEntry:
    if not comment.strip():
        return entry
    from dataclasses import replace

    return replace(entry, detail=comment.strip())
