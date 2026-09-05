"""`cinqflow reset`: refuses without --yes, and with it drops exactly the
schemas this platform owns and leaves a working, empty install behind."""

from __future__ import annotations

import psycopg
import pytest

from cinqflow.cli import main
from cinqflow.dataplane.contract import Layer
from cinqflow.settings import Settings
from tests.conftest import requires_db

pytestmark = requires_db


def _schema_exists(database_url: str, schema: str) -> bool:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,)
            )
            return cur.fetchone() is not None


@pytest.fixture
def reset_env(tmp_path, monkeypatch, settings):
    """A throwaway set of the schemas `reset` owns, isolated from every other
    test the same way conftest's own fixtures are (random suffix).

    `auth_schema` is isolated here too. It was not, and `cmd_reset` drops it
    (cli.py's `schemas` list), so running this suite against a database that
    also holds a real environment did `DROP SCHEMA auth CASCADE` on it and
    reinstalled it empty - deleting every real user. The suffix keeps that
    inside the test.

    `Layer.BRONZE.value` is still the real `bronze` schema: it is a hardcoded
    enum in `cmd_reset`, not a setting, so a test cannot redirect it. Running
    this file against a database holding real Bronze tables still drops them.
    Making the Bronze schema name a setting is the fix; until then, run the
    suite against a database of its own."""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    s = Settings(
        database_url=settings.database_url,
        workflow_schema=f"test_reset_wf_{suffix}",
        queue_schema=f"test_reset_q_{suffix}",
        silver_schema=f"test_reset_silver_{suffix}",
        auth_schema=f"test_reset_auth_{suffix}",
        landing_root=tmp_path / "landing",
        knowledge_root=settings.knowledge_root,
        llm_provider="stub",
    )
    for key, value in {
        "CINQFLOW_DATABASE_URL": s.database_url,
        "CINQFLOW_WORKFLOW_SCHEMA": s.workflow_schema,
        "CINQFLOW_QUEUE_SCHEMA": s.queue_schema,
        "CINQFLOW_SILVER_SCHEMA": s.silver_schema,
        "CINQFLOW_AUTH_SCHEMA": s.auth_schema,
        "CINQFLOW_LANDING_ROOT": str(s.landing_root),
        "CINQFLOW_KNOWLEDGE_ROOT": str(s.knowledge_root),
        "CINQFLOW_LLM_PROVIDER": "stub",
    }.items():
        monkeypatch.setenv(key, value)
    from cinqflow.settings import get_settings

    get_settings.cache_clear()
    yield s
    get_settings.cache_clear()
    with psycopg.connect(s.database_url) as conn:
        with conn.cursor() as cur:
            for schema in (
                s.workflow_schema,
                s.queue_schema,
                s.silver_schema,
                s.auth_schema,
                Layer.BRONZE.value,
            ):
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()


def test_without_yes_prints_the_plan_and_touches_nothing(reset_env, capsys):
    main(["install"])
    assert _schema_exists(reset_env.database_url, reset_env.workflow_schema)

    code = main(["reset"])
    assert code == 1
    out = capsys.readouterr().out
    assert reset_env.workflow_schema in out
    assert "--yes" in out
    # Nothing was dropped.
    assert _schema_exists(reset_env.database_url, reset_env.workflow_schema)


def test_with_yes_drops_and_reinstalls_empty(reset_env, capsys):
    main(["install"])
    reset_env.landing_root.mkdir(parents=True, exist_ok=True)
    (reset_env.landing_root / "leftover.txt").write_text("stale test file")

    code = main(["reset", "--yes"])
    assert code == 0
    out = capsys.readouterr().out
    assert "dropped" in out
    assert "reinstalled" in out

    # The schema exists again (reinstalled) but is empty.
    assert _schema_exists(reset_env.database_url, reset_env.workflow_schema)
    with psycopg.connect(reset_env.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {reset_env.workflow_schema}.upload")
            assert cur.fetchone()[0] == 0

    # The landing zone was cleared and recreated, not left with stale files.
    assert reset_env.landing_root.exists()
    assert list(reset_env.landing_root.iterdir()) == []
