"""Admin-only user provisioning. No self-registration and no DELETE - accounts
are created by an administrator and deactivated, never removed, matching this
codebase's append-only stance elsewhere (see
docs/blueprints/auth-and-user-management.md §3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException, status

from cinqflow.api.deps import make_get_current_user, require_role
from cinqflow.auth.ddl import ADMINISTRATOR
from cinqflow.auth.models import CreateUserRequest, CurrentUser, Role, SetActiveRequest, UserOut
from cinqflow.auth.security import hash_password
from cinqflow.auth.store import AuthStore, EmailAlreadyExists, UnknownRole, UnknownUser
from cinqflow.settings import Settings


def build_router(settings: Settings, get_conn: Callable[[], Iterator]) -> APIRouter:
    s = settings
    get_current_user = make_get_current_user(s, get_conn)
    require_admin = require_role(ADMINISTRATOR, get_current_user)
    router = APIRouter(tags=["users"])

    @router.get("/api/roles", response_model=list[Role])
    def list_roles(conn=Depends(get_conn), _admin: CurrentUser = Depends(require_admin)):
        return AuthStore(conn, s).list_roles()

    @router.get("/api/users", response_model=list[UserOut])
    def list_users(conn=Depends(get_conn), _admin: CurrentUser = Depends(require_admin)):
        return AuthStore(conn, s).list_users()

    @router.post("/api/users", response_model=UserOut, status_code=201)
    def create_user(
        body: CreateUserRequest,
        conn=Depends(get_conn),
        admin: CurrentUser = Depends(require_admin),
    ):
        store = AuthStore(conn, s)
        try:
            return store.create_user(
                email=body.email,
                hashed_password=hash_password(body.password),
                display_name=body.display_name or body.email,
                role_names=body.roles,
                created_by=admin.email,
            )
        except EmailAlreadyExists:
            raise HTTPException(status.HTTP_409_CONFLICT, "email_already_exists") from None
        except UnknownRole as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown_role:{exc.names}") from None

    @router.patch("/api/users/{user_id}", response_model=UserOut)
    def set_active(
        user_id: str,
        body: SetActiveRequest,
        conn=Depends(get_conn),
        _admin: CurrentUser = Depends(require_admin),
    ):
        try:
            return AuthStore(conn, s).set_active(user_id, body.is_active)
        except UnknownUser:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown_user") from None

    return router
