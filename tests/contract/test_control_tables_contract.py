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

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, FileState, Layer
from cinqflow.ports.control_tables import (
    CONTROL_TABLES,
    BatchControl,
    BatchNotFoundError,
    ControlTableError,
    ControlTablesPort,
    DropLedgerEntry,
    ErrorRecord,
    FeedSlaConfig,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    SchemaDrift,
    SlaAlert,
    SlaCycle,
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


def test_schema_drift_is_recorded_whether_or_not_it_blocked_the_batch(
    control: ControlTablesPort,
) -> None:
    """schema_drift_log is a governance record, not only an incident log: an
    ADDED column that never blocked anything is still drift a steward should
    be able to see."""
    control.record_schema_drift(
        SchemaDrift(
            batch_id=BATCH,
            feed_id=FEED,
            classification="added",
            column_name="middle_name",
            detail="'middle_name' is in the file but not under contract — ignored, not dropped",
            blocked_batch=False,
            detected_ts=NOW,
        )
    )
    control.record_schema_drift(
        SchemaDrift(
            batch_id=BATCH,
            feed_id=FEED,
            classification="removed",
            column_name="MemberID",
            detail="'MemberID' is under contract but absent from the file",
            blocked_batch=True,
            detected_ts=NOW,
        )
    )
    drift = control.get_schema_drift(BATCH)
    assert {d.column_name: d.blocked_batch for d in drift} == {
        "middle_name": False,
        "MemberID": True,
    }


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


# ── the SLA clock's three tables ─────────────────────────────────────────────
#
# CF-V2-E12-01. Wave 0 declared eleven control tables and wrote eight; these
# verbs write the other three. Every assertion below is a property `workers/sla`
# depends on, and the whole point of putting them in the ONE contract suite is
# that `mock` and `pg-control` must answer identically — a clock that is
# idempotent in memory and not on Postgres is a clock nobody can trust.

CYCLE_DAY = date(2026, 8, 1)


def sla_config(**overrides: Any) -> FeedSlaConfig:
    base: dict[str, Any] = {
        "feed_id": FEED,
        "feed_version": 1,
        "domain": "enrollment",
        "source_system": "fidelis",
        "file_format": "xlsx",
        "landing_path": "enrollment/fidelis_downstate/",
        "file_pattern": r"_CINQDOWNSTATE_Member_Roster_.*\.xlsx",
        "schedule_cron": "0 6 * * *",
        "expected_file_count": 1,
        "grace_period_minutes": 30,
        "created_ts": NOW,
    }
    base.update(overrides)
    return FeedSlaConfig(**base)


def cycle(**overrides: Any) -> SlaCycle:
    base: dict[str, Any] = {
        "feed_id": FEED,
        "cycle_date": CYCLE_DAY,
        "expected_ts": datetime(2026, 8, 1, 6, tzinfo=UTC),
        "sla_status": "Delayed",
    }
    base.update(overrides)
    return SlaCycle(**base)


def test_a_feeds_delivery_contract_round_trips(control: ControlTablesPort) -> None:
    control.upsert_feed_sla_config(sla_config())
    found = control.feed_sla_configs(feed_ids=[FEED])
    assert [c.feed_id for c in found] == [FEED]
    assert found[0].schedule_cron == "0 6 * * *"
    assert found[0].grace_period_minutes == 30


def test_re_publishing_a_feed_updates_its_contract_rather_than_duplicating_it(
    control: ControlTablesPort,
) -> None:
    """Primary key is (feed_id, feed_version). A payer moving their delivery
    from 06:00 to 05:00 amends the row; it does not grow a second one."""
    control.upsert_feed_sla_config(sla_config())
    control.upsert_feed_sla_config(sla_config(schedule_cron="0 5 * * *"))

    found = control.feed_sla_configs(feed_ids=[FEED])
    assert len(found) == 1
    assert found[0].schedule_cron == "0 5 * * *"


def test_a_new_version_of_a_feed_is_a_second_contract(control: ControlTablesPort) -> None:
    """Version 2 does not overwrite version 1: a batch that ran under the old
    schedule must still be judged against the window it was actually owed.

    The new version names a DIFFERENT file pattern — "no two active feeds
    claim the same landing path and pattern" is a documented CF-V0-E3-01
    don't, enforced as a database constraint that does not exempt two
    versions of the same feed. A version bump that changes nothing about
    what arrives is not a version bump; it is the same contract republished.
    """
    control.upsert_feed_sla_config(sla_config())
    control.upsert_feed_sla_config(
        sla_config(feed_version=2, file_pattern=r"_CINQDOWNSTATE_Member_Roster_v2_.*\.xlsx")
    )
    assert len(control.feed_sla_configs(feed_ids=[FEED])) == 2


def test_a_materialised_cycle_is_readable_for_its_day(control: ControlTablesPort) -> None:
    control.upsert_sla_instance(cycle())
    found = control.sla_instances(cycle_date=CYCLE_DAY)
    assert [c.feed_id for c in found] == [FEED]
    assert found[0].expected_ts == datetime(2026, 8, 1, 6, tzinfo=UTC)


def test_materialising_the_same_cycle_twice_writes_one_row(control: ControlTablesPort) -> None:
    """UNIQUE (feed_id, cycle_date) is the worker's idempotency guarantee: it
    can run on any cadence, restart mid-run, or be replayed by a chaos test
    without producing a second expectation."""
    control.upsert_sla_instance(cycle())
    control.upsert_sla_instance(cycle())
    assert len(control.sla_instances(cycle_date=CYCLE_DAY)) == 1


def test_the_clock_never_erases_an_arrival(control: ControlTablesPort) -> None:
    """ARRIVAL IS RECORDED BY THE PIPELINE, NOT BY THE CLOCK.

    The sharpest property in this suite. A worker re-materialising today's
    cycles must not blank the `actual_ts` a file landing wrote ten minutes ago
    — that would make a delivered feed look missing, on the busiest screen in
    the product.
    """
    control.upsert_sla_instance(cycle())
    landed = datetime(2026, 8, 1, 6, 12, tzinfo=UTC)
    control.record_sla_arrival(
        feed_id=FEED, cycle_date=CYCLE_DAY, actual_ts=landed, batch_id=BATCH, status="On-Time"
    )

    control.upsert_sla_instance(cycle())

    found = control.sla_instances(cycle_date=CYCLE_DAY)
    assert found[0].actual_ts == landed
    assert found[0].batch_id == BATCH
    assert found[0].sla_status == "On-Time"


def test_cycles_can_be_narrowed_to_the_feeds_a_person_may_see(
    control: ControlTablesPort,
) -> None:
    """Scope filtering happens in the QUERY. A board that fetched everything
    and filtered afterwards would put out-of-scope feed names in a response
    body before anybody checked."""
    control.upsert_sla_instance(cycle())
    control.upsert_sla_instance(cycle(feed_id="uhc-md-daily"))

    assert len(control.sla_instances(cycle_date=CYCLE_DAY)) == 2
    narrowed = control.sla_instances(cycle_date=CYCLE_DAY, feed_ids=[FEED])
    assert [c.feed_id for c in narrowed] == [FEED]


def test_a_feeds_arrival_history_is_newest_first_and_bounded(
    control: ControlTablesPort,
) -> None:
    """The reliability trend reads this. Newest first because "how has it been
    lately" is the question, and bounded because ninety days of a 15-minute ADT
    feed is 8,640 rows nobody asked for."""
    for offset in range(5):
        control.upsert_sla_instance(
            cycle(
                cycle_date=CYCLE_DAY - timedelta(days=offset),
                expected_ts=datetime(2026, 8, 1, 6, tzinfo=UTC) - timedelta(days=offset),
            )
        )
    history = control.sla_history(FEED, days=3)
    assert [c.cycle_date for c in history] == [
        CYCLE_DAY,
        CYCLE_DAY - timedelta(days=1),
        CYCLE_DAY - timedelta(days=2),
    ]


def test_history_for_a_feed_that_never_ran_is_empty_not_an_error(
    control: ControlTablesPort,
) -> None:
    assert control.sla_history("a-feed-nobody-onboarded", days=30) == ()


def test_an_alert_is_recorded_with_its_citations(control: ControlTablesPort) -> None:
    """An alert whose facts cannot be opened is the context-free alert
    CF-V2-E12-05 exists to abolish."""
    control.upsert_sla_instance(cycle())
    control.record_sla_alert(
        SlaAlert(
            alert_id=str(uuid.uuid4()),
            feed_id=FEED,
            cycle_date=CYCLE_DAY,
            severity="critical",
            summary="fidelis-downstate-roster: expected 6:00 AM — not received",
            citations=("feed:fidelis-downstate-roster",),
            raised_ts=NOW,
        )
    )
    alerts = control.sla_alerts(cycle_date=CYCLE_DAY)
    assert len(alerts) == 1
    assert alerts[0].citations == ("feed:fidelis-downstate-roster",)
    assert alerts[0].acknowledged_by == ""


def test_an_alert_about_a_cycle_that_was_never_materialised_is_refused(
    control: ControlTablesPort,
) -> None:
    """An alert about a cycle that does not exist is a bug in the caller, not
    a row worth writing."""
    with pytest.raises(ControlTableError):
        control.record_sla_alert(
            SlaAlert(
                alert_id="al-orphan",
                feed_id="no-such-feed",
                cycle_date=CYCLE_DAY,
                severity="critical",
                summary="not received",
                raised_ts=NOW,
            )
        )


def test_acknowledging_an_alert_names_the_person_and_the_moment(
    control: ControlTablesPort,
) -> None:
    control.upsert_sla_instance(cycle())
    alert_id = str(uuid.uuid4())
    control.record_sla_alert(
        SlaAlert(
            alert_id=alert_id,
            feed_id=FEED,
            cycle_date=CYCLE_DAY,
            severity="critical",
            summary="not received",
            raised_ts=NOW,
        )
    )
    seen_at = NOW + timedelta(minutes=4)
    control.acknowledge_alert(alert_id, by="sam@cinqcare.test", at=seen_at)

    alerts = control.sla_alerts(cycle_date=CYCLE_DAY)
    assert alerts[0].acknowledged_by == "sam@cinqcare.test"
    assert alerts[0].acknowledged_ts == seen_at


def test_acknowledging_an_alert_nobody_raised_is_refused(control: ControlTablesPort) -> None:
    with pytest.raises(ControlTableError):
        control.acknowledge_alert(str(uuid.uuid4()), by="sam@cinqcare.test", at=NOW)
