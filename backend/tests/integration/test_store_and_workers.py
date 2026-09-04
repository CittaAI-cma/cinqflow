"""Store invariants and the two Stage 1 workers, against real Postgres."""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.dataplane.filestore import FileStore, Folder, fingerprint_bytes, landing_key
from cinqflow.queue.worker import drain
from cinqflow.workers import interpret_upload, profile_upload
from cinqflow.workflow.states import IllegalTransition, UploadStatus
from cinqflow.workflow.store import DuplicateUpload, WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db


def _land(settings, content: bytes, filename: str = "roster.csv") -> tuple[str, str]:
    key = landing_key(
        domain="enrollments",
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        folder=Folder.INCOMING,
        business_date="2026-06-01",
        filename=filename,
    )
    FileStore(settings).place(key, content)
    return key, fingerprint_bytes(content)


def _create_upload(conn, settings, content: bytes, filename: str = "roster.csv"):
    key, fingerprint = _land(settings, content, filename)
    return WorkflowStore(conn, settings).create_upload(
        fingerprint=fingerprint,
        filename=filename,
        file_type="xlsx" if filename.endswith(".xlsx") else "csv",
        size_bytes=len(content),
        uploader="analyst@cinqcare.com",
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain="enrollments",
        business_date=date(2026, 6, 1),
        landing_key=key,
    )


def test_duplicate_fingerprint_is_refused(conn, settings, small_csv_bytes):
    _create_upload(conn, settings, small_csv_bytes)
    with pytest.raises(DuplicateUpload):
        WorkflowStore(conn, settings).create_upload(
            fingerprint=fingerprint_bytes(small_csv_bytes),
            filename="same-bytes-different-name.csv",
            file_type="csv",
            size_bytes=len(small_csv_bytes),
            uploader="a@b.c",
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            domain="enrollments",
            business_date=date(2026, 6, 1),
            landing_key="x/y/z/incoming/2026-06-01/other.csv",
        )


def test_store_enforces_the_lifecycle(conn, settings, small_csv_bytes):
    upload = _create_upload(conn, settings, small_csv_bytes)
    store = WorkflowStore(conn, settings)
    with pytest.raises(IllegalTransition):
        store.set_status(upload.upload_id, UploadStatus.INTERPRETED)


def test_profile_worker_persists_facts_and_queues_interpretation(conn, settings, small_csv_bytes):
    upload = _create_upload(conn, settings, small_csv_bytes)
    conn.commit()

    result = profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    assert result["status"] == UploadStatus.PROFILED
    store = WorkflowStore(conn, settings)
    assert store.get_upload(upload.upload_id).status == UploadStatus.PROFILED

    profile = store.get_profile(upload.upload_id)
    assert profile is not None
    assert profile.facts.row_count == 3
    assert profile.profile_id == result["profile_id"]

    from cinqflow.queue.queue import Queue

    assert Queue(conn, settings).depth("upload.interpret") == 1


def test_reprofiling_is_idempotent(conn, settings, small_csv_bytes):
    upload = _create_upload(conn, settings, small_csv_bytes)
    conn.commit()
    first = profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    # Re-running the same job writes the same profile row, not a second one.
    store = WorkflowStore(conn, settings)
    facts = store.get_profile(upload.upload_id).facts
    store.put_profile(
        profile_id=first["profile_id"],
        upload_id=upload.upload_id,
        profiler_version="1",
        facts=facts,
    )
    conn.commit()
    count = conn.execute(
        f"SELECT count(*) AS n FROM {settings.workflow_schema}.profile WHERE upload_id = %s",
        (upload.upload_id,),
    ).fetchone()["n"]
    assert count == 1


def test_unparseable_file_lands_in_profile_failed_with_the_error(conn, settings):
    upload = _create_upload(conn, settings, b"\x00\x01\x02not-a-csv", "broken.xlsx")
    conn.commit()

    result = profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    assert result["status"] == UploadStatus.PROFILE_FAILED
    stored = WorkflowStore(conn, settings).get_upload(upload.upload_id)
    assert stored.status == UploadStatus.PROFILE_FAILED
    assert stored.error and "cannot read xlsx" in stored.error
    # the original is retained regardless
    assert FileStore(settings).exists(stored.landing_key)


def test_interpret_worker_persists_a_structured_artifact(conn, settings, small_csv_bytes):
    upload = _create_upload(conn, settings, small_csv_bytes)
    conn.commit()
    profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    result = interpret_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    assert result["status"] == UploadStatus.INTERPRETED
    interpretation = WorkflowStore(conn, settings).get_interpretation(upload.upload_id)
    assert interpretation is not None
    assert interpretation.version == 1
    assert interpretation.status == "draft"
    assert interpretation.provenance.prompt == "interpret_file@2"
    assert interpretation.provenance.knowledge  # knowledge was cited
    fields = {c.field for c in interpretation.content.claims}
    assert {"likely_domain", "likely_dataset", "likely_grain"} <= fields
    assert all(c.evidence for c in interpretation.content.claims)


def test_interpretation_failure_is_recorded_and_retryable(conn, settings, small_csv_bytes):
    upload = _create_upload(conn, settings, small_csv_bytes)
    conn.commit()
    profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    class Exploding:
        def run(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = interpret_upload.handle(
        conn, {"upload_id": upload.upload_id}, settings, runtime=Exploding()
    )
    conn.commit()
    assert result["status"] == UploadStatus.INTERPRET_FAILED
    stored = WorkflowStore(conn, settings).get_upload(upload.upload_id)
    assert "provider unavailable" in stored.error
    # the profile survived the failure
    assert WorkflowStore(conn, settings).get_profile(upload.upload_id) is not None

    # retry succeeds
    interpret_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()
    assert WorkflowStore(conn, settings).get_upload(upload.upload_id).status == (
        UploadStatus.INTERPRETED
    )


def test_second_interpretation_supersedes_the_first(conn, settings, small_csv_bytes):
    upload = _create_upload(conn, settings, small_csv_bytes)
    conn.commit()
    profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    interpret_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    conn.commit()

    store = WorkflowStore(conn, settings)
    profile = store.get_profile(upload.upload_id)
    first = store.get_interpretation(upload.upload_id)
    store.put_interpretation(
        upload_id=upload.upload_id,
        profile_id=profile.profile_id,
        provenance=first.provenance,
        content=first.content,
    )
    conn.commit()

    current = store.get_interpretation(upload.upload_id)
    assert current.version == 2
    assert current.status == "draft"
    prior = conn.execute(
        f"""SELECT status FROM {settings.workflow_schema}.interpretation
            WHERE upload_id = %s AND version = 1""",
        (upload.upload_id,),
    ).fetchone()["status"]
    assert prior == "superseded"


def test_worker_drain_runs_the_whole_chain(conn, settings, small_csv_bytes):
    from cinqflow.queue.queue import Queue

    upload = _create_upload(conn, settings, small_csv_bytes)
    Queue(conn, settings).enqueue(
        profile_upload.TOPIC,
        {"upload_id": upload.upload_id},
        dedupe_key=f"profile/{upload.upload_id}",
    )
    conn.commit()

    processed = drain(conn, settings)
    assert processed == 2  # profile, then interpret
    assert WorkflowStore(conn, settings).get_upload(upload.upload_id).status == (
        UploadStatus.INTERPRETED
    )
