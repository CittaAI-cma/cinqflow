from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.security import create_token, decode_token, verify_password
from db.existing_models import User  # <- point at your real User model
from db.session import get_db
from schemas.auth import CurrentUser, LoginRequest, TokenPair

from .deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def _set_auth_cookies(response: Response, user_id: UUID) -> None:
    response.set_cookie(
        ACCESS_COOKIE, create_token(user_id, "access"),
        httponly=True, samesite="lax", secure=True,
    )
    response.set_cookie(
        REFRESH_COOKIE, create_token(user_id, "refresh"),
        httponly=True, samesite="lax", secure=True, path="/auth/refresh",
    )


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
    _set_auth_cookies(response, user.id)
    return TokenPair(access_token="set_via_cookie")


@router.post("/refresh", response_model=TokenPair)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no_refresh_token")
    try:
        user_id = decode_token(refresh_token, "refresh")
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")
    if db.get(User, user_id) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found")
    _set_auth_cookies(response, user_id)
    return TokenPair(access_token="set_via_cookie")


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE, path="/auth/refresh")
    return {"ok": True}


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUser = Depends(get_current_user)):
    return user
