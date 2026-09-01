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

from cinqflow.core.identity import CrosswalkEntry, MatchOutcome
from cinqflow.core.identity.telemetry import CoverageSnapshot, ParityCheckSummary
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
    IdentityRequestLogEntry,
    IdentityResponseLogEntry,
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


def test_a_model_version_starts_unset(opened: ControlTablesPort) -> None:
    assert opened.get_batch(BATCH).model_version is None


def test_recording_the_model_version_stamps_the_batch(opened: ControlTablesPort) -> None:
    """ "Every batch records the model version it loaded into" — FIG 10.
    CF-V3-E8-05 stamps this once the batch's rows actually land."""
    opened.record_model_version(BATCH, "1")
    assert opened.get_batch(BATCH).model_version == "1"


def test_recording_the_model_version_on_a_missing_batch_is_a_named_failure(
    control: ControlTablesPort,
) -> None:
    with pytest.raises(BatchNotFoundError):
        control.record_model_version("does-not-exist", "1")


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


def test_batch_inputs_are_scoped_to_the_batch_not_the_feed_s_whole_history(
    opened: ControlTablesPort,
) -> None:
    """The batch drawer's Inputs tab has a batch_id, never a feed_id — it must
    be answerable from that alone. A prior file from the SAME feed, landed
    under a different batch, must not appear."""
    opened.register_input_file(
        InputFile(
            batch_id=BATCH,
            feed_id=FEED,
            key="incoming/2026-08-01/roster.xlsx",
            filename="roster.xlsx",
            size_bytes=1024,
            fingerprint="sha256-this-batch",
            state=FileState.ACCEPTED,
            arrived_ts=NOW,
        )
    )
    opened.register_input_file(
        InputFile(
            batch_id="8841",
            feed_id=FEED,
            key="incoming/2026-07-25/roster.xlsx",
            filename="roster.xlsx",
            size_bytes=1024,
            fingerprint="sha256-a-different-batch",
            state=FileState.ACCEPTED,
            arrived_ts=NOW - timedelta(days=7),
        )
    )
    (found,) = opened.list_batch_inputs(BATCH)
    assert found.fingerprint == "sha256-this-batch"


def test_an_empty_or_unknown_batch_has_no_inputs_not_an_error(
    control: ControlTablesPort,
) -> None:
    assert control.list_batch_inputs("no-such-batch") == ()


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
    """Newest first, asserted on ORDER rather than on the whole list.

    The assertion used to be `== ["8843", "8842"]`, which is a claim that the
    feed has exactly two batches ever. That is true of an empty plane and false
    of a real one: this suite runs against the same Postgres the pipeline
    commits to, and the moment that plane carried real batches for this feed a
    correct implementation started failing. What the contract actually promises
    is descending order and that both batches appear — so that is what is
    checked, and the check now holds however much history the feed has.
    """
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
    batches = list(opened.list_batches(FEED))
    listed = [b.batch_id for b in batches]
    assert {"8843", "8842"} <= set(listed), listed
    # The newer batch comes FIRST. Stated as a relative position rather than an
    # index, because a real plane may hold other batches for this feed between
    # or around these two, and their presence is not a defect.
    assert listed.index("8843") < listed.index("8842"), listed
    started = [b.started_ts for b in batches]
    assert started == sorted(started, reverse=True), "list_batches must be newest first"


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


def test_a_read_back_timestamp_is_the_same_instant_and_the_same_wall_clock_hour(
    control: ControlTablesPort,
) -> None:
    """THE REGRESSION FOR A REAL DEFECT, found while wiring the SLA worker
    against a live plane whose session timezone was `Asia/Kolkata`.

    Comparing aware datetimes as absolute instants — `==`, `<`, `>` — is
    correct regardless of which timezone a value happens to be rendered in.
    But `strftime` is not a comparison: `cycle.why(now)` renders "expected
    6:00 AM — not received", and a session that hands back `11:30` for a row
    stored as `06:00 UTC` makes every sentence an operator reads wrong, even
    though every STATUS the platform computed from it was right the whole
    time. `hour == 6` is the assertion that catches what `==` against another
    UTC-aware datetime cannot: the actual number a person would read.
    """
    control.upsert_sla_instance(cycle(expected_ts=datetime(2026, 8, 1, 6, tzinfo=UTC)))
    (found,) = control.sla_instances(cycle_date=CYCLE_DAY)
    assert found.expected_ts.utcoffset() == timedelta(0)
    assert found.expected_ts.hour == 6


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


