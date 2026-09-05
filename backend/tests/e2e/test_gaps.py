"""The four backend gaps identified for the analyst-forward-flow adoption
(docs/blueprints/forward-flow-adoption.md §6): retry, the land stage + batch_id
on the progress poll, a lightweight batch progress mirror, and the worklist.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.worker import drain
from cinqflow.workflow.states import RunState, UploadStatus
from tests.conftest import authed_client, requires_db

pytestmark = requires_db

FEED = "test_e2e_gaps"


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


def _upload(client, filename: str, content: bytes, content_type: str, feed: str = FEED) -> str:
    response = client.post(
        "/api/uploads",
        files={"file": (filename, content, content_type)},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": feed,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["upload_id"]


def _upload_and_interpret(client, settings, content: bytes, name="roster.csv", feed=FEED) -> str:
    upload_id = _upload(client, name, content, "text/csv", feed)
    _drain(settings)
    assert client.get(f"/api/uploads/{upload_id}").json()["upload"]["status"] == (
        UploadStatus.INTERPRETED
    )
    return upload_id


# --------------------------------------------------------------------- retry


def test_retry_reenqueues_a_failed_profile(client, settings):
    upload_id = _upload(client, "broken.xlsx", b"\x00not-a-workbook", "application/vnd.ms-excel")
    _drain(settings)
    assert client.get(f"/api/uploads/{upload_id}").json()["upload"]["status"] == (
        UploadStatus.PROFILE_FAILED
    )

    retry = client.post(f"/api/uploads/{upload_id}/retry")
    assert retry.status_code == 202, retry.text
    assert retry.json()["queued"] == "upload.profile"

    # The same broken bytes fail the same way every time - proving the retry
    # actually re-ran the worker (not a silent no-op) is what matters here.
    assert _drain(settings) >= 1
    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.PROFILE_FAILED
    assert detail["upload"]["error"]


def test_retry_refuses_a_status_with_nothing_to_retry(client, settings, small_csv_bytes):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes)
    retry = client.post(f"/api/uploads/{upload_id}/retry")
    assert retry.status_code == 409
    assert "interpreted" in retry.json()["detail"]["message"]


def test_retry_of_unknown_upload_is_404(client):
    assert client.post("/api/uploads/6f0c0000-0000-0000-0000-000000000000/retry").status_code == 404


# ------------------------------------------------------- progress: land + batch_id


def test_progress_reports_land_stage_and_batch_id(client, settings, small_csv_bytes):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes)

    approve = client.post(f"/api/uploads/{upload_id}/approve", json={})
    assert approve.status_code == 202

    # Queued, not yet run: the land stage is already "running" (the analyst
    # authorised it), but no run row exists yet, so batch_id is still null.
    queued_progress = client.get(f"/api/uploads/{upload_id}/progress").json()
    land_stage = next(s for s in queued_progress["stages"] if s["key"] == "land")
    assert land_stage["state"] == "running"
    assert queued_progress["batch_id"] is None
    # The ledger is more precise: queued, no worker has taken it yet.
    assert next(s for s in queued_progress["steps"] if s["key"] == "land")["state"] == "pending"

    _drain(settings)

    landed_progress = client.get(f"/api/uploads/{upload_id}/progress").json()
    land_stage = next(s for s in landed_progress["stages"] if s["key"] == "land")
    assert land_stage["state"] == "done"
    assert landed_progress["batch_id"]
    assert next(s for s in landed_progress["steps"] if s["key"] == "land")["state"] == "done"

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert landed_progress["batch_id"] == detail["runs"][0]["batch_id"]


# ------------------------------------------------------------ batch progress


def test_batch_progress_mirrors_the_run_without_the_rest_of_batch_detail(
    client, settings, small_csv_bytes
):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes)
    client.post(f"/api/uploads/{upload_id}/approve", json={})
    _drain(settings)

    detail = client.get(f"/api/uploads/{upload_id}").json()
    batch_id = detail["runs"][0]["batch_id"]

    progress = client.get(f"/api/batches/{batch_id}/progress?kind=land_bronze")
    assert progress.status_code == 200
    body = progress.json()
    assert body["batch_id"] == batch_id
    assert body["kind"] == "land_bronze"
    assert body["state"] == RunState.COMPLETED
    assert body["balanced"] is True
    # None of BatchDetail's other sections leak into the lightweight shape.
    assert "lineage" not in body
    assert "upload" not in body


def test_batch_progress_for_unknown_batch_is_404(client):
    assert client.get("/api/batches/bt_does_not_exist/progress").status_code == 404


# ---------------------------------------------------------------- worklist


def test_worklist_lists_uploads_waiting_at_g1(client, settings, small_csv_bytes):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes)

    worklist = client.get("/api/worklist").json()
    assert upload_id in [u["upload_id"] for u in worklist["uploads_at_g1"]]

    client.post(f"/api/uploads/{upload_id}/approve", json={})

    worklist = client.get("/api/worklist").json()
    assert upload_id not in [u["upload_id"] for u in worklist["uploads_at_g1"]]


# ------------------------------------------------------------------ PHI on the wire


def test_upload_detail_never_carries_phi_sample_values(client, settings, small_csv_bytes):
    """Found by the 2026-09-05 end-to-end run: `GET /api/uploads/{id}` masked the
    sample rows but returned PHI columns' `sample_values` verbatim. The whole
    profile goes through `mask_facts` now."""
    upload_id = _upload(client, "phi.csv", small_csv_bytes, "text/csv")
    _drain(settings)
    facts = client.get(f"/api/uploads/{upload_id}").json()["profile"]["facts"]
    by_name = {c["name"]: c for c in facts["columns"]}
    for name in facts["phi_candidates"]:
        column = by_name[name]
        assert column["sample_values"] == [], name
        assert column["top_values"] == [] and column["min"] is None and column["max"] is None, name
    assert by_name["product"]["sample_values"]  # non-PHI keeps its examples
    assert all(row["member_first_name"] == "•••" for row in facts["sample_rows"])
