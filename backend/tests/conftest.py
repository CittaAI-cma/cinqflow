"""Test fixtures. Integration and e2e tests run against a real Postgres, in
throwaway schemas, so nothing touches the schemas already in this database."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from cinqflow.auth import ddl as auth_ddl
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
def conn(settings: Settings):
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
