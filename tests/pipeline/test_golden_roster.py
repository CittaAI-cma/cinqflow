"""CF-V0-E8-01 — the golden pipeline. The demo, as a test.

    "Given the Fidelis downstate roster feed is Published with schema v1 and
     mapping v1, when the monthly file lands, then Bronze and Silver Raw are
     loaded, five seeded bad rows sit in quarantine with reasons, and the batch
     shows Completed with counts at every stage."
    — CF-V0-E8-01, happy path

Layer 3 of the pyramid: a known input yields byte-exact expected outputs,
INCLUDING the exact quarantine rows and their reasons. Every test here runs on
the real Postgres plane inside a rolled-back transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.compiler import compile_feed
from cinqflow.core.landing import LandingOutcome
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, LandingFolder, Layer
from cinqflow.core.registry.contract import (
    ContractColumn,
    SchemaContract,
    Severity,
    not_null,
)
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.schema_spec import TypeName
from cinqflow.workers.pipeline import PipelineRunner

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

FEED = FeedRecord(
    feed_id="fidelis-downstate-roster",
    domain="enrollments",
    source_system="fidelis",
    file_format="csv",
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_CINQDOWNSTATE_Member_Roster_\d{6}\.csv",
    schedule_cron="0 3 1 * *",
    sample_filename="_CINQDOWNSTATE_Member_Roster_202608.csv",
    min_size_bytes=100,
    max_size_bytes=30_000_000,
)

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn(
            "source_member_id", TypeName.STRING, nullable=False, source_name="MemberID", is_phi=True
        ),
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn("last_name", TypeName.STRING, source_name="Last_Name", is_phi=True),
        ContractColumn("date_of_birth", TypeName.DATE, source_name="DOB", is_phi=True),
        ContractColumn("line_of_business", TypeName.STRING, source_name="LOB"),
    ),
    key_columns=("source_member_id",),
)

DQ_002 = not_null(
    "DQ-002",
    "first_name",
    name="Member First Name Not Null",
    severity=Severity.HIGH,
    description="Required for member outreach, care coordination and CMS submissions",
    glossary_id="BG-002",
)

PLAN = compile_feed(feed=FEED, feed_version=1, contract=CONTRACT, rules=(DQ_002,))
KEY = (
    "enrollments/fidelis_downstate/roster/incoming/2026-08-01/"
    "_CINQDOWNSTATE_Member_Roster_202608.csv"
)


def _roster(rows: int = 200, null_names: int = 5, bad_dates: int = 0) -> bytes:
    """A synthetic roster from the REAL layout. Zero member-derived values."""
    lines = ["MemberID,First_Name,Last_Name,DOB,LOB"]
    for index in range(1, rows + 1):
        first = "" if index <= null_names else f"FIRST{index:05d}"
        dob = "17530101" if null_names < index <= null_names + bad_dates else "19900101"
        lines.append(f"MBR{index:06d},{first},LAST{index:05d},{dob},MEDICAID")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def runner(plane: Connection) -> tuple[PipelineRunner, MemFsStorage, PostgresControlTables]:
    storage = MemFsStorage()
    control = PostgresControlTables(plane)
    compute = PostgresCompute(plane)
    return (
        PipelineRunner(storage=storage, control=control, compute=compute, source_system="fidelis"),
        storage,
        control,
    )


def _run(bundle, content: bytes = b"", key: str = KEY, **overrides):
    runner, storage, _ = bundle
    storage.place(key, content or _roster())
    file = next(f for f in storage.list_files("enrollments/") if f.key == key)
    return runner.run(
        file,
        feed=FEED,
        feed_version=1,
        contract=CONTRACT,
        rules=(DQ_002,),
        plan=PLAN,
        business_date="2026-08-01",
        **overrides,
    )


# ── happy path ───────────────────────────────────────────────────────────────
def test_the_roster_flows_landing_to_bronze_to_silver_raw_from_metadata(runner) -> None:
    """The whole point: the pipeline was GENERATED from a feed record and a
    contract. No code anywhere knows what a Fidelis roster is."""
    outcome = _run(runner)
    assert outcome.decision.outcome is LandingOutcome.ACCEPTED
    assert outcome.state is BatchState.COMPLETED
    assert outcome.stages_completed == (Layer.BRONZE, Layer.SILVER_RAW)


def test_five_seeded_bad_rows_sit_in_quarantine_with_reasons(runner) -> None:
    """ "five seeded bad rows sit in quarantine WITH REASONS" — CF-V0-E8-01"""
    outcome = _run(runner)
    assert outcome.result is not None
    assert len(outcome.result.quarantined) == 5
    assert {q.rule_id for q in outcome.result.quarantined} == {"DQ-002"}
    assert {q.reason for q in outcome.result.quarantined} == {"Member First Name Not Null"}


def test_the_counts_balance_at_every_stage(runner) -> None:
    """ "Row counts balance exactly at every stage: rows in = rows out +
    quarantined" — the measurable bar."""
    outcome = _run(runner)
    assert outcome.result is not None and outcome.result.balances
    assert outcome.result.reconciliation.explain() == (
        "200 in = 195 out + 5 Member First Name Not Null (DQ-002). Balanced."
    )


