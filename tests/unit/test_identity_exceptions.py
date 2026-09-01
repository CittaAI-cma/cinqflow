"""CF-V3-E9-02 — one queue, deduplicated by person, aged and assignable.

    "one queue holding every identity exception — failed calls,
     retry-exhausted requests, low-confidence matches, unresolved records —
     triaged, deduplicated, aged and assignable"
    "Deduplicate — the same person failing in three batches is one exception
     with three occurrences, not three items."
    "Show queue health (volume, aging, resolution rate) per source."
    "Feed resolved exceptions back for reprocessing automatically."
    "Given an exception ages past its SLA without action, when the daily
     evaluation runs, then it escalates to the steward's manager per the
     escalation chain, visibly on the item."
    "Auto-resolve anything — this queue prepares decisions; the next story
     governs making them." (a documented don't)
    — CF-V3-E9-02

THE DESIGN DECISION UNDER TEST: dedup keys on (source_system,
source_member_id) — a PERSON — never on batch_id, so the SAME failure
reported by three nightly batches folds into one exception with three
occurrences rather than three queue items. This is a different shape from
`ops.incident_event` (keyed to batch_id + signature) on purpose: an identity
exception OUTLIVES the batch that first raised it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.identity import MatchOutcome
from cinqflow.core.identity.exceptions import (
    ExceptionEventAction,
    ExceptionOccurrence,
    ExceptionState,
    IdentityException,
    IdentityExceptionError,
    IdentityExceptionEvent,
    assign,
    escalate_if_breached,
    exception_key,
    fold,
    health_by_source,
    merge_occurrence,
    resolve,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 8, 1, tzinfo=UTC)
T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)
NOW = T0


def _occurrence(
    batch_id: str, *, at: datetime, outcome: MatchOutcome = MatchOutcome.UNRESOLVED
) -> ExceptionOccurrence:
    return ExceptionOccurrence(batch_id=batch_id, outcome=outcome, occurred_ts=at)


# ── the dedupe — a person, never a batch ─────────────────────────────────────


def test_the_same_person_failing_in_three_batches_is_one_exception() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    exc = merge_occurrence(
        exc, _occurrence("b-2", at=T1), source_system="FIDELIS", source_member_id="M-1"
    )
    exc = merge_occurrence(
        exc, _occurrence("b-3", at=T2), source_system="FIDELIS", source_member_id="M-1"
    )
    assert exc.occurrence_count == 3
    assert exc.key == exception_key("FIDELIS", "M-1")


def test_a_new_exception_opens_with_exactly_one_occurrence() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    assert exc.occurrence_count == 1
    assert exc.state is ExceptionState.OPEN


def test_an_exception_with_no_occurrences_never_happened() -> None:
    with pytest.raises(IdentityExceptionError):
        IdentityException(
            source_system="FIDELIS",
            source_member_id="M-1",
            state=ExceptionState.OPEN,
            occurrences=(),
        )


def test_opened_ts_is_the_first_occurrence_not_the_latest() -> None:
    """Aging measures from when the problem was FIRST seen — a third
    occurrence must not reset the clock a steward's SLA is measured against."""
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    exc = merge_occurrence(
        exc, _occurrence("b-2", at=T2), source_system="FIDELIS", source_member_id="M-1"
    )
    assert exc.opened_ts == T0
    assert exc.latest_ts == T2


# ── aging and escalation ──────────────────────────────────────────────────────


def test_an_exception_older_than_its_sla_is_breached() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    now = T0 + timedelta(days=10)
    assert exc.is_breached(sla=timedelta(days=5), now=now)
    assert not exc.is_breached(sla=timedelta(days=20), now=now)


def test_a_resolved_exception_is_never_breached_no_matter_its_age() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    exc = resolve(exc)
    assert not exc.is_breached(sla=timedelta(days=1), now=T0 + timedelta(days=365))


