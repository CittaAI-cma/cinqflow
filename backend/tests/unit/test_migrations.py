"""`cinqflow.migrations` discovery and rendering - the rules that refuse a bad set
before any SQL runs. No database needed here; the runner itself is covered in
tests/integration/test_migrations.py."""

from __future__ import annotations

import pytest

from cinqflow import migrations
from cinqflow.migrations import MigrationError
from cinqflow.settings import Settings


def _write(directory, name: str, body: str = "SELECT 1;") -> None:
    (directory / name).write_text(body)


def test_discover_parses_and_sorts(tmp_path):
    _write(tmp_path, "002_second.sql")
    _write(tmp_path, "001_first.sql")
    found = migrations.discover(tmp_path)
    assert [(m.version, m.name) for m in found] == [(1, "first"), (2, "second")]
    assert found[0].label == "001_first"
    assert found[0].filename == "001_first.sql"


def test_discover_ignores_non_sql_files(tmp_path):
    _write(tmp_path, "001_first.sql")
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "README.md").write_text("notes")
    assert [m.name for m in migrations.discover(tmp_path)] == ["first"]


def test_discover_empty_directory_is_a_valid_empty_set(tmp_path):
    assert migrations.discover(tmp_path) == []


def test_shipped_directory_is_a_valid_set():
    # The package's own directory must always pass its own rules, even when it holds
    # no migrations yet (PR-0 ships the mechanism; 001_step_run.sql lands in PR-2).
    migrations.discover()


def test_discover_refuses_a_gap(tmp_path):
    _write(tmp_path, "001_first.sql")
    _write(tmp_path, "003_third.sql")
    with pytest.raises(MigrationError, match="contiguous.*missing 002"):
        migrations.discover(tmp_path)


def test_discover_refuses_not_starting_at_001(tmp_path):
    _write(tmp_path, "002_second.sql")
    with pytest.raises(MigrationError, match="missing 001"):
        migrations.discover(tmp_path)


def test_discover_refuses_a_duplicate_version(tmp_path):
    _write(tmp_path, "001_first.sql")
    _write(tmp_path, "001_other.sql")
    with pytest.raises(MigrationError, match="duplicate migration version 001"):
        migrations.discover(tmp_path)


@pytest.mark.parametrize(
    "bad",
    ["1_first.sql", "001-first.sql", "001_First.sql", "001_first.SQL", "001.sql", "first.sql"],
)
def test_discover_refuses_misnamed_sql_files(tmp_path, bad):
    _write(tmp_path, bad)
    with pytest.raises(MigrationError, match="misnamed migration file"):
        migrations.discover(tmp_path)


def test_render_substitutes_every_schema_token():
    s = Settings(workflow_schema="wf_x", queue_schema="q_x", auth_schema="auth_x")
    out = migrations.render(
        "CREATE TABLE {{workflow}}.a (); ALTER TABLE {{queue}}.m ADD c int; -- {{auth}}", s
    )
    assert out == "CREATE TABLE wf_x.a (); ALTER TABLE q_x.m ADD c int; -- auth_x"


def test_render_refuses_unknown_tokens():
    s = Settings()
    # No data-plane token on purpose: Bronze/Silver DDL is rendered from the contract.
    with pytest.raises(MigrationError, match=r"unknown schema token\(s\) \{\{bronze\}\}"):
        migrations.render("CREATE TABLE {{bronze}}.x ()", s)


def test_render_leaves_sql_without_tokens_untouched():
    s = Settings()
    sql = "DO $m$ BEGIN PERFORM 1; END $m$;"
    assert migrations.render(sql, s) == sql
