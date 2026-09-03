"""cinqflow CLI: install, work, status, reset."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys

from cinqflow.dataplane.contract import Layer
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.db import connect
from cinqflow.settings import get_settings
from cinqflow.workflow import ddl


def cmd_install(_: argparse.Namespace) -> int:
    """Idempotent: workflow + queue schemas, and the Bronze layer namespace.

    Bronze *tables* are provisioned per feed at landing time from the contract,
    so nothing here assumes which feeds exist.
    """
    s = get_settings()
    with connect(s) as conn:
        ddl.install(conn, s)
        PostgresDataPlane(conn).install_layer(Layer.BRONZE.value)
        conn.commit()
    s.landing_root.mkdir(parents=True, exist_ok=True)
    print(f"installed: schemas {s.workflow_schema}, {s.queue_schema}, {Layer.BRONZE.value}")
    print(f"landing root: {s.landing_root}")
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
    schemas = [s.workflow_schema, s.queue_schema, Layer.BRONZE.value, s.silver_schema]

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
        PostgresDataPlane(conn).install_layer(Layer.BRONZE.value)
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

    sub.add_parser("install", help="create/upgrade workflow + queue schemas").set_defaults(
        func=cmd_install
    )
    work = sub.add_parser("work", help="run the queue worker")
    work.add_argument("--once", action="store_true", help="drain the queue and exit")
    work.set_defaults(func=cmd_work)
    sub.add_parser("status", help="show queue depth and recent uploads").set_defaults(
        func=cmd_status
    )
    reset = sub.add_parser(
        "reset", help="drop every schema this platform owns and reinstall empty"
    )
    reset.add_argument(
        "--yes", action="store_true", help="actually do it (otherwise just prints the plan)"
    )
    reset.set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
