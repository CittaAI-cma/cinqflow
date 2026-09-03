"""Consumer loop. Refuses unknown topics before claiming anything."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg

from cinqflow.db import connect
from cinqflow.queue.queue import Queue
from cinqflow.settings import Settings, get_settings
from cinqflow.workers import (
    analyze_bronze,
    interpret_upload,
    land_bronze,
    profile_upload,
    promote_silver,
    reject_upload,
    run_preview,
)

log = logging.getLogger(__name__)

Handler = Callable[[psycopg.Connection, dict], dict]


def handlers(settings: Settings) -> dict[str, Handler]:
    """Every topic the platform knows how to run."""
    return {
        profile_upload.TOPIC: lambda conn, payload: profile_upload.handle(conn, payload, settings),
        interpret_upload.TOPIC: lambda conn, payload: interpret_upload.handle(
            conn, payload, settings
        ),
        land_bronze.TOPIC: lambda conn, payload: land_bronze.handle(conn, payload, settings),
        reject_upload.TOPIC: lambda conn, payload: reject_upload.handle(conn, payload, settings),
        analyze_bronze.TOPIC: lambda conn, payload: analyze_bronze.handle(conn, payload, settings),
        run_preview.TOPIC: lambda conn, payload: run_preview.handle(conn, payload, settings),
        promote_silver.TOPIC: lambda conn, payload: promote_silver.handle(
            conn, payload, settings
        ),
    }


class NoHandler(Exception):
    pass


def run_once(conn: psycopg.Connection, settings: Settings | None = None) -> dict | None:
    """Claim and process at most one message. Returns its result, or None."""
    s = settings or get_settings()
    registry = handlers(s)
    queue = Queue(conn, s)
    with queue.claim(list(registry)) as message:
        if message is None:
            return None
        handler = registry.get(message.topic)
        if handler is None:  # pragma: no cover - claim() filtered by topic
            raise NoHandler(message.topic)
        log.info("handling %s (attempt %s)", message.topic, message.attempts)
        return handler(conn, message.payload)


def drain(conn: psycopg.Connection, settings: Settings | None = None, limit: int = 100) -> int:
    """Process until the queue is empty. Used by tests and by `cinqflow work --once`."""
    processed = 0
    while processed < limit:
        if run_once(conn, settings) is None:
            break
        processed += 1
    return processed


def serve(poll_seconds: float = 1.0) -> None:  # pragma: no cover - long running
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("worker up; topics=%s", ", ".join(handlers(s)))
    while True:
        try:
            with connect(s) as conn:
                if drain(conn, s) == 0:
                    time.sleep(poll_seconds)
        except KeyboardInterrupt:
            log.info("worker down")
            return
        except Exception:
            log.exception("worker loop error; backing off")
            time.sleep(poll_seconds * 5)


if __name__ == "__main__":  # pragma: no cover
    serve()
