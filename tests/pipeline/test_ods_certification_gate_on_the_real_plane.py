"""CF-V3-E10-03 — the ODS certification gate, against the REAL Postgres twin.

    "Exception — Given 0.3% of claims reference members absent from the
     member table, when the gate runs, then publication holds, the
     orphaned claims are listed with their source batches, and the
     incident flow picks it up with the evidence attached."
    — CF-V3-E10-03

Proven against the REAL, already-deployed `MEMBER_DOMAIN_V1` (CF-V3-E10-01/
E8-05's own live state) and a REAL orphaned row inserted directly into
`silver_ods."Members_Addresses"` — not a live pipeline claim (no address
feed loads that table yet, see `core.registry.ods_model_member_mapping`'s
own docstring), but a real check against the real schema and a real
Postgres LEFT JOIN, which the mock plane cannot certify on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.local.pg_ods_load import PostgresOdsLoad
from cinqflow.adapters.mock.notification import ConsoleNotification
from cinqflow.core.certification import Verdict
from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer
from cinqflow.core.relationship_integrity import Relationship
from cinqflow.ports.control_tables import BatchControl, Reconciliation
from cinqflow.workers.incidents import IncidentWorker
from cinqflow.workers.ods_certification import OdsCertificationGate

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

BATCH_ID = "e10-03-live-8842"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
AUTHOR = Actor(subject="cinqflow.ods_certification", actor_type=ActorType.SYSTEM)
RELATIONSHIP = Relationship(
    child_entity="Members_Addresses",
    child_column="OurId",
    parent_entity="Members",
    parent_column="OurId",
)


@pytest.fixture
def rig(plane: object):
    control = PostgresControlTables(plane)
    metadata = PostgresMetadataDb(plane)
    ods = PostgresOdsLoad(plane)
    notify = ConsoleNotification()
    control.open_batch(
        BatchControl(
            batch_id=BATCH_ID,
            feed_id="fidelis-downstate-roster",
            feed_version=1,
            business_date="2026-09-01",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )
    control.record_reconciliation(
        Reconciliation(
            batch_id=BATCH_ID,
            stage=Layer.SILVER_ODS,
            records_in=1,
            records_out=1,
            quarantined=0,
            attributed_drops=0,
            drop_ledger=(),
        )
    )
    incidents = IncidentWorker(control=control, metadata=metadata)
    gate = OdsCertificationGate(
        ods=ods, control=control, metadata=metadata, incidents=incidents, notify=notify
    )
    return gate, ods, control, metadata


def test_the_real_deployed_member_domain_has_exactly_one_published_version(plane: object) -> None:
    """Sanity check this test's own precondition — CF-V3-E10-01/E10-02's
    real, already-published `silver_ods` model must exist for the gate to
    read a real `model_version` from."""
    metadata = PostgresMetadataDb(plane)
    published = metadata.get(ObjectType.ODS_MODEL, "silver_ods")
    assert published.version == 1


def test_a_batch_with_no_orphans_certifies_against_the_real_plane(rig) -> None:
    gate, ods, _control, metadata = rig
    ods.upsert_current_row(
        "Members",
        "OurId",
        {
            "OurId": 700001,
            "LinkId": "LNK-1",
            "IsActive": True,
            "RecordHash": "abc",
            "FeedName": "fidelis-downstate-roster",
            "SourceSystem": "fidelis",
            "CreatedBy": "test",
            "CreatedAt": NOW,
            "BatchId": BATCH_ID,
        },
    )

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id="fidelis-downstate-roster",
        model_version="1",
        relationships=(RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert outcome.certification.verdict is Verdict.CERTIFIED
    assert not outcome.held
    stored = metadata.get(ObjectType.ODS_BATCH_CERTIFICATION, BATCH_ID)
    assert stored.object_id == BATCH_ID


def test_a_real_orphaned_address_row_holds_publication(rig) -> None:
    gate, ods, control, _metadata = rig
    ods.insert_effective_dated_row(
        "Members_Addresses",
        {
            "AddressRecordId": "12345678-1234-1234-1234-123456789012",
            "OurId": 799999999,
            "SourceSystemId": "src-orphan",
            "EffectiveStartDate": "2026-01-01",
            "EffectiveEndDate": None,
            "IsActive": True,
            "RecordHash": "abc",
            "SourceSystem": "fidelis",
            "FeedName": "fidelis-downstate-roster",
            "CreatedBy": "test",
            "CreatedAt": NOW,
            "BatchId": BATCH_ID,
        },
    )

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id="fidelis-downstate-roster",
        model_version="1",
        relationships=(RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert outcome.certification.verdict is Verdict.NOT_CERTIFIED
    assert outcome.held
    assert control.get_batch(BATCH_ID).state is BatchState.BLOCKED
    errors = control.list_errors(batch_id=BATCH_ID)
    assert any(e.record_key == "799999999" for e in errors)
    (relationship_check,) = [
        c for c in outcome.certification.checks if c.kind.value == "relationship_integrity"
    ]
    assert "799999999" in relationship_check.evidence
    assert BATCH_ID in relationship_check.evidence