def test_bronze_holds_an_untouched_copy_and_silver_raw_holds_the_survivors(
    runner, plane: Connection
) -> None:
    """ "Keep Bronze as an untouched copy of the source — no edits, ever."

    Bronze keeps all 200 rows including the five that were quarantined, which
    is exactly what makes reprocessing possible six months later.
    """
    outcome = _run(runner)
    bronze = plane.fetch_one(
        "SELECT count(*) FROM bronze.members_raw WHERE batch_id = %s", (outcome.batch_id,)
    )
    silver = plane.fetch_one(
        "SELECT count(*) FROM silver_raw.members WHERE batch_id = %s", (outcome.batch_id,)
    )
    assert bronze == (200,)
    assert silver == (195,)


def test_the_control_rows_are_the_observable_behaviour(runner, plane: Connection) -> None:
    """A stage that ran but wrote no batch_stage_status row did not happen, as
    far as every screen, every recon query and every agent is concerned."""
    outcome = _run(runner)
    _, _, control = runner
    stages = control.get_stages(outcome.batch_id)
    assert {s.stage for s in stages} == {Layer.BRONZE, Layer.SILVER_RAW}
    silver = next(s for s in stages if s.stage is Layer.SILVER_RAW)
    assert (silver.records_in, silver.records_out, silver.attributed_drops) == (200, 195, 5)
    assert control.get_batch(outcome.batch_id).state is BatchState.COMPLETED


def test_the_drop_ledger_names_the_rule_and_reconciliation_balances(runner) -> None:
    _, _, control = runner
    outcome = _run(runner)
    (recon,) = control.get_reconciliation(outcome.batch_id)
    assert recon.balances is True
    assert [(d.rule_id, d.record_count) for d in recon.drop_ledger] == [("DQ-002", 5)]


def test_each_excluded_row_gets_one_deduplicated_error_row(runner) -> None:
    """The deterministic hash means five excluded rows produce five errors —
    not five, then ten after a reprocess."""
    _, _, control = runner
    outcome = _run(runner)
    errors = control.list_errors(outcome.batch_id)
    assert len(errors) == 5
    assert {e.rule_id for e in errors} == {"DQ-002"}


def test_the_quarantined_rows_are_stored_so_they_can_be_reprocessed(
    runner, plane: Connection
) -> None:
    """ "reprocess only the failed records" needs the records. Quarantine
    STORAGE holds them; no summary and no certified query tool can reach
    them."""
    outcome = _run(runner)
    row = plane.fetch_one(
        "SELECT count(*) FROM quarantine.quarantined_rows WHERE batch_id = %s",
        (outcome.batch_id,),
    )
    assert row == (5,)


