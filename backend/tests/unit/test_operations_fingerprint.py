"""CF-V2-E12-04 — 'what is this?' becomes 'apply the known fix?'.

    "Given batch #1244 fails with a missing-parameter error seen 14 times
     before, when fingerprinting runs, then within a minute the incident shows
     the root cause, guide BH-AF-002, '14 prior occurrences, mean fix 18
     minutes', and a one-click proposed fix awaiting approval."
    "Given a genuinely novel failure matches nothing, when fingerprinting runs,
     then the incident says so honestly, presents the evidence bundle organized
     for a human, and offers to save the eventual resolution as a new draft
     guide."
    — CF-V2-E12-04

Every message here comes from the incident library
(`memory/05-ground-truth/04-incident-library.md`) rather than being written for
the occasion, which is the rule the golden sets are held to: a test set invented
alongside the thing it tests measures nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.model.vocabulary import ErrorCategory, Layer, StatusWord
from cinqflow.core.operations.actions import OpsAction
from cinqflow.core.operations.fingerprint import (
    FingerprintError,
    GuideMatch,
    IncidentKind,
    IncidentState,
    IncidentTransitionError,
    Precision,
    PriorIncident,
    RecoveryGuide,
    draft_guide_from,
    fingerprint_batch,
    library_from,
    measure_precision,
    normalise,
    signature,
)
from cinqflow.ports.control_tables import ErrorRecord

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 3, 3, tzinfo=UTC)
BATCH = "1244"
FEED = "fidelis-downstate-roster"

#: The canonical example, verbatim from the incident library.
BH_AF_002_MESSAGE = (
    "evaluate_bronze_load: required key 'business_date' absent in XCom from upstream validate_input"
)


def error(
    digest: str,
    message: str,
    *,
    seconds: int = 0,
    layer: Layer = Layer.BRONZE,
    category: ErrorCategory = ErrorCategory.SYSTEM,
    rule_id: str | None = None,
    batch_id: str = BATCH,
) -> ErrorRecord:
    return ErrorRecord(
        error_id_hash=digest,
        batch_id=batch_id,
        stage=layer,
        category=category,
        message=message,
        occurred_ts=NOW + timedelta(seconds=seconds),
        rule_id=rule_id,
    )


def guide(**overrides: object) -> RecoveryGuide:
    base: dict[str, object] = {
        "guide_id": "BH-AF-002",
        "title": "Missing mandatory task parameter",
        "signatures": frozenset(
            {
                signature(
                    stage=Layer.BRONZE,
                    category=ErrorCategory.SYSTEM,
                    message=BH_AF_002_MESSAGE,
                )
            }
        ),
        "steps": ("Re-run the upstream validate_input task, then retry the batch.",),
        "remedy": OpsAction.RETRY,
        "is_transient": True,
    }
    base.update(overrides)
    return RecoveryGuide(**base)  # type: ignore[arg-type]


def priors(count: int = 14, minutes: int = 18) -> list[PriorIncident]:
    return [
        PriorIncident(
            incident_id=f"INC-prior-{i}",
            occurred_ts=NOW - timedelta(days=i + 1),
            fix_minutes=minutes,
            batch_id=f"prior-{i}",
        )
        for i in range(count)
    ]


# ── the happy path, figure for figure ────────────────────────────────────────
def test_batch_1244_reads_exactly_as_the_story_writes_it() -> None:
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[
            error("root", BH_AF_002_MESSAGE),
            error("fallout-1", "evaluate_bronze_load downstream task failed", seconds=2),
            error("fallout-2", "load_silver_raw skipped: upstream failed", seconds=4),
        ],
        guides=[guide()],
        history=priors(),
        now=NOW,
    )
    assert incident.kind is IncidentKind.KNOWN
    assert incident.root_cause is not None
    assert incident.root_cause.error_id_hash == "root"
    assert len(incident.cascade.consequences) == 2

    assert incident.match is not None
    assert incident.match.guide.guide_id == "BH-AF-002"
    assert incident.match.occurrences == 14
    assert incident.match.mean_fix_minutes == 18
    assert "14 prior occurrences, mean fix 18 minutes" in incident.match.explain()

    # A one-click proposed fix — pressed on the ACTION SURFACE, not here.
    assert incident.proposed_remedy is OpsAction.RETRY


def test_the_signature_ignores_which_key_was_missing() -> None:
    """A missing `business_date` and a missing `run_date` are the same failure
    class with the same fix — splitting them makes "14 prior occurrences" read
    as two counts of seven."""
    business = signature(
        stage=Layer.BRONZE, category=ErrorCategory.SYSTEM, message=BH_AF_002_MESSAGE
    )
    run_date = signature(
        stage=Layer.BRONZE,
        category=ErrorCategory.SYSTEM,
        message=BH_AF_002_MESSAGE.replace("business_date", "run_date"),
    )
    assert business == run_date


def test_the_task_name_is_part_of_the_signature() -> None:
    """`evaluate_bronze_load` failing and `resolve_identity` failing are
    different problems for different people."""
    one = signature(stage=Layer.BRONZE, category=ErrorCategory.SYSTEM, message=BH_AF_002_MESSAGE)
    other = signature(
        stage=Layer.BRONZE,
        category=ErrorCategory.SYSTEM,
        message=BH_AF_002_MESSAGE.replace("evaluate_bronze_load", "resolve_identity"),
    )
    assert one != other


def test_the_same_words_at_different_layers_are_different_problems() -> None:
    bronze = signature(stage=Layer.BRONZE, category=ErrorCategory.SYSTEM, message=BH_AF_002_MESSAGE)
    ods = signature(
        stage=Layer.SILVER_ODS, category=ErrorCategory.SYSTEM, message=BH_AF_002_MESSAGE
    )
    assert bronze != ods


def test_a_dq_rule_failure_is_identified_by_its_rule() -> None:
    """The message is decoration when a rule fired."""
    one = signature(
        stage=Layer.SILVER_RAW,
        category=ErrorCategory.VALIDATION,
        message="1,204 rows failed",
        rule_id="DQ-002",
    )
    other = signature(
        stage=Layer.SILVER_RAW,
        category=ErrorCategory.VALIDATION,
        message="17 rows failed",
        rule_id="DQ-002",
    )
    assert one == other


# ── normalisation ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("volatile", "expected"),
    [
        ("batch 8842 failed", "batch <n> failed"),
        ("failed at 2026-08-30T03:03:11", "failed at <ts>"),
        ("service month 1000-01-01 is impossible", "service month <date> is impossible"),
        ("could not read /landing/fidelis/roster.xlsx", "could not read <path>"),
        ("fetch s3://bucket/key failed", "fetch <uri> failed"),
        ("fingerprint sha256-abc123def456 mismatch", "fingerprint <id> mismatch"),
        ("column 'pcp_npi' was null", "column <v> was null"),
    ],
)
def test_what_varies_between_occurrences_is_stripped(volatile: str, expected: str) -> None:
    assert normalise(volatile) == expected


def test_two_occurrences_of_one_incident_normalise_together() -> None:
    """Incident #2 from the library: null `pcp_npi` dropping records."""
    monday = "member_provider: 1,204 rows dropped where 'pcp_npi' was null in batch 8842"
    tuesday = "member_provider: 17 rows dropped where 'pcp_npi' was null in batch 8901"
    assert normalise(monday) == normalise(tuesday)


