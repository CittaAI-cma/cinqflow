"""Versioned schema migrations for the control-plane schemas (workflow, queue, auth).

Why this exists: `workflow/ddl.py` and `auth/ddl.py` are idempotent `CREATE ... IF NOT
EXISTS` - they install a fresh database but cannot change a table that already exists.
Every epic from here on adds or widens a column, and Stage 6 already had to smuggle two
`ALTER`s into `ddl.py` behind a guarded `DO` block. This module is where such changes go
instead, numbered and recorded, so a database can say exactly which shape it has.

Conventions - each one is enforced by `discover()`/`apply_pending()`, not by review:

- A migration is a plain SQL file in this directory named `NNN_snake_case_name.sql`
  (three-digit version, then a name). Files ship inside the package, next to the code that
  needs them, exactly as `intelligence/prompts/*.md` do - so Docker, Railway and a bare
  `poetry run` all see the same files with no extra packaging step.
- Versions are contiguous from `001`. A gap, a duplicate, or a `.sql` file that does not
  match the pattern refuses the whole set before anything is applied.
- Schema names are never hardcoded: write `{{workflow}}`, `{{queue}}` or `{{auth}}` and the
  runner substitutes `Settings.workflow_schema` etc. - the same per-test schema isolation
  every other DDL here already has. Any other `{{token}}` is an error. There is deliberately
  no token for the data plane: Bronze/Silver DDL is rendered from `dataplane/contract.py`
  by `dataplane/pg.py` and stays there (structure.md boundary 6).
- Each file is applied in its own transaction under an advisory lock and recorded in
  `{{workflow}}.schema_version` with its name and `applied_ts`. A failure rolls that file
  back completely and stops; nothing after it runs.
- An applied migration is history: never edit it, never rename it, never delete it. The
  runner refuses to start if an applied version's file is missing or renamed, because a
  database that claims a shape its code no longer describes is worse than a failed deploy.
- From this module's first release, `ddl.py`/`auth/ddl.py` are the frozen baseline. New
  tables and every column change are migrations - including tables that only exist because
  of a later stage (the first one is `001_step_run.sql`, PR-2).

Not Alembic, and not an ORM: `db.py` says why - every statement is visible SQL, and the
whole mechanism is one table and a directory listing.

Entry points: `cinqflow install` applies pending migrations after the baseline DDL (so the
compose `migrate` service and Railway's `bootstrap.sh` need no change), and
`cinqflow migrate [--status]` applies or lists them on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from cinqflow.db import fetch_all
from cinqflow.settings import Settings

#: Where shipped migrations live. Read at call time so a test can point the runner at a
#: temporary directory by monkeypatching this name.
MIGRATIONS_DIR = Path(__file__).parent

#: One lock for every runner in this database, so two `cinqflow install`s (Railway's
#: pre-deploy and a compose `migrate` service, say) apply each file once between them.
_ADVISORY_LOCK_KEY = 0x63696E71_666C6F77  # "cinqflow" as two little-endian words

_FILENAME = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")
_TOKEN = re.compile(r"\{\{(\w+)\}\}")


class MigrationError(RuntimeError):
    """A migration set that cannot be applied safely - refused before anything runs."""


@dataclass(frozen=True, order=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def label(self) -> str:
        return f"{self.version:03d}_{self.name}"


# ----------------------------------------------------------------------- discovery
def discover(directory: Path | None = None) -> list[Migration]:
    """Every migration in `directory`, sorted, validated as a contiguous 001..N set."""
    root = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(root.iterdir()):
        # Anything that *looks* like SQL must be a well-formed migration - matching the
        # suffix case-insensitively is what makes `001_first.SQL` a refusal rather than a
        # file that silently never runs. Everything else (`__init__.py`, `__pycache__/`,
        # a README) is simply not a migration.
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"misnamed migration file {path.name!r}: expected NNN_snake_case_name.sql"
            )
        found.append(Migration(int(match["version"]), match["name"], path))

    found.sort()
    seen: dict[int, Migration] = {}
    for migration in found:
        if migration.version in seen:
            raise MigrationError(
                f"duplicate migration version {migration.version:03d}: "
                f"{seen[migration.version].filename} and {migration.filename}"
            )
        seen[migration.version] = migration

    expected = list(range(1, len(found) + 1))
    actual = [m.version for m in found]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        raise MigrationError(
            f"migration versions must be contiguous from 001; missing "
            f"{', '.join(f'{v:03d}' for v in missing) or 'none'}, found "
            f"{', '.join(f'{v:03d}' for v in actual) or 'none'}"
        )
    return found


def render(sql: str, settings: Settings) -> str:
    """Substitute the schema tokens. Unknown tokens are an error, not a pass-through."""
    schemas = {
        "workflow": settings.workflow_schema,
        "queue": settings.queue_schema,
        "auth": settings.auth_schema,
    }
    unknown = sorted({t for t in _TOKEN.findall(sql) if t not in schemas})
    if unknown:
        raise MigrationError(
            f"unknown schema token(s) {', '.join('{{' + t + '}}' for t in unknown)}; "
            f"allowed: {', '.join('{{' + k + '}}' for k in schemas)}"
        )
    return _TOKEN.sub(lambda m: schemas[m.group(1)], sql)


# ------------------------------------------------------------------- version table
def ensure_version_table(conn: psycopg.Connection, settings: Settings) -> None:
    """Idempotent, and committed on its own so a later rollback can never undo it."""
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {settings.workflow_schema}")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {settings.workflow_schema}.schema_version (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_ts TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def applied(conn: psycopg.Connection, settings: Settings) -> list[dict]:
    """Rows of `schema_version`, oldest first: version, name, applied_ts."""
    return fetch_all(
        conn,
        f"""SELECT version, name, applied_ts FROM {settings.workflow_schema}.schema_version
            ORDER BY version""",
    )


def pending(
    conn: psycopg.Connection, settings: Settings, directory: Path | None = None
) -> list[Migration]:
    """Migrations on disk that this database has not applied, after checking that
    what it *has* applied still matches the files exactly."""
    files = discover(directory)
    by_version = {m.version: m for m in files}
    done = applied(conn, settings)

    for row in done:
        on_disk = by_version.get(row["version"])
        if on_disk is None or on_disk.name != row["name"]:
            raise MigrationError(
                f"applied migration {row['version']:03d}_{row['name']} has no matching file "
                f"(renamed or deleted?) - applied migrations are history and must stay put"
            )

    applied_versions = {row["version"] for row in done}
    high_water = max(applied_versions, default=0)
    # `discover` guarantees the files are contiguous and the loop above that every applied
    # version has its file, so a file at or below the high-water mark that is *not*
    # recorded can only mean `schema_version` itself has a hole - refuse rather than
    # quietly apply out of order.
    out_of_order = [
        m for m in files if m.version <= high_water and m.version not in applied_versions
    ]
    if out_of_order:
        names = ", ".join(m.label for m in out_of_order)
        raise MigrationError(
            f"migration(s) {names} sit below the applied high-water mark {high_water:03d} but "
            f"are not recorded as applied - refusing to apply out of order"
        )
    return [m for m in files if m.version > high_water]


# ------------------------------------------------------------------------- apply
def apply_pending(
    conn: psycopg.Connection, settings: Settings, directory: Path | None = None
) -> list[Migration]:
    """Apply every pending migration, one transaction each, and return what was applied.

    The set is validated in full before the first file runs (`pending`), so a gap or a
    misnamed file refuses everything rather than applying half a release.
    """
    ensure_version_table(conn, settings)
    todo = pending(conn, settings, directory)
    done: list[Migration] = []

    for migration in todo:
        sql = render(migration.path.read_text(encoding="utf-8"), settings)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
                # Another runner may have applied this exact version while we waited
                # for the lock - the version row is the truth, so skip rather than fail.
                cur.execute(
                    f"SELECT 1 FROM {settings.workflow_schema}.schema_version WHERE version = %s",
                    (migration.version,),
                )
                if cur.fetchone() is not None:
                    conn.rollback()
                    continue
                # No parameters, so psycopg sends the whole file as one multi-statement
                # command - `DO $$ … $$` blocks included.
                cur.execute(sql)
                cur.execute(
                    f"""INSERT INTO {settings.workflow_schema}.schema_version (version, name)
                        VALUES (%s, %s)""",
                    (migration.version, migration.name),
                )
            conn.commit()
        except MigrationError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised with the migration named
            conn.rollback()
            raise MigrationError(
                f"migration {migration.label} failed and was rolled back: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        done.append(migration)

    return done