def test_escalate_if_breached_moves_only_the_breached_ones() -> None:
    now = T0 + timedelta(days=10)
    stale = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-old"
    )
    fresh = merge_occurrence(
        None,
        _occurrence("b-2", at=now - timedelta(days=1)),
        source_system="FIDELIS",
        source_member_id="M-new",
    )
    escalated = escalate_if_breached((stale, fresh), sla=timedelta(days=5), now=now)
    by_key = {e.key: e for e in escalated}
    assert by_key[stale.key].state is ExceptionState.ESCALATED
    assert by_key[fresh.key].state is ExceptionState.OPEN


def test_escalate_if_breached_never_touches_a_resolved_exception() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    exc = resolve(exc)
    now = T0 + timedelta(days=365)
    (result,) = escalate_if_breached((exc,), sla=timedelta(days=1), now=now)
    assert result.state is ExceptionState.RESOLVED


# ── assignment and resolution — never automatic ──────────────────────────────


def test_assign_names_an_owner_and_moves_off_open() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    assigned = assign(exc, to="steward-jane")
    assert assigned.state is ExceptionState.ASSIGNED
    assert assigned.assigned_to == "steward-jane"


def test_assigning_a_resolved_exception_is_refused() -> None:
    exc = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    exc = resolve(exc)
    with pytest.raises(IdentityExceptionError, match="resolved"):
        assign(exc, to="steward-jane")


def test_merge_occurrence_never_auto_resolves() -> None:
    """The documented don't, as a property: no sequence of occurrences,
    however long, transitions an exception to RESOLVED on its own — only a
    steward's explicit `resolve()` call does."""
    exc = None
    for i in range(50):
        exc = merge_occurrence(
            exc,
            _occurrence(f"b-{i}", at=T0 + timedelta(days=i)),
            source_system="FIDELIS",
            source_member_id="M-1",
        )
    assert exc is not None
    assert exc.state is not ExceptionState.RESOLVED


# ── queue health, per source ──────────────────────────────────────────────────


def test_queue_health_reports_open_breached_and_resolved_counts_per_source() -> None:
    fidelis_open = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    fidelis_breached = merge_occurrence(
        None, _occurrence("b-2", at=T0), source_system="FIDELIS", source_member_id="M-2"
    )
    optum_resolved = resolve(
        merge_occurrence(
            None, _occurrence("b-3", at=T0), source_system="OPTUM", source_member_id="M-3"
        )
    )
    now = T0 + timedelta(days=10)
    health = health_by_source(
        (fidelis_open, fidelis_breached, optum_resolved), sla=timedelta(days=5), now=now
    )
    by_source = {h.source_system: h for h in health}
    assert by_source["FIDELIS"].open_count == 2
    assert by_source["FIDELIS"].breached_count == 2
    assert by_source["OPTUM"].resolved_count == 1
    assert by_source["OPTUM"].open_count == 0


def test_a_payer_sending_bad_demographics_is_visible_in_its_own_row() -> None:
    """ "a payer sending bad demographics becomes visible" — health is reported
    PER SOURCE, never as one platform-wide number a single bad feed can hide
    inside."""
    fidelis = merge_occurrence(
        None, _occurrence("b-1", at=T0), source_system="FIDELIS", source_member_id="M-1"
    )
    optum = merge_occurrence(
        None, _occurrence("b-2", at=T0), source_system="OPTUM", source_member_id="M-2"
    )
    health = health_by_source((fidelis, optum), sla=timedelta(days=5), now=T0)
    assert {h.source_system for h in health} == {"FIDELIS", "OPTUM"}


# ── fold() — CF-V3-E9-01/02, the ledger's own replay ─────────────────────────


def test_fold_of_one_occurrence_opens_an_exception() -> None:
    event = IdentityExceptionEvent(
        event_id="EVT-1",
        exception_key=exception_key("fidelis", "M-1"),
        action=ExceptionEventAction.OCCURRENCE,
        source_system="fidelis",
        source_member_id="M-1",
        occurred_ts=NOW,
        batch_id="B-1",
        outcome=MatchOutcome.UNRESOLVED,
    )
    exc = fold([event])
    assert exc.state is ExceptionState.OPEN
    assert exc.occurrence_count == 1
    assert exc.occurrences[0].batch_id == "B-1"


