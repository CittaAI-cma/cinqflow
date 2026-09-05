"""cinqflow CLI: install, migrate, work, status, reset."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys

from cinqflow import migrations
from cinqflow.auth import ddl as auth_ddl
from cinqflow.auth.store import bootstrap_admin
from cinqflow.dataplane.contract import Layer
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.db import connect
from cinqflow.settings import get_settings
from cinqflow.workflow import ddl


def _migrations_line(applied: list[migrations.Migration], total_applied: int) -> str:
    if applied:
        return "migrations: applied " + ", ".join(m.label for m in applied)
    return f"migrations: none pending ({total_applied} applied)"


def cmd_install(_: argparse.Namespace) -> int:
    """Idempotent: workflow + queue + auth schemas, the Bronze layer namespace, then
    every pending schema migration.

    Bronze *tables* are provisioned per feed at landing time from the contract,
    so nothing here assumes which feeds exist. The baseline DDL (`workflow/ddl.py`,
    `auth/ddl.py`) is frozen; every schema change since ships as a numbered file in
    `cinqflow/migrations/` and is applied here, in order, after the baseline - so the
    compose `migrate` service and Railway's pre-deploy `bootstrap.sh`, which both run
    this command, pick migrations up with no change. Auth also seeds the MVP role
    list and, if CINQFLOW_BOOTSTRAP_ADMIN_EMAIL is set, one administrator -
    see docs/blueprints/auth-and-user-management.md.
    """
    s = get_settings()
    with connect(s) as conn:
        ddl.install(conn, s)
        auth_ddl.install(conn, s)
        PostgresDataPlane(conn).install_layer(Layer.BRONZE.value)
        applied = migrations.apply_pending(conn, s)
        total_applied = len(migrations.applied(conn, s))
        admin = bootstrap_admin(conn, s)
        conn.commit()
    s.landing_root.mkdir(parents=True, exist_ok=True)
    print(
        f"installed: schemas {s.workflow_schema}, {s.queue_schema}, "
        f"{s.auth_schema}, {Layer.BRONZE.value}"
    )
    print(_migrations_line(applied, total_applied))
    print(f"landing root: {s.landing_root}")
    if admin is not None:
        print(f"bootstrapped administrator: {admin.email}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """Apply pending schema migrations on their own, or with --status just list what
    this database has applied and what is still pending. `install` already applies
    them; this exists for an operator looking at one database."""
    s = get_settings()
    with connect(s) as conn:
        migrations.ensure_version_table(conn, s)
        if args.status:
            done = migrations.applied(conn, s)
            todo = migrations.pending(conn, s)
            print(f"applied: {len(done)}")
            for row in done:
                print(
                    f"  {row['version']:03d}_{row['name']}  {row['applied_ts']:%Y-%m-%dT%H:%M:%SZ}"
                )
            print(f"pending: {len(todo)}")
            for m in todo:
                print(f"  {m.label}")
            return 0
        applied = migrations.apply_pending(conn, s)
        total_applied = len(migrations.applied(conn, s))
        conn.commit()
    print(_migrations_line(applied, total_applied))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Drop every schema cinqflow owns and the landing zone, then reinstall
    fresh - a cold, empty environment with the same DDL `install` produces.

    Deliberately narrow: only `DROP SCHEMA ... CASCADE` on the four schemas
    this platform's own settings name (workflow, queue, bronze, silver). This
    database is shared with a prior implementation (see settings.py's own
    comments on `jobq`/`silver` vs. its `queue`/`silver_raw`) - this command
    must never touch schemas cinqflow does not own, so it never enumerates
    "everything in the database" and never runs without --yes.

    `DROP SCHEMA ... CASCADE` is DDL, not a DELETE/UPDATE/TRUNCATE against an
    existing table - Bronze's append-only trigger (dataplane/pg.py) guards
    row-level mutation of a table that keeps existing; it was never meant to,
    and does not, survive the table itself being dropped as part of tearing
    down the whole environment. That is categorically different from - and
    this command is not a substitute for - deleting one upload's rows while
    everything else stays (workflow/store.py's `delete_upload`).
    """
    s = get_settings()
    schemas = [
        s.workflow_schema,
        s.queue_schema,
        s.auth_schema,
        Layer.BRONZE.value,
        s.silver_schema,
    ]

    if not args.yes:
        print("This drops every table cinqflow owns and starts empty:")
        for schema in schemas:
            print(f"  DROP SCHEMA {schema} CASCADE")
        print(f"  rm -rf {s.landing_root}")
        print(f"\nDatabase: {s.database_url}")
        print("\nRe-run with --yes to actually do this. Nothing has been touched.")
        return 1

    with connect(s) as conn:
        with conn.cursor() as cur:
            for schema in schemas:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.commit()
    print(f"dropped: {', '.join(schemas)}")

    if s.landing_root.exists():
        shutil.rmtree(s.landing_root)
    print(f"cleared landing root: {s.landing_root}")

    with connect(s) as conn:
        ddl.install(conn, s)
        auth_ddl.install(conn, s)
        PostgresDataPlane(conn).install_layer(Layer.BRONZE.value)
        # A reset must land on the same shape `install` produces - baseline DDL alone
        # is the frozen starting point, not the current schema.
        migrations.apply_pending(conn, s)
        bootstrap_admin(conn, s)
        conn.commit()
    s.landing_root.mkdir(parents=True, exist_ok=True)
    print("reinstalled: empty, ready for a first upload")
    return 0


def cmd_work(args: argparse.Namespace) -> int:
    from cinqflow.queue import worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.once:
        s = get_settings()
        with connect(s) as conn:
            processed = worker.drain(conn, s)
        print(f"processed {processed} message(s)")
        return 0
    worker.serve()
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    from cinqflow.queue.queue import Queue
    from cinqflow.workflow.store import WorkflowStore

    s = get_settings()
    with connect(s) as conn:
        uploads = WorkflowStore(conn, s).list_uploads(20)
        depth = Queue(conn, s).depth()
    print(json.dumps({"pending_messages": depth}, indent=2))
    for u in uploads:
        print(f"{u.created_ts:%Y-%m-%d %H:%M}  {u.status:<18} {u.filename}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cinqflow")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "install", help="create the baseline schemas, then apply pending migrations"
    ).set_defaults(func=cmd_install)
    migrate = sub.add_parser(
        "migrate", help="apply pending schema migrations (install does this too)"
    )
    migrate.add_argument(
        "--status", action="store_true", help="list applied and pending migrations; apply nothing"
    )
    migrate.set_defaults(func=cmd_migrate)
    work = sub.add_parser("work", help="run the queue worker")
    work.add_argument("--once", action="store_true", help="drain the queue and exit")
    work.set_defaults(func=cmd_work)
    sub.add_parser("status", help="show queue depth and recent uploads").set_defaults(
        func=cmd_status
    )
    reset = sub.add_parser("reset", help="drop every schema this platform owns and reinstall empty")
    reset.add_argument(
        "--yes", action="store_true", help="actually do it (otherwise just prints the plan)"
    )
    reset.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
