"""Stage 4 end to end: an analyst turns an AI proposal into a valid, versioned
mapping they own. This is the DoD, exercised through the API the UI calls.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table
from cinqflow.queue.worker import drain
from cinqflow.workflow.store import WorkflowStore
from tests.conftest import authed_client, requires_db

pytestmark = requires_db

FEED = "test_e2e_mapping"
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


@pytest.fixture
def proposal_id(client, settings) -> str:
    """Run Stages 1-3 so a real proposal exists to seed the draft from."""
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
    return client.get(f"/api/batches/{batch_id}/proposal").json()["proposal_id"]


def test_draft_v1_is_seeded_from_the_proposal(client, proposal_id):
    created = client.post(
        f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id}
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["origin"] == "proposal"
    assert body["origin_proposal_id"] == proposal_id
    assert body["derived_from"] is None
    assert body["editable"] is True

    # only defensible candidates were carried over; unknowns were not invented
    sources = [f["source"] for f in body["spec"]["fields"]]
    assert "member_dob" in sources
    assert "harp_eligible" not in sources
    assert all(f["edited"] is False for f in body["spec"]["fields"])


def test_proposal_is_fetchable_by_its_own_id_before_any_draft_exists(client, proposal_id):
    """The studio's empty state only ever has `?proposal=<id>`, never a batch id -
    this is what lets it show what "Start draft" is about to seed from."""
    response = client.get(f"/api/mapping-proposals/{proposal_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposal_id"] == proposal_id
    assert body["feed"] == FEED
    assert body["authoritative"] is False
    assert any(f["source"] == "member_dob" for f in body["content"]["fields"])


def test_unknown_proposal_id_is_404(client):
    assert (
        client.get("/api/mapping-proposals/00000000-0000-0000-0000-000000000000").status_code == 404
    )


def test_the_studio_receives_the_legal_vocabulary(client, proposal_id):
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    detail = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()

    vocabulary = detail["vocabulary"]
    assert "members.date_of_birth" in vocabulary["targets"]
    assert "members.member_uuid" not in vocabulary["targets"]  # not in the DDL
    assert "members.record_hash" not in vocabulary["targets"]  # platform-populated
    assert vocabulary["target_types"]["members.date_of_birth"] == "timestamp"
    assert "parse_date" in vocabulary["ops"]
    assert "exec_python" not in vocabulary["ops"]
    assert set(vocabulary["on_null"]) == {"reject", "default", "pass"}

    # `members`' identity is a single mappable column, so it is surfaced as
    # required; a composite-key entity (e.g. `members_enrollment_segments`) is
    # not asked to satisfy every key column - that is feed-dependent judgment,
    # not a blanket rule.
    assert vocabulary["primary_keys"]["members"] == ["members.source_system_id"]
    assert "members_enrollment_segments" not in vocabulary["primary_keys"]


def test_the_studio_receives_the_dependencies_between_those_choices(client, proposal_id):
    """The vocabulary carries the *rules*, not only the option lists.

    Four edits are reachable with nothing but a dropdown and each makes the spec
    invalid on its own: `on_null` -> default with no default, `on_unmapped_value`
    -> quarantine/null with no value_map, any transform that takes an argument
    with no argument, and a cast the target's declared type cannot accept. A
    save is all-or-nothing over one artifact, so any one of them discarded every
    unrelated edit in the table - and the editor could only learn the rule by
    being refused. These four tables let it require the box the rule needs at
    the moment the dropdown selects it. They are the validator's own constants,
    so the editor cannot drift from what will judge it.
    """
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    vocabulary = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["vocabulary"]

    assert vocabulary["op_args"]["parse_date"] == ["format"]
    assert vocabulary["op_args"]["substring"] == ["start"]
    # An op that takes no arguments is absent, not present-and-empty.
    assert "trim" not in vocabulary["op_args"]

    # `members.date_of_birth` is declared timestamp, which `date` also satisfies.
    assert vocabulary["casts_for_type"]["timestamp"] == ["date", "timestamp"]
    assert vocabulary["casts_for_type"]["string"] == ["string"]

    assert vocabulary["on_null_needs_default"] == ["default"]
    assert vocabulary["on_unmapped_needs_value_map"] == ["null", "quarantine"]
    # `pass` is the one rule that means something without a value_map.
    assert "pass" not in vocabulary["on_unmapped_needs_value_map"]


def test_studio_carries_forward_the_proposal_s_rationale(client, proposal_id):
    """`ai_context` lets the studio show confidence/evidence/concept next to a
    field the analyst is editing, not only at the moment the draft was seeded."""
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    detail = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()

    ai_context = detail["ai_context"]
    assert "member_id" in ai_context
    rationale = ai_context["member_id"]
    assert {"confidence", "evidence", "concept", "status"} <= set(rationale)
    assert rationale["evidence"]
    assert rationale["status"] == "candidate"


def test_analyst_edits_are_saved_and_marked(client, proposal_id):
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]

    # the analyst adds a mapping the AI left unknown, and edits one it proposed
    spec["fields"].append(
        {
            "source": "harp_eligible",
            "target": "members.dual_status_code",
            "cast": "string",
            "edited": True,
            "note": "HARP eligibility recorded as dual status pending payer confirmation",
        }
    )
    for field in spec["fields"]:
        if field["source"] == "member_sex":
            field["value_map"] = {"M": "male", "F": "female", "U": "unknown"}
            field["on_unmapped_value"] = "quarantine"
            field["edited"] = True

    saved = client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec)
    assert saved.status_code == 200, saved.text
    body = saved.json()

    by_source = {f["source"]: f for f in body["spec"]["fields"]}
    assert by_source["harp_eligible"]["target"] == "members.dual_status_code"
    assert by_source["member_sex"]["value_map"]["M"] == "male"
    assert by_source["member_sex"]["on_unmapped_value"] == "quarantine"
    assert body["updated_ts"] is not None
    assert body["status"] == "draft"

    diff = client.get(f"/api/feeds/{FEED}/mapping-versions/1/diff").json()
    assert "harp_eligible" in diff["diff"]["analyst_edited"]
    assert "member_id" in diff["diff"]["from_proposal"]


def test_an_invalid_spec_is_refused_with_field_level_errors(client, proposal_id):
    """Acceptance 3: non-canonical target and unsupported transform both rejected."""
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})

    response = client.put(
        f"/api/feeds/{FEED}/mapping-versions/1",
        json={
            "target_table": "silver_raw.members",
            "fields": [
                {"source": "member_id", "target": "members.member_uuid"},
                {
                    "source": "member_first_name",
                    "target": "members.first_name",
                    "transform": {"op": "exec_python", "args": {"code": "os.system('x')"}},
                },
                {"source": "member_dob", "target": "members.date_of_birth", "cast": "string"},
            ],
        },
    )

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert {e["attribute"] for e in errors} == {"target", "transform", "cast"}
    by_attribute = {e["attribute"]: e for e in errors}
    assert by_attribute["target"]["source"] == "member_id"
    assert by_attribute["target"]["field_index"] == 0
    assert "not a field in the canonical model" in by_attribute["target"]["message"]
    assert "not a supported transform" in by_attribute["transform"]["message"]

    # nothing was saved
    unchanged = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()
    assert "members.member_uuid" not in str(unchanged["spec"])


def test_editing_an_approved_version_is_refused_and_creates_nothing(
    client, conn, settings, proposal_id
):
    """Acceptance 1: PUT against an approved version returns 409."""
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]

    # Stage 6 owns the G2 endpoint; the state is set directly here so the rule is proven now.
    WorkflowStore(conn, settings).set_mapping_status(feed=FEED, version=1, status="approved")
    conn.commit()

    refused = client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec)
    assert refused.status_code == 409
    assert refused.json()["detail"]["status"] == "approved"
    assert "derive_from_version" in refused.json()["detail"]["hint"]

    # no implicit version was created
    assert [
        v["version"] for v in client.get(f"/api/feeds/{FEED}/mapping-versions").json()["versions"]
    ] == [1]


def test_editing_after_approval_derives_the_next_version(client, conn, settings, proposal_id):
    """Acceptance 2: v(N+1) copies the spec and records derived_from."""
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    WorkflowStore(conn, settings).set_mapping_status(feed=FEED, version=1, status="approved")
    conn.commit()

    created = client.post(f"/api/feeds/{FEED}/mapping-versions", json={"derive_from_version": 1})
    assert created.status_code == 201
    v2 = created.json()
    assert v2["version"] == 2
    assert v2["status"] == "draft"
    assert v2["derived_from"] == 1
    assert v2["editable"] is True

    v1 = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()
    assert v1["status"] == "approved"  # untouched until a later G2 supersedes it
    assert v2["spec"]["fields"] == v1["spec"]["fields"]

    # v2 is editable, and diffs against the approved v1
    spec = v2["spec"]
    spec["fields"][0]["note"] = "revisited after payer call"
    spec["fields"][0]["edited"] = True
    assert client.put(f"/api/feeds/{FEED}/mapping-versions/2", json=spec).status_code == 200

    diff = client.get(f"/api/feeds/{FEED}/mapping-versions/2/diff").json()
    assert diff["against"] == 1
    assert diff["against_status"] == "approved"


def test_a_second_open_draft_is_refused(client, proposal_id):
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    again = client.post(
        f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id}
    )
    assert again.status_code == 409
    assert again.json()["detail"]["version"] == 1


def test_unknown_proposal_and_version_are_404(client):
    missing = client.post(
        f"/api/feeds/{FEED}/mapping-versions",
        json={"from_proposal_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/7").status_code == 404
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/7/diff").status_code == 404


def test_g2_cannot_approve_a_version_nobody_has_previewed(client, proposal_id):
    """Stage 4's guarantee, now that the G2 route exists: authority still comes
    from a preview of this exact spec, never from the draft alone."""
    client.post(f"/api/feeds/{FEED}/mapping-versions", json={"from_proposal_id": proposal_id})
    refused = client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    assert refused.status_code == 409
    assert "no preview" in refused.json()["detail"]["message"]
