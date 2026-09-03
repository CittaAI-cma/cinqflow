"""Preview against real Postgres: persistence, staleness, and no model involved."""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.dataplane.contract import bronze_table
from cinqflow.dataplane.filestore import FileStore, Folder, fingerprint_bytes, landing_key
from cinqflow.engine.mapping_exec import spec_fingerprint
from cinqflow.workers import interpret_upload, land_bronze, profile_upload, run_preview
from cinqflow.workflow.models import MappingField, MappingSpec, Transform
from cinqflow.workflow.states import UploadStatus
from cinqflow.workflow.store import WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_preview_feed"
ROSTER = (
    b"member_id,member_first_name,member_dob,member_sex\n"
    b"M001,DANIELLE,1997-11-04,F\n"
    b"M002,KEVIN,13/45/1990,M\n"      # unparseable date
    b",ALEX,2000-01-01,M\n"           # missing identifier
    b"M004,SAM,2001-02-03,X\n"        # value not in the map
)


@pytest.fixture(autouse=True)
def drop_feed_table(conn, settings):
    yield
    conn.rollback()
    table = bronze_table(FEED)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table.schema}."{table.name}" CASCADE')
    conn.commit()


def _spec() -> MappingSpec:
    return MappingSpec(
        target_table="silver_raw.members",
        fields=[
            MappingField(
                source="member_id", target="members.source_system_id", on_null="reject"
            ),
            MappingField(source="member_first_name", target="members.first_name"),
            MappingField(
                source="member_dob",
                target="members.date_of_birth",
                cast="timestamp",
                transform=Transform(op="parse_date", args={"format": "%Y-%m-%d"}),
            ),
            MappingField(
                source="member_sex",
                target="members.sex",
                value_map={"M": "male", "F": "female"},
                on_unmapped_value="quarantine",
            ),
        ],
    )


@pytest.fixture
def landed(conn, settings):
    """A landed batch plus a draft mapping version to preview."""
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
    outcome = land_bronze.handle(conn, {"upload_id": upload.upload_id}, settings)
    mapping = store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=_spec(), created_by="analyst@cinqcare.com"
    )
    conn.commit()
    return outcome["batch_id"], mapping


def test_preview_describes_what_the_mapping_does_to_real_bronze_rows(conn, settings, landed):
    batch_id, _ = landed
    result = run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()

    assert result["previewed"] is True
    assert result["batch_id"] == batch_id

    preview = WorkflowStore(conn, settings).get_preview(FEED, 1)
    assert preview.sample.batch_id == batch_id
    assert preview.sample.bronze_table == f"bronze.{FEED}_raw"
    assert preview.sample.rows == 4
    assert preview.sample.rows_in_batch == 4
    assert preview.sample.is_sample is False
    # Sampling is spread across the batch by default, so a clean preview is not
    # merely a clean first window. This batch is smaller than the sample, so the
    # spread covers all of it.
    assert preview.sample.selector == "spread_200"

    aggregates = preview.aggregates
    assert aggregates.rows_previewed == 4
    assert aggregates.rows_ok == 1
    assert aggregates.rows_with_failures == 1
    assert aggregates.rows_rejected == 1
    assert aggregates.rows_quarantined == 1
    assert aggregates.failures_by_rule == {
        "member_dob:parse_date": 1,
        "member_id:on_null": 1,
        "member_sex:on_unmapped_value": 1,
    }
    assert aggregates.null_or_invalid["members.date_of_birth"] == 1


def test_every_row_and_field_is_recorded_with_source_and_mapped_values(conn, settings, landed):
    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()
    preview = WorkflowStore(conn, settings).get_preview(FEED, 1)

    assert [r.row_number for r in preview.row_results] == [1, 2, 3, 4]
    first = {f.source: f for f in preview.row_results[0].fields}
    assert first["member_sex"].source_value == "F"
    assert first["member_sex"].mapped_value == "female"      # value_map applied
    # The cast writes the offset out, so the value does not depend on the timezone
    # of whichever connection stores it.
    assert first["member_dob"].mapped_value == "1997-11-04T00:00:00+00:00"

    failed = {f.source: f for f in preview.row_results[1].fields}
    assert failed["member_dob"].outcome == "failure"
    assert failed["member_dob"].source_value == "13/45/1990"
    assert failed["member_dob"].mapped_value is None
    assert "does not match format" in failed["member_dob"].reason