def test_a_cast_failure_is_attributed_separately_from_a_dq_failure(runner) -> None:
    """22,000 = 21,820 + 175 (DQ-002) + 5 (structure) — two named reasons, not
    one bucket. Incident #8: legacy dates of 1753-01."""
    outcome = _run(runner, content=_roster(rows=200, null_names=5, bad_dates=3))
    assert outcome.result is not None
    ledger = {d.rule_id: d.record_count for d in outcome.result.reconciliation.drops}
    assert ledger == {"DQ-002": 5, "CAST-date_of_birth": 3}
    assert outcome.result.balances is True


# ── guardrail: never process twice ───────────────────────────────────────────
def test_the_same_file_dropped_twice_is_skipped_with_an_audit_entry(runner) -> None:
    """ "Given the same file is dropped into landing a second time, when the
    engine detects its fingerprint in input_registry, then the file is skipped
    as already processed, with an audit entry — IT IS NEVER LOADED TWICE."
    — CF-V0-E8-01, guardrail"""
    first = _run(runner)
    assert first.state is BatchState.COMPLETED

    second = _run(runner, key=KEY.replace("202608.csv", "202608_resent.csv"))
    assert second.decision.outcome is LandingOutcome.SKIPPED
    assert second.decision.audit_required is True
    assert second.batch_id is None


def test_a_replay_loads_no_additional_rows(runner, plane: Connection) -> None:
    """The guarantee behind the guardrail: Bronze does not grow."""
    _run(runner)
    before = plane.fetch_one("SELECT count(*) FROM bronze.members_raw")
    _run(runner, key=KEY.replace("202608.csv", "202608_again.csv"))
    assert plane.fetch_one("SELECT count(*) FROM bronze.members_raw") == before


# ── exception: restart from the last completed stage ─────────────────────────
def test_restart_resumes_at_silver_raw_without_reloading_bronze(runner, plane: Connection) -> None:
    """ "Given the run fails at the Silver Raw step, when an engineer restarts
    the batch, then processing resumes from Silver Raw only — BRONZE IS NOT
    RE-LOADED, no duplicates appear, and the restart is recorded on the batch."
    — CF-V0-E8-01, exception

    Bronze is append-only at the database layer, so a re-land would either
    duplicate the roster or be refused by the trigger. Both are defects.
    """
    first = _run(runner)
    bronze_after_first = plane.fetch_one(
        "SELECT count(*) FROM bronze.members_raw WHERE batch_id = %s", (first.batch_id,)
    )

    # An engineer restarts THE BATCH. The file is of course already in the
    # input registry — it arrived — so a restart that went through arrival
    # dedup could never run, which would make recovery impossible for exactly
    # the batches that need it.
    runner_obj, storage, _ = runner
    file = next(f for f in storage.list_files("enrollments/") if f.key.endswith("202608.csv"))
    resumed = runner_obj.run(
        file,
        feed=FEED,
        feed_version=1,
        contract=CONTRACT,
        rules=(DQ_002,),
        plan=PLAN,
        business_date="2026-08-01",
        resume_from=Layer.SILVER_RAW,
        batch_id=first.batch_id,
    )

    assert resumed.state is BatchState.COMPLETED
    assert (
        plane.fetch_one(
            "SELECT count(*) FROM bronze.members_raw WHERE batch_id = %s", (first.batch_id,)
        )
        == bronze_after_first
    ), "Bronze was re-loaded on restart"


# ── the landing outcomes, end to end ─────────────────────────────────────────
def test_a_truncated_file_is_rejected_with_a_stated_reason(runner) -> None:
    outcome = _run(runner, content=b"MemberID,First_Name,Last_Name,DOB,LOB\n")
    assert outcome.decision.outcome is LandingOutcome.REJECTED
    assert outcome.decision.check_name == "size_bounds"
    assert outcome.decision.move_to is LandingFolder.REJECTED


def test_an_unexpected_file_is_parked_and_still_registered(runner) -> None:
    outcome = _run(
        runner,
        key="enrollments/fidelis_downstate/roster/incoming/2026-08-01/MYSTERY_FILE.csv",
    )
    assert outcome.decision.outcome is LandingOutcome.UNEXPECTED
    assert outcome.decision.move_to is LandingFolder.PARKED
    assert outcome.decision.registered is True


