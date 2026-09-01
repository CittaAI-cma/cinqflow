"""CF-V3-E10-03 — the ODS certification gate, against the mock plane.

"Happy path — Given an enrollment batch loads ODS cleanly, when the
 gate runs, then relationships validate, certification attaches,
 consumers are notified, and the batch appears in their views
 atomically."
"Exception — Given 0.3% of claims reference members absent from the
 member table, when the gate runs, then publication holds, the
 orphaned claims are listed with their source batches, and the
 incident flow picks it up with the evidence attached."
"Guardrail — Given an approval is attempted by the author or an
 unauthorized role, when they approve, then the system blocks it and
 records the attempt."
— CF-V3-E10-03
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.notification import ConsoleNotification
from cinqflow.adapters.mock.ods_load import MemOdsLoad
from cinqflow.core.certification import Verdict
from cinqflow.core.lifecycle import ApprovalRoutingError, approve
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType, SelfApprovalError
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer
from cinqflow.core.relationship_integrity import Relationship
from cinqflow.core.variance import Variance, VarianceKind
from cinqflow.ports.control_tables import BatchControl
from cinqflow.workers.incidents import IncidentWorker
from cinqflow.workers.ods_certification import OdsCertificationGate

pytestmark = pytest.mark.unit

BATCH_ID = "batch-8842"
FEED_ID = "fidelis-downstate-roster"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
AUTHOR = Actor(subject="cinqflow.ods_certification", actor_type=ActorType.SYSTEM, display_name="")
STEWARD = Actor(subject="steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Dana")
STEWARD_ROLES = frozenset({Role.DATA_STEWARD})

_RELATIONSHIP = Relationship(
    child_entity="Members_Addresses",
    child_column="OurId",
    parent_entity="Members",
    parent_column="OurId",
)


def _record_balanced_reconciliation(control: MemStoreControlTables, *, stage: Layer) -> None:
    control.record_reconciliation(_reconciliation(stage=stage, records_in=10, records_out=10))


def _reconciliation(*, stage: Layer, records_in: int, records_out: int, drops: tuple = ()):
    from cinqflow.ports.control_tables import DropLedgerEntry, Reconciliation

    return Reconciliation(
        batch_id=BATCH_ID,
        stage=stage,
        records_in=records_in,
        records_out=records_out,
        quarantined=0,
        attributed_drops=records_in - records_out,
        drop_ledger=tuple(
            DropLedgerEntry(rule_id=d.rule_id, reason=d.reason, record_count=d.record_count)
            for d in drops
        ),
    )


@pytest.fixture
def rig():
    from cinqflow.adapters.mock.metadata_db import MemMetadataDb

    ods = MemOdsLoad()
    control = MemStoreControlTables()
    metadata = MemMetadataDb()
    notify = ConsoleNotification()
    control.open_batch(
        BatchControl(
            batch_id=BATCH_ID,
            feed_id=FEED_ID,
            feed_version=1,
            business_date="2026-09-01",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )
    incidents = IncidentWorker(control=control, metadata=metadata)
    gate = OdsCertificationGate(
        ods=ods, control=control, metadata=metadata, incidents=incidents, notify=notify
    )
    return gate, ods, control, metadata, notify


def test_a_clean_batch_certifies_and_drafts_a_governed_record(rig) -> None:
    gate, ods, control, metadata, _ = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert not outcome.held
    assert outcome.certification.verdict is Verdict.CERTIFIED
    assert outcome.draft is not None
    assert outcome.draft.lifecycle_state is LifecycleState.PENDING_REVIEW
    stored = metadata.get(ObjectType.ODS_BATCH_CERTIFICATION, BATCH_ID)
    assert stored.object_id == BATCH_ID


def test_an_orphaned_relationship_holds_publication(rig) -> None:
    """ "Downstream never sees an uncertified batch." """
    gate, ods, control, metadata, _ = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.insert_effective_dated_row(
        "Members_Addresses",
        {
            "AddressRecordId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "OurId": 999999,
            "SourceSystemId": "src-1",
            "EffectiveEndDate": None,
            "BatchId": BATCH_ID,
        },
    )

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert outcome.held
    assert outcome.certification.verdict is Verdict.NOT_CERTIFIED
    with pytest.raises(Exception, match="ods_batch_certification"):
        # No draft was ever saved for a held batch.
        metadata.get(ObjectType.ODS_BATCH_CERTIFICATION, BATCH_ID)


def test_a_held_batch_is_blocked_and_opens_one_incident(rig) -> None:
    gate, ods, control, _metadata, _notify = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.insert_effective_dated_row(
        "Members_Addresses",
        {
            "AddressRecordId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "OurId": 888888,
            "SourceSystemId": "src-1",
            "EffectiveEndDate": None,
            "BatchId": BATCH_ID,
        },
    )

    gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert control.get_batch(BATCH_ID).state is BatchState.BLOCKED
    errors = control.list_errors(batch_id=BATCH_ID)
    assert any(e.rule_id == "CERT-RELATIONSHIP_INTEGRITY" for e in errors)
    assert any(e.record_key == "888888" for e in errors)


def test_no_reconciliation_yet_is_pending_not_a_failure(rig) -> None:
    """A batch mid-flight is neither certified nor uncertified —
    `certify()`'s own PENDING verdict, reached here with zero new code."""
    gate, ods, _control, _metadata, _notify = rig
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert outcome.certification.verdict is Verdict.PENDING
    assert outcome.held