def test_fold_replays_regardless_of_the_order_given() -> None:
    first = IdentityExceptionEvent(
        event_id="EVT-1",
        exception_key=exception_key("fidelis", "M-1"),
        action=ExceptionEventAction.OCCURRENCE,
        source_system="fidelis",
        source_member_id="M-1",
        occurred_ts=NOW,
        batch_id="B-1",
        outcome=MatchOutcome.UNRESOLVED,
    )
    second = IdentityExceptionEvent(
        event_id="EVT-2",
        exception_key=exception_key("fidelis", "M-1"),
        action=ExceptionEventAction.OCCURRENCE,
        source_system="fidelis",
        source_member_id="M-1",
        occurred_ts=NOW + timedelta(days=1),
        batch_id="B-2",
        outcome=MatchOutcome.UNRESOLVED,
    )
    assert fold([first, second]).occurrence_count == fold([second, first]).occurrence_count == 2


def test_fold_applies_assignment_and_resolution_transitions() -> None:
    key = exception_key("fidelis", "M-1")
    events = [
        IdentityExceptionEvent(
            event_id="EVT-1",
            exception_key=key,
            action=ExceptionEventAction.OCCURRENCE,
            source_system="fidelis",
            source_member_id="M-1",
            occurred_ts=NOW,
            batch_id="B-1",
            outcome=MatchOutcome.UNRESOLVED,
        ),
        IdentityExceptionEvent(
            event_id="EVT-2",
            exception_key=key,
            action=ExceptionEventAction.ASSIGNED,
            source_system="fidelis",
            source_member_id="M-1",
            occurred_ts=NOW + timedelta(hours=1),
            actor_subject="rule-engine",
            detail="enrollment-steward@cinqcare.test",
        ),
        IdentityExceptionEvent(
            event_id="EVT-3",
            exception_key=key,
            action=ExceptionEventAction.RESOLVED,
            source_system="fidelis",
            source_member_id="M-1",
            occurred_ts=NOW + timedelta(hours=2),
            actor_subject="enrollment-steward@cinqcare.test",
        ),
    ]
    exc = fold(events)
    assert exc.assigned_to == "enrollment-steward@cinqcare.test"
    assert exc.state is ExceptionState.RESOLVED


def test_fold_of_no_events_refuses() -> None:
    with pytest.raises(IdentityExceptionError):
        fold([])


def test_a_transition_before_any_occurrence_refuses() -> None:
    with pytest.raises(IdentityExceptionError):
        fold(
            [
                IdentityExceptionEvent(
                    event_id="EVT-1",
                    exception_key=exception_key("fidelis", "M-1"),
                    action=ExceptionEventAction.ASSIGNED,
                    source_system="fidelis",
                    source_member_id="M-1",
                    occurred_ts=NOW,
                    detail="someone@cinqcare.test",
                )
            ]
        )


def test_an_occurrence_event_requires_batch_and_outcome() -> None:
    with pytest.raises(IdentityExceptionError):
        IdentityExceptionEvent(
            event_id="EVT-1",
            exception_key=exception_key("fidelis", "M-1"),
            action=ExceptionEventAction.OCCURRENCE,
            source_system="fidelis",
            source_member_id="M-1",
            occurred_ts=NOW,
        )


def test_a_transition_event_may_not_carry_an_occurrence() -> None:
    with pytest.raises(IdentityExceptionError):
        IdentityExceptionEvent(
            event_id="EVT-1",
            exception_key=exception_key("fidelis", "M-1"),
            action=ExceptionEventAction.RESOLVED,
            source_system="fidelis",
            source_member_id="M-1",
            occurred_ts=NOW,
            batch_id="B-1",
        )


def test_an_event_whose_key_disagrees_with_its_own_fields_refuses() -> None:
    with pytest.raises(IdentityExceptionError):
        IdentityExceptionEvent(
            event_id="EVT-1",
            exception_key="mismatched-key",
            action=ExceptionEventAction.OCCURRENCE,
            source_system="fidelis",
            source_member_id="M-1",
            occurred_ts=NOW,
            batch_id="B-1",
            outcome=MatchOutcome.UNRESOLVED,
        )
