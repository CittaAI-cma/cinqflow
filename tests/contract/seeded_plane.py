"""The seeded plane every intelligence contract test runs against.

One feed, one contract, one rule set, one completed batch that balances — and a
CANARY string threaded through every free-text field that a careless projection
could leak. Shared rather than duplicated so that a tool which starts returning
member data fails in the catalogue test AND in the agent test, in one run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, FileState, Layer
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.registry.contract import ContractColumn, DqRule, SchemaContract, Severity
from cinqflow.core.schema_spec import TypeName
from cinqflow.ports.control_tables import (
    BatchControl,
    DropLedgerEntry,
    ErrorRecord,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    StageStatus,
)

#: If this string reaches a tool result, a member reached a model.
CANARY = "CANARY-MEMBER-JOSE-MARIA-8675309"

FEED_ID = "fidelis-downstate-roster"
BATCH_ID = "8842"
NOW = datetime(2026, 8, 29, 3, 14, tzinfo=UTC)
AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")
REVIEWER = Actor(subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")


def _publish(store: MemMetadataDb, obj: Any) -> None:
    reviewed, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=AUTHOR)
    approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=REVIEWER)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=REVIEWER)
    store.save(published)


def build_plane() -> tuple[MemMetadataDb, MemStoreControlTables]:
    """A plane whose every free-text field carries the canary."""
    store = MemMetadataDb()
    control = MemStoreControlTables()

    _publish(
        store,
        feed_registry.FeedRecord(
            feed_id=FEED_ID,
            domain="membership",
            source_system="fidelis",
            file_format="xlsx",
            landing_path="landing/fidelis/roster",
            file_pattern=r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$",
            schedule_cron="0 6 * * 1",
            sample_filename="_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
        ).as_governed(author=AUTHOR),
    )
    _publish(
        store,
        contract_registry.contract_as_governed(
            SchemaContract(
                feed_id=FEED_ID,
                version=1,
                columns=(
                    ContractColumn(name="source_member_id", type=TypeName.STRING, nullable=False,
                                   is_phi=True),
                    ContractColumn(name="date_of_birth", type=TypeName.DATE, is_phi=True),
                    ContractColumn(name="plan_code", type=TypeName.STRING),
                ),
                key_columns=("source_member_id",),
            ),
            author=AUTHOR,
        ),
    )
    _publish(
        store,
        contract_registry.rules_as_governed(
            FEED_ID,
            (
                DqRule(
                    rule_id="DQ-002",
                    name="date_of_birth present",
                    description="Every member must carry a date of birth.",
                    severity=Severity.CRITICAL,
                    columns=("date_of_birth",),
                ),
            ),
            author=AUTHOR,
        ),
    )

    control.open_batch(
        BatchControl(
            batch_id=BATCH_ID, feed_id=FEED_ID, feed_version=1, business_date="2026-08-01",
            state=BatchState.COMPLETED, started_ts=NOW, completed_ts=NOW,
        )
    )
    control.record_stage(
        StageStatus(
            batch_id=BATCH_ID, stage=Layer.SILVER_RAW, state=BatchState.COMPLETED,
            started_ts=NOW, completed_ts=NOW, records_in=22_000, records_out=21_820,
            quarantined=175, attributed_drops=5,
        )
    )
    control.record_reconciliation(
        Reconciliation(
            batch_id=BATCH_ID, stage=Layer.SILVER_RAW, records_in=22_000, records_out=21_820,
            quarantined=175, attributed_drops=5,
            drop_ledger=(
                DropLedgerEntry(rule_id="DQ-002", reason="missing date_of_birth",
                                record_count=175, financial_impact=Decimal("0")),
                DropLedgerEntry(rule_id="STRUCTURE-001", reason="short row", record_count=5),
            ),
        )
    )
    control.record_quarantine(
        QuarantineSummary(
            batch_id=BATCH_ID, stage=Layer.SILVER_RAW, rule_id="DQ-002",
            reason="missing date_of_birth", column_names=("date_of_birth",), record_count=175,
        )
    )
    # The error message and the record key both carry the canary. If either
    # reaches a result, a member reached a model.
    control.record_error(
        ErrorRecord(
            error_id_hash="a1b2c3d4", batch_id=BATCH_ID, stage=Layer.SILVER_RAW,
            category=ErrorCategory.VALIDATION,
            message="row failed DQ-002", occurred_ts=NOW,
            record_key=CANARY, rule_id="DQ-002",
        )
    )
    control.register_input_file(
        InputFile(
            batch_id=BATCH_ID, feed_id=FEED_ID,
            key=f"landing/fidelis/roster/incoming/{CANARY}.xlsx",
            filename="_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
            size_bytes=4_100_000, fingerprint="f00d", state=FileState.PROCESSED,
            arrived_ts=NOW, record_count=22_000,
        )
    )
    return store, control
