"""G1: approvals are append-only and are the only authorisation to write Bronze."""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.dataplane.contract import bronze_table
from cinqflow.dataplane.filestore import FileStore, Folder, fingerprint_bytes, landing_key
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.workers import land_bronze
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import AlreadyDecided, WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_gate"


@pytest.fixture
def interpreted_upload(conn, settings, small_csv_bytes):
    key = landing_key(
        domain="enrollments",
        source_system="fidelis_ny_upstate",
        feed=FEED,
        folder=Folder.INCOMING,
        business_date="2026-06-01",
        filename="roster.csv",
    )
    FileStore(settings).place(key, small_csv_bytes)
    store = WorkflowStore(conn, settings)
    upload = store.create_upload(
        fingerprint=fingerprint_bytes(small_csv_bytes),
        filename="roster.csv",
        file_type="csv",
        size_bytes=len(small_csv_bytes),
        uploader="analyst@cinqcare.com",
        source_system="fidelis_ny_upstate",
        feed=FEED,
        domain="enrollments",
        business_date=date(2026, 6, 1),
        landing_key=key,
    )
    from cinqflow.workers import interpret_upload, profile_upload

    profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    interpret_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()
    return store.get_upload(upload.upload_id)


@pytest.fixture(autouse=True)
def drop_feed_table(conn, settings):
    yield
    conn.rollback()
    table = bronze_table(FEED)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table.schema}."{table.name}" CASCADE')
    conn.commit()


def _approve(conn, settings, upload, decision="approved") -> None:
    store = WorkflowStore(conn, settings)
    interpretation = store.get_interpretation(upload.upload_id)
    store.put_approval(
        gate="G1",
        artifact_type="interpretation",
        artifact_id=interpretation.interpretation_id,
        artifact_version=interpretation.version,
        upload_id=upload.upload_id,
        decision=decision,
        approver="analyst@cinqcare.com",
    )
    store.set_status(
        upload.upload_id,
        UploadStatus.APPROVED if decision == "approved" else UploadStatus.REJECTED,
    )
    conn.commit()


def test_approval_records_who_decided_what_and_when(conn, settings, interpreted_upload):
    _approve(conn, settings, interpreted_upload)
    approvals = WorkflowStore(conn, settings).list_approvals(interpreted_upload.upload_id)

    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.gate == "G1"
    assert approval.decision == "approved"
    assert approval.approver == "analyst@cinqcare.com"
    assert approval.artifact_type == "interpretation"
    assert approval.artifact_version == 1
    assert approval.decided_ts is not None


def test_a_second_decision_on_the_same_version_is_refused(conn, settings, interpreted_upload):
    _approve(conn, settings, interpreted_upload)
    store = WorkflowStore(conn, settings)
    interpretation = store.get_interpretation(interpreted_upload.upload_id)

    with pytest.raises(AlreadyDecided):
        store.put_approval(
            gate="G1",
            artifact_type="interpretation",
            artifact_id=interpretation.interpretation_id,
            artifact_version=interpretation.version,
            upload_id=interpreted_upload.upload_id,
            decision="rejected",
            approver="someone_else@cinqcare.com",
        )


def test_landing_is_refused_without_approval(conn, settings, interpreted_upload):
    """The worker will not write Bronze for a merely interpreted upload."""
    result = land_bronze.handle(conn, {"upload_id": interpreted_upload.upload_id}, settings)
    conn.commit()

    assert result["landed"] is False
    assert result["status"] == UploadStatus.INTERPRETED
    assert not PostgresDataPlane(conn).table_exists(bronze_table(FEED))
    assert WorkflowStore(conn, settings).list_runs(upload_id=interpreted_upload.upload_id) == []


def test_landing_proceeds_after_approval(conn, settings, interpreted_upload):
    _approve(conn, settings, interpreted_upload)
    result = land_bronze.handle(conn, {"upload_id": interpreted_upload.upload_id}, settings)
    conn.commit()

    assert result["landed"] is True
    assert result["counts"] == {
        "records_in": 3,
        "records_out": 3,
        "quarantined": 0,
        "attributed_drops": 0,
    }
    plane = PostgresDataPlane(conn)
    assert plane.count_rows(bronze_table(FEED), result["batch_id"]) == 3


def test_rejected_upload_is_never_landed(conn, settings, interpreted_upload):
    _approve(conn, settings, interpreted_upload, decision="rejected")
    result = land_bronze.handle(conn, {"upload_id": interpreted_upload.upload_id}, settings)
    conn.commit()

    assert result["landed"] is False
    assert not PostgresDataPlane(conn).table_exists(bronze_table(FEED))


def test_reject_worker_moves_the_file(conn, settings, interpreted_upload):
    from cinqflow.workers import reject_upload

    _approve(conn, settings, interpreted_upload, decision="rejected")
    result = reject_upload.handle(conn, {"upload_id": interpreted_upload.upload_id}, settings)
    conn.commit()

    assert result["moved"] is True
    assert "/rejected/" in result["landing_key"]
    assert FileStore(settings).exists(result["landing_key"])
