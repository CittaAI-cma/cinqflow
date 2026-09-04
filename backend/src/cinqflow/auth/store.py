"""The only place auth SQL lives. Parameterised statements throughout, same
convention as `workflow/store.py`."""

from __future__ import annotations

import uuid

from cinqflow.auth.models import CurrentUser, Role, UserOut
from cinqflow.db import execute, fetch_all, fetch_one
from cinqflow.settings import Settings


class EmailAlreadyExists(Exception):
    def __init__(self, email: str) -> None:
        super().__init__(f"a user with this email already exists: {email}")
        self.email = email


class UnknownUser(Exception):
    pass


class UnknownRole(Exception):
    def __init__(self, names: list[str]) -> None:
        super().__init__(f"unknown role(s): {', '.join(names)}")
        self.names = names


class AuthStore:
    def __init__(self, conn, settings: Settings) -> None:
        self.conn = conn
        self.s = settings

    # ------------------------------------------------------------------ roles
    def list_roles(self) -> list[Role]:
        rows = fetch_all(self.conn, f"SELECT * FROM {self.s.auth_schema}.role ORDER BY name")
        return [Role(**row) for row in rows]

    def _role_ids(self, names: list[str]) -> dict[str, uuid.UUID]:
        if not names:
            return {}
        rows = fetch_all(
            self.conn,
            f"SELECT id, name FROM {self.s.auth_schema}.role WHERE name = ANY(%s)",
            (names,),
        )
        found = {row["name"]: row["id"] for row in rows}
        missing = [name for name in names if name not in found]
        if missing:
            raise UnknownRole(missing)
        return found

    # ------------------------------------------------------------------ users
    def create_user(
        self,
        *,
        email: str,
        hashed_password: str,
        display_name: str,
        role_names: list[str],
        created_by: str | None,
    ) -> UserOut:
        existing = fetch_one(
            self.conn,
            f'SELECT id FROM {self.s.auth_schema}."user" WHERE email = %s',
            (email,),
        )
        if existing:
            raise EmailAlreadyExists(email)

        role_ids = self._role_ids(role_names)  # raises UnknownRole before any write

        user_id = uuid.uuid4()
        execute(
            self.conn,
            f"""
            INSERT INTO {self.s.auth_schema}."user"
                (id, email, hashed_password, display_name, is_active, created_by)
            VALUES (%s, %s, %s, %s, true, %s)
            """,
            (user_id, email, hashed_password, display_name, created_by),
        )
        for role_id in role_ids.values():
            execute(
                self.conn,
                f"""INSERT INTO {self.s.auth_schema}.user_role (user_id, role_id)
                    VALUES (%s, %s)""",
                (user_id, role_id),
            )
        return self.get_user(str(user_id))

    def get_user(self, user_id: str) -> UserOut:
        row = fetch_one(
            self.conn,
            f'SELECT * FROM {self.s.auth_schema}."user" WHERE id = %s',
            (user_id,),
        )
        if not row:
            raise UnknownUser(user_id)
        return self._to_user_out(row)

    def get_user_by_email(self, email: str) -> dict | None:
        """Raw row (carries `hashed_password`) - login is the one call site
        allowed to see it; everything else goes through `UserOut`."""
        return fetch_one(
            self.conn,
            f'SELECT * FROM {self.s.auth_schema}."user" WHERE email = %s',
            (email,),
        )

    def get_user_row(self, user_id: str) -> dict | None:
        return fetch_one(
            self.conn,
            f'SELECT * FROM {self.s.auth_schema}."user" WHERE id = %s',
            (user_id,),
        )

    def list_users(self) -> list[UserOut]:
        rows = fetch_all(
            self.conn,
            f'SELECT * FROM {self.s.auth_schema}."user" ORDER BY created_ts DESC',
        )
        return [self._to_user_out(row) for row in rows]

    def set_active(self, user_id: str, is_active: bool) -> UserOut:
        self.get_user(user_id)  # raises UnknownUser
        execute(
            self.conn,
            f"""UPDATE {self.s.auth_schema}."user"
                SET is_active = %s, updated_ts = now() WHERE id = %s""",
            (is_active, user_id),
        )
        return self.get_user(user_id)

    def roles_for_user(self, user_id: str) -> list[str]:
        rows = fetch_all(
            self.conn,
            f"""SELECT r.name FROM {self.s.auth_schema}.role r
                JOIN {self.s.auth_schema}.user_role ur ON ur.role_id = r.id
                WHERE ur.user_id = %s ORDER BY r.name""",
            (user_id,),
        )
        return [row["name"] for row in rows]

    def current_user(self, user_id: str) -> CurrentUser | None:
        row = self.get_user_row(user_id)
        if row is None or not row["is_active"]:
            return None
        return CurrentUser(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            roles=self.roles_for_user(user_id),
        )

    def _to_user_out(self, row: dict) -> UserOut:
        return UserOut(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            is_active=row["is_active"],
            roles=self.roles_for_user(str(row["id"])),
            created_ts=row["created_ts"],
            updated_ts=row["updated_ts"],
        )


def bootstrap_admin(conn, settings: Settings) -> UserOut | None:
    """Idempotent: create one administrator from env vars if no user with that
    email exists yet. Returns None if `bootstrap_admin_email` is unset or the
    email is already taken (already bootstrapped - not an error)."""
    if not settings.bootstrap_admin_email:
        return None
    from cinqflow.auth.security import hash_password

    store = AuthStore(conn, settings)
    if store.get_user_by_email(settings.bootstrap_admin_email) is not None:
        return None
    if not settings.bootstrap_admin_password:
        raise ValueError(
            "CINQFLOW_BOOTSTRAP_ADMIN_EMAIL is set but CINQFLOW_BOOTSTRAP_ADMIN_PASSWORD is not"
        )
    return store.create_user(
        email=settings.bootstrap_admin_email,
        hashed_password=hash_password(settings.bootstrap_admin_password),
        display_name=settings.bootstrap_admin_name,
        role_names=["administrator"],
        created_by="bootstrap",
    )
