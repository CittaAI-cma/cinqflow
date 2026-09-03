"""Bronze profiling and proposal persistence, against real Postgres."""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.dataplane.contract import bronze_table
from cinqflow.dataplane.filestore import FileStore, Folder, fingerprint_bytes, landing_key
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine.bronze_profiler import profile_batch
from cinqflow.workers import analyze_bronze, interpret_upload, land_bronze, profile_upload
from cinqflow.workflow.states import RunState, UploadStatus
from cinqflow.workflow.store import WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_bronze_intel"
ROSTER = (
    b"member_id,member_first_name,member_dob,member_sex,product,harp_eligible\n"
    b"M001,DANIELLE,1997-11-04,F,TANF Adult,Yes\n"
    b"M002,KEVIN,2013-11-04,M,TANF Child,\n"
    b"M003,ALEX,,U,TANF Adult,Yes\n"
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
def landed_batch(conn, settings):
    """A batch that has been through Stages 1 and 2, ready for intelligence."""
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
    result = land_bronze.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()
    assert result["landed"] is True
    return result["batch_id"], upload


# ---------------------------------------------------------- bronze profiling


SOURCE_ORDER = [
    "member_id",
    "member_first_name",
    "member_dob",
    "member_sex",
    "product",
    "harp_eligible",
]


def test_bronze_profile_describes_what_landed(conn, settings, landed_batch):
    batch_id, _ = landed_batch
    result = profile_batch(
        PostgresDataPlane(conn),
        bronze_table(FEED),
        batch_id,
        settings=settings,
        column_order=SOURCE_ORDER,
    )

    assert result.rows_in_batch == 3
    assert result.rows_profiled == 3
    assert result.is_sample is False
    assert [c.name for c in result.facts.columns] == SOURCE_ORDER
    by_name = {c.name: c for c in result.facts.columns}
    assert by_name["member_dob"].null_count == 1
    assert by_name["harp_eligible"].null_count == 1
    assert ["member_id"] in result.facts.candidate_keys


def test_jsonb_storage_does_not_preserve_source_column_order(conn, settings, landed_batch):
    """Documents a real property of the Stage 2 raw_row JSONB decision: without a
    supplied order, columns come back normalised, not in file order."""
    batch_id, _ = landed_batch
    unordered = profile_batch(
        PostgresDataPlane(conn), bronze_table(FEED), batch_id, settings=settings
    )
    assert [c.name for c in unordered.facts.columns] != SOURCE_ORDER
    assert sorted(c.name for c in unordered.facts.columns) == sorted(SOURCE_ORDER)


def test_bronze_profile_agrees_with_the_upload_profile(conn, settings, landed_batch):
    """Landing must not alter the data: the substantive facts must match."""
    batch_id, upload = landed_batch
    store = WorkflowStore(conn, settings)
    upload_profile = store.get_profile(upload.upload_id)
    bronze = profile_batch(
        PostgresDataPlane(conn),
        bronze_table(FEED),
        batch_id,
        settings=settings,
        column_order=[c.name for c in upload_profile.facts.columns],
    )

    assert bronze.facts.row_count == upload_profile.facts.row_count
    assert [c.name for c in bronze.facts.columns] == [
        c.name for c in upload_profile.facts.columns
    ]
    def comparable(facts) -> dict:
        return {
            c.name: (c.null_count, c.distinct_count, c.inferred_type) for c in facts.columns
        }

    assert comparable(bronze.facts) == comparable(upload_profile.facts)
    assert bronze.facts.candidate_keys == upload_profile.facts.candidate_keys
    # The ids differ by design: each profile records where its rows were read from
    # (the file vs the Bronze table), and that provenance is part of the facts.
    assert bronze.profile_id != upload_profile.profile_id
    assert bronze.facts.sheets[0].name == f"bronze.{FEED}_raw"


def test_sampling_is_recorded_when_the_window_is_smaller_than_the_batch(
    conn, settings, landed_batch
):
    batch_id, _ = landed_batch
    result = profile_batch(
        PostgresDataPlane(conn), bronze_table(FEED), batch_id, settings=settings, sample_rows=2
    )
    assert result.rows_in_batch == 3
    assert result.rows_profiled == 2
    assert result.is_sample is True


# ------------------------------------------------------------------- worker


def test_analyze_worker_persists_profile_and_proposal(conn, settings, landed_batch):
    batch_id, upload = landed_batch
    result = analyze_bronze.handle(conn, {"batch_id": batch_id}, settings)
    conn.commit()

    assert result["analysed"] is True
    assert result["proposal_status"] == "proposed"

    store = WorkflowStore(conn, settings)
    profile = store.get_bronze_profile(batch_id)
    assert profile is not None
    assert profile.bronze_table == f"bronze.{FEED}_raw"
    assert profile.rows_in_batch == 3

    proposal = store.get_proposal(batch_id)
    assert proposal is not None
    assert proposal.batch_id == batch_id
    assert proposal.upload_id == upload.upload_id
    assert proposal.domain == "enrollment"  # singular canonical domain
    assert proposal.bronze_profile_id == profile.profile_id
    assert proposal.provenance.prompt == "recommend_mapping@3"
    assert any("canonical/enrollment.yaml" in c for c in proposal.provenance.knowledge)

    by_source = {f.source: f for f in proposal.content.fields}
    assert by_source["member_id"].target == "members.source_system_id"
    assert by_source["harp_eligible"].target is None
    assert by_source["harp_eligible"].status == "unknown"


def test_analysis_is_refused_for_a_batch_that_did_not_complete(conn, settings, landed_batch):
    batch_id, _ = landed_batch
    WorkflowStore(conn, settings).set_run_state(batch_id, RunState.FAILED)
    conn.commit()

    result = analyze_bronze.handle(conn, {"batch_id": batch_id}, settings)
    conn.commit()

    assert result["analysed"] is False
    assert "failed" in result["reason"]
    assert WorkflowStore(conn, settings).get_proposal(batch_id) is None


def test_profile_survives_a_failing_model(conn, settings, landed_batch):
    """The deterministic half is not lost when the AI half fails."""
    batch_id, _ = landed_batch

    class Exploding:
        def run(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = analyze_bronze.handle(
        conn, {"batch_id": batch_id}, settings, runtime=Exploding()
    )
    conn.commit()

    assert result["analysed"] is True
    assert result["proposal"] is None
    assert "provider unavailable" in result["error"]

    store = WorkflowStore(conn, settings)
    assert store.get_bronze_profile(batch_id) is not None
    assert store.get_proposal(batch_id) is None


def test_landing_queues_bronze_analysis(conn, settings, landed_batch):
    """Stage 2 chains into Stage 3 without a manual step."""
    from cinqflow.queue.queue import Queue

    _batch_id, _upload = landed_batch
    assert Queue(conn, settings).depth(analyze_bronze.TOPIC) == 1


def test_reanalysis_reuses_the_immutable_profile_row(conn, settings, landed_batch):
    batch_id, _ = landed_batch
    analyze_bronze.handle(conn, {"batch_id": batch_id}, settings)
    conn.commit()
    analyze_bronze.handle(conn, {"batch_id": batch_id}, settings)
    conn.commit()

    profiles = conn.execute(
        f"SELECT count(*) AS n FROM {settings.workflow_schema}.bronze_profile WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()["n"]
    proposals = conn.execute(
        f"SELECT count(*) AS n FROM {settings.workflow_schema}.proposal WHERE batch_id = %s",
        (batch_id,),
    ).fetchone()["n"]

    assert profiles == 1  # same facts, same id, one row
    assert proposals == 2  # each analysis is its own proposal
