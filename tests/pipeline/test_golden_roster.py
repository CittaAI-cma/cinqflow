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

from datetime import UTC, date, datetime

import psycopg
import pytest

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.landing import LandingOutcome
from cinqflow.core.mapping import (
    FeedMapping,
    MappingLine,
    Transform,
    TransformKind,
    UnlistedCode,
    from_governed,
    mapping_as_governed,
)
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import (
    ActorType,
    BatchState,
    ErrorCategory,
    LandingFolder,
    Layer,
)
from cinqflow.core.registry.golden_fidelis import CONTRACT, DQ_002, FEED, PLAN, landing_key
from cinqflow.core.registry.golden_fidelis import roster_csv as _roster
from cinqflow.core.schema_spec import TypeName
from cinqflow.workers.pipeline import PipelineRunner

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

KEY = landing_key("2026-08-01")

#: W1-30's mapping fixtures. Separate actors from `test_mapping_on_the_real_plane.py`
#: on purpose — this file is proving the MAP step, not mapping storage, and a
#: shared constant would make it look like the same concern.
MAPPING_BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
MAPPING_STEWARD = Actor(
    subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ola"
)
MAPPING_NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)


def _fidelis_mapping(*, on_unlisted: UnlistedCode = UnlistedCode.SUBSTITUTE) -> FeedMapping:
    """Every CONTRACT column, mapped — one CAST, one LOOKUP, the rest DIRECT.

    Deliberately covers the whole contract: a line silently missing would
    leave a column always empty, which would be a fixture bug wearing the
    costume of a pipeline bug.
    """
    return FeedMapping(
        feed_id=FEED.feed_id,
        version=1,
        contract_version=CONTRACT.version,
        lines=(
            MappingLine(
                target_entity="members",
                target_field="source_member_id",
                source_columns=("MemberID",),
            ),
            MappingLine(
                target_entity="members", target_field="first_name", source_columns=("First_Name",)
            ),
            MappingLine(
                target_entity="members", target_field="last_name", source_columns=("Last_Name",)
            ),
            MappingLine(
                target_entity="members",
                target_field="date_of_birth",
                source_columns=("DOB",),
                transform=Transform(kind=TransformKind.CAST, target_type=TypeName.DATE),
            ),
            MappingLine(
                target_entity="members",
                target_field="line_of_business",
                source_columns=("LOB",),
                transform=Transform(
                    kind=TransformKind.LOOKUP,
                    lookup=(("MEDICAID", "Medicaid"),),
                    on_unlisted=on_unlisted,
                    default_value="Other",
                ),
            ),
        ),
    )


def _publish_mapping(plane: Connection, mapping: FeedMapping) -> FeedMapping:
    """DRAFT -> PENDING_REVIEW -> APPROVED -> PUBLISHED, the one real
    lifecycle every governed object travels — then read back through
    `from_governed`, exactly as `cinqflow ingest` would load it. Proving the
    round trip matters here: the object `PipelineRunner.run` receives must be
    the one the store actually holds, not the Python value the test built."""
    store = PostgresMetadataDb(plane)
    draft = store.save(mapping_as_governed(mapping, author=MAPPING_BA, created_ts=MAPPING_NOW))
    for target, actor in (
        (LifecycleState.PENDING_REVIEW, MAPPING_BA),
        (LifecycleState.APPROVED, MAPPING_STEWARD),
        (LifecycleState.PUBLISHED, MAPPING_STEWARD),
    ):
        draft, entry = draft.transition_to(target, actor=actor, now=MAPPING_NOW)
        draft = store.record_transition(draft, entry)
    published = store.get(ObjectType.MAPPING, FEED.feed_id)
    assert published.is_executable, "the fixture must publish, or this test proves nothing"
    return from_governed(published)


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

    _, _, control = runner
    (drift,) = control.get_schema_drift(outcome.batch_id)
    assert drift.classification == "removed"
    assert drift.column_name == "MemberID"
    assert drift.blocked_batch is True


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


# ── W1-30: the published mapping governs the MAP step ────────────────────────
#
#     "FeedMapping.apply_to is called nowhere in production code, only in its
#      own unit test. Until this lands, a published mapping is decorative."
#      — W1-30
#
# The rest of this file never publishes a mapping, so every test above it is
# ALSO this slab's regression suite: if the wiring below broke a feed with no
# published FeedMapping, those tests — not these — would be the ones to fail.


