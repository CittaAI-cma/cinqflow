"""Stage 6 end to end, and with it the whole target flow:

    Analyst -> upload -> AI understanding -> G1 -> Bronze -> AI bronze understanding
    -> mapping recommendation -> editable mapping -> preview -> G2 -> Silver Raw

One roster file, driven only through the API the UI calls, asserting every
artifact, both gates, the balance equation and the lineage that proves it.
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

FEED = "test_e2e_silver"
ROSTER = (
    b"member_id,member_first_name,member_last_name,member_dob,member_sex,"
    b"member_email,member_city,member_phone\n"
    b"M001,DANIELLE,DYER,1997-11-04,F,d@example.org,ALBANY,5550100\n"
    b"M002,KEVIN,KANE,2013-11-04,M,,TROY,5550101\n"
    b",ALEX,ADAMS,2000-01-01,M,a@example.org,UTICA,5550102\n"
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


@pytest.fixture
def previewed(client, settings) -> tuple[str, str]:
    """Everything up to G2: a previewed mapping version over a landed batch."""
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

    # The analyst owns the spec: the identifier must not be empty, and sex is
    # constrained to the values the canonical model expects.
    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]
    for mapping in spec["fields"]:
        if mapping["source"] == "member_id":
            mapping["on_null"] = "reject"
            mapping["edited"] = True
        if mapping["source"] == "member_sex":
            mapping["value_map"] = {"M": "male", "F": "female"}
            mapping["edited"] = True
    assert client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec).status_code == 200

    client.post(f"/api/feeds/{FEED}/mapping-versions/1/preview", json={})
    _drain(settings)
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()["is_current"]
    return upload_id, batch_id


def test_g2_refuses_a_touched_entity_missing_its_identifier(client, settings, previewed):
    """`members` is touched (first_name, dob, sex, ...) but its own identity -
    `source_system_id` - is what makes a Silver row locatable at all. Removing
    it must close G2 even though a current preview exists, because the preview
    itself would have run with rows now missing an identifier."""
    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]
    spec["fields"] = [f for f in spec["fields"] if f["target"] != "members.source_system_id"]
    assert client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec).status_code == 200

    refused = client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "members.source_system_id" in detail["missing_required"]
    assert "required" in detail["message"]

    # Nothing was queued or frozen by the refused attempt.
    assert client.get("/api/queue/depth").json().get("mapping_promote", 0) == 0
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["status"] == "draft"


def test_g2_is_queued_not_executed_inline(client, settings, previewed):
    _, batch_id = previewed
    response = client.post(
        f"/api/feeds/{FEED}/mapping-versions/1/approve",
        json={"note": "matches the preview"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["queued"] == "mapping.promote"
    assert body["batch_id"] == batch_id
    assert body["status"] == "approved"
    assert body["approval"]["gate"] == "G2"
    assert body["approval"]["artifact_type"] == "mapping_version"

    # The decision is recorded and the version frozen before anything is written.
    assert client.get("/api/queue/depth").json()["mapping_promote"] == 1
    version = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()
    assert version["status"] == "approved" and version["editable"] is False

    assert _drain(settings) == 1


def test_the_whole_flow_reaches_silver_raw(client, settings, previewed):
    """The DoD: one real file, both gates, and rows in Silver Raw to show for it."""
    upload_id, batch_id = previewed
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    _drain(settings)

    members = _query(
        settings,
        f'SELECT * FROM {settings.silver_schema}."members" WHERE batch_id = %s '
        "ORDER BY source_system_id",
        (batch_id,),
    )
    assert [m["source_system_id"] for m in members] == ["M001", "M002"]
    assert members[0]["first_name"] == "DANIELLE"
    assert members[0]["last_name"] == "DYER"
    assert members[0]["sex"] == "female"
    assert members[0]["source_system"] == "fidelis_ny_upstate"
    assert members[0]["batch_id"] == batch_id

    # The child entities the same rows populate, keyed back to their member.
    emails = _query(
        settings,
        f'SELECT * FROM {settings.silver_schema}."members_emails" WHERE batch_id = %s',
        (batch_id,),
    )
    assert [(e["source_system_id"], e["email_address"]) for e in emails] == [
        ("M001", "d@example.org")
    ]
    cities = _query(
        settings,
        f'SELECT source_system_id, city FROM {settings.silver_schema}."members_addresses" '
        "WHERE batch_id = %s ORDER BY source_system_id",
        (batch_id,),
    )
    assert [c["city"] for c in cities] == ["ALBANY", "TROY"]

    # The row with no identifier is quarantined, with the rule that refused it.
    refused = _query(
        settings,
        f'SELECT * FROM {settings.silver_schema}."{FEED}_quarantine" WHERE batch_id = %s',
        (batch_id,),
    )
    assert len(refused) == 1
    assert refused[0]["row_number"] == 3
    assert refused[0]["outcome"] == "rejected"
    assert refused[0]["reasons"][0]["source"] == "member_id"
    assert refused[0]["reasons"][0]["rule"] == "on_null"

    # The ledger has the whole journey, both gates included (PR-2).
    steps = {s["key"]: s for s in client.get(f"/api/uploads/{upload_id}/progress").json()["steps"]}
    assert [s["state"] for s in steps.values()] == ["done"] * 8
    assert steps["promote"]["run"]["scope_id"] == batch_id
    assert steps["gate_g2"]["run"]["artifact_type"] == "approval"
    batch_steps = client.get(f"/api/batches/{batch_id}/progress").json()["steps"]
    assert {s["key"]: s["state"] for s in batch_steps}["promote"] == "done"


def test_lineage_proves_the_chain_from_either_end(client, settings, previewed):
    upload_id, batch_id = previewed
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    _drain(settings)

    chain = client.get(f"/api/lineage/{batch_id}").json()
    link = chain["chain"]

    assert link["upload_id"] == upload_id
    assert link["fingerprint"].startswith("sha256-")
    assert "/processed/" in link["landing_key"]
    assert link["batch_id"] == batch_id
    assert link["bronze_table"] == f"bronze.{FEED}_raw"
    assert link["mapping"] == {"feed": FEED, "version": 1}
    assert link["silver_table"] == f"{settings.silver_schema}.members"
    assert link["silver_tables"][f"{settings.silver_schema}.members"] == 2

    # Both gates, in the order they were passed.
    assert chain["gates"]["G1"]["artifact_type"] == "interpretation"
    assert chain["gates"]["G2"]["artifact_type"] == "mapping_version"
    assert chain["gates"]["G2"]["artifact_version"] == 1
    assert [a["gate"] for a in chain["approvals"]] == ["G1", "G2"]

    # Both executions of this batch, each with its own balanced counts.
    assert {r["kind"] for r in chain["runs"]} == {"land_bronze", "promote_silver"}
    promotion = chain["promotion"]
    assert promotion["state"] == "completed"
    assert promotion["balanced"] is True
    assert promotion["mapping_version"] == 1
    assert promotion["counts"] == {
        "records_in": 3,
        "records_out": 2,
        "quarantined": 1,
        "attributed_drops": 0,
    }
    # And from the upload's end, the same batch.
    assert client.get(f"/api/uploads/{upload_id}").json()["runs"][0]["batch_id"] == batch_id


def test_g2_is_refused_when_the_preview_is_no_longer_current(client, settings, previewed):
    """The gate closes the moment the spec changes: what was previewed is what may
    be approved, and nothing else."""
    _, batch_id = previewed
    spec = client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["spec"]

    # Provenance alone does not close the gate: `edited` and `note` are not read
    # by the executor, so the previewed rows are still exactly what this spec
    # produces. Claiming a field and writing down why must not cost a re-preview.
    spec["fields"][0]["note"] = "second thoughts"
    spec["fields"][0]["edited"] = True
    client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec)
    still = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()
    assert still["is_current"] is True and still["approvable"] is True

    # A change the executor *does* read closes it immediately.
    spec["fields"][0]["on_null"] = "reject"
    client.put(f"/api/feeds/{FEED}/mapping-versions/1", json=spec)

    stale = client.get(f"/api/feeds/{FEED}/mapping-versions/1/preview").json()
    assert stale["is_current"] is False and stale["approvable"] is False

    refused = client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    assert refused.status_code == 409
    assert "no preview of its current spec" in refused.json()["detail"]["message"]
    assert client.get("/api/queue/depth").json()["mapping_promote"] == 0
    assert client.get(f"/api/feeds/{FEED}/mapping-versions/1").json()["status"] == "draft"


def test_approving_twice_is_refused_and_promotes_once(client, settings, previewed):
    _, batch_id = previewed
    first = client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    again = client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})

    assert again.status_code == 409
    assert again.json()["detail"]["approval_id"] == first.json()["approval"]["approval_id"]
    assert client.get("/api/queue/depth").json()["mapping_promote"] == 1
    _drain(settings)

    counts = _query(
        settings,
        f'SELECT count(*) AS n FROM {settings.silver_schema}."members" WHERE batch_id = %s',
        (batch_id,),
    )
    assert counts[0]["n"] == 2


def test_replaying_the_promotion_leaves_the_same_silver(client, settings, previewed):
    """Re-promoting rebuilds this batch and only this batch, and the rows come out
    identical because every hash is derived from content."""
    _, batch_id = previewed
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    _drain(settings)

    def snapshot() -> list[tuple]:
        return [
            (m["source_system_id"], m["record_hash"])
            for m in _query(
                settings,
                f'SELECT source_system_id, record_hash FROM {settings.silver_schema}."members" '
                "WHERE batch_id = %s ORDER BY source_system_id",
                (batch_id,),
            )
        ]

    before = snapshot()
    from cinqflow.workers import promote_silver

    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as worker_conn:
        replay = promote_silver.handle(
            worker_conn, {"feed": FEED, "version": 1, "batch_id": batch_id}, settings
        )
        worker_conn.commit()

    assert replay["rebuilt"] is True
    assert snapshot() == before

    # Bronze is untouched by either promotion.
    rows = _query(
        settings,
        f'SELECT count(*) AS n FROM bronze."{FEED}_raw" WHERE batch_id = %s',
        (batch_id,),
    )
    assert rows[0]["n"] == 3


def test_the_analysts_decisions_became_knowledge(client, settings, previewed):
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    _drain(settings)

    import yaml

    exported = settings.knowledge_root / "mappings" / "approved" / f"{FEED}.yaml"
    document = yaml.safe_load(exported.read_text())
    decisions = document["decision_sets"][0]["decisions"]
    by_source = {d["source_field"]: d for d in decisions}

    assert by_source["member_id"]["target"] == "members.source_system_id"
    assert by_source["member_id"]["on_null"] == "reject"
    assert by_source["member_id"]["decided_by"] == "analyst"
    assert by_source["member_sex"]["value_map"] == {"M": "male", "F": "female"}


def test_nothing_reaches_silver_without_g2(client, settings, previewed):
    """The gate is the only door: a previewed, unapproved version writes nothing."""
    _, batch_id = previewed
    assert _drain(settings) == 0

    with psycopg.connect(settings.database_url) as check, check.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS present", (f'{settings.silver_schema}."members"',))
        assert cur.fetchone()[0] is None

    from cinqflow.workers import promote_silver

    with psycopg.connect(
        settings.database_url, row_factory=dict_row, options="-c TimeZone=UTC"
    ) as worker_conn:
        refused = promote_silver.handle(
            worker_conn, {"feed": FEED, "version": 1, "batch_id": batch_id}, settings
        )
    assert refused["promoted"] is False
    assert refused["reason"] == "previewed"


def test_the_promotion_can_be_rerun_from_the_api_and_leaves_the_same_silver(
    client, settings, previewed
):
    """PR-3 closes `forward-flow-adoption.md §6.5`: promotion re-queued on demand,
    identical rows out (features.md Stage 6 acceptance 3), generation 2 on record."""
    _, batch_id = previewed
    client.post(f"/api/feeds/{FEED}/mapping-versions/1/approve", json={})
    _drain(settings)

    def snapshot() -> list[tuple]:
        return [
            (m["source_system_id"], m["record_hash"])
            for m in _query(
                settings,
                f'SELECT source_system_id, record_hash FROM {settings.silver_schema}."members" '
                "WHERE batch_id = %s ORDER BY source_system_id",
                (batch_id,),
            )
        ]

    before = snapshot()
    res = client.post(f"/api/batches/{batch_id}/steps/promote/rerun")
    assert res.status_code == 202, res.text
    assert res.json()["generation"] == 2
    assert _drain(settings) == 1
    assert snapshot() == before

    steps = {s["key"]: s for s in client.get(f"/api/batches/{batch_id}/progress").json()["steps"]}
    assert steps["promote"]["state"] == "done"
    assert steps["promote"]["run"]["generation"] == 2
