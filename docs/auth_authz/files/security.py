from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: UUID, token_type: TokenType) -> str:
    ttl = (
        timedelta(minutes=settings.access_token_expire_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_expire_days)
    )
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": datetime.now(timezone.utc) + ttl,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise ValueError("invalid_token") from e
    if payload.get("type") != expected_type:
        raise ValueError("wrong_token_type")
    return UUID(payload["sub"])