def test_a_relationship_with_zero_rows_passes_trivially(rig) -> None:
    """Members_Addresses has never been loaded for this batch (no live
    source yet, CF-V3-E8-05) — "0 rows checked" is a real, honest pass,
    never a fabricated claim about data that does not exist."""
    gate, ods, control, _metadata, _notify = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert outcome.certification.verdict is Verdict.CERTIFIED
    (relationship_check,) = [
        c for c in outcome.certification.checks if c.kind.value == "relationship_integrity"
    ]
    assert "0 row(s) checked" in relationship_check.evidence


def test_the_author_may_not_approve_their_own_certification(rig) -> None:
    """The universal negative, inherited from `core.lifecycle.approve` —
    never re-implemented here, only exercised, exactly as E10-01 proved
    for `ODS_MODEL`."""
    gate, ods, control, _metadata, _notify = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )
    with pytest.raises(SelfApprovalError):
        approve(outcome.draft, actor=AUTHOR, roles=STEWARD_ROLES, comment="looks fine", now=NOW)


def test_only_a_data_steward_may_approve_a_certification(rig) -> None:
    gate, ods, control, _metadata, _notify = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )
    with pytest.raises(ApprovalRoutingError):
        approve(
            outcome.draft,
            actor=STEWARD,
            roles=frozenset({Role.PLATFORM_ENGINEER}),
            comment="looks fine",
            now=NOW,
        )


def test_a_data_steward_may_approve_a_clean_certification(rig) -> None:
    gate, ods, control, _metadata, _notify = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )
    approved, _entry = approve(
        outcome.draft, actor=STEWARD, roles=STEWARD_ROLES, comment="Evidence reviewed.", now=NOW
    )
    assert approved.lifecycle_state is LifecycleState.APPROVED
    assert approved.approved_by == STEWARD


def test_notify_consumers_alerts_when_there_are_consumers(rig) -> None:
    gate, _, _, _, notify = rig
    gate.notify_consumers(("CMS Quality Report",), batch_id=BATCH_ID, entity="Members")
    assert len(notify.dispatched) == 1
    assert "CMS Quality Report" in notify.dispatched[0].detail


def test_notify_consumers_is_silent_when_nobody_consumes_it(rig) -> None:
    gate, _, _, _, notify = rig
    gate.notify_consumers((), batch_id=BATCH_ID, entity="Members")
    assert notify.dispatched == []


# ── CF-V3-E13-02 — the financial/member-universe packs attach here ──────────


def test_an_open_financial_variance_holds_publication_even_with_clean_relationships(rig) -> None:
    """ "The pack attaches to the month's certification" — proven by
    seeding exactly what `core.financial_reconciliation.financial_variance`
    would have written to the SAME ledger the manual variance route uses,
    with no change to how the gate reads relationships or reconciliation."""
    gate, ods, control, metadata, _ = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    variance = Variance(
        variance_id="var-fin-1",
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        kind=VarianceKind.FINANCIAL,
        expected=Decimal(22000),
        actual=Decimal(21952),
        tolerance=Decimal(10),
        opened_by="cinqflow.reconciliation_packs",
        opened_ts=NOW,
        explanation="paid for fidelis, professional, 2026-08: netted to 21952 against 22000.",
    )
    metadata.record_variance_event(
        variance, actor_subject="cinqflow.reconciliation_packs", occurred_ts=NOW
    )

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert outcome.held
    assert outcome.certification.verdict is Verdict.NOT_CERTIFIED
    assert variance in outcome.certification.blocking


def test_a_corrected_financial_variance_no_longer_holds_publication(rig) -> None:
    gate, ods, control, metadata, _ = rig
    _record_balanced_reconciliation(control, stage=Layer.SILVER_ODS)
    ods.upsert_current_row("Members", "OurId", {"OurId": 1, "BatchId": BATCH_ID})
    variance = Variance(
        variance_id="var-fin-2",
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        kind=VarianceKind.FINANCIAL,
        expected=Decimal(22000),
        actual=Decimal(21952),
        tolerance=Decimal(10),
        opened_by="cinqflow.reconciliation_packs",
        opened_ts=NOW,
    )
    corrected = variance.correct(by="steward@cinqcare.test", note="duplicate file removed")
    metadata.record_variance_event(
        corrected, actor_subject="steward@cinqcare.test", occurred_ts=NOW
    )

    outcome = gate.run(
        batch_id=BATCH_ID,
        feed_id=FEED_ID,
        model_version="1",
        relationships=(_RELATIONSHIP,),
        author=AUTHOR,
        now=NOW,
    )

    assert not outcome.held
    assert outcome.certification.verdict is Verdict.CERTIFIED
