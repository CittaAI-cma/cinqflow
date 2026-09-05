"""Dependency wiring: settings-bound FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import psycopg
from fastapi import Depends, Header, HTTPException, status

from cinqflow.auth.security import InvalidToken, decode_token
from cinqflow.auth.store import AuthStore
from cinqflow.db import connect
from cinqflow.settings import Settings


def make_get_conn(settings: Settings) -> Callable[[], Iterator[psycopg.Connection]]:
    """A per-request DB connection dependency bound to `settings`."""

    def get_conn() -> Iterator[psycopg.Connection]:
        with connect(settings) as conn:
            yield conn

    return get_conn


def make_get_current_user(settings: Settings, get_conn: Callable[[], Iterator]):
    """Bearer-token auth. Re-reads the user (and their roles) from the database
    on every call rather than trusting the token's signature alone, so a
    deactivated account loses access immediately instead of riding out the
    access token's TTL - see docs/blueprints/auth-and-user-management.md §3."""

    def get_current_user(
        authorization: str | None = Header(default=None),
        conn=Depends(get_conn),
    ):
        if authorization is None or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
        token = authorization.split(" ", 1)[1].strip()
        try:
            user_id = decode_token(token, "access", settings)
        except InvalidToken:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token") from None

        user = AuthStore(conn, settings).current_user(str(user_id))
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found_or_inactive")
        return user

    return get_current_user


def require_role(name: str, get_current_user: Callable):
    """A dependency that only passes callers holding `name`. Gates by role, not
    by fine-grained permission - Phase 1's deliberate scope, see
    docs/blueprints/auth-and-user-management.md §2."""

    def dependency(user=Depends(get_current_user)):
        if not user.has_role(name):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing_role:{name}")
        return user

    return dependency


def require_capability(name: str, get_current_user: Callable):
    """Like `require_role`, but on a derived capability (`auth/persona.py`) -
    `can_decide_gates`, `can_rerun_steps`, `can_manage_users`. The gate
    endpoints and retry/re-run use this: the question is "may this caller
    *do* this", which several roles answer yes to, not "does the caller hold
    role X". The 403 detail names the capability so the UI can say, in the
    analyst's words, why the control is not hers."""

    def dependency(user=Depends(get_current_user)):
        if not user.has_capability(name):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing_capability:{name}")
        return user

    return dependency