def test_preview_marks_the_version_previewed(conn, settings, landed):
    store = WorkflowStore(conn, settings)
    assert store.get_mapping_version(FEED, 1).status == "draft"

    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()

    version = store.get_mapping_version(FEED, 1)
    assert version.status == "previewed"
    assert version.editable is True  # a previewed draft can still be corrected


def test_editing_the_draft_makes_the_preview_stale(conn, settings, landed):
    """Nothing is deleted: the fingerprints simply stop matching."""
    store = WorkflowStore(conn, settings)
    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()
    preview = store.get_preview(FEED, 1)
    before = store.get_mapping_version(FEED, 1)
    assert preview.spec_fingerprint == spec_fingerprint(before.spec)

    edited = _spec()
    edited.fields[3].value_map = {"M": "male", "F": "female", "X": "unknown"}
    store.update_draft_spec(feed=FEED, version=1, spec=edited)
    conn.commit()

    after = store.get_mapping_version(FEED, 1)
    assert after.status == "draft"  # back to draft, as Stage 4 requires
    assert store.get_current_preview(FEED, 1, spec_fingerprint(after.spec)) is None
    # the old preview is still on record, just no longer current
    assert store.get_preview(FEED, 1).preview_id == preview.preview_id


def test_re_previewing_the_corrected_spec_becomes_current(conn, settings, landed):
    store = WorkflowStore(conn, settings)
    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()

    fixed = _spec()
    fixed.fields[3].value_map = {"M": "male", "F": "female", "X": "unknown"}
    store.update_draft_spec(feed=FEED, version=1, spec=fixed)
    conn.commit()
    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()

    current = store.get_current_preview(FEED, 1, spec_fingerprint(fixed))
    assert current is not None
    assert current.aggregates.rows_quarantined == 0  # the fix worked
    assert current.aggregates.rows_ok == 2


def test_the_same_spec_and_sample_produce_an_identical_preview(conn, settings, landed):
    """Acceptance 1: deterministic, and the artifact is not duplicated."""
    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()
    first = WorkflowStore(conn, settings).get_preview(FEED, 1)

    run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()
    second = WorkflowStore(conn, settings).get_preview(FEED, 1)

    assert first.preview_id == second.preview_id  # one row, not two
    assert first.aggregates.model_dump() == second.aggregates.model_dump()
    assert [r.model_dump() for r in first.row_results] == [
        r.model_dump() for r in second.row_results
    ]
    rows = conn.execute(
        f"SELECT count(*) AS n FROM {settings.workflow_schema}.preview WHERE feed = %s",
        (FEED,),
    ).fetchone()["n"]
    assert rows == 1


def test_preview_never_calls_a_model(conn, settings, landed, monkeypatch):
    """Acceptance 1, the other half: a poisoned runtime must go untouched."""

    def explode(*args, **kwargs):
        raise AssertionError("preview must not invoke the AI runtime")

    monkeypatch.setattr("cinqflow.intelligence.runtime.AgentRuntime.__init__", explode)
    monkeypatch.setattr("cinqflow.intelligence.runtime.AgentRuntime.run", explode)
    monkeypatch.setattr("cinqflow.intelligence.llm.StubClient.complete_json", explode)

    result = run_preview.handle(conn, {"feed": FEED, "version": 1}, settings)
    conn.commit()
    assert result["previewed"] is True


def test_a_bounded_sample_is_recorded_as_partial(conn, settings, landed):
    run_preview.handle(conn, {"feed": FEED, "version": 1, "rows": 2}, settings)
    conn.commit()
    preview = WorkflowStore(conn, settings).get_preview(FEED, 1)

    assert preview.sample.rows == 2
    assert preview.sample.rows_in_batch == 4
    assert preview.sample.is_sample is True
    assert preview.aggregates.rows_previewed == 2


def test_preview_is_refused_when_there_is_no_batch(conn, settings):
    store = WorkflowStore(conn, settings)
    store.create_mapping_version(
        feed="feed_without_batch", domain="enrollment", spec=_spec(), created_by="a"
    )
    conn.commit()

    result = run_preview.handle(conn, {"feed": "feed_without_batch", "version": 1}, settings)
    assert result["previewed"] is False
    assert result["reason"] == "no batch"
    assert store.get_preview("feed_without_batch", 1) is None
