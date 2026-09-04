"""Login, token refresh, and "who am I". Stateless JWTs over JSON - no cookies
set here; that's Next.js's job (docs/blueprints/auth-and-user-management.md §3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException, status

from cinqflow.api.deps import make_get_current_user
from cinqflow.auth.models import CurrentUser, LoginRequest, RefreshRequest, TokenPair
from cinqflow.auth.security import InvalidToken, create_token, decode_token, verify_password
from cinqflow.auth.store import AuthStore
from cinqflow.settings import Settings


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    get_current_user = make_get_current_user(s, get_conn)
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login", response_model=TokenPair)
    def login(body: LoginRequest, conn=Depends(get_conn)) -> TokenPair:
        store = AuthStore(conn, s)
        row = store.get_user_by_email(body.email)
        if row is None or not verify_password(body.password, row["hashed_password"] or ""):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
        if not row["is_active"]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account_deactivated")

        user = store.current_user(str(row["id"]))
        assert user is not None  # just confirmed active, above
        return TokenPair(
            access_token=create_token(row["id"], "access", s),
            refresh_token=create_token(row["id"], "refresh", s),
            user=user,
        )

    @router.post("/refresh", response_model=TokenPair)
    def refresh(body: RefreshRequest, conn=Depends(get_conn)) -> TokenPair:
        try:
            user_id = decode_token(body.refresh_token, "refresh", s)
        except InvalidToken:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token") from None

        user = AuthStore(conn, s).current_user(str(user_id))
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found_or_inactive")
        return TokenPair(
            access_token=create_token(user_id, "access", s),
            refresh_token=create_token(user_id, "refresh", s),
            user=user,
        )

    @router.get("/me", response_model=CurrentUser)
    def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        return user

    return router