def test_two_runs_whose_traces_differ_still_share_a_fingerprint() -> None:
    """A signature that kept the whole trace would give every run its own
    fingerprint — a library that looks full and matches nothing."""
    monday = (
        BH_AF_002_MESSAGE + " " + " ".join(f"at line {100 + i} in worker 8842" for i in range(200))
    )
    tuesday = (
        BH_AF_002_MESSAGE + " " + " ".join(f"at line {700 + i} in worker 9013" for i in range(200))
    )
    assert signature(
        stage=Layer.BRONZE, category=ErrorCategory.SYSTEM, message=monday
    ) == signature(stage=Layer.BRONZE, category=ErrorCategory.SYSTEM, message=tuesday)


# ── the cascade feeds the signature ──────────────────────────────────────────
def test_the_signature_comes_from_the_root_cause_alone() -> None:
    """Computing it over all three would fingerprint the same failure
    differently depending on how many downstream tasks failed behind it."""
    alone = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    with_fallout = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[
            error("root", BH_AF_002_MESSAGE),
            error("f1", "downstream failed", seconds=1),
            error("f2", "downstream skipped", seconds=2),
        ],
        now=NOW,
    )
    assert alone.signature == with_fallout.signature


def test_one_incident_per_batch_not_one_per_error() -> None:
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[
            error("root", BH_AF_002_MESSAGE),
            error("f1", "downstream failed", seconds=1),
        ],
        now=NOW,
    )
    assert "2 errors logged; 1 is a consequence of the first" in incident.explain()


