"""Auth DDL, rendered from one declaration and applied idempotently - same shape
as `workflow/ddl.py`. Three tables: `role`, `user`, and the `user_role` membership
join. No `permission`/`role_permission` tables yet - Phase 1 gates by role name
only (see docs/blueprints/auth-and-user-management.md §5)."""

from __future__ import annotations

from cinqflow.settings import Settings

#: The MVP role list (MVP_objective.docx, Epic 2). Order is display order.
ROLES: list[tuple[str, str]] = [
    ("business_analyst", "Business Analyst"),
    ("data_steward", "Data Steward"),
    ("data_engineer", "Data Engineer"),
    ("operations", "Operations"),
    ("approver", "Approver"),
    ("administrator", "Administrator"),
    ("read_only", "Read-Only User"),
]

ADMINISTRATOR = "administrator"


def statements(settings: Settings) -> list[str]:
    a = settings.auth_schema
    return [
        f"CREATE SCHEMA IF NOT EXISTS {a}",
        f"""
        CREATE TABLE IF NOT EXISTS {a}.role (
            id          UUID PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL
        )
        """,
        # `hashed_password` is nullable on purpose: an SSO-only identity (Epic 2's
        # Entra ID phase) is a user row with no local password at all, not a
        # sentinel value - see docs/blueprints/auth-and-user-management.md §3.
        f"""
        CREATE TABLE IF NOT EXISTS {a}."user" (
            id              UUID PRIMARY KEY,
            email           TEXT NOT NULL UNIQUE,
            hashed_password TEXT,
            display_name    TEXT NOT NULL,
            is_active       BOOLEAN NOT NULL DEFAULT true,
            created_by      TEXT,
            created_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_ts      TIMESTAMPTZ
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {a}.user_role (
            user_id UUID NOT NULL REFERENCES {a}."user" (id) ON DELETE CASCADE,
            role_id UUID NOT NULL REFERENCES {a}.role (id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, role_id)
        )
        """,
    ]


def install(conn, settings: Settings) -> None:
    import uuid

    with conn.cursor() as cur:
        for sql in statements(settings):
            cur.execute(sql)
        for name, description in ROLES:
            cur.execute(
                f"""INSERT INTO {settings.auth_schema}.role (id, name, description)
                    VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING""",
                (uuid.uuid4(), name, description),
            )
