"""Bronze writes, append-only enforcement, and the landing run - real Postgres."""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from cinqflow.dataplane.contract import BronzeRow, bronze_table, new_batch_id, record_hash
from cinqflow.dataplane.filestore import (
    FileStore,
    Folder,
    fingerprint_bytes,
    landing_key,
)
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine.runner import LandingFailure, PipelineRunner
from cinqflow.settings import Settings
from cinqflow.workflow.states import RunState, UploadStatus
from cinqflow.workflow.store import WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_roster"


@pytest.fixture
def plane(conn, settings: Settings):
    """A feed table in the real `bronze` schema, dropped after the test.

    The schema is shared with the previous implementation, so the table name is
    test-scoped and only that table is removed.
    """
    table = bronze_table(FEED)
    dataplane = PostgresDataPlane(conn)
    dataplane.ensure_table(table)
    conn.commit()
    try:
        yield dataplane, table
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS {table.schema}."{table.name}" CASCADE')
        conn.commit()


def _row(batch_id: str, n: int, values: dict[str, str]) -> BronzeRow:
    return BronzeRow(
        bronze_id=str(uuid.uuid4()),
        feed_id=FEED,
        row_number=n,
        raw_row=values,
        source_system="fidelis_ny_upstate",
        batch_id=batch_id,
        record_hash=record_hash(values),
    )


def test_append_and_read_back_preserves_source_values(plane, conn):
    dataplane, table = plane
    batch = new_batch_id()

    written = dataplane.append_bronze(
        table,
        [
            _row(batch, 1, {"member_id": "M1", "product": "TANF Adult"}),
            _row(batch, 2, {"member_id": "M2", "product": "TANF Child"}),
        ],
    )
    conn.commit()

    assert written == 2
    assert dataplane.count_rows(table, batch) == 2
    rows = dataplane.read_rows(table, batch, limit=10)
    assert [r["row_number"] for r in rows] == [1, 2]
    # values land exactly as received
    assert rows[0]["raw_row"] == {"member_id": "M1", "product": "TANF Adult"}
    assert rows[0]["source_system"] == "fidelis_ny_upstate"
    assert rows[0]["ingestion_ts"] is not None


def test_bronze_refuses_update(plane, conn):
    dataplane, table = plane
    batch = new_batch_id()
    dataplane.append_bronze(table, [_row(batch, 1, {"member_id": "M1"})])
    conn.commit()

    with pytest.raises(psycopg.errors.RaiseException) as exc:
        with conn.cursor() as cur:
            cur.execute(f'UPDATE {table.schema}."{table.name}" SET feed_id = %s', ("tampered",))
    assert "append-only" in str(exc.value)
    conn.rollback()
    assert dataplane.count_rows(table, batch) == 1


