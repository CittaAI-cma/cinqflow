"""The `authn` pin — identify the caller and their scopes.

    verb: identity_and_scopes   mock: static   dev: keycloak_oidc
    target: entra_oidc
    — docs/architecture/plates/04-pin-out-map.md

OIDC is OIDC. Keycloak at rung 1 and Entra ID at rung 3 are the same libraries
and the same code path; the profile carries the discovery URL and the claim
mapping. That is why this pin's swap cost is PROFILE rather than ADAPTER.

CF-V0-E2-01's first "don't" is "store any credentials of its own", so there is
no `authenticate(username, password)` verb here and never will be. The platform
verifies a token someone else issued; it never holds a password.

The value types — Role, Scopes, Principal — live in `core/model/identity.py`
and are re-exported here. A port declares verbs; the domain owns the nouns.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from cinqflow.core.model.identity import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    Role,
    Scopes,
)

__all__ = [
    "AuthenticationError",
    "AuthnPort",
    "AuthorizationError",
    "Principal",
    "Role",
    "Scopes",
]


@runtime_checkable
class AuthnPort(Protocol):
    def verify(self, token: str) -> Principal:
        """Verify a bearer token against the issuer's JWKS.

        Raises AuthenticationError. Never returns an anonymous principal — a
        falsy return would be checked inconsistently at call sites, and the one
        site that forgot would be an anonymous write.
        """
        ...

    def discovery_url(self) -> str:
        """From the profile. Keycloak, Entra — same code, different URL."""
        ...

    def directory(self) -> Sequence[Principal]:
        """Who the issuer says exists, and what groups they are in.

        Read-only, and that is the whole point: CINQFLOW is never the source of
        truth for identity. The Users & Roles screen SHOWS what the IdP asserts;
        assigning access happens in the IdP, so there is no path here that could
        create a user the directory does not have.

        At rung 3 this is a Graph group-membership read. At rung 0.5 it is the
        dev user table. Same verb, same screen.
        """
        ...
