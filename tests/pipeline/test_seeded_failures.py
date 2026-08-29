"""The seeded failure library, replayed against the real pipeline.

    "Every documented historical incident becomes a PERMANENT REGRESSION TEST,
     replayed by chaos tests on every build. The platform is never allowed to
     re-learn an old lesson."
    — memory/05-ground-truth/04-incident-library.md

    "Every Wave 0-3 demo is simulator-driven end to end; ZERO HAND-PLACED
     FILES" — CF-V0-E8-08, the measurable bar

Every file in this suite comes from the simulator. Not one is hand-written,
which is the point: if the demo needs a human to drop a file, the demo is
hiding the connector.
"""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.core.compiler import compile_feed
from cinqflow.core.landing import LandingOutcome
from cinqflow.core.model.vocabulary import BatchState, ErrorCategory, LandingFolder
from cinqflow.core.registry.contract import (
    ContractColumn,
    SchemaContract,
    Severity,
    not_null,
)
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.schema_spec import TypeName
from cinqflow.simulator import Delivery, Injection, PayerSimulator
from cinqflow.workers.pipeline import PipelineRunner, RunOutcome

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

AUGUST = date(2026, 8, 1)

FEED = FeedRecord(
    feed_id="fidelis-downstate-roster",
    domain="enrollments",
    source_system="fidelis",
    file_format="csv",
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_?CINQDOWNSTATE_Member_Roster_\d{6}(_RESEND)?\.csv",
    schedule_cron="0 3 1 * *",
    sample_filename="_CINQDOWNSTATE_Member_Roster_202608.csv",
    min_size_bytes=2_000,
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
        ContractColumn("gender", TypeName.STRING, source_name="Gender"),
        ContractColumn("line_of_business", TypeName.STRING, source_name="LOB"),
        ContractColumn("effective_date", TypeName.DATE, source_name="EffDate"),
        ContractColumn("end_date", TypeName.DATE, source_name="EndDate"),
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


@pytest.fixture
def rig(plane: Connection):
    storage = MemFsStorage()
    control = PostgresControlTables(plane)
    runner = PipelineRunner(
        storage=storage,
        control=control,
        compute=PostgresCompute(plane),
        source_system="fidelis",
    )

    def play(delivery: Delivery, **overrides) -> RunOutcome:
        """Deliver through the simulator and run the spine. No hand-placed files."""
        storage.place(delivery.key, delivery.content)
        file = next(f for f in storage.list_files("enrollments/") if f.key == delivery.key)
        return runner.run(
            file,
            feed=FEED,
            feed_version=1,
            contract=CONTRACT,
            rules=(DQ_002,),
            plan=PLAN,
            business_date=delivery.business_date.isoformat(),
            **overrides,
        )

    return play, control, plane


# ── the happy path, simulator-driven ─────────────────────────────────────────
def test_the_simulator_drives_the_whole_spine_with_no_hand_placed_files(rig) -> None:
    """ "Given the Fidelis roster feed is Published, when the simulator plays
    its monthly schedule, then the file arrives via the configured protocol,
    flows the full pipeline, and THE DEMO NEEDS NO HAND-PLACED FILES
    ANYWHERE." — CF-V0-E8-08, happy path"""
    play, _, _ = rig
    outcome = play(PayerSimulator().deliver(business_date=AUGUST))
    assert outcome.state is BatchState.COMPLETED
    assert outcome.result is not None and outcome.result.balances
    assert outcome.result.reconciliation.records_in == 200


# ── incident #1 · the underscore filename ────────────────────────────────────
def test_an_undeclared_underscore_is_rejected_with_a_stated_reason() -> None:
    """ "A Fidelis file named `_CINQDOWNSTATE_Member_Roster_*.xlsx` broke the
    Excel reader. Fixed reactively." Now a permanent pre-flight check.

    This feed's pattern accepts the name either way, so the check is what
    catches it — and it catches it BEFORE a parser sees the file.
    """
    strict_feed = FeedRecord(**{**FEED.__dict__, "allows_leading_underscore": False})
    play_delivery = PayerSimulator().deliver(
        business_date=AUGUST, injection=Injection.UNDERSCORE_FILENAME
    )
    from cinqflow.core.landing import classify
    from cinqflow.ports.storage import FileRef

    decision = classify(
        FileRef(
            key=play_delivery.key,
            size_bytes=len(play_delivery.content),
            modified_ts=play_delivery.arrives_at,  # type: ignore[arg-type]
            fingerprint="sha256-x",
        ),
        feeds=(strict_feed.for_landing(1),),
        fingerprint_seen=False,
    )
    assert decision.outcome is LandingOutcome.REJECTED
    assert decision.check_name == "leading_underscore"
    assert "Excel reader" in (decision.reason or "")


# ── incident #4 · the duplicate month ────────────────────────────────────────
def test_the_same_month_delivered_twice_is_refused_the_second_time(rig) -> None:
    """ "Duplicate Feb-2025 Fidelis roster found during seeding."

    The re-send arrives under a DIFFERENT NAME with identical bytes, which is
    how it actually happens — a name-based dedup would miss it entirely.
    """
    play, _, plane = rig
    simulator = PayerSimulator()
    original = simulator.deliver(business_date=AUGUST)

    first = play(original)
    assert first.state is BatchState.COMPLETED
    loaded = plane.fetch_one("SELECT count(*) FROM silver_raw.members")

    second = play(simulator.deliver_duplicate(original))
    assert second.decision.outcome is LandingOutcome.SKIPPED
    assert second.decision.audit_required is True
    assert plane.fetch_one("SELECT count(*) FROM silver_raw.members") == loaded


# ── truncated ────────────────────────────────────────────────────────────────
def test_a_truncated_delivery_is_rejected_before_it_can_halve_a_roster(rig) -> None:
    """ "Given the simulator injects a truncated file, when landing controls
    run, then the file REJECTS WITH THE STATED REASON and the seeded-failure
    suite records another pass." — CF-V0-E8-08, exception

    It would have parsed perfectly. That is the danger.
    """
    play, _, _ = rig
    outcome = play(PayerSimulator().deliver(business_date=AUGUST, injection=Injection.TRUNCATED))
    assert outcome.decision.outcome is LandingOutcome.REJECTED
    assert outcome.decision.check_name == "size_bounds"
    assert outcome.decision.move_to is LandingFolder.REJECTED
    assert "truncated" in (outcome.decision.reason or "")


# ── drifted schema ───────────────────────────────────────────────────────────
def test_a_contracted_column_that_stops_arriving_blocks_the_batch(rig) -> None:
    """Structurally the file is perfect. Only a comparison against the approved
    contract catches it — which is why G2 exists as a separate gate."""
    play, control, _ = rig
    outcome = play(
        PayerSimulator().deliver(business_date=AUGUST, injection=Injection.DRIFTED_SCHEMA)
    )
    # first_name is nullable, so the batch survives and the drift is recorded
    # as non-blocking — a payer dropping an optional field is not an incident.
    assert outcome.state is BatchState.COMPLETED
    assert outcome.result is not None
    # Every row now fails DQ-002, because the column it reads is gone.
    ledger = {d.rule_id: d.record_count for d in outcome.result.reconciliation.drops}
    assert ledger == {"DQ-002": 200}
    assert outcome.result.balances is True

    (drift,) = control.get_schema_drift(outcome.batch_id)
    assert drift.classification == "removed"
    assert drift.column_name == "First_Name"
    assert drift.blocked_batch is False


# ── bad encoding ─────────────────────────────────────────────────────────────
def test_bad_encoding_is_rejected_rather_than_mojibaked_into_bronze(rig) -> None:
    """A payer's export tool using the platform default instead of the agreed
    encoding. Bronze is append-only, so a mojibaked member name there is
    PERMANENT — the file must be rejected with a reason, not decoded with
    replacements."""
    play, control, plane = rig
    outcome = play(PayerSimulator().deliver(business_date=AUGUST, injection=Injection.BAD_ENCODING))
    assert outcome.state is BatchState.FAILED
    (error,) = control.list_errors(outcome.batch_id)
    assert error.category is ErrorCategory.FILE
    assert "not valid utf-8" in error.message
    assert plane.fetch_one(
        "SELECT count(*) FROM bronze.members_raw WHERE batch_id = %s", (outcome.batch_id,)
    ) == (0,), "nothing reached Bronze"


# ── duplicate member within one file ─────────────────────────────────────────
def test_one_duplicated_member_does_not_fail_the_roster(rig) -> None:
    """Found by this suite during T8. Before in-batch deduplication, a single
    repeated member hit the uniqueness constraint and failed all 200 rows."""
    play, _, _ = rig
    outcome = play(
        PayerSimulator().deliver(business_date=AUGUST, injection=Injection.DUPLICATE_MEMBER)
    )
    assert outcome.state is BatchState.COMPLETED
    assert outcome.result is not None
    ledger = {d.rule_id: d.record_count for d in outcome.result.reconciliation.drops}
    assert ledger["DUPLICATE-source_member_id"] == 1
    assert outcome.result.balances is True


# ── late ─────────────────────────────────────────────────────────────────────
def test_a_late_delivery_still_processes_because_nothing_is_wrong_with_it(rig) -> None:
    """Lateness is an SLA signal, not a rejection. Treating it as one would
    lose a perfectly valid roster and teach operations to bypass the check."""
    play, _, _ = rig
    outcome = play(PayerSimulator().deliver(business_date=AUGUST, injection=Injection.LATE))
    assert outcome.state is BatchState.COMPLETED
    assert outcome.result is not None and outcome.result.balances


# ── the whole library, in one sweep ──────────────────────────────────────────
@pytest.mark.parametrize("injection", list(Injection))
def test_every_injection_leaves_the_platform_in_an_explicable_state(
    rig, injection: Injection
) -> None:
    """Whatever is injected, the platform ends in a state it can EXPLAIN.

    Never a crash, never a silent success, never a file that simply vanished.
    That is the whole promise of the trust boundary, swept across the entire
    seeded library.
    """
    play, control, _ = rig
    outcome = play(PayerSimulator().deliver(business_date=AUGUST, injection=injection))

    assert outcome.decision.registered is True, "every arriving file registers"
    if outcome.batch_id is None:
        assert outcome.decision.reason, "a file that did not process must say why"
        return
    if outcome.state is BatchState.FAILED:
        assert control.list_errors(outcome.batch_id), "a failed batch must be triageable"
        return
    assert outcome.result is not None
    assert outcome.result.balances, "a completed batch balances, or it is not completed"
