"""Selective re-run (PR-3), end to end: the routes, their refusals, and what a
re-run does to the ledger, the queue and the artifacts."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.worker import drain
from cinqflow.workflow.states import UploadStatus
from tests.conftest import authed_client, requires_db

pytestmark = requires_db

FEED = "test_e2e_rerun"
UNKNOWN = "6f0c0000-0000-0000-0000-000000000000"


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


def _steps(client, upload_id: str) -> dict[str, dict]:
    progress = client.get(f"/api/uploads/{upload_id}/progress").json()
    return {s["key"]: s for s in progress["steps"]}


def _landed(client, settings, content: bytes) -> tuple[str, str]:
    upload_id = _upload(client, "ok.csv", content)
    _drain(settings)
    assert client.post(f"/api/uploads/{upload_id}/approve", json={}).status_code == 202
    assert _drain(settings) == 2  # land, then analyze
    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.LANDED
    return upload_id, detail["runs"][0]["batch_id"]


# ------------------------------------------------------------------ the happy path


def test_rerun_of_a_failed_step_is_a_new_generation_that_actually_runs(client, settings):
    upload_id = _upload(client, "broken.xlsx", b"\x00not-a-workbook", "application/vnd.ms-excel")
    _drain(settings)
    # The handler caught the ParseError and returned: the message is done, the
    # step is failed - exactly the case where a re-run is the only way on.
    assert _steps(client, upload_id)["profile"]["state"] == "failed"

    res = client.post(f"/api/uploads/{upload_id}/steps/profile/rerun")
    assert res.status_code == 202, res.text
    body = res.json()
    assert (body["step"], body["generation"], body["queued"]) == ("profile", 2, "upload.profile")
    step = _steps(client, upload_id)["profile"]
    assert (step["state"], step["run"]["generation"]) == ("pending", 2)
    assert client.get("/api/queue/depth").json()["upload_profile"] == 1

    # Already queued: a second re-run is refused, not double-queued.
    again = client.post(f"/api/uploads/{upload_id}/steps/profile/rerun")
    assert again.status_code == 409
    assert again.json()["detail"]["status"] == "pending"

    assert _drain(settings) >= 1
    step = _steps(client, upload_id)["profile"]
    assert step["state"] == "failed"
    assert (step["run"]["generation"], step["run"]["attempts"]) == (2, 1)
    history = client.get(f"/api/steps?scope_kind=upload&scope_id={upload_id}").json()["steps"]
    assert [(r["step_key"], r["generation"], r["state"]) for r in history] == [
        ("profile", 1, "failed"),
        ("profile", 2, "failed"),
    ]


def test_retry_is_an_alias_for_the_step_rerun(client, settings):
    upload_id = _upload(client, "broken.xlsx", b"\x00nope", "application/vnd.ms-excel")
    _drain(settings)

    retry = client.post(f"/api/uploads/{upload_id}/retry")
    assert retry.status_code == 202, retry.text
    assert retry.json()["queued"] == "upload.profile"
    assert retry.json()["generation"] == 2
    # Same refusal as the route it aliases: already queued.
    assert client.post(f"/api/uploads/{upload_id}/retry").status_code == 409


def test_rerunning_analysis_produces_a_new_proposal_and_keeps_the_old(
    client, settings, small_csv_bytes
):
    upload_id, batch_id = _landed(client, settings, small_csv_bytes)
    first = client.get(f"/api/batches/{batch_id}/proposal").json()["proposal_id"]

    res = client.post(f"/api/batches/{batch_id}/steps/analyze/rerun")
    assert res.status_code == 202, res.text
    assert res.json()["generation"] == 2
    assert _drain(settings) == 1

    second = client.get(f"/api/batches/{batch_id}/proposal").json()["proposal_id"]
    assert second != first
    # The earlier proposal is still on record for lineage.
    assert client.get(f"/api/mapping-proposals/{first}").status_code == 200

    steps = {s["key"]: s for s in client.get(f"/api/batches/{batch_id}/progress").json()["steps"]}
    assert steps["analyze"]["state"] == "done"
    assert steps["analyze"]["run"]["generation"] == 2
    assert steps["analyze"]["run"]["artifact_id"] == second
    # ...and the upload's own progress sees the same batch history.
    assert _steps(client, upload_id)["analyze"]["run"]["generation"] == 2


def test_relanding_a_landed_upload_writes_a_second_batch_and_keeps_the_first(
    client, settings, small_csv_bytes
):
    """The replay `LEGAL_TRANSITIONS` always allowed and no route could reach."""
    upload_id, first_batch = _landed(client, settings, small_csv_bytes)

    res = client.post(f"/api/uploads/{upload_id}/steps/land/rerun")
    assert res.status_code == 202, res.text
    assert _drain(settings) == 2  # land again, then analyze the new batch

    detail = client.get(f"/api/uploads/{upload_id}").json()
    batches = sorted({r["batch_id"] for r in detail["runs"] if r["kind"] == "land_bronze"})
    assert len(batches) == 2 and first_batch in batches
    assert detail["upload"]["status"] == UploadStatus.LANDED
    # Both batches are queryable: Bronze is append-only.
    for batch_id in batches:
        assert client.get(f"/api/batches/{batch_id}/rows").json()["total"] == 3
    land = _steps(client, upload_id)["land"]
    assert land["run"]["generation"] == 2 and land["state"] == "done"


# ---------------------------------------------------------------------- refusals


def test_gates_unknown_steps_and_wrong_scopes_are_refused(client, settings, small_csv_bytes):
    upload_id = _upload(client, "ok.csv", small_csv_bytes)
    _drain(settings)

    gate = client.post(f"/api/uploads/{upload_id}/steps/gate_g1/rerun")
    assert gate.status_code == 409
    assert "decision" in gate.json()["detail"]["message"]

    assert client.post(f"/api/uploads/{upload_id}/steps/nope/rerun").status_code == 404

    wrong = client.post(f"/api/uploads/{upload_id}/steps/analyze/rerun")
    assert wrong.status_code == 409
    assert "batch-scoped" in wrong.json()["detail"]["message"]
    assert "/api/batches/" in wrong.json()["detail"]["hint"]

    assert client.post(f"/api/uploads/{UNKNOWN}/steps/profile/rerun").status_code == 404
    assert client.post(f"/api/batches/{UNKNOWN}/steps/analyze/rerun").status_code == 404
    assert (
        client.post(f"/api/feeds/{FEED}/mapping-versions/9/steps/preview/rerun").status_code == 404
    )


def test_a_step_still_queued_is_not_rerun(client, settings, small_csv_bytes):
    upload_id = _upload(client, "ok.csv", small_csv_bytes)  # profile is queued, not drained
    res = client.post(f"/api/uploads/{upload_id}/steps/profile/rerun")
    assert res.status_code == 409
    assert res.json()["detail"]["status"] == "pending"


def test_an_upload_past_g1_cannot_be_reprofiled(client, settings, small_csv_bytes):
    upload_id, _ = _landed(client, settings, small_csv_bytes)
    res = client.post(f"/api/uploads/{upload_id}/steps/profile/rerun")
    assert res.status_code == 409
    assert res.json()["detail"]["status"] == "landed"
    assert "runs from" in res.json()["detail"]["hint"]


def test_rerun_requires_the_capability(conn, settings):
    steward = authed_client(
        TestClient(create_app(settings)),
        conn,
        settings,
        roles=("data_steward",),
        email="steward@test.cinqflow",
    )
    res = steward.post(f"/api/uploads/{UNKNOWN}/steps/profile/rerun")
    assert res.status_code == 403
    assert res.json()["detail"] == "missing_capability:can_rerun_steps"
