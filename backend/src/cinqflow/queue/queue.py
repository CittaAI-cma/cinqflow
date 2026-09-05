"""Durable queue on Postgres. No Celery, no broker.

Claim uses SELECT ... FOR UPDATE SKIP LOCKED so N workers never collide, and a
crashed worker's message returns to `pending` with attempts incremented.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg

from cinqflow.db import fetch_one
from cinqflow.settings import Settings, get_settings

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Message:
    message_id: str
    topic: str
    dedupe_key: str
    payload: dict[str, Any]
    attempts: int


class Queue:
    def __init__(self, conn: psycopg.Connection, settings: Settings | None = None) -> None:
        self.conn = conn
        self.s = settings or get_settings()

    def enqueue(self, topic: str, payload: dict[str, Any], *, dedupe_key: str) -> str | None:
        """Returns the message id, or None when the dedupe key already exists."""
        row = fetch_one(
            self.conn,
            f"""
            INSERT INTO {self.s.queue_schema}.message (message_id, topic, dedupe_key, payload)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING message_id
            """,
            (str(uuid.uuid4()), topic, dedupe_key, json.dumps(payload)),
        )
        return str(row["message_id"]) if row else None

    def reclaim_stale(self) -> int:
        """Return messages whose worker died mid-handler to `pending`.

        The claim is committed before the handler runs, so a hard crash leaves the
        row `claimed` rather than lost; this is what picks it back up. Attempts are
        already counted, so a repeatedly crashing message still reaches `dead`.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {self.s.queue_schema}.message
                    SET state = 'pending', claimed_ts = NULL
                    WHERE state = 'claimed'
                      AND claimed_ts < now() - make_interval(secs => %s)""",
                (self.s.queue_claim_timeout_seconds,),
            )
            return cur.rowcount

    @contextmanager
    def claim(self, topics: list[str] | None = None) -> Iterator[Message | None]:
        """Claim one pending message. On exception it is returned to `pending`
        (or `dead` past MAX_ATTEMPTS) and the error is recorded.

        The claim itself is committed before the handler runs, so rolling back the
        handler's work cannot rewind the attempt count.
        """
        self.reclaim_stale()
        with self.conn.cursor() as cur:
            if topics:
                cur.execute(
                    f"""SELECT * FROM {self.s.queue_schema}.message
                        WHERE state = 'pending' AND topic = ANY(%s)
                        ORDER BY enqueued_ts FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (topics,),
                )
            else:
                cur.execute(
                    f"""SELECT * FROM {self.s.queue_schema}.message
                        WHERE state = 'pending'
                        ORDER BY enqueued_ts FOR UPDATE SKIP LOCKED LIMIT 1"""
                )
            row = cur.fetchone()
            if row is None:
                yield None
                return

            cur.execute(
                f"""UPDATE {self.s.queue_schema}.message
                    SET state = 'claimed', attempts = attempts + 1, claimed_ts = now()
                    WHERE message_id = %s""",
                (row["message_id"],),
            )
            message = Message(
                message_id=str(row["message_id"]),
                topic=row["topic"],
                dedupe_key=row["dedupe_key"],
                payload=row["payload"],
                attempts=row["attempts"] + 1,
            )
        # Durable before the handler starts: the attempt is spent either way.
        self.conn.commit()

        try:
            yield message
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            self.conn.rollback()
            state = "dead" if message.attempts >= MAX_ATTEMPTS else "pending"
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE {self.s.queue_schema}.message
                        SET state = %s, last_error = %s, claimed_ts = NULL
                        WHERE message_id = %s""",
                    (state, f"{type(exc).__name__}: {exc}"[:2000], message.message_id),
                )
            self.conn.commit()
            raise
        else:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE {self.s.queue_schema}.message
                        SET state = 'done', done_ts = now() WHERE message_id = %s""",
                    (message.message_id,),
                )
            self.conn.commit()

    @property
    def max_attempts(self) -> int:
        return MAX_ATTEMPTS

    def payload_of(self, message_id: str) -> dict[str, Any] | None:
        """The payload a message carried - so a re-run (workflow/rerun.py) can
        repeat exactly what the last generation was asked to do."""
        row = fetch_one(
            self.conn,
            f"SELECT payload FROM {self.s.queue_schema}.message WHERE message_id = %s",
            (message_id,),
        )
        return dict(row["payload"]) if row else None

    def state_of(self, message_id: str) -> dict[str, Any] | None:
        """`{state, attempts, last_error}` for one message, or None."""
        return fetch_one(
            self.conn,
            f"""SELECT state, attempts, last_error FROM {self.s.queue_schema}.message
                WHERE message_id = %s""",
            (message_id,),
        )

    def depth(self, topic: str | None = None) -> int:
        sql = f"SELECT count(*) AS n FROM {self.s.queue_schema}.message WHERE state = 'pending'"
        params: tuple = ()
        if topic:
            sql += " AND topic = %s"
            params = (topic,)
        return fetch_one(self.conn, sql, params)["n"]
