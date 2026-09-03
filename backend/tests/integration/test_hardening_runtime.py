"""The hardening changes that need a real batch: spread sampling, the per-entity
reconciliation, and quarantine visibility."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from cinqflow.api.app import create_app
from cinqflow.dataplane.contract import bronze_table, quarantine_table, silver_table
from cinqflow.dataplane.filestore import FileStore, Folder, fingerprint_bytes, landing_key
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine.runner import PipelineRunner, PromotionFailure
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.workers import (
    interpret_upload,
    land_bronze,
    profile_upload,
    promote_silver,
    run_preview,
)
from cinqflow.workflow.models import MappingField, MappingSpec
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_hardening"

#: 20 rows. Only the last five carry a city, so a first-N sample of five would
#: report "no addresses" and a spread sample would not.
def _roster() -> bytes:
    header = b"member_id,member_first_name,member_city\n"
    early = b"".join(
        f"M{i:03d},NAME{i},\n".encode() for i in range(1, 16)
    )
    late = b"".join(
        f"M{i:03d},NAME{i},ALBANY\n".encode() for i in range(16, 21)
    )
    return header + early + late


ROSTER = _roster()


def _spec() -> MappingSpec:
    return MappingSpec(
        target_table="silver_raw.members",
        fields=[
            MappingField(source="member_id", target="members.source_system_id", on_null="reject"),
            MappingField(source="member_first_name", target="members.first_name"),
            MappingField(source="member_city", target="members_addresses.city"),
        ],
    )


@pytest.fixture(autouse=True)
def drop_feed_table(conn, settings):
    yield
    conn.rollback()
    table = bronze_table(FEED)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table.schema}."{table.name}" CASCADE')
    conn.commit()


@pytest.fixture
def landed(conn, settings):
    key = landing_key(
        domain="enrollments",
        source_system="fidelis_ny_upstate",
        feed=FEED,
        folder=Folder.INCOMING,
        business_date="2026-06-01",
        filename="roster.csv",
    )
    FileStore(settings).place(key, ROSTER)
    store = WorkflowStore(conn, settings)
    upload = store.create_upload(
        fingerprint=fingerprint_bytes(ROSTER),
        filename="roster.csv",
        file_type="csv",
        size_bytes=len(ROSTER),
        uploader="analyst@cinqcare.com",
        source_system="fidelis_ny_upstate",
        feed=FEED,
        domain="enrollments",
        business_date=date(2026, 6, 1),
        landing_key=key,
    )
    profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    interpret_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    interpretation = store.get_interpretation(upload.upload_id)
    store.put_approval(
        gate="G1",
        artifact_type="interpretation",
        artifact_id=interpretation.interpretation_id,
        artifact_version=interpretation.version,
        upload_id=upload.upload_id,
        decision="approved",
        approver="analyst@cinqcare.com",
    )
    store.set_status(upload.upload_id, UploadStatus.APPROVED)
    conn.commit()
    landing = land_bronze.handle(conn, {"upload_id": upload.upload_id}, settings)
    mapping = store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=_spec(), created_by="analyst@cinqcare.com"
    )
    conn.commit()
    return landing["batch_id"], upload, mapping


# ------------------------------------------------------------- spread sampling


def test_a_spread_sample_sees_the_whole_batch_where_first_n_would_not(conn, settings, landed):
    """The point of the change: only the last five rows have a city, so a first-5
    window reports no addresses at all."""
    batch_id, _, _ = landed

    run_preview.handle(
        conn, {"feed": FEED, "version": 1, "rows": 5, "strategy": "first"}, settings
    )
    conn.commit()
    first = WorkflowStore(conn, settings).get_preview(FEED, 1)
    assert first.sample.selector == "first_5"
    assert [r.row_number for r in first.row_results] == [1, 2, 3, 4, 5]
    assert first.aggregates.null_or_invalid["members_addresses.city"] == 5  # all empty

    run_preview.handle(
        conn, {"feed": FEED, "version": 1, "rows": 5, "strategy": "spread"}, settings
    )
    conn.commit()
    spread = WorkflowStore(conn, settings).get_preview(FEED, 1)
    assert spread.sample.selector == "spread_5"
    # Every 4th row of 20, and the row numbers are the batch's, not the sample's.
    assert [r.row_number for r in spread.row_results] == [1, 5, 9, 13, 17]
    assert spread.aggregates.null_or_invalid["members_addresses.city"] == 4  # row 17 has one


def test_both_samples_are_kept_and_each_is_reproducible(conn, settings, landed):
    """Different windows are different facts, so neither overwrites the other."""
    for strategy in ("first", "spread"):
        for _ in range(2):
            run_preview.handle(
                conn, {"feed": FEED, "version": 1, "rows": 5, "strategy": strategy}, settings
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT sample->>'selector' AS selector, count(*) AS n
                FROM {settings.workflow_schema}.preview
                WHERE feed = %s GROUP BY 1 ORDER BY 1""",
            (FEED,),
        )
        assert {r["selector"]: r["n"] for r in cur.fetchall()} == {"first_5": 1, "spread_5": 1}


