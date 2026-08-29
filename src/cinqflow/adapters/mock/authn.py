"""static — a dev identity table. Holds no passwords, by design."""

from __future__ import annotations

from collections.abc import Sequence

from cinqflow.ports import port
from cinqflow.ports.authn import AuthenticationError, Principal, Role, Scopes


@port("authn", "mock")
class StaticAuthn:
    """A stand-in for an OIDC provider, not a credential store.

    CF-V0-E2-01's first "don't" is "store any credentials of its own", so this
    maps a token that IS a subject to the claims Keycloak (rung 1) and Entra
    (rung 3) will emit. Same code path downstream; only the profile moves.

    It deliberately keeps the "user in no CINQFLOW group" case as a valid
    PRINCIPAL rather than an error, because the story requires that person to
    reach a clear "no access assigned" page — never a broken or empty app.
    """

    def __init__(self, users: dict[str, Principal] | None = None) -> None:
        self._users = dict(users or _DEFAULT_USERS)

    def verify(self, token: str) -> Principal:
        try:
            return self._users[token.strip()]
        except KeyError:
            raise AuthenticationError(
                "no valid identity for that token — nobody touches the platform anonymously"
            ) from None

    def discovery_url(self) -> str:
        return "static://dev-users"

    def directory(self) -> Sequence[Principal]:
        """The dev user table, including the person in no group — because the
        Users & Roles screen has to be able to show them to the administrator
        who is supposed to fix it."""
        return tuple(sorted(self._users.values(), key=lambda p: p.subject))


def _principal(subject: str, name: str, *roles: Role) -> Principal:
    return Principal(
        subject=subject,
        display_name=name,
        roles=frozenset(roles),
        scopes=Scopes(
            domains=frozenset({"*"}) if roles else frozenset(),
            feeds=frozenset({"*"}) if roles else frozenset(),
            environments=frozenset({"dev"}) if roles else frozenset(),
        ),
    )


_DEFAULT_USERS: dict[str, Principal] = {
    "dev-engineer@cinqcare.test": _principal(
        "dev-engineer@cinqcare.test", "Arun Menon", Role.ENGINEER
    ),
    "dev-analyst@cinqcare.test": _principal(
        "dev-analyst@cinqcare.test", "Priya Nair", Role.READ_ONLY
    ),
    "dev-admin@cinqcare.test": _principal(
        "dev-admin@cinqcare.test", "Steve Mathews", Role.ADMINISTRATOR
    ),
    # A real person in no CINQFLOW group — the exception case, not an error case.
    "dev-nogroup@cinqcare.test": _principal("dev-nogroup@cinqcare.test", "Unassigned User"),
}
