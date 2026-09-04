"""Auth-facing shapes. Never serialize `hashed_password` - `UserOut`/`CurrentUser`
are the only auth models that leave the process."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class Role(BaseModel):
    id: UUID
    name: str
    description: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str
    roles: list[str] = Field(default_factory=list)


class SetActiveRequest(BaseModel):
    is_active: bool


class SetPasswordRequest(BaseModel):
    """Admin-only reset - there is still no self-service "forgot password"
    flow (no email sending in this build), so this is the only way anyone's
    password ever changes after account creation."""

    password: str = Field(min_length=8)


class UserOut(BaseModel):
    """A user as the admin console lists/creates them. No password, ever."""

    id: UUID
    email: EmailStr
    display_name: str
    is_active: bool
    roles: list[str]
    created_ts: datetime
    updated_ts: datetime | None = None


class CurrentUser(BaseModel):
    """The authenticated caller, as `GET /api/auth/me` and `get_current_user`
    return it - carries the flattened role set every permission check reads."""

    id: UUID
    email: EmailStr
    display_name: str
    roles: list[str]

    def has_role(self, name: str) -> bool:
        return name in self.roles


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: CurrentUser
