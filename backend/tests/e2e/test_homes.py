"""The two homes' endpoints (PR-4): what waits on an analyst, and what needs the
platform team - both read off the ledger and the queue, nothing new stored."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.queue import MAX_ATTEMPTS, Queue
from cinqflow.queue.worker import drain, run_once
from cinqflow.workers import interpret_upload
from cinqflow.workflow.store import UnknownUpload
from tests.conftest import authed_client, requires_db

pytestmark = requires_db

FEED = "test_e2e_homes"
GHOST = "6f0c0000-0000-0000-0000-00000000dead"


@pytest.fixture
def client(conn, settings):
    return authed_client(TestClient(create_app(settings)), conn, settings)


@pytest.fixture(autouse=True)
def drop_feed_table(conn, settings):
    yield
    conn.rollback()
    table = bronze_table(FEED)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table.schema}."{table.name}" CASCADE')
    conn.commit()


def _drain(settings) -> int:
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as worker_conn:
        return drain(worker_conn, settings)


def _upload(client, filename: str, content: bytes, content_type: str = "text/csv") -> str:
    response = client.post(
        "/api/uploads",
        files={"file": (filename, content, content_type)},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["upload_id"]


# ---------------------------------------------------------------- worklist


def test_worklist_counts_and_says_how_long_a_gate_has_been_open(client, settings, small_csv_bytes):
    upload_id = _upload(client, "roster.csv", small_csv_bytes)
    _drain(settings)  # profile + interpret: G1 opens

    worklist = client.get("/api/worklist").json()
    assert worklist["counts"]["waiting_at_g1"] >= 1
    assert "approvable_at_g2" in worklist["counts"]
    item = next(u for u in worklist["uploads_at_g1"] if u["upload_id"] == upload_id)
    # The moment the gate opened, from the ledger - not the upload's creation.
    steps = {s["key"]: s for s in client.get(f"/api/uploads/{upload_id}/progress").json()["steps"]}
    assert item["waiting_since"] == steps["gate_g1"]["run"]["started_ts"]
    assert upload_id in [u["upload_id"] for u in worklist["recent_uploads"]]

    # Deciding takes it off the list.
    assert client.post(f"/api/uploads/{upload_id}/approve", json={}).status_code == 202
    after = client.get("/api/worklist").json()
    assert upload_id not in [u["upload_id"] for u in after["uploads_at_g1"]]


# --------------------------------------------------------------- attention


def test_attention_shows_in_flight_then_failed_steps_with_where_to_go(client, settings):
    upload_id = _upload(client, "broken.xlsx", b"\x00not-a-workbook", "application/vnd.ms-excel")

    # Queued, no worker yet: in flight, named by feed and file, with its route.
    attention = client.get("/api/attention").json()
    queued = next(s for s in attention["in_flight_steps"] if s["scope_id"] == upload_id)
    assert (queued["step_key"], queued["state"]) == ("profile", "pending")
    assert (queued["feed"], queued["filename"]) == (FEED, "broken.xlsx")
    assert queued["href"] == f"/uploads/{upload_id}"
    assert attention["queue_depth"]["pending_total"] >= 1
    assert attention["queue_depth"]["upload.profile"] >= 1

    _drain(settings)
    attention = client.get("/api/attention").json()
    failed = next(s for s in attention["failed_steps"] if s["scope_id"] == upload_id)
    assert failed["step_key"] == "profile"
    assert failed["label"] == "Parse and profile"
    assert failed["error"].startswith("ParseError")
    assert failed["href"] == f"/uploads/{upload_id}"
    assert upload_id not in [s["scope_id"] for s in attention["in_flight_steps"]]

    # The feed's latest upload is adverse, so the feed sorts first.
    feed = attention["feeds"][0]
    assert feed["feed"] == FEED and feed["adverse"] is True
    assert feed["status"] == "profile_failed"


def test_a_rejected_gate_is_a_decision_not_a_failure(client, settings, small_csv_bytes):
    upload_id = _upload(client, "reject.csv", small_csv_bytes)
    _drain(settings)
    assert client.post(f"/api/uploads/{upload_id}/reject", json={}).status_code == 202

    attention = client.get("/api/attention").json()
    assert all(
        not (s["scope_id"] == upload_id and s["step_key"] == "gate_g1")
        for s in attention["failed_steps"]
    )


def test_dead_letter_messages_are_listed_with_their_last_error(conn, settings, client):
    message_id = Queue(conn, settings).enqueue(
        interpret_upload.TOPIC, {"upload_id": GHOST}, dedupe_key="homes/ghost"
    )
    conn.commit()
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(UnknownUpload):
            run_once(conn, settings)

    attention = client.get("/api/attention").json()
    dead = next(m for m in attention["dead_messages"] if m["message_id"] == message_id)
    assert dead["topic"] == interpret_upload.TOPIC
    assert dead["attempts"] == MAX_ATTEMPTS
    assert dead["last_error"].startswith("UnknownUpload")
    assert dead["payload"]["upload_id"] == GHOST
