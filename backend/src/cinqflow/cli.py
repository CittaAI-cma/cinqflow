"""cinqflow CLI: install, work, status."""

from __future__ import annotations

import argparse
import json
import logging
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
