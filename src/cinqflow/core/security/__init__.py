"""CF-V0-E2-01 — who may do what, decided in one place.

    "Given a Read-Only user crafts a direct link to an edit screen, when they
     open it, then the request is DENIED AT THE SERVER (not just hidden in the
     menu), and the attempt is recorded."
    — CF-V0-E2-01, guardrail

The authorization DECISION is pure and lives here; the API merely asks. Two
consequences worth the indirection:

  • It can be tested exhaustively — every role against every action, in
    milliseconds, with no server running. A permission matrix tested by
    clicking is a permission matrix nobody tests.
  • There is exactly ONE place that says yes. A second call site with its own
    `if role == ...` is how a platform grows a permission bug that no audit
    finds, because the audit only sees the path that asked.

Wave 0 ships two roles plus the administrator. The full seven-role matrix with
source/feed/domain/environment scoping is CF-V4-E2-02 — but the SHAPE here is
already the scoped shape, so that story widens a decision rather than replacing
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from cinqflow.core.model.identity import Principal, Role


@unique
class Action(StrEnum):
    """What a caller can try to do. Named for the business act, not the verb.

    `RUN_PIPELINE` rather than `POST /batches` — because the permission
    question is "may this person run a feed?", and that stays the same question
    when the route is renamed.
    """

    VIEW = "view"
    CREATE_FEED = "create_feed"
    EDIT_FEED = "edit_feed"
    SUBMIT_FOR_REVIEW = "submit_for_review"
    APPROVE = "approve"
    PUBLISH = "publish"
    RETIRE = "retire"
    RUN_PIPELINE = "run_pipeline"
    RETRY_BATCH = "retry_batch"
    MANAGE_USERS = "manage_users"
    ASK_AGENT = "ask_agent"

    @property
    def changes_things(self) -> bool:
        """Read-Only users get full visibility and no buttons that change
        anything — so this property IS the Read-Only rule."""
        return self not in {Action.VIEW, Action.ASK_AGENT}


# The matrix. Data, not code: CF-V4-E2-02 widens the table rather than
# rewriting the decision. WHICH object types a holder of APPROVE may approve is
# a second, separate question — answered by `core/lifecycle.APPROVAL_ROUTING`,
# never here. This table says what a role may attempt; the router says where.
_PERMITTED: dict[Role, frozenset[Action]] = {
    Role.READ_ONLY: frozenset({Action.VIEW, Action.ASK_AGENT}),
    # The engineer BUILDS and OPERATES. Note what is still absent, unchanged
    # from Wave 0: approve and publish. "Separate create, approve, publish and
    # operate rights" — the person who builds a feed does not sign it off.
    Role.ENGINEER: frozenset(
        {
            Action.VIEW,
            Action.ASK_AGENT,
            Action.CREATE_FEED,
            Action.EDIT_FEED,
            Action.SUBMIT_FOR_REVIEW,
            Action.RUN_PIPELINE,
            Action.RETRY_BATCH,
        }
    ),
    # Plate 14's `platform_engineer` — the technical approver, and a DIFFERENT
    # person from the one who wrote the thing.
    Role.PLATFORM_ENGINEER: frozenset(
        {Action.VIEW, Action.ASK_AGENT, Action.APPROVE, Action.PUBLISH, Action.RETIRE}
    ),
    # The BA authors and submits; nothing they author can they approve — and
    # even if a BA were also granted APPROVE, the core's SelfApprovalError
    # holds. Two independent layers, deliberately.
    Role.BUSINESS_ANALYST: frozenset(
        {
            Action.VIEW,
            Action.ASK_AGENT,
            Action.CREATE_FEED,
            Action.EDIT_FEED,
            Action.SUBMIT_FOR_REVIEW,
        }
    ),
    Role.DATA_STEWARD: frozenset(
        {Action.VIEW, Action.ASK_AGENT, Action.APPROVE, Action.PUBLISH, Action.RETIRE}
    ),
    Role.BUSINESS_APPROVER: frozenset(
        {Action.VIEW, Action.ASK_AGENT, Action.APPROVE, Action.PUBLISH}
    ),
    # An administrator manages ACCESS. Note what is absent: an administrator
    # cannot approve either. "Separate create, approve, publish and operate
    # rights" is the MVP's segregation requirement, and the person who grants
    # permissions being able to use them all is how segregation dies.
    Role.ADMINISTRATOR: frozenset({Action.VIEW, Action.ASK_AGENT, Action.MANAGE_USERS}),
}


@dataclass(frozen=True)
class Decision:
    """Allowed or not, and WHY — so a refusal can explain itself and be logged.

    A boolean would make every call site invent its own message, and the audit
    row would say only "denied".
    """

    allowed: bool
    reason: str = ""
    scope_miss: bool = False
    """True when the caller has the permission but not the reach.

    Carried on the decision rather than sniffed from the reason string, because
    the API must answer a scope miss with a NOT-FOUND shape: a 403 saying
    "out of scope" tells the caller the feed exists, which is the leak the
    scope check was there to prevent.
    """

    def __bool__(self) -> bool:
        return self.allowed


def may(principal: Principal, action: Action, *, feed_id: str | None = None) -> Decision:
    """The one place that says yes.

    Scope is checked as part of the decision, not afterwards: an out-of-scope
    feed must be indistinguishable from a feed that does not exist, or the
    denial itself leaks which feeds are real.
    """
    if not principal.has_access:
        return Decision(False, "no access assigned — contact your administrator")

    permitted: frozenset[Action] = frozenset()
    for role in principal.roles:
        permitted |= _PERMITTED.get(role, frozenset())

    if action not in permitted:
        roles = ", ".join(sorted(r.value for r in principal.roles))
        return Decision(False, f"{action.value} is not permitted for {roles}")

    if feed_id is not None and not principal.scopes.covers_feed(feed_id):
        # Deliberately the same shape as "not found". An out-of-scope feed must
        # never be distinguishable from a non-existent one — otherwise the
        # denial tells the caller the feed exists.
        return Decision(False, "out of scope", scope_miss=True)

    return Decision(True)


def visible_feeds(principal: Principal, feed_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Filter a list to what this caller may see.

    "an out-of-scope feed is invisible in lists, search and exports alike" —
    and the filter is applied where the list is BUILT, never to a response
    that was already assembled.
    """
    if not principal.has_access:
        return ()
    return tuple(feed_id for feed_id in feed_ids if principal.scopes.covers_feed(feed_id))