def test_a_published_mapping_governs_the_map_step(runner, plane: Connection) -> None:
    """The richer taxonomy applies at RUNTIME now, not only in
    `test_mapping_taxonomy.py`: a LOOKUP translates `line_of_business`, and a
    CAST line still lands `date_of_birth` as a real date — proving the
    contract's own caster still owns the arithmetic, exactly as
    `core.mapping.apply`'s docstring insists it must."""
    mapping = _publish_mapping(plane, _fidelis_mapping())
    outcome = _run(runner, mapping=mapping)

    assert outcome.state is BatchState.COMPLETED
    assert outcome.result is not None
    # The same 5 seeded null-name rows, still caught the same way: the
    # mapping's `first_name` line is a plain DIRECT read of `First_Name`, so
    # DQ-002 sees exactly what it always saw.
    assert outcome.result.reconciliation.explain() == (
        "200 in = 195 out + 5 Member First Name Not Null (DQ-002). Balanced."
    )
    row = plane.fetch_one(
        "SELECT line_of_business, date_of_birth FROM silver_raw.members "
        "WHERE batch_id = %s AND source_member_id = %s",
        (outcome.batch_id, "MBR000006"),
    )
    assert row == ("Medicaid", date(1990, 1, 1)), (
        "the LOOKUP line never ran, or the CAST line's value never reached the contract's caster"
    )


def test_with_no_published_mapping_the_bare_rename_path_is_unchanged(
    runner, plane: Connection
) -> None:
    """THE acceptance criterion: a feed with no published FeedMapping — still
    the common case, since nothing publishes one automatically — must behave
    BYTE-IDENTICALLY to how it behaved before W1-30. No mapping is published
    in this test at all."""
    outcome = _run(runner)

    assert outcome.state is BatchState.COMPLETED
    assert outcome.result is not None
    assert outcome.result.reconciliation.explain() == (
        "200 in = 195 out + 5 Member First Name Not Null (DQ-002). Balanced."
    )
    row = plane.fetch_one(
        "SELECT line_of_business, date_of_birth FROM silver_raw.members "
        "WHERE batch_id = %s AND source_member_id = %s",
        (outcome.batch_id, "MBR000006"),
    )
    assert row == ("MEDICAID", date(1990, 1, 1)), (
        "the contract's bare rename must be untouched when no mapping is published"
    )


def test_a_mapping_rejection_routes_to_quarantine_not_a_silent_drop(
    runner, plane: Connection
) -> None:
    """`FeedMapping.apply_to`'s own contract: the FIRST rejecting line wins,
    and the row is attributed — not silently dropped. Reuses the exact
    quarantine path a CAST failure already uses, so a mapping-refused row is
    just as recoverable as any other attributed exclusion."""
    mapping = _publish_mapping(plane, _fidelis_mapping(on_unlisted=UnlistedCode.REJECT_ROW))
    lines = ["MemberID,First_Name,Last_Name,DOB,LOB"]
    for index in range(1, 5):
        lines.append(f"MBR{index:06d},FIRST{index},LAST{index},19900101,MEDICAID")
    lines.append("MBR000005,FIRST5,LAST5,19900101,TRICARE")  # not in the lookup table
    outcome = _run(runner, content=("\n".join(lines) + "\n").encode(), mapping=mapping)

    assert outcome.state is BatchState.COMPLETED, "one refused row must not fail the whole batch"
    assert outcome.result is not None
    assert outcome.result.balances is True
    assert outcome.result.reconciliation.records_in == 5
    assert outcome.result.reconciliation.records_out == 4
    assert outcome.result.reconciliation.attributed_drops == 1
    (dropped,) = outcome.result.quarantined
    assert dropped.rule_id == "MAPPING-members.line_of_business"
    assert "line_of_business" in dropped.reason  # attributed, not a bare drop with no reason
    assert "TRICARE" not in dropped.reason, "the payer's value must never land in a log message"

    quarantined_row = plane.fetch_one(
        "SELECT count(*) FROM quarantine.quarantined_rows WHERE batch_id = %s AND rule_id = %s",
        (outcome.batch_id, "MAPPING-members.line_of_business"),
    )
    assert quarantined_row == (1,), "the refused row must be stored, so it can be reprocessed"
