"""Wave 2's stories, executed as their own worked examples.

Every assertion here quotes a line from `CINQFLOW_User_Stories_Final.docx`. This
file is the RED step for the Wave-2 reconciliation: it was written against the
MERGED surface before that surface existed, so the imports failing is the first
thing it proves and the last thing it should ever prove again.

WHY THIS FILE IS SEPARATE FROM `tests/unit`. A unit test asks "does this
function behave". These ask "does the platform do what the client was promised",
in the client's own numbers — 12/10/1/1, 214 records, `BH-AF-002`, $48,000, five
ADT feeds, one grouped alert. When one of these fails, a commitment broke, not
an implementation detail. Keeping them together means the wave's exit demo is a
test run rather than a script somebody performs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from cinqflow.core.certification import Check, CheckKind, Verdict, certify, evidence_document
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import (
    ActorType,
    BatchState,
    ErrorCategory,
    Layer,
    StatusWord,
)
from cinqflow.core.operations import ArrivalCondition, Expectation
from cinqflow.core.operations.actions import (
    ALLOWED_STATES,
    ActionPhase,
    ActionRequest,
    Environment,
    OpsAction,
    RefusedError,
    authorize,
    offered,
    request_action,
    verify,
)
from cinqflow.core.operations.fingerprint import (
    Incident,
    IncidentState,
    IncidentTransitionError,
    PriorIncident,
    RecoveryGuide,
    fingerprint_batch,
    normalise,
    signature,
)
from cinqflow.core.operations.monitor import ErrorRole, separate_cascade
from cinqflow.core.operations.recovery import (
    DoubleLoadError,
    ReplayMode,
    backdate,
    reprocess_batch,
    reprocess_failed_only,
    restart_from,
)
from cinqflow.core.reliability import Score, Signal, Weights, score_for
from cinqflow.core.sla import ArrivalBoard, Cycle, SlaStatus, alerts_for, grouped
from cinqflow.core.variance import Variance, VarianceError, VarianceKind, Waiver
from cinqflow.ports.control_tables import ErrorRecord

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
T0 = datetime(2026, 8, 30, 3, 3, tzinfo=UTC)
SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")


def error(
    hash_: str,
    stage: Layer,
    message: str,
    ts: datetime,
    *,
    record_key: str | None = None,
    rule_id: str | None = None,
) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=hash_,
        batch_id="1244",
        stage=stage,
        category=ErrorCategory.SYSTEM,
        message=message,
        occurred_ts=ts,
        record_key=record_key,
        rule_id=rule_id,
    )


# ══ CF-V2-E12-01 · the arrival board ════════════════════════════════════════
#
#   "Given twelve feeds expected today, ten received, one missing, one at risk,
#    when an operator opens the home page, then the counters read 12/10/1/1,
#    the missing UHC file leads the attention list with 'expected 6:00 AM — not
#    received'."


def board() -> ArrivalBoard:
    return ArrivalBoard(
        cycles=(
            Cycle("uhc_md_daily", date(2026, 8, 30), datetime(2026, 8, 30, 6, tzinfo=UTC)),
            Cycle(
                "fidelis_roster",
                date(2026, 8, 30),
                datetime(2026, 8, 30, 5, tzinfo=UTC),
                actual_ts=datetime(2026, 8, 30, 5, 12, tzinfo=UTC),
                files_received=1,
            ),
            Cycle("optum_ny", date(2026, 8, 30), datetime(2026, 8, 30, 8, 50, tzinfo=UTC)),
        ),
        now=NOW,
    )


def test_the_counters_read_expected_received_missing_at_risk() -> None:
    counters = board().counters()
    assert (counters["expected"], counters["received"]) == (3, 1)
    assert counters["missing"] == 1
    # Past its expected time but still inside grace. This is the counter the
    # morning routine is actually for — it lets somebody act BEFORE the
    # deadline rather than explain afterwards.
    assert counters["at_risk"] == 1


def test_the_missing_file_says_expected_6_00_am_not_received() -> None:
    assert board().cycles[0].why(NOW) == "expected 6:00 AM — not received"


def test_a_late_but_arrived_file_is_not_missing() -> None:
    arrived = board().cycles[1]
    assert arrived.user_status(NOW) is StatusWord.RECEIVED
    # Inside grace, so On-Time. Collapsing grace into the expected time is how
    # a platform manufactures its own alert fatigue on day one.
    assert arrived.status(NOW) is SlaStatus.ON_TIME


def test_the_attention_list_ranks_by_business_impact_not_by_timestamp() -> None:
    ranked = board().needs_attention(harm={"optum_ny": 9, "uhc_md_daily": 2})
    assert ranked[0].feed_id == "optum_ny"


def test_a_file_that_landed_after_its_deadline_reads_received_but_records_breached() -> None:
    """The board and the control table hold two DIFFERENT true facts.

    An operator looking at the morning board wants to know the file is here.
    `sla_instance.sla_status` has to record that it arrived outside its window,
    or the feed's reliability trend quietly forgets every late delivery that
    eventually turned up. One expectation answers both, so they cannot drift.
    """
    expectation = Expectation(
        feed_id="uhc_md_daily",
        domain="enrollment",
        business_date="2026-08-30",
        due_ts=datetime(2026, 8, 30, 6, tzinfo=UTC),
        grace_minutes=30,
    )
    landed = datetime(2026, 8, 30, 8, 15, tzinfo=UTC)

    assert expectation.condition(arrived=True, now=NOW) is ArrivalCondition.RECEIVED
    assert expectation.sla_status(actual_ts=landed, now=NOW) is SlaStatus.BREACHED
    # ...and one that landed inside grace is On-Time, not merely "not late".
    inside = datetime(2026, 8, 30, 6, 12, tzinfo=UTC)
    assert expectation.sla_status(actual_ts=inside, now=NOW) is SlaStatus.ON_TIME


def test_an_expectation_and_a_cycle_agree_because_one_derives_from_the_other() -> None:
    """`Expectation` is a projection of `Cycle`, not a second SLA clock.

    Two implementations of "is this late" is how a board says 1 missing and a
    trend says none.
    """
    expectation = Expectation(
        feed_id="uhc_md_daily",
        domain="enrollment",
        business_date="2026-08-30",
        due_ts=datetime(2026, 8, 30, 6, tzinfo=UTC),
        grace_minutes=30,
    )
    cycle = expectation.as_cycle()
    assert cycle.feed_id == "uhc_md_daily"
    assert cycle.cycle_date == date(2026, 8, 30)
    assert cycle.expected_ts == expectation.due_ts
    assert cycle.deadline_ts == datetime(2026, 8, 30, 6, 30, tzinfo=UTC)

    # Inside grace: at risk on the board, Delayed in the control table.
    watching = datetime(2026, 8, 30, 6, 10, tzinfo=UTC)
    assert expectation.condition(arrived=False, now=watching) is ArrivalCondition.AT_RISK
    assert cycle.status(watching) is SlaStatus.DELAYED

    # Past the deadline: missing on the board, Breached in the control table.
    chasing = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)
    assert expectation.condition(arrived=False, now=chasing) is ArrivalCondition.MISSING
    assert cycle.status(chasing) is SlaStatus.BREACHED


# ══ CF-V2-E12-05 · five ADT feeds, one upstream fault ═══════════════════════
#
#   "Given five ADT feeds miss the same arrival window, when alerts fire, then
#    ONE GROUPED ALERT explains 'identical window, likely a shared upstream
#    fault — treat as one incident'."


def test_five_feeds_on_one_window_group_into_a_single_alert() -> None:
    adt = tuple(
        Cycle(f"adt_source_{i}", date(2026, 8, 30), datetime(2026, 8, 30, 6, tzinfo=UTC))
        for i in range(5)
    )
    groups = list(grouped(alerts_for(adt, NOW)))
    assert len(groups) == 1
    assert len(groups[0][1]) == 5


def test_every_alert_carries_a_citation() -> None:
    adt = (Cycle("adt_healthix_v3", date(2026, 8, 30), datetime(2026, 8, 30, 6, tzinfo=UTC)),)
    assert all(alert.cited for alert in alerts_for(adt, NOW))


# ══ CF-V2-E12-04 · fingerprinting is arithmetic ═════════════════════════════
#
#   ">= 95% fingerprint precision on the seeded failure library BEFORE the
#    feature ships enabled."


def test_two_messages_differing_only_in_a_literal_share_one_shape() -> None:
    a = normalise("required key 'business_date' absent in XCom from upstream validate_input")
    b = normalise("required key 'member_id' absent in XCom from upstream validate_input")
    assert a == b


def test_the_same_failure_class_digests_the_same_with_no_model_in_the_path() -> None:
    one = signature(
        category=ErrorCategory.SYSTEM,
        message="required key 'business_date' absent",
        stage=Layer.BRONZE,
    )
    two = signature(
        category=ErrorCategory.SYSTEM, message="required key 'run_id' absent", stage=Layer.BRONZE
    )
    assert one == two


def test_a_different_verb_is_a_different_class() -> None:
    absent = signature(
        category=ErrorCategory.SYSTEM,
        message="required key 'business_date' absent",
        stage=Layer.BRONZE,
    )
    refused = signature(
        category=ErrorCategory.SYSTEM, message="connection refused", stage=Layer.BRONZE
    )
    assert absent != refused


def test_three_errors_logged_two_are_consequences_of_the_first() -> None:
    cascade = separate_cascade(
        [
            error("h1", Layer.BRONZE, "required key 'business_date' absent", T0),
            error(
                "h2",
                Layer.SILVER_RAW,
                "upstream stage produced no output",
                T0 + timedelta(seconds=2),
            ),
            error("h3", Layer.SILVER_RAW, "load skipped: no input", T0 + timedelta(seconds=3)),
        ]
    )
    assert len(cascade.all) == 3
    assert len(cascade.consequences) == 2
    assert cascade.first is not None
    assert cascade.first.error_id_hash == "h1"


def test_a_per_record_failure_is_independent_not_buried_under_the_root() -> None:
    """214 quarantine reasons must not vanish under one 'root cause'.

    A later error carrying its own `record_key` is its own fact. Burying it is
    how a platform hides a second problem behind the first.
    """
    cascade = separate_cascade(
        [
            error("h1", Layer.BRONZE, "required key 'business_date' absent", T0),
            error("h2", Layer.SILVER_RAW, "upstream produced no output", T0 + timedelta(seconds=2)),
            error(
                "h3",
                Layer.SILVER_RAW,
                "date of birth is not a date",
                T0 + timedelta(seconds=3),
                record_key="M-88213",
            ),
        ]
    )
    roles = {view.error_id_hash: view.role for view in cascade.all}
    assert roles["h1"] is ErrorRole.ACTIONABLE
    assert roles["h2"] is ErrorRole.CONSEQUENCE
    assert roles["h3"] is ErrorRole.INDEPENDENT
    assert len(cascade.independent) == 1


BH_AF_002_ERRORS = [
    error("h1", Layer.BRONZE, "required key 'business_date' absent", T0),
    error("h2", Layer.SILVER_RAW, "upstream stage produced no output", T0 + timedelta(seconds=2)),
    error("h3", Layer.SILVER_RAW, "load skipped: no input", T0 + timedelta(seconds=3)),
]


def novel_incident() -> Incident:
    """The canonical batch, fingerprinted against an EMPTY library."""
    return fingerprint_batch(
        batch_id="1244", feed_id="fidelis_roster", errors=BH_AF_002_ERRORS, now=T0
    )


def test_a_matched_guide_reads_14_prior_occurrences_mean_fix_18_minutes() -> None:
    found = novel_incident().signature
    guide = RecoveryGuide(
        guide_id="BH-AF-002",
        title="Missing mandatory task parameter",
        signatures=frozenset({found}),
        steps=("Re-run validate_input, then evaluate_bronze_load.",),
    )
    history = [
        PriorIncident(
            incident_id=f"INC-{n}",
            occurred_ts=T0 - timedelta(days=n + 1),
            fix_minutes=18,
            batch_id=f"12{n:02d}",
        )
        for n in range(14)
    ]
    matched = fingerprint_batch(
        batch_id="1244",
        feed_id="fidelis_roster",
        errors=BH_AF_002_ERRORS,
        guides=[guide],
        history=history,
        now=T0,
    )
    assert matched.match is not None
    assert matched.match.guide.guide_id == "BH-AF-002"
    # The story's own sentence, verbatim. "1 prior occurrence(s)" reads as a
    # machine nobody proofread.
    assert matched.match.summary() == "14 prior occurrences, mean fix 18 minutes"


def test_a_single_prior_is_written_in_the_singular() -> None:
    found = novel_incident().signature
    matched = fingerprint_batch(
        batch_id="1244",
        feed_id="fidelis_roster",
        errors=BH_AF_002_ERRORS,
        guides=[RecoveryGuide(guide_id="BH-AF-002", title="x", signatures=frozenset({found}))],
        history=[PriorIncident(incident_id="INC-1", occurred_ts=T0, fix_minutes=18)],
        now=T0,
    )
    assert matched.match is not None
    assert matched.match.summary() == "1 prior occurrence, mean fix 18 minutes"


def test_a_novel_failure_matches_nothing_and_says_so_honestly() -> None:
    incident = novel_incident()
    assert incident.match is None
    assert incident.status is StatusWord.NEEDS_ATTENTION
    # The evidence bundle is built identically for known and novel — a novel
    # failure gets organised evidence, not an empty screen.
    assert incident.evidence_bundle()["root_cause"] is not None


# ══ CF-V2-E16-07 · only closed narratives become knowledge ══════════════════
#
#   "Embed an unresolved incident's speculation — only closed narratives become
#    knowledge."


def test_an_open_incident_is_not_embeddable() -> None:
    incident = novel_incident()
    assert incident.state is IncidentState.OPEN
    assert not incident.embeddable


def test_a_resolved_but_unclosed_incident_is_still_not_embeddable() -> None:
    resolved = (
        novel_incident()
        .acknowledge(by="ops@cinqcare.test")
        .resolve(
            resolution="Re-ran validate_input; business_date restored.",
            at=T0 + timedelta(minutes=18),
        )
    )
    assert not resolved.embeddable


def test_a_closed_incident_embeds_and_its_narrative_carries_the_signature() -> None:
    closed = (
        novel_incident()
        .acknowledge(by="ops@cinqcare.test")
        .resolve(resolution="Re-ran validate_input.", at=T0 + timedelta(minutes=18))
        .close()
    )
    assert closed.embeddable
    assert closed.state is IncidentState.CLOSED
    assert closed.signature in closed.narrative()


def test_a_resolution_nobody_wrote_is_refused() -> None:
    """A resolution nobody wrote is a guide nobody can retrieve."""
    with pytest.raises(IncidentTransitionError):
        novel_incident().acknowledge(by="ops@cinqcare.test").resolve(resolution="   ", at=NOW)


def test_an_incident_cannot_skip_straight_to_closed() -> None:
    with pytest.raises(IncidentTransitionError):
        novel_incident().close()


# ── E16-07's pipeline half was blocked on E16-05; the SPINE now exists ──────
#
#   The five tests above are the GATE — `Incident.embeddable` and
#   `.narrative()` — and they pass today, unconditionally: an open incident
#   does not embed, a resolved-but-unclosed one does not embed, a closed one
#   does and carries its signature. `wave2.md` says exactly this: "already
#   enforce the first test; the story is the pipeline around them."
#
#   That pipeline is `CF-V2-E16-05`: "Knowledge ingestion + embedding pipeline
#   (Inbox -> Parse -> Chunk -> PHI-verify -> Steward approve -> Embed)",
#   whose own dependency row in `wave2.md` reads "E16-07 entirely — it *is*
#   the write side of this pipeline." E16-05 is a WAVE-1 story — not one of
#   Wave 2's eleven (E12-01..05, E8-04, E5-04, E7-05, E13-03, E13-04, E16-07).
#
#   W1-25 landed its CHUNK -> PHI-verify -> Embed + Index spine: `core.knowledge`
#   (pure chunk-boundary and idempotency logic; see
#   `tests/unit/test_knowledge_chunking.py`) and `workers.knowledge
#   .KnowledgeIngestWorker` (the wired stage; see
#   `tests/contract/test_knowledge_ingestion_pipeline.py`), scoped HONESTLY to
#   the two content sources that are real today — a closed `Incident`'s
#   narrative and a Published `RUNBOOK`'s steps, both already-parsed Python
#   objects. "Inbox" and "Parse" in the full generic sense (a document-upload
#   surface with layout-aware PDF parsing) remain unbuilt, on purpose — see
#   `core.knowledge`'s own module docstring.
#
#   What is STILL missing, and what keeps the two tests below skipped: nothing
#   calls `KnowledgeIngestWorker` automatically. `Incident.close()` does not
#   invoke it; a runbook `transition_to(PUBLISHED)` does not either. Wiring
#   that hook, plus runbook-supersede atomicity, stale-runbook propagation
#   into citing alerts, and the five-minute close-to-retrievable lag
#   measurement, are E16-07's own remaining scope — a Wave-2 story, not this
#   Wave-1 slab's.
#
#   The two tests below are SKIPPED, not xfailed. `xfail(strict=True)` (see
#   tests/audit/test_wave0_gap_findings.py's own docstring) asserts today's
#   behaviour is a BUG that should already work — that is not this. Nothing
#   here is broken; a story is waiting on a dependency outside this phase.
#   Marking it xfail would misreport a scoping decision as a defect.


@pytest.mark.skip(
    reason="the CHUNK -> PHI-verify -> Embed spine exists (W1-25, "
    "workers.knowledge.KnowledgeIngestWorker.ingest_incident) and is proven "
    "callable directly in tests/contract/test_knowledge_ingestion_pipeline.py — "
    "what is still missing is the HOOK: nothing calls it when Incident.close() "
    "runs. That wiring is CF-V2-E16-07's own remaining scope."
)
def test_the_embed_on_close_hook_calls_a_real_pipeline_when_an_incident_closes() -> None:
    """The write side: a closed incident's `narrative()` reaches the vector
    store through E16-05's `Inbox -> Parse -> Chunk -> PHI-verify -> Steward
    approve -> Embed`, and an OPEN or resolved-but-unclosed incident is
    refused by the pipeline the same way `.narrative()` already refuses it.

    The pipeline this describes is callable today —
    `workers.knowledge.KnowledgeIngestWorker.ingest_incident`. What is not
    built is the AUTOMATIC hook: `Incident.close()` does not call it, so a
    real incident closing anywhere in this codebase does not yet reach the
    vector store on its own. `Incident.embeddable` and `.narrative()` are
    proven above; the pipeline itself is proven in
    `tests/contract/test_knowledge_ingestion_pipeline.py`; this is the wiring
    between the two that E16-07 still owes.
    """


@pytest.mark.skip(
    reason="the CHUNK -> PHI-verify -> Embed spine exists (W1-25) — "
    "runbook-supersede atomicity, stale-runbook propagation into citing "
    "alerts, and the five-minute close-to-retrievable lag are E16-07's OWN "
    "remaining acceptance criteria, not blocked on a missing Embed stage "
    "any more."
)
def test_runbook_publish_supersedes_atomically_and_a_retired_feeds_runbook_reads_stale() -> None:
    """The other three acceptance criteria named in `wave2.md`'s E16-07 row:

    a runbook publish supersedes the prior version ATOMICALLY; a retired
    feed's linked runbook is flagged stale IN THE ALERT THAT CITES IT, not
    just on the runbook's own page; and the median lag from an incident
    closing to its narrative being retrievable is under five minutes. All
    three now have somewhere to attach — `workers.knowledge
    .KnowledgeIngestWorker.ingest_runbook` embeds a Published runbook's steps
    — but none of the three is wired or measured yet; that remains E16-07's.
    """


# ══ CF-V2-E12-03 · the governed action surface ══════════════════════════════


def retry(**overrides: object) -> ActionRequest:
    base: dict[str, object] = {
        "action": OpsAction.RETRY,
        "target": "1244",
        "actor": SAM,
        "reason": "Transient cluster error; the guide says retry.",
    }
    base.update(overrides)
    return ActionRequest(**base)  # type: ignore[arg-type]


def test_a_retry_on_a_running_batch_is_refused() -> None:
    with pytest.raises(RefusedError):
        authorize(retry(), environment=Environment.DEVELOPMENT, batch_state=BatchState.IN_PROGRESS)


def test_a_retry_on_a_paused_feed_is_refused_naming_who_paused_it() -> None:
    with pytest.raises(RefusedError) as raised:
        authorize(
            retry(),
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.FAILED,
            feed_paused=True,
            paused_reason="feed paused by J. Smith — mapping change pending",
        )
    assert "J. Smith" in raised.value.refusal.detail


def test_a_production_mutation_without_an_approval_identifier_is_refused() -> None:
    with pytest.raises(RefusedError):
        authorize(
            retry(), environment=Environment.PRODUCTION, batch_state=BatchState.FAILED, now=NOW
        )


def test_requested_is_not_succeeded() -> None:
    request = retry()
    authorize(request, environment=Environment.DEVELOPMENT, batch_state=BatchState.FAILED)
    record = request_action(request, now=NOW)
    assert record.phase is ActionPhase.REQUESTED
    assert not record.is_complete


def test_verification_reads_the_control_tables_and_promotes_to_verified() -> None:
    """The measurable: 'verified after execution'.

    `expected` is supplied per call because success differs by action — a retry
    expects COMPLETED; a pause expects the FEED paused and the batch state is
    irrelevant. One hard-coded success state would make one of those two lie.
    """
    record = request_action(retry(), now=NOW)
    done = verify(
        record,
        observed_state=BatchState.COMPLETED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="resumed from silver_raw; 9,992 rows loaded",
        now=NOW,
    )
    assert done.phase is ActionPhase.VERIFIED
    assert done.is_complete


def test_a_retry_that_did_not_work_reports_failed_not_verified() -> None:
    record = request_action(retry(), now=NOW)
    outcome = verify(
        record,
        observed_state=BatchState.FAILED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="the cluster refused the job again",
        now=NOW,
    )
    assert outcome.phase is ActionPhase.FAILED
    assert not outcome.is_complete


def test_the_surface_offers_only_actions_that_would_authorize() -> None:
    running = offered(batch_state=BatchState.IN_PROGRESS)
    assert OpsAction.RETRY not in running
    assert OpsAction.REPROCESS_BATCH not in running


def test_every_action_has_a_row_in_the_allowed_state_matrix() -> None:
    """A new action costs a row. That toll is what stops a seventh arriving as
    a text box."""
    assert set(ALLOWED_STATES) == set(OpsAction)


# ══ CF-V2-E8-04 · recovery cannot double-load ═══════════════════════════════
#
#   "Given a mapping fix is approved for a batch with 214 quarantined records,
#    when the operator runs reprocess-failed-only, then EXACTLY 214 records
#    re-enter at Silver Raw."


def test_exactly_214_records_re_enter_at_silver_raw() -> None:
    plan = reprocess_failed_only(
        batch_id="1244",
        feed_id="fidelis_roster",
        record_keys=[f"M-{n}" for n in range(214)],
    )
    plan.prove_idempotent()
    assert plan.row_count == 214
    assert plan.start_stage is Layer.SILVER_RAW
    assert plan.mode is ReplayMode.IN_PLACE
    assert "214 rows re-enter" in plan.preview().explain()


def test_an_in_place_bronze_replay_is_refused_before_anything_executes() -> None:
    """Bronze is append-only. Refuse it here, where the message is useful."""
    with pytest.raises(DoubleLoadError):
        restart_from(
            batch_id="1244", feed_id="fidelis_roster", stage=Layer.BRONZE, rows=10
        ).prove_idempotent()


def test_a_backdate_over_an_existing_period_demands_an_explicit_supersede() -> None:
    with pytest.raises(DoubleLoadError):
        backdate(
            feed_id="fidelis_roster",
            business_date=date(2026, 7, 1),
            new_batch_id="1301",
            rows=100,
            overlapping=["1244"],
        ).prove_idempotent()


def test_an_acknowledged_supersede_is_permitted() -> None:
    backdate(
        feed_id="fidelis_roster",
        business_date=date(2026, 7, 1),
        new_batch_id="1301",
        rows=100,
        overlapping=["1244"],
        supersede_acknowledged=True,
    ).prove_idempotent()


def test_a_first_time_backdate_for_an_unprocessed_period_is_permitted() -> None:
    """The regression for a real defect.

    A backdate for a period never processed before overlaps nothing, so there
    is no batch to supersede — and refusing it as 'superseding but names no
    batch' made a legitimate recovery unreachable. Silence is not consent, but
    an empty overlap set is not silence: it is a checked fact.
    """
    plan = backdate(
        feed_id="fidelis_roster", business_date=date(2026, 7, 1), new_batch_id="1301", rows=100
    )
    plan.prove_idempotent()
    assert plan.overlapping_batches == ()


def test_a_reprocess_after_a_fix_supersedes_the_batch_it_replaces() -> None:
    plan = reprocess_batch(
        batch_id="1244", feed_id="fidelis_roster", rows=9_992, new_batch_id="1301"
    )
    plan.prove_idempotent()
    assert plan.mode is ReplayMode.SUPERSEDING
    assert plan.supersedes == "1244"


def test_no_recovery_path_needs_a_manual_control_row_delete() -> None:
    """The era this ends, asserted over the CODE rather than the behaviour.

    The incident library records it by name: "delete the control rows so bronze
    will accept the replay". This test is what ends it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "cinqflow"
    watched = [*(root / "core" / "operations").rglob("*.py"), *(root / "workers").rglob("*.py")]
    offenders = [
        path.name for path in watched if "DELETE FROM control." in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ══ CF-V2-E13-03 · variance, waiver, and what cannot be waived ══════════════


def financial() -> Variance:
    return Variance(
        variance_id="V1",
        batch_id="1244",
        feed_id="claims",
        kind=VarianceKind.FINANCIAL,
        expected=Decimal("100000"),
        actual=Decimal("52000"),
        tolerance=Decimal("100"),
        opened_by="steward@cinqcare.test",
        opened_ts=NOW,
    )


def count_variance() -> Variance:
    return Variance(
        variance_id="V2",
        batch_id="1244",
        feed_id="claims",
        kind=VarianceKind.COUNT,
        expected=Decimal("1000"),
        actual=Decimal("998"),
        tolerance=Decimal("5"),
        opened_by="steward@cinqcare.test",
        opened_ts=NOW,
    )


def test_a_48000_dollar_financial_variance_is_critical() -> None:
    assert financial().delta == Decimal("-48000")
    assert financial().critical


def test_a_critical_variance_cannot_be_waived_at_all() -> None:
    """'Block publication MECHANICALLY — not by convention.'"""
    with pytest.raises(VarianceError):
        financial().waive(
            Waiver("other@cinqcare.test", "known payer quirk", date(2026, 8, 30), date(2026, 11, 1))
        )


def test_the_person_who_opened_a_variance_may_not_decide_it() -> None:
    with pytest.raises(VarianceError):
        count_variance().waive(
            Waiver(
                "steward@cinqcare.test",
                "known payer quirk",
                date(2026, 8, 30),
                date(2026, 11, 1),
            )
        )


def test_n_a_is_refused_as_a_reason() -> None:
    with pytest.raises(VarianceError):
        Waiver("other@cinqcare.test", "n/a", date(2026, 8, 30), date(2026, 9, 30))


def test_a_waiver_longer_than_ninety_days_is_refused() -> None:
    with pytest.raises(VarianceError):
        Waiver("other@cinqcare.test", "known quirk", date(2026, 8, 30), date(2027, 8, 30))


def test_a_waiver_stops_blocking_and_blocks_again_the_day_it_lapses() -> None:
    waived = count_variance().waive(
        Waiver(
            "other@cinqcare.test",
            "known payer cancellation lag",
            date(2026, 8, 30),
            date(2026, 11, 28),
        )
    )
    assert not waived.blocks_publication(date(2026, 9, 1))
    assert waived.blocks_publication(date(2026, 12, 1))


# ══ CF-V2-E13-04 · certification derives ════════════════════════════════════
#
#   "Derive certification mechanically from the checks — no manual 'mark as
#    certified' button exists."


def all_checks() -> list[Check]:
    return [
        Check(CheckKind.BALANCE, True, "rows_in 22,014,882 == out + quarantined + drops"),
        Check(CheckKind.RECONCILIATION, True, "all stages balanced"),
        Check(CheckKind.DROP_LEDGER, True, "0 unattributed drops"),
        Check(CheckKind.DQ_RULES, True, "18 of 18 rules passed"),
        Check(CheckKind.SLA_WINDOW, True, "arrived 12 minutes inside grace"),
        Check(CheckKind.SCHEMA_CONTRACT, True, "contract v3, no drift"),
    ]


def test_a_clean_batch_certifies() -> None:
    assert certify(batch_id="1244", feed_id="claims", checks=all_checks(), now=NOW).verdict is (
        Verdict.CERTIFIED
    )


def test_one_waiver_produces_certified_with_waiver() -> None:
    """A distinct verdict, not a flag: a payer must see at a glance that
    something was ACCEPTED rather than passed."""
    waived = count_variance().waive(
        Waiver(
            "other@cinqcare.test",
            "known payer cancellation lag",
            date(2026, 8, 30),
            date(2026, 11, 28),
        )
    )
    result = certify(
        batch_id="1244", feed_id="claims", checks=all_checks(), variances=[waived], now=NOW
    )
    assert result.verdict is Verdict.CERTIFIED_WITH_WAIVER


def test_an_open_critical_variance_blocks_certification_under_any_circumstances() -> None:
    result = certify(
        batch_id="1244", feed_id="claims", checks=all_checks(), variances=[financial()], now=NOW
    )
    assert result.verdict is Verdict.NOT_CERTIFIED
    assert not result.publishable


def test_a_failed_mandatory_check_cannot_be_certified() -> None:
    checks = [*all_checks()[1:], Check(CheckKind.BALANCE, False, "17 rows unexplained")]
    assert certify(batch_id="1244", feed_id="claims", checks=checks, now=NOW).verdict is (
        Verdict.NOT_CERTIFIED
    )


def test_an_incomplete_check_set_is_pending_not_failed() -> None:
    partial = certify(batch_id="1244", feed_id="claims", checks=all_checks()[:2], now=NOW)
    assert partial.verdict is Verdict.PENDING


def test_a_check_still_running_holds_the_verdict_at_pending() -> None:
    """The regression for a real defect.

    A batch mid-flight is neither certified nor uncertified. Only mandatory
    checks were consulted for completeness, so a DQ sweep still running could
    be certified around — a verdict reached before the evidence was in.
    """
    checks = [*all_checks()[:5], Check(CheckKind.SCHEMA_CONTRACT, True, "running", completed=False)]
    assert certify(batch_id="1244", feed_id="claims", checks=checks, now=NOW).verdict is (
        Verdict.PENDING
    )


def test_the_export_names_the_waiver_and_re_derives_byte_identically() -> None:
    """'Evidence never degrades' — four months later, the same bytes."""
    waived = count_variance().waive(
        Waiver(
            "other@cinqcare.test",
            "known payer cancellation lag",
            date(2026, 8, 30),
            date(2026, 11, 28),
        )
    )

    def derive() -> str:
        return evidence_document(
            certify(
                batch_id="1244",
                feed_id="claims",
                checks=all_checks(),
                variances=[waived],
                now=NOW,
            )
        )

    document = derive()
    assert "known payer cancellation lag" in document
    assert document == derive()


# ══ CF-V2-E12-05 · the reliability score decomposes ═════════════════════════
#
#   "Show the score's ingredients on click (DQ 92, SLA 97, reconciliation 99…)
#    — A SCORE NO ONE CAN DECOMPOSE IS A RUMOR."


def score() -> Score:
    return score_for(
        feed_id="fidelis_roster",
        as_of=date(2026, 8, 30),
        observations={
            Signal.DQ: (92.0, "18 of 18 rules passed over 6 batches", 6),
            Signal.SLA: (97.0, "1 late arrival in 30 cycles", 30),
            Signal.RECONCILIATION: (99.0, "all stages balanced", 6),
            Signal.SCHEMA: (85.0, "1 compatible drift", 6),
            Signal.PIPELINE: (94.0, "1 restart in 30 runs", 30),
        },
        weights=Weights(),
    )


def test_the_score_decomposes_into_six_named_ingredients() -> None:
    assert len(score().decompose()) == 6


def test_an_unmeasured_signal_lowers_confidence_rather_than_the_score() -> None:
    """Identity resolution arrives in Wave 3. Scoring it zero until then would
    drag every feed into the critical band for a capability that does not
    exist yet."""
    assert 90 <= score().overall <= 100
    assert 0.85 <= score().confidence < 1.0


def test_the_weakest_signal_is_named_for_the_alert() -> None:
    weakest = score().weakest()
    assert weakest is not None
    assert weakest.signal is Signal.SCHEMA


def test_unnormalised_weights_are_refused() -> None:
    """Weights summing past 1.0 produce scores above 100, and a score above 100
    destroys the bands for everyone who has learned to read them."""
    with pytest.raises(ValueError, match=r"1\.0"):
        Weights(dq=0.9, sla=0.9, reconciliation=0.9, schema=0.9, identity=0.9, pipeline=0.9)
