"""Stage 5 end to end: the analyst sees exactly what vN does before any Silver
write, through the API the studio calls. This is the DoD.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.worker import drain
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_e2e_preview"
ROSTER = (
    b"member_id,member_first_name,member_dob,member_sex,harp_eligible\n"
    b"M001,DANIELLE,1997-11-04,F,Yes\n"
    b"M002,KEVIN,13/45/1990,M,\n"
    b",ALEX,2000-01-01,M,Yes\n"
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
def draft(client, settings) -> str:
    """Stages 1-4: upload, G1, Bronze, proposal, draft v1, then analyst edits."""
    upload_id = client.post(
        "/api/uploads",
        files={"file": ("roster.csv", ROSTER, "text/csv")},
        data={
            "source_system": "fidelis_ny_upstate",
            "feed": FEED,
            "domain": "enrollments",
            "business_date": "2026-06-01",
        },
    ).json()["upload_id"]
    _drain(settings)
    client.post(f"/api/uploads/{upload_id}/approve", json={})
    _drain(settings)

    batch_id = client.get(f"/api/uploads/{upload_id}").json()["runs"][0]["batch_id"]
    proposal_id = client.get(f"/api/batches/{batch_id}/proposal").json()["proposal_id"]
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})

    # The analyst tightens the spec the AI seeded: the identifier must not be
    # empty, and sex is constrained to a value map. (member_id is already mapped
    # here - this roster has no competing identifier column, so it was not
    # contested and the proposal carried it.)
    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]
    for field in spec["fields"]:
        if field["source"] == "member_id":
            field["on_null"] = "reject"
            field["edited"] = True
        if field["source"] == "member_sex":
            field["value_map"] = {"M": "male", "F": "female"}
            field["on_unmapped_value"] = "quarantine"
            field["edited"] = True
    assert any(f["source"] == "member_id" for f in spec["fields"])
    assert client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec).status_code == 200
    return batch_id


def test_preview_is_queued_not_executed_inline(client, settings, draft):
    response = client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] == "mapping.preview"
    assert body["batch_id"] == draft
    assert body["spec_fingerprint"]

    # nothing computed yet
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").status_code == 404
    assert client.get("/api/queue/depth").json()["mapping_preview"] == 1

    assert _drain(settings) == 1
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").status_code == 200


def test_the_analyst_sees_source_values_mapped_values_and_failures(client, settings, draft):
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    preview = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()

    assert preview["is_current"] is True
    assert preview["approvable"] is True
    assert preview["sample"]["bronze_table"] == f"bronze.{FEED}_raw"
    assert preview["sample"]["rows"] == 3
    assert preview["sample_is_partial"] is False

    aggregates = preview["aggregates"]
    assert aggregates["rows_previewed"] == 3
    assert aggregates["rows_ok"] == 1
    assert aggregates["rows_with_failures"] == 1   # the unparseable date
    assert aggregates["rows_rejected"] == 1        # the missing identifier
    assert "member_dob:parse_date" in aggregates["failures_by_rule"]
    assert "member_id:on_null" in aggregates["failures_by_rule"]
    assert aggregates["null_or_invalid"]["members.date_of_birth"] == 1

    # PHI columns are masked in every row, in every one of source value /
    # mapped value / failure reason - the one place in the API that used to
    # show real, per-record PHI unmasked. `member_sex` is masked too: the
    # canonical model declares `members.sex` PHI (enrollment.yaml), same as
    # name and DOB - this asserts the platform's own governed classification,
    # not a guess this test is making.
    assert set(preview["phi_masked"]) >= {"member_first_name", "member_dob", "member_sex"}

    rows = {r["row_number"]: r for r in preview["row_results"]}
    assert rows[1]["outcome"] == "ok"
    clean = {f["source"]: f for f in rows[1]["fields"]}
    assert clean["member_sex"]["source_value"] == "•••"
    assert clean["member_sex"]["mapped_value"] == "•••"
    assert clean["member_first_name"]["source_value"] == "•••"
    assert clean["member_first_name"]["mapped_value"] == "•••"

    broken = {f["source"]: f for f in rows[2]["fields"]}
    assert broken["member_dob"]["outcome"] == "failure"
    assert broken["member_dob"]["source_value"] == "•••"  # was "13/45/1990"
    assert broken["member_dob"]["mapped_value"] is None
    assert "13/45/1990" not in broken["member_dob"]["reason"]
    assert "PHI" in broken["member_dob"]["reason"]
    # The rule that fired is still knowable - just not from a leaked value.
    assert "member_dob:parse_date" in aggregates["failures_by_rule"]

    rejected = {f["source"]: f for f in rows[3]["fields"]}
    assert rejected["member_id"]["outcome"] == "rejected"
    # `members.source_system_id` is PHI too (enrollment.yaml) - its reason is
    # redacted like the others, even though this particular rule's message
    # ("on_null: reject" firing) never interpolated a value. Blanket redaction
    # per PHI field is the simple, robust rule: it does not depend on knowing
    # which reason templates are safe today and staying right about that
    # forever as `mapping_exec` changes.
    assert rejected["member_id"]["reason"] == "rejected (reason withheld — source is PHI)"


def test_previewing_marks_the_version_and_editing_makes_it_stale(client, settings, draft):
    """Acceptance 3's precondition: a stale preview cannot authorise anything."""
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["status"] == "previewed"

    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]
    for field in spec["fields"]:
        if field["source"] == "member_sex":
            field["value_map"] = {"M": "male", "F": "female", "X": "unknown"}
    client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec)

    version = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()
    assert version["status"] == "draft"  # editing returns it to draft

    stale = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()
    assert stale["is_current"] is False
    assert stale["approvable"] is False
    assert "run it again" in stale["stale_reason"]

    # re-previewing the corrected spec makes it current again
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    fresh = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()
    assert fresh["is_current"] is True
    assert fresh["approvable"] is True


