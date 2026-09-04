from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.security import decode_token
from db.existing_models import User  # <- point at your real User model
from db.models import Permission, Role, UserRole, role_permissions
from db.session import get_db
from schemas.auth import CurrentUser


def get_current_user(
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if access_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    try:
        user_id: UUID = decode_token(access_token, "access")
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found")

    roles = db.scalars(
        select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    ).all()
    permissions = db.scalars(
        select(Permission.code)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .join(Role, Role.id == role_permissions.c.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    ).all()

    return CurrentUser(
        id=user_id,
        email=user.email,
        roles=[r.name for r in roles],
        permissions=list(set(permissions)),
    )


def require_permission(code: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if code not in user.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing_permission:{code}")
        return user

    return dependency


def require_role(name: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if name not in user.roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing_role:{name}")
        return user

    return dependency