def test_bronze_refuses_delete_and_truncate(plane, conn):
    dataplane, table = plane
    batch = new_batch_id()
    dataplane.append_bronze(table, [_row(batch, 1, {"member_id": "M1"})])
    conn.commit()

    for statement in (
        f'DELETE FROM {table.schema}."{table.name}"',
        f'TRUNCATE {table.schema}."{table.name}"',
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.cursor() as cur:
                cur.execute(statement)
        conn.rollback()

    assert dataplane.count_rows(table, batch) == 1


def test_ensure_table_is_idempotent(plane, conn):
    dataplane, table = plane
    batch = new_batch_id()
    dataplane.append_bronze(table, [_row(batch, 1, {"member_id": "M1"})])
    conn.commit()

    dataplane.ensure_table(table)  # again
    conn.commit()
    assert dataplane.count_rows(table, batch) == 1  # data survived


def test_the_previous_builds_table_is_untouched(conn):
    """bronze.members_raw belongs to the prior implementation."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('bronze.members_raw') IS NOT NULL AS present")
        if not cur.fetchone()["present"]:
            pytest.skip("prior implementation's table is not in this database")
        cur.execute(
            """SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'bronze' AND c.relname = 'members_raw'
                 AND NOT t.tgisinternal"""
        )
        triggers = {r["tgname"] for r in cur.fetchall()}
    assert triggers == {"trg_members_raw_append_only"}


# --------------------------------------------------------------- PipelineRunner


@pytest.fixture
def approved_upload(conn, settings, small_csv_bytes):
    """An upload in the state G1 approval leaves it in."""
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
    for status in (
        UploadStatus.PROFILING,
        UploadStatus.PROFILED,
        UploadStatus.INTERPRETING,
        UploadStatus.INTERPRETED,
        UploadStatus.APPROVED,
    ):
        store.set_status(upload.upload_id, status)
    conn.commit()
    return store.get_upload(upload.upload_id)


def test_landing_writes_bronze_moves_the_original_and_balances(
    conn, settings, approved_upload, plane
):
    dataplane, table = plane
    outcome = PipelineRunner(conn, settings, plane=dataplane).land_bronze(approved_upload)
    conn.commit()

    assert outcome.bronze_table == table.qualified
    assert outcome.counts.records_in == 3
    assert outcome.counts.records_out == 3
    assert outcome.counts.balanced
    assert dataplane.count_rows(table, outcome.batch_id) == 3

    store = WorkflowStore(conn, settings)
    assert store.get_upload(approved_upload.upload_id).status == UploadStatus.LANDED

    run = store.get_run(outcome.batch_id)
    assert run.state == RunState.COMPLETED
    assert run.balanced is True
    assert run.counts.records_out == 3

    # the original left `incoming` only after its rows were safe
    assert "/processed/" in outcome.landing_key
    assert FileStore(settings).exists(outcome.landing_key)
    assert not FileStore(settings).exists(approved_upload.landing_key)
    assert store.get_upload(approved_upload.upload_id).landing_key == outcome.landing_key


def test_lineage_connects_upload_file_batch_and_bronze(conn, settings, approved_upload, plane):
    dataplane, _ = plane
    outcome = PipelineRunner(conn, settings, plane=dataplane).land_bronze(approved_upload)
    conn.commit()

    lineage = WorkflowStore(conn, settings).get_lineage(outcome.batch_id)
    assert lineage.upload_id == approved_upload.upload_id
    assert lineage.fingerprint == approved_upload.fingerprint
    assert lineage.bronze_table == outcome.bronze_table
    assert "/processed/" in lineage.landing_key


def test_row_numbers_and_hashes_are_stable_across_reruns(conn, settings, approved_upload, plane):
    """Replay from the same original re-derives identical row hashes."""
    dataplane, table = plane
    runner = PipelineRunner(conn, settings, plane=dataplane)
    first = runner.land_bronze(approved_upload)
    conn.commit()

    hashes_first = {
        r["row_number"]: r["record_hash"]
        for r in dataplane.read_rows(table, first.batch_id, limit=10)
    }

    # re-landing the moved original as a second batch
    store = WorkflowStore(conn, settings)
    replayed = store.get_upload(approved_upload.upload_id)
    store.set_status(replayed.upload_id, UploadStatus.LANDING)
    conn.commit()
    second = runner.land_bronze(store.get_upload(replayed.upload_id))
    conn.commit()

    hashes_second = {
        r["row_number"]: r["record_hash"]
        for r in dataplane.read_rows(table, second.batch_id, limit=10)
    }
    assert hashes_first == hashes_second
    assert first.batch_id != second.batch_id
    # the first batch's rows are still there: Bronze is never rewritten
    assert dataplane.count_rows(table, first.batch_id) == 3


def test_unparseable_original_fails_the_run_and_writes_nothing(conn, settings, approved_upload):
    """A file that cannot be parsed leaves a failed run and no Bronze table."""
    store = WorkflowStore(conn, settings)
    FileStore(settings).remove(approved_upload.landing_key)
    FileStore(settings).place(approved_upload.landing_key, b"\x00\x01not-a-csv")
    # pretend it was declared xlsx so parsing definitely fails
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {settings.workflow_schema}.upload SET file_type = 'xlsx' WHERE upload_id = %s",
            (approved_upload.upload_id,),
        )
    conn.commit()
    upload = store.get_upload(approved_upload.upload_id)

    with pytest.raises(LandingFailure):
        PipelineRunner(conn, settings).land_bronze(upload)

    assert store.get_upload(upload.upload_id).status == UploadStatus.LAND_FAILED
    runs = store.list_runs(upload_id=upload.upload_id)
    assert runs and runs[0].state == RunState.FAILED
    assert "cannot read xlsx" in runs[0].error
    # the original stays put for another attempt
    assert FileStore(settings).exists(upload.landing_key)
    assert not PostgresDataPlane(conn).table_exists(bronze_table(FEED))


def test_reject_moves_the_original_and_writes_no_bronze(conn, settings, approved_upload):
    store = WorkflowStore(conn, settings)
    new_key = PipelineRunner(conn, settings).reject(approved_upload)
    conn.commit()

    assert "/rejected/" in new_key
    assert FileStore(settings).exists(new_key)
    assert store.get_upload(approved_upload.upload_id).landing_key == new_key
    assert not PostgresDataPlane(conn).table_exists(bronze_table(FEED))