def test_identical_requests_produce_one_identical_preview(client, settings, draft):
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    first = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()

    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    second = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()

    assert first["preview_id"] == second["preview_id"]
    assert first["aggregates"] == second["aggregates"]
    assert first["row_results"] == second["row_results"]


def test_a_bounded_sample_is_labelled_as_partial(client, settings, draft):
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={"rows": 2})
    _drain(settings)
    preview = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()

    assert preview["sample"]["rows"] == 2
    assert preview["sample"]["rows_in_batch"] == 3
    assert preview["sample_is_partial"] is True
    assert preview["aggregates"]["rows_previewed"] == 2


def test_preview_row_results_are_paged(client, settings, draft):
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    preview = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview?limit=1").json()

    assert len(preview["row_results"]) == 1
    assert preview["row_results_total"] == 3


def test_preview_is_refused_without_a_batch_or_version(client, settings):
    assert client.post(f"/api/feeds/{FEED}/mapping-versions/9/preview", json={}).status_code == 404
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/9/preview").status_code == 404

    unknown = client.post(
        "/api/feeds/feed_with_no_batch/mapping-versions",
        json={"domain": "enrollment"},
    )
    assert unknown.status_code == 201
    refused = client.post(
        "/api/feeds/feed_with_no_batch/mapping-versions/1/preview", json={}
    )
    # an empty draft has nothing to preview
    assert refused.status_code == 409
    assert "no fields" in refused.json()["detail"]["message"]


def test_unknown_batch_is_refused(client, settings, draft):
    response = client.post(
        f"/api/feeds/{FEED}/mapping-versions/1/preview", json={"batch_id": "deadbeef1234"}
    )
    assert response.status_code == 404


def test_preview_alone_writes_no_silver(client, settings, draft):
    """A preview reads Bronze and writes an artifact. It does not promote, and it
    does not queue a promotion: only G2 does that."""
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)

    depth = client.get("/api/queue/depth").json()
    assert depth["mapping_promote"] == 0

    with psycopg.connect(settings.database_url, row_factory=dict_row) as check:
        with check.cursor() as cur:
            # A silver_raw.members table exists in this database, but it belongs to
            # the PREVIOUS implementation (its columns are that build's schema).
            # What matters is that Stage 5 wrote nothing anywhere in the plane.
            cur.execute("SELECT to_regclass('silver_raw.members') AS present")
            if cur.fetchone()["present"] is not None:
                cur.execute("SELECT count(*) AS n FROM silver_raw.members")
                assert cur.fetchone()["n"] == 0
            cur.execute(
                f"SELECT count(*) AS n FROM {settings.workflow_schema}.run WHERE kind = %s",
                ("promote_silver",),
            )
            assert cur.fetchone()["n"] == 0
            # The version is `previewed`, not `approved`: a preview is not consent.
            cur.execute(
                f"""SELECT status FROM {settings.workflow_schema}.mapping_version
                    WHERE feed = %s AND version = 1""",
                (FEED,),
            )
            assert cur.fetchone()["status"] == "previewed"