def test_the_api_refuses_a_sampling_strategy_it_cannot_reproduce(conn, settings, landed):
    client = TestClient(create_app(settings))
    refused = client.post(
        f"/api/feeds/{FEED}/mapping-versions/1/preview", json={"strategy": "random"}
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["allowed"] == ["first", "spread"]

    accepted = client.post(
        f"/api/feeds/{FEED}/mapping-versions/1/preview", json={"rows": 5, "strategy": "first"}
    )
    assert accepted.status_code == 202
    assert accepted.json()["selector"] == "first_5"


# ------------------------------------------------- per-entity reconciliation


def _approve_and_promote(conn, settings, batch_id, upload):
    store = WorkflowStore(conn, settings)
    store.approve_mapping_version(
        feed=FEED, version=1, upload_id=upload.upload_id, approver="lead@cinqcare.com"
    )
    conn.commit()
    return promote_silver.handle(
        conn, {"feed": FEED, "version": 1, "batch_id": batch_id}, settings
    )


def test_a_member_with_only_an_identifier_is_written(conn, settings, landed):
    """Fifteen rows have a name but no city; all twenty are members."""
    batch_id, upload, _ = landed
    result = _approve_and_promote(conn, settings, batch_id, upload)
    conn.commit()

    assert result["counts"] == {
        "records_in": 20,
        "records_out": 20,
        "quarantined": 0,
        "attributed_drops": 0,
    }
    assert result["silver_tables"] == {
        f"{settings.silver_schema}.members": 20,
        f"{settings.silver_schema}.members_addresses": 5,
    }


def test_a_fan_out_that_loses_an_entity_fails_the_run(conn, settings, landed, monkeypatch):
    """The gap this closes: the source-row balance equation alone would still add
    up if every address row vanished."""
    batch_id, upload, mapping = landed
    store = WorkflowStore(conn, settings)
    store.approve_mapping_version(
        feed=FEED, version=1, upload_id=upload.upload_id, approver="lead@cinqcare.com"
    )
    conn.commit()

    from cinqflow.engine import runner as runner_module

    real = runner_module.group_by_entity

    def drops_addresses(mapped):
        grouped = real(mapped)
        grouped.pop("members_addresses", None)
        return grouped

    monkeypatch.setattr(runner_module, "group_by_entity", drops_addresses)
    canonical = load_canonical(YamlKnowledgeProvider(settings), "enrollment")
    with pytest.raises(PromotionFailure, match="balance failed"):
        PipelineRunner(conn, settings).promote_silver(
            batch_id=batch_id,
            mapping=store.get_mapping_version(FEED, 1),
            canonical=canonical,
            upload=store.get_upload(upload.upload_id),
        )
    conn.commit()

    run = store.get_run(batch_id, kind="promote_silver")
    assert run.state.value == "failed"
    assert "members_addresses" in run.error
    assert "prepared 0 rows where the mapped values account for 5" in run.error


# ------------------------------------------------------ quarantine visibility


def test_quarantined_rows_are_readable_with_their_reasons_and_masked_values(
    conn, settings, landed
):
    batch_id, upload, _ = landed
    store = WorkflowStore(conn, settings)

    # A version that refuses every row without a city.
    spec = _spec()
    spec.fields[2].on_null = "reject"
    store.update_draft_spec(feed=FEED, version=1, spec=spec)
    store.approve_mapping_version(
        feed=FEED, version=1, upload_id=upload.upload_id, approver="lead@cinqcare.com"
    )
    conn.commit()
    result = promote_silver.handle(
        conn, {"feed": FEED, "version": 1, "batch_id": batch_id}, settings
    )
    conn.commit()
    assert result["counts"]["quarantined"] == 15

    client = TestClient(create_app(settings))
    body = client.get(f"/api/batches/{batch_id}/quarantine?limit=5").json()

    assert body["table"] == f"{settings.silver_schema}.{FEED}_quarantine"
    assert body["total"] == 15                     # the whole batch
    assert body["by_outcome"] == {"rejected": 15}  # the whole batch
    assert len(body["rows"]) == 5                  # this page
    assert body["by_rule"] == {"member_city:on_null": 5}

    row = body["rows"][0]
    assert row["mapping_version"] == 1
    assert row["outcome"] == "rejected"
    assert row["reasons"][0]["target"] == "members_addresses.city"
    # PHI candidate values are masked here exactly as they are on the Bronze rows.
    assert "member_id" in body["phi_masked"]
    assert row["raw_row"]["member_id"] == "•••"


def test_quarantine_is_empty_rather_than_missing_before_any_promotion(conn, settings, landed):
    batch_id, _, _ = landed
    body = TestClient(create_app(settings)).get(f"/api/batches/{batch_id}/quarantine").json()
    assert body == {
        "batch_id": batch_id,
        "table": f"{settings.silver_schema}.{FEED}_quarantine",
        "total": 0,
        "by_outcome": {},
        "by_rule": {},
        "rows": [],
    }


def test_quarantine_of_an_unknown_batch_is_404(conn, settings):
    assert TestClient(create_app(settings)).get("/api/batches/nope/quarantine").status_code == 404


def test_re_promoting_re_drives_quarantined_rows_through_the_new_version(
    conn, settings, landed
):
    """There is no separate re-drive path, and there does not need to be: a fix is a
    new approved version, and promoting the batch again re-reads Bronze."""
    batch_id, upload, _ = landed
    store = WorkflowStore(conn, settings)

    strict = _spec()
    strict.fields[2].on_null = "reject"
    store.update_draft_spec(feed=FEED, version=1, spec=strict)
    store.approve_mapping_version(
        feed=FEED, version=1, upload_id=upload.upload_id, approver="lead@cinqcare.com"
    )
    conn.commit()
    promote_silver.handle(conn, {"feed": FEED, "version": 1, "batch_id": batch_id}, settings)
    conn.commit()

    # v2 relaxes the rule the analyst now regrets.
    relaxed = store.create_mapping_version(
        feed=FEED,
        domain="enrollment",
        spec=_spec(),
        created_by="analyst@cinqcare.com",
        derived_from=1,
    )
    store.approve_mapping_version(
        feed=FEED, version=relaxed.version, upload_id=upload.upload_id, approver="lead@x.org"
    )
    conn.commit()
    again = promote_silver.handle(
        conn, {"feed": FEED, "version": relaxed.version, "batch_id": batch_id}, settings
    )
    conn.commit()

    assert again["counts"]["records_out"] == 20
    assert again["counts"]["quarantined"] == 0
    assert again["rebuilt"] is True

    plane = PostgresDataPlane(conn)
    quarantine = quarantine_table(FEED, schema=settings.silver_schema)
    assert plane.count_rows(quarantine, batch_id) == 0

    members = silver_table(
        "members",
        load_canonical(YamlKnowledgeProvider(settings), "enrollment").fields_of("members"),
        schema=settings.silver_schema,
    )
    assert plane.count_rows(members, batch_id) == 20
    assert store.get_mapping_version(FEED, 1).status == "superseded"