# ── recon.rule_results · CF-V2-E7-05 ─────────────────────────────────────────
def _rule_result(rule_id: str, *, failed: int = 0, excluded: int = 0, minute: int = 0):
    from cinqflow.ports.control_tables import RuleResult

    return RuleResult(
        batch_id=BATCH,
        feed_id=FEED,
        rule_id=rule_id,
        evaluated=200,
        failed=failed,
        excluded=excluded,
        recorded_ts=NOW + timedelta(minutes=minute),
    )


def test_a_clean_pass_is_a_row_not_an_absence(opened: ControlTablesPort) -> None:
    """ "Record every rule's execution result on every batch, including clean
    passes — silence is data too." — CF-V2-E7-05"""
    opened.record_rule_result(_rule_result("DQ-002"))
    (result,) = opened.rule_results(BATCH)
    assert result.clean is True
    assert result.evaluated == 200
    assert result.pass_rate == 1.0


def test_a_rerun_records_again_and_reads_fold_to_the_newest(
    opened: ControlTablesPort,
) -> None:
    """Append-only like every ledger — the first run's verdict survives, and
    the current answer is the newest row per (batch, rule)."""
    opened.record_rule_result(_rule_result("DQ-002", failed=40, excluded=40))
    opened.record_rule_result(_rule_result("DQ-002", failed=0, minute=30))
    (result,) = opened.rule_results(BATCH)
    assert result.clean is True


def test_the_history_is_newest_first_and_bounded(opened: ControlTablesPort) -> None:
    for minute in range(5):
        opened.record_rule_result(_rule_result(f"DQ-{minute:03d}", minute=minute))
    history = opened.rule_result_history(FEED, limit=3)
    assert [r.rule_id for r in history] == ["DQ-004", "DQ-003", "DQ-002"]


def test_a_feed_with_no_rule_runs_is_empty_not_an_error(control: ControlTablesPort) -> None:
    assert control.rule_result_history("a-feed-nobody-onboarded") == ()


# ── the identity stage · CF-V3-E9-01 ─────────────────────────────────────────


def _crosswalk_entry(
    *, source_member_id: str = "M-1", outcome: MatchOutcome = MatchOutcome.RESOLVED
) -> CrosswalkEntry:
    return CrosswalkEntry(
        source_system="fidelis",
        source_member_id=source_member_id,
        internal_member_id=f"internal-{source_member_id}",
        verato_person_id="verato-1" if outcome is MatchOutcome.RESOLVED else None,
        batch_id=BATCH,
        outcome=outcome,
    )


def test_a_recorded_request_and_response_are_stored_with_their_hashes(
    opened: ControlTablesPort,
) -> None:
    """ "Store full request and response payloads with hashes ... the audit
    trail is the design's core." — CF-V3-E9-01. Storage is write-only from
    this pin's own contract (no read verb is required by the story); what
    matters here is that recording twice — a request AND its matching
    response — never refuses."""
    request_id = str(uuid.uuid4())
    opened.record_identity_request(
        IdentityRequestLogEntry(
            request_id=request_id,
            batch_id=BATCH,
            source_system="fidelis",
            source_member_id="M-1",
            payload={"first_name": "Jane", "last_name": "Doe"},
            payload_hash="deadbeef",
            sent_ts=NOW,
        )
    )
    opened.record_identity_response(
        IdentityResponseLogEntry(
            response_id=str(uuid.uuid4()),
            request_id=request_id,
            batch_id=BATCH,
            payload={"outcome": "resolved", "verato_person_id": "verato-1"},
            payload_hash="cafebabe",
            outcome=MatchOutcome.RESOLVED,
            received_ts=NOW,
        )
    )


