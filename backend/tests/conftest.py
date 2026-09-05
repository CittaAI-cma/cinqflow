"""Test fixtures. Integration and e2e tests run against a real Postgres, in
throwaway schemas, so nothing touches the schemas already in this database."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow import migrations
from cinqflow.auth import ddl as auth_ddl
from cinqflow.auth.security import hash_password
from cinqflow.auth.store import AuthStore
from cinqflow.settings import Settings
from cinqflow.workflow import ddl

DSN = os.environ.get("CINQFLOW_TEST_DATABASE_URL", "postgresql://localhost/cinqflow")

SAMPLES = (
    Path(__file__).resolve().parents[2]
    / "docs/04_source_data_samples_and_profiles/1-Enrollment/1.Fedelis_NY"
)
ROSTER_CSV = SAMPLES / "deidentified_CINQUPSTATE_Member_Roster_03_05_2026_1.csv"
ROSTER_XLSX = SAMPLES / "deidentified__CINQDOWNSTATE_Member_Roster_03_05_2026_1.xlsx"

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "knowledge"


def _database_available() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=3):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _database_available(), reason=f"no Postgres at {DSN}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Unique schemas per test, dropped afterwards.

    Knowledge is a copy of the real tree: tests read the governed documents the
    platform actually ships, and a G2 export cannot write into the repository.
    """
    suffix = uuid.uuid4().hex[:8]
    knowledge = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE_ROOT, knowledge)
    return Settings(
        database_url=DSN,
        workflow_schema=f"test_wf_{suffix}",
        queue_schema=f"test_q_{suffix}",
        # Silver tables are named after canonical entities, not feeds, so they need
        # a schema of their own per test - and `silver_raw` in this database holds
        # the previous implementation's tables.
        silver_schema=f"test_silver_{suffix}",
        auth_schema=f"test_auth_{suffix}",
        landing_root=tmp_path / "landing",
        knowledge_root=knowledge,
        llm_provider="stub",
        jwt_secret="test-secret-at-least-32-bytes-long-for-hs256",
    )


@pytest.fixture
def bare_conn(settings: Settings):
    """Baseline DDL only - the frozen `CREATE IF NOT EXISTS` set, with the
    version table still empty. The migration tests build on this so they can
    point the runner at their own directories."""
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as connection:
        ddl.install(connection, settings)
        auth_ddl.install(connection, settings)
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            with connection.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {settings.workflow_schema} CASCADE")
                cur.execute(f"DROP SCHEMA IF EXISTS {settings.queue_schema} CASCADE")
                cur.execute(f"DROP SCHEMA IF EXISTS {settings.silver_schema} CASCADE")
                cur.execute(f"DROP SCHEMA IF EXISTS {settings.auth_schema} CASCADE")
            connection.commit()


@pytest.fixture
def conn(bare_conn, settings: Settings):
    """The production shape: baseline plus every migration this package ships,
    which is what `cinqflow install` leaves behind. Everything but the
    migration runner's own tests wants this."""
    migrations.apply_pending(bare_conn, settings)
    bare_conn.commit()
    return bare_conn


# ------------------------------------------------------------- authenticated client

TEST_OPERATOR_EMAIL = "operator@test.cinqflow"
TEST_OPERATOR_PASSWORD = "operator-pass-1"
#: Hashed once per session, not once per test: bcrypt is deliberately slow, and
#: dozens of e2e tests each signing a client in would otherwise add seconds apiece.
_TEST_OPERATOR_HASH = hash_password(TEST_OPERATOR_PASSWORD)

#: One role from each side of the persona split, so a single signed-in client can
#: both decide a gate (`approver`) and retry a step (`data_engineer`) - which is
#: what the stage e2e tests exercise end to end.
TEST_OPERATOR_ROLES: tuple[str, ...] = ("approver", "data_engineer")


def authed_client(
    client: TestClient,
    conn,
    settings: Settings,
    *,
    roles: tuple[str, ...] = TEST_OPERATOR_ROLES,
    email: str = TEST_OPERATOR_EMAIL,
) -> TestClient:
    """Signs `client` in as a throwaway user holding `roles` and returns it.

    The gate and retry endpoints are capability-gated (`require_capability`,
    api/deps.py), so a test that exercises a stage end to end has to be
    someone. The user is created directly through `AuthStore` and committed
    (the client serves requests on its own connection), then signed in through
    the real `/api/auth/login` so the bearer token is a real one.
    """
    store = AuthStore(conn, settings)
    if store.get_user_by_email(email) is None:
        store.create_user(
            email=email,
            hashed_password=_TEST_OPERATOR_HASH,
            display_name="Test Operator",
            role_names=list(roles),
            created_by="test",
        )
        conn.commit()
    res = client.post("/api/auth/login", json={"email": email, "password": TEST_OPERATOR_PASSWORD})
    assert res.status_code == 200, res.text
    client.headers["Authorization"] = f"Bearer {res.json()['access_token']}"
    return client


@pytest.fixture
def roster_csv_bytes() -> bytes:
    if not ROSTER_CSV.exists():
        pytest.skip(f"sample not present: {ROSTER_CSV}")
    return ROSTER_CSV.read_bytes()


@pytest.fixture
def roster_xlsx_bytes() -> bytes:
    if not ROSTER_XLSX.exists():
        pytest.skip(f"sample not present: {ROSTER_XLSX}")
    return ROSTER_XLSX.read_bytes()


@pytest.fixture
def small_csv_bytes() -> bytes:
    """A hand-built roster-shaped file: known values, known defects."""
    return (
        b"member_id,member_first_name,member_dob,member_sex,product\n"
        b"M001,DANIELLE,1997-11-04,F,TANF Adult\n"
        b"M002,KEVIN,2013-11-04,M,TANF Child\n"
        b"M003,ALEX,,U,TANF Adult\n"
    )
