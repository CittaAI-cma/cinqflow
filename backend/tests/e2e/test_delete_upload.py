"""Deleting an upload: frees its fingerprint for re-upload, purges the
workflow-schema rows it owns, and never touches the append-only Bronze it
may have landed - that guard is a stated platform guarantee, not a bug.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.worker import drain
from tests.conftest import authed_client, requires_db

pytestmark = requires_db

FEED = "test_e2e_delete"
ROSTER = (
    b"member_id,member_first_name,member_dob,member_sex,product,harp_eligible\n"
    b"M001,DANIELLE,1997-11-04,F,TANF Adult,Yes\n"
    b"M002,KEVIN,2013-11-04,M,TANF Child,\n"
)


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


def _query(settings, sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as check:
        with check.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _upload(client, settings, feed: str = FEED, content: bytes = ROSTER) -> str:
    response = client.post(
        "/api/uploads",
        files={"file": ("roster.csv", content, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": feed,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["upload_id"]


def test_delete_before_g1_purges_everything_and_the_file(client, settings):
    upload_id = _upload(client, settings)
    _drain(settings)  # profile + interpret; never landed

    deleted = client.delete(f"/api/uploads/{upload_id}")
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted"]["upload"] == 1
    assert body["deleted"]["profile"] == 1
    assert body["deleted"]["interpretation"] == 1
    assert body["preserved_batches"] == []  # never landed - nothing guarded to report
    assert body["file_removed"] is True

    assert client.get(f"/api/uploads/{upload_id}").status_code == 404


def test_delete_after_landing_preserves_bronze_and_reports_it(client, settings):
    upload_id = _upload(client, settings)
    _drain(settings)
    client.post(f"/api/uploads/{upload_id}/approve", json={})
    _drain(settings)  # lands to Bronze, queues bronze.analyze
    _drain(settings)  # bronze.analyze -> proposal

    batch_id = client.get(f"/api/uploads/{upload_id}").json()["runs"][0]["batch_id"]
    table = bronze_table(FEED)
    before = _query(settings, f'SELECT count(*) AS n FROM {table.schema}."{table.name}"')[0]["n"]
    assert before == 2

    deleted = client.delete(f"/api/uploads/{upload_id}")
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted"]["lineage"] == 1
    assert body["deleted"]["run"] == 1
    assert body["deleted"]["bronze_profile"] == 1
    assert body["deleted"]["proposal"] == 1
    assert body["deleted"]["approval"] == 1
    assert body["preserved_batches"] == [
        {"batch_id": batch_id, "bronze_table": table.qualified, "silver_tables": None}
    ]

    # Every workflow reference to the upload is gone...
    assert client.get(f"/api/uploads/{upload_id}").status_code == 404
    assert client.get(f"/api/batches/{batch_id}").status_code == 404

    # ...but the Bronze rows themselves are untouched - append-only means
    # append-only, even from this endpoint.
    after = _query(settings, f'SELECT count(*) AS n FROM {table.schema}."{table.name}"')[0]["n"]
    assert after == before


def test_delete_frees_the_fingerprint_for_re_upload(client, settings):
    upload_id = _upload(client, settings)
    _drain(settings)

    duplicate = client.post(
        "/api/uploads",
        files={"file": ("roster.csv", ROSTER, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert duplicate.status_code == 409  # same bytes, still on record

    client.delete(f"/api/uploads/{upload_id}")

    retried = client.post(
        "/api/uploads",
        files={"file": ("roster.csv", ROSTER, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["upload_id"] != upload_id


def test_delete_purges_mapping_drafts_when_the_feed_has_no_other_upload(client, settings):
    upload_id = _upload(client, settings)
    _drain(settings)
    client.post(f"/api/uploads/{upload_id}/approve", json={})
    _drain(settings)
    _drain(settings)

    batch_id = client.get(f"/api/uploads/{upload_id}").json()["runs"][0]["batch_id"]
    proposal_id = client.get(f"/api/batches/{batch_id}/proposal").json()["proposal_id"]
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1").status_code == 200

    body = client.delete(f"/api/uploads/{upload_id}").json()
    assert body["feed_mapping_purged"] is True
    assert body["deleted"]["mapping_version"] == 1

    assert client.get(f"/api/feeds/{FEED}/mapping-versions").json()["versions"] == []


def test_unknown_upload_is_404(client):
    assert client.delete("/api/uploads/00000000-0000-0000-0000-000000000000").status_code == 404


def test_refuses_to_delete_while_a_batch_is_still_running(client, settings, monkeypatch):
    """A batch mid-flight (`received`/`in_progress`) must not be deleted out
    from under the worker that is about to look its upload_id up again."""
    upload_id = _upload(client, settings)
    _drain(settings)
    client.post(f"/api/uploads/{upload_id}/approve", json={})
    # Deliberately not drained: the `land_bronze` run row exists as `received`
    # the moment it is opened, before any row moves (engine/runner.py) - but
    # reaching that exact instant needs the worker running, not `drain()`
    # (which runs the job to completion in one call). Simulate it directly.
    from cinqflow.db import connect

    with connect(settings) as write_conn:
        with write_conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {settings.workflow_schema}.run
                    (batch_id, upload_id, feed, kind, state)
                    VALUES ('simulated-inflight', %s, %s, 'land_bronze', 'received')""",
                (upload_id, FEED),
            )
        write_conn.commit()

    refused = client.delete(f"/api/uploads/{upload_id}")
    assert refused.status_code == 409, refused.text
    assert "simulated-inflight" in refused.json()["detail"]["batch_ids"]
