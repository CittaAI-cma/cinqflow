"""Stage 1 end to end: the API takes a real roster file, the worker finishes it,
and the API can show the profile and interpretation. This is the Definition of Done.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.queue.worker import drain
from cinqflow.workflow.states import UploadStatus
from tests.conftest import authed_client, requires_db

pytestmark = requires_db


@pytest.fixture
def client(conn, settings):  # conn creates/drops the schemas
    return authed_client(TestClient(create_app(settings)), conn, settings)


def _drain(settings) -> int:
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as worker_conn:
        return drain(worker_conn, settings)


def _upload(client, name: str, content: bytes, mime: str) -> dict:
    return client.post(
        "/api/uploads",
        files={"file": (name, content, mime)},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": "member_roster",
            "domain": "enrollments",
            "business_date": "2026-06-01",
            "uploader": "analyst@cinqcare.com",
        },
    )


def test_csv_upload_reaches_interpreted_with_no_manual_step(client, settings, small_csv_bytes):
    response = _upload(client, "roster.csv", small_csv_bytes, "text/csv")
    assert response.status_code == 202, response.text
    upload_id = response.json()["upload_id"]
    assert response.json()["status"] == UploadStatus.RECEIVED

    # the request did not do the work
    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["profile"] is None
    assert client.get("/api/queue/depth").json()["upload_profile"] == 1

    assert _drain(settings) == 2

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.INTERPRETED
    assert detail["profile"]["facts"]["row_count"] == 3
    assert detail["interpretation"]["provenance"]["prompt"] == "interpret_file@3"
    fields = {c["field"] for c in detail["interpretation"]["content"]["claims"]}
    assert {"likely_domain", "likely_dataset", "likely_grain"} <= fields


def test_real_roster_csv_end_to_end(client, settings, roster_csv_bytes):
    response = _upload(
        client,
        "deidentified_CINQUPSTATE_Member_Roster_03_05_2026_1.csv",
        roster_csv_bytes,
        "text/csv",
    )
    assert response.status_code == 202
    upload_id = response.json()["upload_id"]
    _drain(settings)

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.INTERPRETED
    assert detail["profile"]["facts"]["row_count"] == 28333
    assert len(detail["profile"]["facts"]["columns"]) == 45
    domain = next(
        c for c in detail["interpretation"]["content"]["claims"] if c["field"] == "likely_domain"
    )
    assert domain["value"] == "enrollments"
    assert domain["kind"] == "governed_knowledge"


def test_real_roster_xlsx_end_to_end(client, settings, roster_xlsx_bytes):
    response = _upload(
        client,
        "deidentified__CINQDOWNSTATE_Member_Roster_03_05_2026_1.xlsx",
        roster_xlsx_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert response.status_code == 202
    upload_id = response.json()["upload_id"]
    _drain(settings)

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.INTERPRETED
    assert len(detail["profile"]["facts"]["columns"]) == 45
    assert detail["profile"]["facts"]["sheets"][0]["name"] == "Sheet1"


def test_identical_bytes_are_refused_the_second_time(client, small_csv_bytes):
    first = _upload(client, "roster.csv", small_csv_bytes, "text/csv")
    assert first.status_code == 202
    second = _upload(client, "same-bytes.csv", small_csv_bytes, "text/csv")
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["upload_id"] == first.json()["upload_id"]
    assert "already been uploaded" in detail["message"]


def test_unsupported_file_type_is_refused_before_anything_is_stored(client):
    response = client.post(
        "/api/uploads",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": "member_roster",
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert response.status_code == 415
    assert client.get("/api/uploads").json()["uploads"] == []


def test_empty_file_is_refused(client):
    response = _upload(client, "empty.csv", b"", "text/csv")
    assert response.status_code == 400


def test_phi_candidate_sample_values_are_masked_in_the_api(client, settings, small_csv_bytes):
    upload_id = _upload(client, "roster.csv", small_csv_bytes, "text/csv").json()["upload_id"]
    _drain(settings)
    facts = client.get(f"/api/uploads/{upload_id}").json()["profile"]["facts"]

    assert "member_first_name" in facts["phi_candidates"]
    for row in facts["sample_rows"]:
        assert row["member_first_name"] == "•••"
        assert row["product"] in {"TANF Adult", "TANF Child"}  # non-PHI passes through


def test_malformed_file_is_visible_as_failed(client, settings):
    upload_id = _upload(
        client, "broken.xlsx", b"\x00not-a-workbook", "application/vnd.ms-excel"
    ).json()["upload_id"]
    _drain(settings)

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.PROFILE_FAILED
    assert detail["upload"]["error"]
    assert detail["profile"] is None


def test_upload_list_shows_status(client, settings, small_csv_bytes):
    _upload(client, "roster.csv", small_csv_bytes, "text/csv")
    _drain(settings)
    uploads = client.get("/api/uploads").json()["uploads"]
    assert len(uploads) == 1
    assert uploads[0]["status"] == UploadStatus.INTERPRETED
    assert uploads[0]["filename"] == "roster.csv"


def test_unknown_upload_is_404(client):
    assert client.get("/api/uploads/6f0c0000-0000-0000-0000-000000000000").status_code == 404
