"""Who is calling, and may they. The only two questions the routes ask.

    "Given a Read-Only user crafts a direct link to an edit screen, when they
     open it, then the request is DENIED AT THE SERVER (not just hidden in the
     menu), and the attempt is recorded."
    — CF-V0-E2-01, guardrail

The shape here is the whole point. `require(Action.EDIT_FEED)` is a dependency,
so a route cannot be added without stating what permission it needs — there is
no code path from an HTTP request to a handler that skips the question. The
alternative (checking inside handlers) makes "did we remember?" a review
question on every future route, forever.

`Depends` on a route is also introspectable, which is how the catalogue test
below asserts that EVERY mutating route carries a permission — a guarantee no
amount of care provides.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from cinqflow.api.audit import AuditLog
from cinqflow.core.model.governed import ObjectType
from cinqflow.core.security import Action, may
from cinqflow.ports.authn import AuthenticationError, AuthnPort, Principal


@dataclass
class Wiring:
    """The pins this API is fitted to. Injected, never imported.

    The API knows the `authn` and `metadata_db` PROTOCOLS. Which adapter is
    behind them — static or Keycloak, memory or Postgres — is a profile line,
    and that is what makes rung 0.5 and rung 3 the same code path.
    """

    authn: AuthnPort
    audit: AuditLog


def wiring_of(request: Request) -> Wiring:
    return request.app.state.wiring  # type: ignore[no-any-return]


SIGN_IN_REQUIRED = "sign in to continue — nobody touches the platform anonymously"

# One sentence for "does not exist" and for "not yours". Two sentences would be
# an oracle: ask for every feed id and the difference tells you which are real.
NOT_FOUND = "no such feed"


def current_principal(request: Request) -> Principal:
    """Verify the bearer token, or refuse. Never returns an anonymous caller.

    A falsy "anonymous principal" would be checked inconsistently at call sites,
    and the one site that forgot would be an anonymous write.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SIGN_IN_REQUIRED)
    try:
        return wiring_of(request).authn.verify(token)
    except AuthenticationError as failure:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(failure)) from None


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(
    action: Action, *, object_type: ObjectType = ObjectType.FEED
) -> Callable[..., Awaitable[Principal]]:
    """A dependency that asks `core.security.may` and audits the refusal.

    Note what it does NOT do: decide. The decision is pure and lives in
    `core/security`, tested exhaustively with no server running. This function
    is only the place the answer is enforced and recorded.
    """

    async def dependency(request: Request, principal: CurrentPrincipal) -> Principal:
        feed_id = request.path_params.get("feed_id")
        decision = may(principal, action, feed_id=feed_id)
        if decision:
            return principal

        wiring_of(request).audit.record_denial(
            actor=principal.as_actor(),
            action=action,
            decision=decision,
            object_type=object_type,
            object_id=feed_id or "-",
        )
        if decision.scope_miss:
            # NOT-FOUND shaped, deliberately. The caller has the permission but
            # not the reach; a 403 saying "out of scope" would confirm the feed
            # exists, which is precisely what the scope check exists to prevent.
            # The audit row above still records the real reason — the ledger
            # knows what the response does not say.
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)

    # Stamped so the catalogue test can ASSERT that every mutating route carries
    # a permission, rather than a reviewer checking each new route by eye.
    dependency.cinqflow_action = action  # type: ignore[attr-defined]
    return dependency


def declared_action(dependency: object) -> Action | None:
    """The action a `require(...)` dependency enforces, or None if it is not one."""
    return getattr(dependency, "cinqflow_action", None)