def test_rerunning_fingerprinting_does_not_manufacture_a_second_incident() -> None:
    errors = [error("root", BH_AF_002_MESSAGE)]
    first = fingerprint_batch(batch_id=BATCH, feed_id=FEED, errors=errors, now=NOW)
    second = fingerprint_batch(batch_id=BATCH, feed_id=FEED, errors=errors, now=NOW)
    assert first.incident_id == second.incident_id


# ── the don'ts ───────────────────────────────────────────────────────────────
def test_a_match_with_no_fingerprint_evidence_cannot_be_constructed() -> None:
    """THE DON'T, AS A TYPE: "claim a match without showing the fingerprint
    evidence"."""
    with pytest.raises(FingerprintError) as refused:
        GuideMatch(guide=guide(), signature="", matched_errors=("e",))
    assert "no evidence" in str(refused.value)

    with pytest.raises(FingerprintError) as no_rows:
        GuideMatch(guide=guide(), signature="fp-x", matched_errors=())
    assert "3 AM" in str(no_rows.value)


def test_nothing_in_this_module_executes_a_fix() -> None:
    """ "Auto-apply any fix in this story." The remedy is an OpsAction the
    operator presses on the action surface, with its approvals."""
    import cinqflow.core.operations.fingerprint as module

    forbidden = {"apply", "run", "execute", "fix", "remediate", "retry"}
    exported = {name for name in dir(module) if not name.startswith("_")}
    assert not (exported & forbidden)


def test_a_novel_failure_proposes_no_remedy() -> None:
    """A remedy offered without a matched guide would be the auto-apply the
    don't refuses, one indirection removed."""
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", "something new")], now=NOW
    )
    assert incident.proposed_remedy is None


# ── the exception: a genuinely novel failure ─────────────────────────────────
def test_a_novel_failure_says_so_honestly_and_hands_over_what_it_knows() -> None:
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[
            error("root", "Verato returned an unexpected envelope version", layer=Layer.IDENTITY),
            error("f1", "identity stage aborted", seconds=1, layer=Layer.IDENTITY),
        ],
        guides=[guide()],
        now=NOW,
    )
    assert incident.kind is IncidentKind.NOVEL
    assert incident.match is None
    assert "matches nothing in the recovery library" in incident.explain()
    assert "saved as a new draft guide" in incident.explain()

    bundle = incident.evidence_bundle()
    assert bundle["kind"] == "novel"
    assert bundle["root_cause"]["message"].startswith("Verato returned")
    assert len(bundle["consequences"]) == 1
    assert bundle["guide"] is None


def test_a_resolved_novel_incident_becomes_a_draft_guide_with_no_remedy() -> None:
    """The first version of a guide is one person's account of what worked
    once; binding a platform action to it before anybody else has seen it
    happen is how a wrong fix becomes the recommended one."""
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", "novel thing")], now=NOW
    )
    drafted = draft_guide_from(
        incident, title="Verato envelope version", steps=("Pin the envelope version.",)
    )
    assert drafted.remedy is None
    assert incident.signature in drafted.signatures
    assert drafted.guide_id.startswith("DRAFT-")


def test_a_batch_with_no_errors_cannot_become_a_guide() -> None:
    incident = fingerprint_batch(batch_id=BATCH, feed_id=FEED, errors=[], now=NOW)
    with pytest.raises(FingerprintError):
        draft_guide_from(incident, title="nothing", steps=())


# ── matching is exact, never a score ─────────────────────────────────────────
def test_a_near_miss_matches_nothing_rather_than_matching_weakly() -> None:
    """A threshold would be a knob somebody tunes until the demo matches, and
    the precision gate would then measure the knob."""
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[error("root", "required key absent from a completely different system")],
        guides=[guide()],
        now=NOW,
    )
    assert incident.match is None


