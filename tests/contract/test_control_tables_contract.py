"""The ONE contract suite for the `control_tables` pin.

memstore today; the Postgres `control` schema at rung 0.5; Delta via SQL
Warehouse at the target. All three run THIS file.

    "One join key — batch_id — threads arrival, execution, failure and
     reconciliation."
    — docs/architecture/plates/07-control-table-and-governed-object-model.md

For an engine story the control rows ARE the observable behaviour, so the
writes are specified first and the reads are specified in terms of them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, FileState, Layer
from cinqflow.ports.control_tables import (
    CONTROL_TABLES,
    BatchControl,
    BatchNotFoundError,
    ControlTablesPort,
    DropLedgerEntry,
    ErrorRecord,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    StageStatus,
)

from .conftest import adapters_for

NOW = datetime(2026, 8, 1, 3, 14, tzinfo=UTC)
BATCH = "8842"
FEED = "fidelis-downstate-roster"

pytestmark = pytest.mark.contract


@pytest.fixture(params=adapters_for("control_tables"))
def control(request: pytest.FixtureRequest, make: Callable[..., Any]) -> ControlTablesPort:
    return make(request.param)


@pytest.fixture
def opened(control: ControlTablesPort) -> ControlTablesPort:
    control.open_batch(
        BatchControl(
            batch_id=BATCH,
            feed_id=FEED,
            feed_version=1,
            business_date="2026-08-01",
            state=BatchState.RECEIVED,
            started_ts=NOW,
        )
    )
    return control


def test_there_are_exactly_eleven_control_tables() -> None:
    """A twelfth is an architecture change, not a migration."""
    assert len(CONTROL_TABLES) == 11
    assert "batch_control" in CONTROL_TABLES
    assert "batch_reconciliation" in CONTROL_TABLES


def test_a_batch_is_retrievable_by_the_one_join_key(opened: ControlTablesPort) -> None:
    batch = opened.get_batch(BATCH)
    assert (batch.feed_id, batch.feed_version, batch.state) == (FEED, 1, BatchState.RECEIVED)


def test_a_missing_batch_is_a_named_failure_not_none(control: ControlTablesPort) -> None:
    """Returning None would be checked inconsistently, and the one call site
    that forgot would treat "no such batch" as "batch with no rows"."""
    with pytest.raises(BatchNotFoundError):
        control.get_batch("does-not-exist")


def test_stage_status_carries_the_terms_of_the_balance_equation(
    opened: ControlTablesPort,
) -> None:
    """records_in / out / quarantined / attributed_drops are not statistics —
    they are what reconciliation checks."""
    opened.record_stage(
        StageStatus(
            batch_id=BATCH,
            stage=Layer.SILVER_RAW,
            state=BatchState.COMPLETED,
            started_ts=NOW,
            completed_ts=NOW,
            records_in=22_000,
            records_out=21_820,
            quarantined=175,
            attributed_drops=5,
        )
    )
    (stage,) = opened.get_stages(BATCH)
    assert stage.records_in == stage.records_out + stage.quarantined + stage.attributed_drops


def test_recording_the_same_stage_twice_updates_it_rather_than_duplicating(
    opened: ControlTablesPort,
) -> None:
    """Restart re-records a stage. Two rows for one stage would double every
    count a screen or a recon query reads."""
    for records_out in (0, 21_820):
        opened.record_stage(
            StageStatus(
                batch_id=BATCH,
                stage=Layer.SILVER_RAW,
                state=BatchState.COMPLETED,
                started_ts=NOW,
                records_in=22_000,
                records_out=records_out,
            )
        )
    stages = opened.get_stages(BATCH)
    assert len(stages) == 1
    assert stages[0].records_out == 21_820


def test_a_fingerprint_seen_before_is_findable__that_is_exactly_once(
    control: ControlTablesPort,
) -> None:
    """ "the same file presented twice is skipped, with an audit entry"

    This lookup IS the mechanism. Incident #4: a duplicate Feb-2025 Fidelis
    roster was found during seeding.
    """
    fingerprint = "sha256-deadbeef"
    assert control.find_input_by_fingerprint(fingerprint) is None
    control.register_input_file(
        InputFile(
            batch_id=BATCH,
            feed_id=FEED,
            key="incoming/2026-08-01/roster.xlsx",
            filename="roster.xlsx",
            size_bytes=1024,
            fingerprint=fingerprint,
            state=FileState.ACCEPTED,
            arrived_ts=NOW,
        )
    )
    seen = control.find_input_by_fingerprint(fingerprint)
    assert seen is not None and seen.state is FileState.ACCEPTED


def test_an_unexpected_file_is_registered_not_ignored(control: ControlTablesPort) -> None:
    """ "Register every file the moment it arrives — even unexpected ones, which
    are logged and parked, not silently ignored." — CF-V0-E8-02"""
    control.register_input_file(
        InputFile(
            batch_id=None,
            feed_id=None,
            key="incoming/2026-08-01/mystery.csv",
            filename="mystery.csv",
            size_bytes=12,
            fingerprint="sha256-mystery",
            state=FileState.RECEIVED,
            arrived_ts=NOW,
        )
    )
    unexpected = control.find_input_by_fingerprint("sha256-mystery")
    assert unexpected is not None
    assert unexpected.is_unexpected is True


