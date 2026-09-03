"""Stage 3 end to end: a landed batch becomes a persisted, validated mapping
proposal with no manual step. This is the DoD.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.worker import drain
from cinqflow.workflow.states import UploadStatus
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_e2e_intel"
ROSTER = (
    b"member_id,member_first_name,member_last_name,member_dob,member_sex,product,"
    b"member_city,member_state,harp_eligible,recertification_end_date\n"
    b"M001,DANIELLE,DYER,1997-11-04,F,TANF Adult,BROWNTOWN,NY,Yes,2026-06-30\n"
    b"M002,KEVIN,ALLISON,2013-11-04,M,TANF Child,SPRING VALLEY,NY,,2026-07-31\n"
)


@pytest.fixture
def client(conn, settings):
    return TestClient(create_app(settings))


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


@pytest.fixture
def landed(client, settings) -> tuple[str, str]:
    """Upload → profile → interpret → G1 approve → Bronze → analysis, all queued."""
    response = client.post(
        "/api/uploads",
        files={"file": ("roster.csv", ROSTER, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    upload_id = response.json()["upload_id"]
    _drain(settings)
    assert client.post(f"/api/uploads/{upload_id}/approve", json={}).status_code == 202

    # landing runs, then queues analysis; the second drain performs it
    processed = _drain(settings)
    assert processed == 2, "landing should chain into bronze analysis"

    detail = client.get(f"/api/uploads/{upload_id}").json()
    assert detail["upload"]["status"] == UploadStatus.LANDED
    return upload_id, detail["runs"][0]["batch_id"]


def test_bronze_profile_is_persisted_and_visible(client, landed):
    _upload_id, batch_id = landed
    profile = client.get(f"/api/batches/{batch_id}/bronze-profile").json()

    assert profile["batch_id"] == batch_id
    assert profile["bronze_table"] == f"bronze.{FEED}_raw"
    assert profile["rows_in_batch"] == 2
    assert profile["rows_profiled"] == 2
    assert profile["is_sample"] is False
    # source column order survives via the upload profile
    assert [c["name"] for c in profile["facts"]["columns"]][:3] == [
        "member_id",
        "member_first_name",
        "member_last_name",
    ]
    # PHI example values never leave the API
    phi_columns = [c for c in profile["facts"]["columns"] if c["phi_candidate"]]
    assert phi_columns and all(c["sample_values"] == [] for c in phi_columns)


def test_proposal_is_validated_persisted_and_marked_advisory(client, landed):
    _upload_id, batch_id = landed
    proposal = client.get(f"/api/batches/{batch_id}/proposal").json()

    assert proposal["status"] == "proposed"
    assert proposal["authoritative"] is False  # Stage 4 is where the analyst owns it
    assert proposal["provenance"]["prompt"] == "recommend_mapping@3"
    assert any("canonical/enrollment.yaml" in c for c in proposal["provenance"]["knowledge"])
    assert any("mappings/approved" in c for c in proposal["provenance"]["knowledge"])

    by_source = {f["source"]: f for f in proposal["content"]["fields"]}
    # every Bronze column is accounted for
    assert len(by_source) == 10

    # governed knowledge produced landable targets
    assert by_source["member_id"]["target"] == "members.source_system_id"
    assert by_source["member_dob"]["target"] == "members.date_of_birth"
    assert by_source["member_sex"]["target"] == "members.sex"  # DDL name, not "gender"
    assert by_source["member_city"]["target"] == "members_addresses.city"
    assert by_source["product"]["target"] == "members_enrollment_segments.lob"

    # a date going into a timestamp column carries a named transform
    assert by_source["member_dob"]["transform"]["op"] == "parse_date"

    # columns the canonical model has no home for are admitted, not invented
    assert by_source["harp_eligible"]["target"] is None
    assert by_source["harp_eligible"]["status"] == "unknown"
    assert by_source["recertification_end_date"]["target"] is None

    # every field carries evidence, and the counts add up
    assert all(f["evidence"] for f in by_source.values())
    assert sum(proposal["counts"].values()) == 10


def test_no_proposed_target_is_outside_the_canonical_model(client, landed):
    """The validator's guarantee, checked against the DDL-derived target list."""
    _upload_id, batch_id = landed
    proposal = client.get(f"/api/batches/{batch_id}/proposal").json()

    legal = {
        "members.source_system_id",
        "members.source_system_id_type",
        "members.first_name",
        "members.last_name",
        "members.middle_name",
        "members.suffix",
        "members.date_of_birth",
        "members.sex",
        "members.race",
        "members.ethnicity",
        "members.language",
        "members.location",
        "members.care_management_program",
        "members.last_contact",
        "members.dual_status_code",
        "members.death_date",
        "members.dnc",
    }
    for field in proposal["content"]["fields"]:
        if field["target"] and field["target"].startswith("members."):
            assert field["target"] in legal, f"{field['source']} -> {field['target']}"


def test_batch_detail_carries_profile_and_proposal(client, landed):
    _upload_id, batch_id = landed
    batch = client.get(f"/api/batches/{batch_id}").json()

    assert batch["bronze_profile"]["batch_id"] == batch_id
    assert batch["proposal"]["status"] == "proposed"
    assert batch["lineage"]["bronze_table"] == f"bronze.{FEED}_raw"
    # Stage 4-6 links remain explicitly absent
    assert batch["lineage"]["bronze_table"] is not None
    assert batch["run"]["state"] == "completed"


def test_no_profile_or_proposal_before_analysis(client, settings):
    """A batch that has not been analysed says so, rather than inventing an answer."""
    response = client.post(
        "/api/uploads",
        files={"file": ("later.csv", ROSTER, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-09-01",
        },
    )
    upload_id = response.json()["upload_id"]
    _drain(settings)
    client.post(f"/api/uploads/{upload_id}/approve", json={})

    # run landing only, leaving the analysis job queued
    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as worker_conn:
        from cinqflow.queue.worker import run_once

        run_once(worker_conn, settings)

    batch_id = client.get(f"/api/uploads/{upload_id}").json()["runs"][0]["batch_id"]
    assert client.get(f"/api/batches/{batch_id}/bronze-profile").status_code == 404
    assert client.get(f"/api/batches/{batch_id}/proposal").status_code == 404
    assert client.get("/api/queue/depth").json()["bronze_analyze"] == 1
