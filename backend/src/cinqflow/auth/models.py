"""Auth-facing shapes. Never serialize `hashed_password` - `UserOut`/`CurrentUser`
are the only auth models that leave the process."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from cinqflow.auth.persona import Capabilities, Persona


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
    return it - carries the flattened role set every permission check reads,
    plus the persona and capabilities derived from it (`auth/persona.py`), so
    the frontend never re-derives that mapping."""

    id: UUID
    email: EmailStr
    display_name: str
    roles: list[str]
    persona: Persona
    capabilities: Capabilities

    def has_role(self, name: str) -> bool:
        return name in self.roles

    def has_capability(self, name: str) -> bool:
        return bool(getattr(self.capabilities, name, False))


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: CurrentUser


class SetRolesRequest(BaseModel):
    """Admin-only: replaces the user's whole role set. Roles are what persona
    and capabilities derive from (`auth/persona.py`), so this is how an
    administrator-only bootstrap account is given `approver` and can sign a
    gate."""

    roles: list[str]
