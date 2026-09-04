"""Password hashing and JWT issuance/verification. Direct `bcrypt` + `PyJWT`,
not `passlib`/`python-jose` - one well-maintained package per job rather than a
wrapper layer around it (see docs/blueprints/auth-and-user-management.md §3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import bcrypt
import jwt

from cinqflow.settings import Settings

TokenType = Literal["access", "refresh"]


class InvalidToken(Exception):
    """Expired, malformed, wrong-signature, or the wrong type for the call site."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash (e.g. a row with no password set yet, SSO-only) - never
        # a match, never a crash.
        return False


def create_token(user_id: UUID, token_type: TokenType, settings: Settings) -> str:
    ttl = (
        timedelta(minutes=settings.jwt_access_token_expire_minutes)
        if token_type == "access"
        else timedelta(days=settings.jwt_refresh_token_expire_days)
    )
    payload = {
        "sub": str(user_id),
        "type": token_type,
        # `jti`: two tokens minted in the same second for the same user would
        # otherwise be byte-identical (same `sub`/`type`/`iat`/`exp`) - and it's
        # the handle a future revocation table (see the plan's Phase 2 TODOs)
        # would key on.
        "jti": str(uuid.uuid4()),
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType, settings: Settings) -> UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise InvalidToken(f"expected a {expected_type} token, got {payload.get('type')}")
    try:
        return UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidToken("token has no valid subject") from exc
