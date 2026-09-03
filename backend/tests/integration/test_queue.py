"""Queue behaviour against real Postgres: dedupe, claim isolation, crash recovery."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from cinqflow.queue.queue import MAX_ATTEMPTS, Queue
from tests.conftest import requires_db

pytestmark = requires_db


def test_enqueue_then_claim_roundtrip(conn, settings):
    q = Queue(conn, settings)
    assert q.enqueue("t.a", {"n": 1}, dedupe_key="k1") is not None
    conn.commit()

    with q.claim(["t.a"]) as message:
        assert message is not None
        assert message.topic == "t.a"
        assert message.payload == {"n": 1}
        assert message.attempts == 1

    assert q.depth("t.a") == 0


def test_dedupe_key_refuses_the_second_enqueue(conn, settings):
    q = Queue(conn, settings)
    assert q.enqueue("t.a", {"n": 1}, dedupe_key="same") is not None
    assert q.enqueue("t.a", {"n": 2}, dedupe_key="same") is None
    conn.commit()
    assert q.depth("t.a") == 1


def test_claim_skips_locked_rows_so_two_workers_never_collide(conn, settings):
    q = Queue(conn, settings)
    q.enqueue("t.a", {"n": 1}, dedupe_key="only-one")
    conn.commit()

    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as other:
        with q.claim(["t.a"]) as mine:
            assert mine is not None
            with Queue(other, settings).claim(["t.a"]) as theirs:
                assert theirs is None  # locked by the first worker, skipped


def test_failure_returns_the_message_to_pending_with_the_error(conn, settings):
    q = Queue(conn, settings)
    q.enqueue("t.a", {"n": 1}, dedupe_key="fails")
    conn.commit()

    with pytest.raises(RuntimeError):
        with q.claim(["t.a"]) as message:
            assert message is not None
            raise RuntimeError("handler exploded")

    assert q.depth("t.a") == 1
    row = next(
        r
        for r in conn.execute(
            f"SELECT state, attempts, last_error FROM {settings.queue_schema}.message"
        ).fetchall()
    )
    assert row["state"] == "pending"
    assert row["attempts"] == 1
    assert "handler exploded" in row["last_error"]


def test_message_dies_after_max_attempts(conn, settings):
    q = Queue(conn, settings)
    q.enqueue("t.a", {"n": 1}, dedupe_key="doomed")
    conn.commit()

    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(RuntimeError):
            with q.claim(["t.a"]) as message:
                assert message is not None
                raise RuntimeError("nope")

    assert q.depth("t.a") == 0
    state = conn.execute(f"SELECT state FROM {settings.queue_schema}.message").fetchone()["state"]
    assert state == "dead"


def test_claim_only_returns_registered_topics(conn, settings):
    q = Queue(conn, settings)
    q.enqueue("t.unknown", {}, dedupe_key="u1")
    conn.commit()
    with q.claim(["t.a"]) as message:
        assert message is None