def test_a_guide_covering_several_signatures_matches_any_of_them() -> None:
    other = signature(
        stage=Layer.SILVER_RAW, category=ErrorCategory.SYSTEM, message=BH_AF_002_MESSAGE
    )
    wide = guide(signatures=frozenset({*guide().signatures, other}))
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[error("root", BH_AF_002_MESSAGE, layer=Layer.SILVER_RAW)],
        guides=[wide],
        now=NOW,
    )
    assert incident.match is not None


# ── the numbers a person reads ───────────────────────────────────────────────
def test_incidents_with_no_recorded_duration_count_but_do_not_average() -> None:
    """Averaging over a missing number is how "mean fix 18 minutes" becomes a
    figure nobody trusts twice."""
    mixed = [*priors(2, minutes=10), PriorIncident("INC-x", NOW, fix_minutes=None)]
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[error("root", BH_AF_002_MESSAGE)],
        guides=[guide()],
        history=mixed,
        now=NOW,
    )
    assert incident.match is not None
    assert incident.match.occurrences == 3
    assert incident.match.mean_fix_minutes == 10


def test_no_recorded_duration_reads_as_unknown_not_zero() -> None:
    """Zero reads as "instant", and an operator planning their morning around a
    zero-minute fix has been told something false."""
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[error("root", BH_AF_002_MESSAGE)],
        guides=[guide()],
        history=[PriorIncident("INC-x", NOW, fix_minutes=None)],
        now=NOW,
    )
    assert incident.match is not None
    assert incident.match.mean_fix_minutes is None
    assert "no fix duration recorded" in incident.match.explain()


def test_a_stale_guide_is_flagged_in_the_very_alert_that_cites_it() -> None:
    """The incident library's own rule."""
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[error("root", BH_AF_002_MESSAGE)],
        guides=[guide(stale=True)],
        history=priors(1),
        now=NOW,
    )
    assert incident.match is not None
    assert "has retired" in incident.match.explain()


def test_priors_are_counted_per_signature_not_per_guide() -> None:
    """A count over the whole guide would tell an operator that a failure they
    have never seen has happened fourteen times."""
    seen = fingerprint_batch(
        batch_id="a", feed_id=FEED, errors=[error("r", BH_AF_002_MESSAGE)], now=NOW
    )
    unseen = fingerprint_batch(
        batch_id="b",
        feed_id=FEED,
        errors=[error("r", BH_AF_002_MESSAGE, layer=Layer.SILVER_RAW)],
        now=NOW,
    )
    grouped = library_from([guide()], [seen, seen, unseen])
    assert len(grouped[seen.signature]) == 2
    assert len(grouped[unseen.signature]) == 1


# ── the citations ────────────────────────────────────────────────────────────
def test_every_piece_of_evidence_is_citable() -> None:
    incident = fingerprint_batch(
        batch_id=BATCH,
        feed_id=FEED,
        errors=[error("root", BH_AF_002_MESSAGE)],
        guides=[guide()],
        history=priors(3),
        now=NOW,
    )
    assert incident.match is not None
    citations = incident.match.citations
    assert len(citations) == 4  # the guide plus three priors
    assert all(str(c) for c in citations)
    assert incident.status is StatusWord.NEEDS_ATTENTION


# ── the measurable ───────────────────────────────────────────────────────────
def test_a_matcher_that_recognises_nothing_does_not_pass_the_gate() -> None:
    """An eval returning 100% because it matched nothing is the most dangerous
    green there is."""
    assert not Precision(matched=0, correct=0, total=10).passes(0.95)


def test_precision_and_recall_are_reported_together() -> None:
    """Neither number is allowed to travel alone."""
    scored = Precision(matched=20, correct=19, total=25)
    assert scored.passes(0.95)
    report = scored.report(0.95)
    assert "19/20 matches correct (95.0%" in report
    assert "recall 19/25" in report


def test_a_wrong_guide_counts_against_precision_and_a_decline_does_not() -> None:
    """A wrong guide sends somebody to do the wrong thing at 3 AM; a missing
    one sends them to think."""
    right = fingerprint_batch(
        batch_id="a",
        feed_id=FEED,
        errors=[error("r", BH_AF_002_MESSAGE)],
        guides=[guide()],
        now=NOW,
    )
    declined = fingerprint_batch(
        batch_id="b", feed_id=FEED, errors=[error("r", "novel")], guides=[guide()], now=NOW
    )
    scored = measure_precision([(right, "BH-AF-002"), (declined, "BH-AF-002")])
    assert scored.precision == 1.0
    assert scored.recall == 0.5


