"""The migration runner against a real Postgres: applies once, in order, one
transaction each; records what it did; refuses what it must."""

from __future__ import annotations

import pytest

from cinqflow import migrations
from cinqflow.migrations import MigrationError
from tests.conftest import requires_db

pytestmark = requires_db


def _write(directory, name: str, body: str) -> None:
    (directory / name).write_text(body)


def _table_exists(conn, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        return cur.fetchone() is not None


def test_applies_in_order_and_records_each_version(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int PRIMARY KEY);")
    _write(
        tmp_path,
        "002_widget_name.sql",
        "ALTER TABLE {{workflow}}.widget ADD COLUMN name text;"
        " INSERT INTO {{workflow}}.widget VALUES (1, 'a');",
    )

    done = migrations.apply_pending(conn, settings, tmp_path)

    assert [m.label for m in done] == ["001_widgets", "002_widget_name"]
    rows = migrations.applied(conn, settings)
    assert [(r["version"], r["name"]) for r in rows] == [(1, "widgets"), (2, "widget_name")]
    assert all(r["applied_ts"] is not None for r in rows)
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, name FROM {settings.workflow_schema}.widget")
        assert cur.fetchone() == {"id": 1, "name": "a"}


def test_second_run_applies_nothing(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int);")
    assert len(migrations.apply_pending(conn, settings, tmp_path)) == 1

    assert migrations.apply_pending(conn, settings, tmp_path) == []
    assert len(migrations.applied(conn, settings)) == 1


def test_a_new_file_applies_only_itself(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int);")
    migrations.apply_pending(conn, settings, tmp_path)

    _write(tmp_path, "002_gadgets.sql", "CREATE TABLE {{workflow}}.gadget (id int);")
    done = migrations.apply_pending(conn, settings, tmp_path)

    assert [m.label for m in done] == ["002_gadgets"]
    assert _table_exists(conn, settings.workflow_schema, "gadget")


def test_a_gap_refuses_the_whole_set_before_applying_anything(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int);")
    _write(tmp_path, "003_gadgets.sql", "CREATE TABLE {{workflow}}.gadget (id int);")

    with pytest.raises(MigrationError, match="contiguous"):
        migrations.apply_pending(conn, settings, tmp_path)

    assert not _table_exists(conn, settings.workflow_schema, "widget")
    assert migrations.applied(conn, settings) == []


def test_a_failing_migration_rolls_back_and_stops(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int);")
    _write(
        tmp_path,
        "002_broken.sql",
        # The first statement would succeed on its own; the second cannot. Both must
        # roll back together - a half-applied file is exactly what this prevents.
        "CREATE TABLE {{workflow}}.half (id int);"
        " INSERT INTO {{workflow}}.does_not_exist VALUES (1);",
    )
    _write(tmp_path, "003_after.sql", "CREATE TABLE {{workflow}}.after (id int);")

    with pytest.raises(MigrationError, match="002_broken failed and was rolled back"):
        migrations.apply_pending(conn, settings, tmp_path)

    assert _table_exists(conn, settings.workflow_schema, "widget")  # 001 stands
    assert not _table_exists(conn, settings.workflow_schema, "half")  # 002 rolled back
    assert not _table_exists(conn, settings.workflow_schema, "after")  # 003 never ran
    assert [r["version"] for r in migrations.applied(conn, settings)] == [1]

    # The connection is usable afterwards - the runner left no open failed transaction.
    with conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        assert cur.fetchone() == {"ok": 1}


def test_renamed_applied_migration_is_refused(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int);")
    migrations.apply_pending(conn, settings, tmp_path)

    (tmp_path / "001_widgets.sql").rename(tmp_path / "001_renamed.sql")
    with pytest.raises(MigrationError, match="001_widgets has no matching file"):
        migrations.pending(conn, settings, tmp_path)


def test_deleted_applied_migration_is_refused(conn, settings, tmp_path):
    _write(tmp_path, "001_widgets.sql", "CREATE TABLE {{workflow}}.widget (id int);")
    _write(tmp_path, "002_gadgets.sql", "CREATE TABLE {{workflow}}.gadget (id int);")
    migrations.apply_pending(conn, settings, tmp_path)

    (tmp_path / "002_gadgets.sql").unlink()
    with pytest.raises(MigrationError, match="002_gadgets has no matching file"):
        migrations.pending(conn, settings, tmp_path)


def test_dollar_quoted_blocks_and_multiple_statements_apply_as_one(conn, settings, tmp_path):
    _write(
        tmp_path,
        "001_do_block.sql",
        """
        CREATE TABLE {{workflow}}.counter (n int);
        INSERT INTO {{workflow}}.counter VALUES (1);
        DO $m$ BEGIN INSERT INTO {{workflow}}.counter VALUES (2); END $m$;
        INSERT INTO {{workflow}}.counter VALUES (3);
        """,
    )
    migrations.apply_pending(conn, settings, tmp_path)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n, max(n) AS mx FROM {settings.workflow_schema}.counter")
        assert cur.fetchone() == {"n": 3, "mx": 3}


def test_queue_and_auth_tokens_resolve_to_the_test_schemas(conn, settings, tmp_path):
    _write(
        tmp_path,
        "001_cross_schema.sql",
        "ALTER TABLE {{queue}}.message ADD COLUMN IF NOT EXISTS note text;"
        " ALTER TABLE {{auth}}.role ADD COLUMN IF NOT EXISTS note text;",
    )
    migrations.apply_pending(conn, settings, tmp_path)
    for schema, table in ((settings.queue_schema, "message"), (settings.auth_schema, "role")):
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM information_schema.columns
                   WHERE table_schema = %s AND table_name = %s AND column_name = 'note'""",
                (schema, table),
            )
            assert cur.fetchone() is not None, f"{schema}.{table}.note"


def test_shipped_directory_applies_cleanly_on_a_fresh_install(conn, settings):
    """The production path: `cinqflow install` with whatever this package ships. Today
    that is the version table alone; when migrations land this asserts they apply."""
    done = migrations.apply_pending(conn, settings)
    assert [m.label for m in done] == [m.label for m in migrations.discover()]
    assert _table_exists(conn, settings.workflow_schema, "schema_version")
    assert migrations.apply_pending(conn, settings) == []