def test_a_missing_required_column_blocks_the_batch_at_g2(runner) -> None:
    """Drift classified by MEANING: a missing non-nullable column breaks the
    mapping, so the batch stops rather than loading a roster with no ids."""
    without_ids = b"First_Name,Last_Name,DOB,LOB\n" + b"\n".join(
        f"FIRST{i},LAST{i},19900101,MEDICAID".encode() for i in range(1, 201)
    )
    outcome = _run(runner, content=without_ids)
    assert outcome.state is BatchState.FAILED
    assert outcome.drift_blocked
    assert "MemberID" in outcome.drift_blocked[0]


def test_a_failed_batch_records_why_it_failed(runner) -> None:
    """A batch that failed with no error row is a batch nobody can triage."""
    _, _, control = runner
    latin1 = (
        "MemberID,First_Name,Last_Name,DOB,LOB\n"
        + "\n".join(f"MBR{i:06d},JOSÉ,LAST{i},19900101,MEDICAID" for i in range(1, 201))
    ).encode("latin-1")
    outcome = _run(runner, content=latin1)
    assert outcome.state is BatchState.FAILED
    (error,) = control.list_errors(outcome.batch_id)
    assert error.category is ErrorCategory.FILE
    assert "not valid utf-8" in error.message


def test_bronze_still_refuses_mutation_during_a_real_run(runner, plane: Connection) -> None:
    """The guardrail holds when there is real data behind it, not only on an
    empty table."""
    outcome = _run(runner)
    with pytest.raises(psycopg.errors.CheckViolation, match="append-only"):
        plane.execute(
            "UPDATE bronze.members_raw SET feed_id = 'tampered' WHERE batch_id = %s",
            (outcome.batch_id,),
        )
    _ = datetime.now(UTC)


def test_a_member_sent_twice_in_one_file_is_an_attributed_drop_not_an_outage(
    runner,
) -> None:
    """A payer sending the same member twice is an ordinary delivery fault.

    Found by this suite: without in-batch deduplication the uniqueness
    constraint fires and ONE duplicated member fails a 22,000-row roster. The
    right behaviour is FIG 10's — "dedup precedence logged; losing value
    retained in history": first occurrence wins, the loser is quarantined with
    its reason, and the ledger balances.
    """
    lines = ["MemberID,First_Name,Last_Name,DOB,LOB"]
    for index in range(1, 101):
        lines.append(f"MBR{index:06d},FIRST{index},LAST{index},19900101,MEDICAID")
    lines.append("MBR000042,FIRST42,LAST42,19900101,MEDICAID")  # the same member, again
    outcome = _run(runner, content=("\n".join(lines) + "\n").encode())

    assert outcome.state is BatchState.COMPLETED, "one duplicate must not fail a roster"
    assert outcome.result is not None
    ledger = {d.rule_id: d.record_count for d in outcome.result.reconciliation.drops}
    assert ledger == {"DUPLICATE-source_member_id": 1}
    assert outcome.result.balances is True
    assert "already appears in this batch" in outcome.result.quarantined[0].reason


def test_the_first_occurrence_wins_and_reaches_silver_raw(runner, plane: Connection) -> None:
    """Precedence is not arbitrary: the first row is loaded, the later one is
    quarantined, and the quarantined row is still recoverable from Bronze."""
    lines = [
        "MemberID,First_Name,Last_Name,DOB,LOB",
        "MBR000042,ORIGINAL,LAST,19900101,MEDICAID",
        "MBR000042,LATER,LAST,19900101,MEDICAID",
    ]
    outcome = _run(runner, content=("\n".join(lines) + "\n").encode())
    row = plane.fetch_one(
        "SELECT first_name FROM silver_raw.members WHERE batch_id = %s", (outcome.batch_id,)
    )
    assert row == ("ORIGINAL",)
    assert plane.fetch_one(
        "SELECT count(*) FROM bronze.members_raw WHERE batch_id = %s", (outcome.batch_id,)
    ) == (2,), "both rows are in Bronze — the loser stays recoverable"