def test_recording_the_same_crosswalk_entry_twice_is_the_idempotency_guarantee(
    opened: ControlTablesPort,
) -> None:
    """ "Given the same input arrives twice ... it is safely skipped — data is
    never duplicated." The port's own upsert makes the SECOND write a no-op
    on content, which is what lets a worker call this unconditionally rather
    than checking first."""
    opened.record_crosswalk(_crosswalk_entry())
    opened.record_crosswalk(_crosswalk_entry())
    assert len(opened.list_crosswalk(BATCH)) == 1


def test_get_crosswalk_answers_the_workers_own_idempotency_check(
    opened: ControlTablesPort,
) -> None:
    assert (
        opened.get_crosswalk(source_system="fidelis", source_member_id="M-1", batch_id=BATCH)
        is None
    )
    opened.record_crosswalk(_crosswalk_entry())
    entry = opened.get_crosswalk(source_system="fidelis", source_member_id="M-1", batch_id=BATCH)
    assert entry is not None
    assert entry.outcome is MatchOutcome.RESOLVED


def test_list_crosswalk_is_g4s_own_accounting_source(opened: ControlTablesPort) -> None:
    opened.record_crosswalk(_crosswalk_entry(source_member_id="M-1"))
    opened.record_crosswalk(
        _crosswalk_entry(source_member_id="M-2", outcome=MatchOutcome.UNRESOLVED)
    )
    entries = opened.list_crosswalk(BATCH)
    assert {e.source_member_id for e in entries} == {"M-1", "M-2"}
    assert opened.list_crosswalk("a-batch-nobody-ran") == ()


# ── coverage and parity telemetry · CF-V3-E9-04 ──────────────────────────────


def _coverage(
    *, source_system: str = "fidelis", business_date: str = "2026-08-31", total: int = 100
) -> CoverageSnapshot:
    return CoverageSnapshot(
        source_system=source_system,
        business_date=business_date,
        total=total,
        with_link_id=total,
        with_our_id=total,
        with_both=total,
    )


def test_recording_the_same_days_coverage_twice_corrects_rather_than_duplicates(
    control: ControlTablesPort,
) -> None:
    """ "Given the same input arrives twice ... it is safely skipped." """
    control.record_coverage_snapshot(_coverage(total=100))
    control.record_coverage_snapshot(_coverage(total=97))
    (row,) = control.coverage_history("fidelis")
    assert row.total == 97


def test_coverage_history_is_newest_first_and_scoped_to_its_source(
    control: ControlTablesPort,
) -> None:
    control.record_coverage_snapshot(_coverage(business_date="2026-08-29"))
    control.record_coverage_snapshot(_coverage(business_date="2026-08-30"))
    control.record_coverage_snapshot(_coverage(source_system="optum", business_date="2026-08-30"))

    history = control.coverage_history("fidelis")
    assert [row.business_date for row in history] == ["2026-08-30", "2026-08-29"]


def test_coverage_history_for_a_source_never_recorded_is_empty_not_an_error(
    control: ControlTablesPort,
) -> None:
    assert control.coverage_history("a-source-nobody-fed") == ()


def _parity(
    *, source_system: str = "fidelis", business_date: str = "2026-08-31", mismatched: int = 0
) -> ParityCheckSummary:
    return ParityCheckSummary(
        source_system=source_system,
        business_date=business_date,
        checked=100,
        matched=100 - mismatched,
        mismatched=mismatched,
    )


def test_recording_the_same_days_parity_check_twice_corrects_rather_than_duplicates(
    control: ControlTablesPort,
) -> None:
    control.record_parity_check(_parity(mismatched=0))
    control.record_parity_check(_parity(mismatched=3))
    (row,) = control.parity_check_history("fidelis")
    assert row.mismatched == 3


def test_parity_check_history_is_newest_first_and_scoped_to_its_source(
    control: ControlTablesPort,
) -> None:
    control.record_parity_check(_parity(business_date="2026-08-29"))
    control.record_parity_check(_parity(business_date="2026-08-30"))
    control.record_parity_check(_parity(source_system="optum", business_date="2026-08-30"))

    history = control.parity_check_history("fidelis")
    assert [row.business_date for row in history] == ["2026-08-30", "2026-08-29"]
