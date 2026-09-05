"""`cinqflow install` applies pending migrations and says so; `cinqflow migrate
--status` reports without touching anything; `cinqflow migrate` applies on its own."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from cinqflow import migrations
from cinqflow.cli import main
from cinqflow.dataplane.contract import Layer
from cinqflow.settings import Settings
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def cli_env(tmp_path, monkeypatch, settings):
    """Isolated schemas for the CLI (same recipe as test_cli_reset), plus a temporary
    migrations directory the runner is pointed at by monkeypatching MIGRATIONS_DIR."""
    suffix = uuid.uuid4().hex[:8]
    s = Settings(
        database_url=settings.database_url,
        workflow_schema=f"test_mig_wf_{suffix}",
        queue_schema=f"test_mig_q_{suffix}",
        silver_schema=f"test_mig_silver_{suffix}",
        auth_schema=f"test_mig_auth_{suffix}",
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

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", migrations_dir)

    yield s, migrations_dir

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


def _applied_versions(s: Settings) -> list[int]:
    with psycopg.connect(s.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT version FROM {s.workflow_schema}.schema_version ORDER BY version")
            return [row[0] for row in cur.fetchall()]


def test_install_applies_pending_migrations_and_reports_them(cli_env, capsys):
    s, migrations_dir = cli_env
    (migrations_dir / "001_widgets.sql").write_text(
        "CREATE TABLE {{workflow}}.widget (id int PRIMARY KEY);"
    )

    assert main(["install"]) == 0
    out = capsys.readouterr().out
    assert "migrations: applied 001_widgets" in out
    assert _applied_versions(s) == [1]

    # Installing again is a no-op for migrations, and says so.
    assert main(["install"]) == 0
    out = capsys.readouterr().out
    assert "migrations: none pending (1 applied)" in out
    assert _applied_versions(s) == [1]


def test_migrate_status_reports_without_applying(cli_env, capsys):
    s, migrations_dir = cli_env
    main(["install"])
    capsys.readouterr()
    (migrations_dir / "001_widgets.sql").write_text("CREATE TABLE {{workflow}}.widget (id int);")

    assert main(["migrate", "--status"]) == 0
    out = capsys.readouterr().out
    assert "applied: 0" in out
    assert "pending: 1" in out
    assert "001_widgets" in out
    assert _applied_versions(s) == []  # status never applies


def test_migrate_applies_pending(cli_env, capsys):
    s, migrations_dir = cli_env
    main(["install"])
    capsys.readouterr()
    (migrations_dir / "001_widgets.sql").write_text("CREATE TABLE {{workflow}}.widget (id int);")

    assert main(["migrate"]) == 0
    out = capsys.readouterr().out
    assert "migrations: applied 001_widgets" in out
    assert _applied_versions(s) == [1]


def test_reset_reinstalls_and_reapplies_migrations(cli_env, capsys):
    s, migrations_dir = cli_env
    (migrations_dir / "001_widgets.sql").write_text("CREATE TABLE {{workflow}}.widget (id int);")
    main(["install"])
    capsys.readouterr()

    assert main(["reset", "--yes"]) == 0
    # The schema was dropped and rebuilt; the migration is recorded again, exactly once.
    assert _applied_versions(s) == [1]


def test_a_bad_migration_set_fails_install_loudly(cli_env, capsys):
    s, migrations_dir = cli_env
    (migrations_dir / "001_widgets.sql").write_text("CREATE TABLE {{workflow}}.widget (id int);")
    (migrations_dir / "003_gap.sql").write_text("SELECT 1;")

    with pytest.raises(migrations.MigrationError, match="contiguous"):
        main(["install"])
