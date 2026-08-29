"""Identity as the DOMAIN sees it: who is calling, and what they may reach.

    "Nobody anonymous, SSO only."
    — CF-V0-E2-01

These are VALUE TYPES, not port verbs, and the distinction is load-bearing.
`Principal` and `Scopes` are reasoned about by `core/security`, which decides
permissions with no server, no adapter and no token in sight. A port declares
what the platform ASKS an outside system to do; the shapes it speaks in belong
underneath it, or the layering runs backwards and core ends up importing ports
to talk about itself.

`ports/authn.py` re-exports every name here, so nothing outside core needs to
know the move happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType


@unique
class Role(StrEnum):
    """Wave 0 ships two, plus the administrator who assigns them.

    The full seven-role matrix with source/feed/domain/environment scoping is
    CF-V4-E2-02. Starting with two is not a shortcut: it is the smallest set
    that makes the Read-Only server-side denial testable, which is the actual
    Wave-0 guarantee.
    """

    ENGINEER = "engineer"
    READ_ONLY = "read_only"
    ADMINISTRATOR = "administrator"

    @property
    def may_change_things(self) -> bool:
        return self is not Role.READ_ONLY


@dataclass(frozen=True)
class Scopes:
    """What a caller may reach. Applied to the QUERY, never to the results.

        "retrieval applies the caller's RBAC scopes BEFORE any similarity
         computation" / "Apply a scope filter to results rather than to the
         query" (a documented don't)
        — docs/architecture/INVARIANTS.md

    Filtering results is the version that leaks: the row was fetched, it
    existed in memory, and every future code path that forgets the filter
    exposes it. Filtering the query means it was never read.
    """

    domains: frozenset[str] = frozenset()
    feeds: frozenset[str] = frozenset()
    environments: frozenset[str] = frozenset()

    def covers_feed(self, feed_id: str) -> bool:
        return "*" in self.feeds or feed_id in self.feeds

    def covers_domain(self, domain: str) -> bool:
        return "*" in self.domains or domain in self.domains


@dataclass(frozen=True)
class Principal:
    """A signed-in caller. Nobody touches the platform anonymously."""

    subject: str
    display_name: str
    roles: frozenset[Role] = field(default_factory=frozenset)
    scopes: Scopes = field(default_factory=Scopes)

    @property
    def has_access(self) -> bool:
        """A user in no CINQFLOW group.

        They must reach a clear "no access assigned — contact your
        administrator" page, and the attempt must be logged. They are NEVER
        shown a broken or empty application, which is what happens when this
        case is treated as an error instead of a state.
        """
        return bool(self.roles)

    @property
    def may_change_things(self) -> bool:
        return any(role.may_change_things for role in self.roles)

    def as_actor(self) -> Actor:
        return Actor(
            subject=self.subject, actor_type=ActorType.HUMAN, display_name=self.display_name
        )


class AuthenticationError(RuntimeError):
    """No valid identity. Never falls back to a default principal."""


class AuthorizationError(RuntimeError):
    """A valid identity without the required permission.

    Raised at the SERVER, not hidden in a menu:

        "Given a Read-Only user crafts a direct link to an edit screen, when
         they open it, then the request is denied at the server (not just
         hidden in the menu), and the attempt is recorded."
        — CF-V0-E2-01, guardrail
    """