# ── within a minute ──────────────────────────────────────────────────────────
def test_fingerprinting_a_noisy_batch_is_a_rounding_error_in_the_budget() -> None:
    """ "within a minute the incident shows the root cause" — and retrieval and
    the model's explanation of a novel failure also have to fit."""
    import time

    errors = [error("root", BH_AF_002_MESSAGE)] + [
        # Strictly AFTER the root cause. Errors sharing a second are ordered by
        # hash, and `f0` sorts before `root` — so a fallout row would become
        # the root cause. Real cascades are ordered in time; so is this.
        error(f"f{i}", f"downstream task {i} failed", seconds=1 + i % 4)
        for i in range(500)
    ]
    library = [guide(guide_id=f"G-{i}", signatures=frozenset({f"fp-{i}"})) for i in range(200)]
    library.append(guide())

    started = time.perf_counter()
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=errors, guides=library, history=priors(), now=NOW
    )
    elapsed = time.perf_counter() - started
    assert incident.match is not None
    assert elapsed < 2.0, f"fingerprinting took {elapsed:.2f}s of a 60s promise"


# ── the operational machine, and the gate the knowledge loop asks ────────────
def test_a_fresh_incident_is_open_and_teaches_nothing_yet() -> None:
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    assert incident.state is IncidentState.OPEN
    assert not incident.embeddable
    with pytest.raises(IncidentTransitionError, match="only closed narratives"):
        incident.narrative()


def test_an_incident_cannot_skip_from_open_to_closed() -> None:
    """Closing an incident nobody resolved would embed an empty lesson."""
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    with pytest.raises(IncidentTransitionError, match="cannot go open -> closed"):
        incident.close()


def test_an_acknowledgement_names_a_person() -> None:
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    with pytest.raises(IncidentTransitionError, match="names a person"):
        incident.acknowledge(by="   ")


def test_a_resolution_that_says_nothing_is_refused() -> None:
    """A resolution nobody wrote is a guide nobody can retrieve."""
    incident = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    with pytest.raises(IncidentTransitionError, match="teaches nothing"):
        incident.resolve(resolution="  ", at=NOW)


def test_only_a_closed_incident_becomes_knowledge() -> None:
    """CF-V2-E16-07's don't, enforced a wave before its write side lands."""
    opened = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    resolved = opened.acknowledge(by="sam@cinqcare.test").resolve(
        resolution="Re-ran validate_input; business_date restored.",
        at=NOW + timedelta(minutes=18),
    )
    assert not resolved.embeddable

    closed = resolved.close()
    assert closed.embeddable
    narrative = closed.narrative()
    assert closed.signature in narrative
    assert "business_date restored" in narrative
    assert "Time to resolve: 18 minutes." in narrative


def test_a_transition_returns_a_new_incident_and_leaves_the_old_one_alone() -> None:
    """ "what did this look like when it was acknowledged" is a fact the ledger
    can hold rather than a version somebody overwrote."""
    opened = fingerprint_batch(
        batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
    )
    acknowledged = opened.acknowledge(by="sam@cinqcare.test", assigned_to="ops-rota")

    assert opened.state is IncidentState.OPEN
    assert opened.acknowledged_by == ""
    assert acknowledged.state is IncidentState.ACKNOWLEDGED
    assert acknowledged.assigned_to == "ops-rota"
    # The identity is unchanged: it is the same incident, later.
    assert acknowledged.incident_id == opened.incident_id


def test_the_narrative_carries_no_model_output() -> None:
    """What makes the loop safe to run automatically: there is nothing in a
    narrative that a model wrote."""
    closed = (
        fingerprint_batch(
            batch_id=BATCH, feed_id=FEED, errors=[error("root", BH_AF_002_MESSAGE)], now=NOW
        )
        .resolve(resolution="Re-ran validate_input.", at=NOW)
        .close()
    )
    narrative = closed.narrative()
    # Every line traces to an error row, the cascade, or the human's own words.
    assert "Re-ran validate_input." in narrative
    assert closed.batch_id in narrative
    assert closed.feed_id in narrative
