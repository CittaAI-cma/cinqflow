"""queue.message on Postgres — ADR-0014's queue, dev and target IDENTICAL.

    "queue: enqueue/consume   mock: memq   dev: pg_skip_locked
     target: pg_skip_locked"
    — docs/architecture/plates/04-pin-out-map.md

`SELECT ... FOR UPDATE SKIP LOCKED` on the Postgres already running. Volume is
thousands of messages a day, and a proprietary broker SDK would silently weld
the platform to one cloud for throughput nobody needs.

The SAME contract suite that runs against `MemQueue` runs against this. What
the mock keeps as dict semantics this one keeps as constraints: the repeated
dedupe_key is a UNIQUE constraint, and the crashed-worker guarantee is the
claim context manager returning the row to `pending` with its attempt counted.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.ports import port
from cinqflow.ports.queue import Message, QueueStats


def _message(row: tuple[Any, ...]) -> Message:
    message_id, topic, payload, enqueued_ts, attempts, dedupe_key = row
    return Message(
        message_id=str(message_id),
        topic=topic,
        payload=payload,
        enqueued_ts=enqueued_ts,
        attempts=attempts,
        dedupe_key=dedupe_key,
    )


@port("queue", "pg-skip-locked")
class PostgresQueue:
    """Requires a connection, which is why the contract suite constructs it
    with one rather than with defaults."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def enqueue(self, topic: str, payload: dict[str, Any], dedupe_key: str | None = None) -> str:
        import json

        if dedupe_key is not None:
            existing = self._db.fetch_one(
                "SELECT message_id FROM queue.message WHERE dedupe_key = %s",
                (dedupe_key,),
            )
            if existing is not None:
                # Replay safety starts here, not at the consumer.
                return str(existing[0])
        message_id = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO queue.message (message_id, topic, payload, state, dedupe_key, "
            "attempts, enqueued_ts) VALUES (%s, %s, %s, 'pending', %s, 0, %s) "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            (message_id, topic, json.dumps(payload, sort_keys=True), dedupe_key, _now()),
        )
        if dedupe_key is not None:
            # A concurrent producer may have won the ON CONFLICT race; the id
            # returned is whichever row actually holds the dedupe_key.
            row = self._db.fetch_one(
                "SELECT message_id FROM queue.message WHERE dedupe_key = %s",
                (dedupe_key,),
            )
            if row is not None:
                return str(row[0])
        return message_id

    @contextmanager
    def claim(self, topic: str, *, timeout_s: int = 300) -> Iterator[Message | None]:
        _ = timeout_s  # visibility timeout arrives with the reaper worker
        row = self._db.fetch_one(
            "SELECT message_id, topic, payload, enqueued_ts, attempts, dedupe_key "
            "FROM queue.message "
            "WHERE topic = %s AND state = 'pending' "
            "ORDER BY enqueued_ts, message_id "
            "FOR UPDATE SKIP LOCKED LIMIT 1",
            (topic,),
        )
        if row is None:
            yield None
            return
        message = _message(row)
        self._db.execute(
            "UPDATE queue.message SET state = 'in_flight', claimed_ts = %s WHERE message_id = %s",
            (_now(), message.message_id),
        )
        try:
            yield message
        except Exception:
            # Returned to the queue, attempt counted. A crashed worker strands
            # nothing, and a poison message is visible rather than invisible.
            self._db.execute(
                "UPDATE queue.message SET state = 'pending', claimed_ts = NULL, "
                "attempts = attempts + 1 WHERE message_id = %s",
                (message.message_id,),
            )
            raise
        else:
            self._db.execute(
                "UPDATE queue.message SET state = 'done', acked_ts = %s WHERE message_id = %s",
                (_now(), message.message_id),
            )

    def drain(self, topic: str) -> Iterator[Message]:
        rows = self._db.fetch_all(
            "SELECT message_id, topic, payload, enqueued_ts, attempts, dedupe_key "
            "FROM queue.message "
            "WHERE topic = %s AND state = 'pending' ORDER BY enqueued_ts, message_id",
            (topic,),
        )
        yield from (_message(row) for row in rows)

    def stats(self) -> QueueStats:
        rows = self._db.fetch_all(
            "SELECT state, topic, count(*) FROM queue.message "
            "WHERE state IN ('pending','in_flight','failed') GROUP BY state, topic"
        )
        pending = in_flight = failed = 0
        by_topic: dict[str, int] = {}
        for state, topic, count in rows:
            if state == "pending":
                pending += count
                by_topic[topic] = by_topic.get(topic, 0) + count
            elif state == "in_flight":
                in_flight += count
            elif state == "failed":
                failed += count
        return QueueStats(pending=pending, in_flight=in_flight, failed=failed, by_topic=by_topic)


def _now() -> datetime:
    return datetime.now(UTC)
