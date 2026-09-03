"""Stage 2 end to end: one G1 click takes an interpreted upload to queryable
Bronze with lineage, and a rejection touches nothing. This is the DoD.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.queue.worker import drain
from cinqflow.workflow.states import RunState, UploadStatus
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_e2e_roster"


@pytest.fixture
def client(conn, settings):
    return TestClient(create_app(settings))


@pytest.fixture(autouse=True)
def drop_feed_table(conn, settings):
    """The bronze schema is shared with the prior build; remove only our table."""
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


def _upload_and_interpret(client, settings, content: bytes, name="roster.csv") -> str:
    response = client.post(
        "/api/uploads",
        files={"file": (name, content, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert response.status_code == 202, response.text
    upload_id = response.json()["upload_id"]
    _drain(settings)
    assert client.get(f"/api/uploads/{upload_id}").json()["upload"]["status"] == (
        UploadStatus.INTERPRETED
    )
    return upload_id


def test_approve_lands_bronze_with_lineage(client, settings, conn, small_csv_bytes):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes)

    approve = client.post(
        f"/api/uploads/{upload_id}/approve",
        json={"approver": "info@cittaai.com", "note": "Grain confirmed with payer."},
    )
    assert approve.status_code == 202, approve.text
    assert approve.json()["status"] == UploadStatus.APPROVED
    assert approve.json()["queued"] == "batch.land_bronze"

    # the request only queued the work
    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.APPROVED
    assert detail["runs"] == []
    assert client.get("/api/queue/depth").json()["batch_land_bronze"] == 1

    # Two jobs: landing, then the Bronze analysis Stage 3 chains onto it.
    assert _drain(settings) == 2

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.LANDED
    assert "/processed/" in detail["upload"]["landing_key"]

    run = detail["runs"][0]
    batch_id = run["batch_id"]
    assert run["state"] == RunState.COMPLETED
    assert run["balanced"] is True
    # Bronze row count equals the profiled row count
    assert run["counts"]["records_out"] == detail["profile"]["facts"]["row_count"]

    approval = detail["approvals"][0]
    assert approval["gate"] == "G1"
    assert approval["decision"] == "approved"
    assert approval["approver"] == "info@cittaai.com"

    # Bronze is queryable, and PHI in the source row is masked on the way out
    rows = client.get(f"/api/batches/{batch_id}/rows").json()
    assert rows["table"] == f"bronze.{FEED}_raw"
    assert rows["total"] == 3
    assert [r["row_number"] for r in rows["rows"]] == [1, 2, 3]
    assert rows["rows"][0]["raw_row"]["member_first_name"] == "•••"
    assert rows["rows"][0]["raw_row"]["product"] == "TANF Adult"
    assert rows["rows"][0]["record_hash"]

    # lineage connects the whole chain
    chain = client.get(f"/api/lineage/{batch_id}").json()
    assert chain["chain"]["upload_id"] == upload_id
    assert chain["chain"]["fingerprint"] == detail["upload"]["fingerprint"]
    assert chain["chain"]["bronze_table"] == f"bronze.{FEED}_raw"
    assert "/processed/" in chain["chain"]["landing_key"]
    assert chain["chain"]["mapping_version"] is None  # Stage 4
    assert chain["chain"]["silver_table"] is None  # Stage 6
    assert [a["gate"] for a in chain["approvals"]] == ["G1"]

    batch = client.get(f"/api/batches/{batch_id}").json()
    assert batch["run"]["batch_id"] == batch_id
    assert batch["upload"]["upload_id"] == upload_id


def test_reject_writes_nothing_to_the_plane(client, settings, conn, small_csv_bytes):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes, "reject_me.csv")

    reject = client.post(
        f"/api/uploads/{upload_id}/reject", json={"note": "Wrong business date."}
    )
    assert reject.status_code == 202
    assert reject.json()["queued"] == "upload.reject"
    _drain(settings)

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.REJECTED
    assert "/rejected/" in detail["upload"]["landing_key"]
    assert detail["runs"] == []
    assert detail["approvals"][0]["decision"] == "rejected"
    assert not PostgresDataPlane(conn).table_exists(bronze_table(FEED))


def test_approving_twice_is_refused(client, settings, small_csv_bytes):
    upload_id = _upload_and_interpret(client, settings, small_csv_bytes)
    assert client.post(f"/api/uploads/{upload_id}/approve", json={}).status_code == 202
    second = client.post(f"/api/uploads/{upload_id}/approve", json={})
    assert second.status_code == 409
    assert "already approved" in second.json()["detail"]["message"]


def test_gate_requires_an_interpreted_upload(client, settings, small_csv_bytes):
    """Approving before the AI has finished is refused, not queued."""
    response = client.post(
        "/api/uploads",
        files={"file": ("early.csv", small_csv_bytes, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    upload_id = response.json()["upload_id"]

    early = client.post(f"/api/uploads/{upload_id}/approve", json={})
    assert early.status_code == 409
    assert early.json()["detail"]["status"] == UploadStatus.RECEIVED


def test_real_roster_lands_all_rows(client, settings, conn, roster_csv_bytes):
    """The 28,333-row de-identified Fidelis roster, upload to Bronze."""
    upload_id = _upload_and_interpret(
        client, settings, roster_csv_bytes, "deidentified_CINQUPSTATE_Member_Roster.csv"
    )
    assert client.post(f"/api/uploads/{upload_id}/approve", json={}).status_code == 202
    _drain(settings)

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.LANDED
    run = detail["runs"][0]
    assert run["counts"] == {
        "records_in": 28333,
        "records_out": 28333,
        "quarantined": 0,
        "attributed_drops": 0,
    }
    assert run["balanced"] is True

    plane = PostgresDataPlane(conn)
    assert plane.count_rows(bronze_table(FEED), run["batch_id"]) == 28333

    # one row still carries all 45 source columns, unmapped
    rows = client.get(f"/api/batches/{run['batch_id']}/rows?limit=1").json()
    assert len(rows["rows"][0]["raw_row"]) == 45
    assert rows["rows"][0]["raw_row"]["member_dob"] == "•••"
    assert rows["rows"][0]["raw_row"]["member_state"] == "NY"


def test_unknown_batch_is_404(client):
    assert client.get("/api/batches/deadbeef1234").status_code == 404
    assert client.get("/api/lineage/deadbeef1234").status_code == 404