def test_the_error_hash_makes_replay_idempotent_at_the_error_level(
    control: ControlTablesPort,
) -> None:
    """The deterministic error hash is the quiet hero.

    "reprocessing a corrected batch cannot manufacture duplicate incidents —
    which is what allows 'reprocess only the failed records' to exist as a
    safe, ordinary button."
    """
    error = ErrorRecord(
        error_id_hash="9f3c1a7b",
        batch_id=BATCH,
        stage=Layer.SILVER_RAW,
        category=ErrorCategory.VALIDATION,
        message="First_Name is null",
        occurred_ts=NOW,
        record_key="MBR000042",
        rule_id="DQ-002",
    )
    control.record_error(error)
    control.record_error(error)
    assert len(control.list_errors(BATCH)) == 1


def test_errors_are_filterable_by_category(control: ControlTablesPort) -> None:
    for index, category in enumerate((ErrorCategory.FILE, ErrorCategory.VALIDATION)):
        control.record_error(
            ErrorRecord(
                error_id_hash=f"hash-{index}",
                batch_id=BATCH,
                stage=Layer.LANDING,
                category=category,
                message="a failure",
                occurred_ts=NOW,
            )
        )
    assert len(control.list_errors(BATCH, ErrorCategory.FILE)) == 1
    assert len(control.list_errors(BATCH)) == 2


def test_quarantine_is_reported_as_counts_and_reasons_never_rows(
    control: ControlTablesPort,
) -> None:
    """ "counts, reasons, rule ids, column names — NEVER row contents"

    This matters most at rung 3, where quarantine holds real data. The type has
    nowhere to put a member, which is why it cannot leak one.
    """
    control.record_quarantine(
        QuarantineSummary(
            batch_id=BATCH,
            stage=Layer.SILVER_RAW,
            rule_id="DQ-002",
            reason="Member First Name Not Null",
            column_names=("first_name",),
            record_count=175,
        )
    )
    (summary,) = control.get_quarantine_summary(BATCH)
    assert summary.record_count == 175
    for member_field in ("rows", "records", "payload", "values", "sample"):
        assert not hasattr(summary, member_field)


def test_reconciliation_knows_whether_it_balances_and_by_how_much(
    control: ControlTablesPort,
) -> None:
    """ "Zero unexplained records across all transitions, every batch"."""
    recon = Reconciliation(
        batch_id=BATCH,
        stage=Layer.SILVER_RAW,
        records_in=22_000,
        records_out=21_820,
        quarantined=175,
        attributed_drops=5,
        drop_ledger=(
            DropLedgerEntry(rule_id="DQ-002", reason="First_Name is null", record_count=175),
            DropLedgerEntry(rule_id="STRUCTURE", reason="column count mismatch", record_count=5),
        ),
    )
    control.record_reconciliation(recon)
    (stored,) = control.get_reconciliation(BATCH)
    assert stored.balances is True
    assert stored.unexplained == 0


def test_an_unbalanced_reconciliation_reports_the_gap_rather_than_hiding_it() -> None:
    """ "an unexplained difference is a defect, never a footnote"."""
    recon = Reconciliation(
        batch_id=BATCH,
        stage=Layer.SILVER_RAW,
        records_in=22_000,
        records_out=21_820,
        quarantined=0,
        attributed_drops=0,
    )
    assert recon.balances is False
    assert recon.unexplained == 180


def test_batches_list_newest_first_for_a_feed(opened: ControlTablesPort) -> None:
    opened.open_batch(
        BatchControl(
            batch_id="8843",
            feed_id=FEED,
            feed_version=1,
            business_date="2026-09-01",
            state=BatchState.RECEIVED,
            started_ts=datetime(2026, 9, 1, 3, 14, tzinfo=UTC),
        )
    )
    assert [b.batch_id for b in opened.list_batches(FEED)] == ["8843", "8842"]


def test_the_port_offers_no_generic_sql_escape_hatch(control: ControlTablesPort) -> None:
    """A port with `execute(sql)` lets a dialect leak into the core, and the
    core is the one place engine SQL is forbidden."""
    for escape in ("execute", "raw", "sql", "cursor", "connection"):
        assert not hasattr(control, escape), f"control_tables exposes {escape}"
