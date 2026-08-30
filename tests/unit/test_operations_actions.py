"""CF-V2-E12-03 — see, decide, act, all audited, in one place.

    "Given a batch failed on a transient cluster error, when the operator
     clicks Retry and (in production) attaches the approval, then the batch
     resumes from its last completed stage, succeeds, and the issue shows who
     retried, why, and the verified outcome."
    "Given an operator tries to retry a batch whose feed is Paused, when they
     act, then the action is refused with the reason ('feed paused by J. Smith
     — mapping change pending') and a link to the pause record."
    "Given the circuit breaker is open or the target is on hold, when the
     action is triggered, then the system refuses, notifies a human, and
     records the refusal."
    — CF-V2-E12-03

Two tests carry the design. `test_a_requested_action_is_not_a_successful_one`
is the story's own sentence made structural, and
`test_every_offered_action_actually_authorizes` is the archetype's rule that a
console must never draw a button it would then refuse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer, StatusWord
from cinqflow.core.operations.actions import (
    ALLOWED_STATES,
    ActionError,
    ActionPhase,
    ActionRequest,
    Breaker,
    Environment,
    Issue,
    OpsAction,
    RateLimit,
    RefusalReason,
    RefusedError,
    apply_to_issue,
    authorize,
    fail,
    offered,
    preview,
    refused_action,
    request_action,
    unverified,
    verify,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BATCH = "1244"
FEED = "fidelis-downstate-roster"

SAM = Actor(subject="sam@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Sam Okafor")
AGENT = Actor(subject="failure-fingerprint", actor_type=ActorType.AI)


def retry(**overrides: object) -> ActionRequest:
    base: dict[str, object] = {
        "action": OpsAction.RETRY,
        "target": BATCH,
        "actor": SAM,
        "reason": "Transient cluster error; the guide says retry.",
    }
    base.update(overrides)
    return ActionRequest(**base)  # type: ignore[arg-type]


# ── the happy path ───────────────────────────────────────────────────────────
def test_a_production_retry_with_an_approval_is_authorized() -> None:
    authorize(
        retry(approval_identifier="CHG-88421"),
        environment=Environment.PRODUCTION,
        batch_state=BatchState.FAILED,
        now=NOW,
    )


def test_the_issue_shows_who_retried_why_and_the_verified_outcome() -> None:
    request = retry(approval_identifier="CHG-88421")
    authorize(
        request,
        environment=Environment.PRODUCTION,
        batch_state=BatchState.FAILED,
        now=NOW,
    )
    record = request_action(request, now=NOW)
    done = verify(
        record,
        observed_state=BatchState.COMPLETED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="resumed from silver_raw; 9,992 rows loaded",
        now=NOW,
    )

    issue = apply_to_issue(Issue(issue_id="I-1", feed_id=FEED, target=BATCH, opened_ts=NOW), done)
    rendered = issue.render()
    assert "Sam Okafor" in rendered
    assert "Transient cluster error" in rendered
    assert done.is_complete
    assert "9,992 rows loaded" in done.explain()


# ── 'retry requested' is not 'retry succeeded' ───────────────────────────────
def test_a_requested_action_is_not_a_successful_one() -> None:
    """THE STORY'S OWN SENTENCE, MADE STRUCTURAL.

    Every console in this estate's history had a Retry button that turned green
    when the request was accepted.
    """
    record = request_action(retry(), now=NOW)
    assert record.phase is ActionPhase.REQUESTED
    assert not record.is_complete
    assert record.status is StatusWord.PROCESSING
    assert "not yet verified" in record.explain()


def test_a_verification_must_say_what_was_observed() -> None:
    """ "Succeeded" with no detail is the green tick this surface refuses."""
    record = request_action(retry(), now=NOW)
    with pytest.raises(ActionError) as refused:
        verify(
            record,
            observed_state=BatchState.COMPLETED,
            expected=frozenset({BatchState.COMPLETED}),
            outcome="   ",
            now=NOW,
        )
    assert "green tick" in str(refused.value)


def test_an_action_that_ran_and_failed_is_recorded_not_silent() -> None:
    record = request_action(retry(), now=NOW)
    failed = fail(record, outcome="the cluster refused the job again", now=NOW)
    assert failed.phase is ActionPhase.FAILED
    assert failed.status is StatusWord.NEEDS_ATTENTION
    assert not failed.is_complete


def test_an_outcome_cannot_be_recorded_twice() -> None:
    record = verify(
        request_action(retry(), now=NOW),
        observed_state=BatchState.COMPLETED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="fine",
        now=NOW,
    )
    with pytest.raises(ActionError):
        verify(
            record,
            observed_state=BatchState.COMPLETED,
            expected=frozenset({BatchState.COMPLETED}),
            outcome="fine again",
            now=NOW,
        )


def test_actions_nobody_verified_are_a_finding_the_api_can_ask_for() -> None:
    """The measurable is "production actions ... are verified after execution",
    and this is how that is checked rather than assumed."""
    stale = request_action(retry(), now=NOW - timedelta(hours=2))
    fresh = request_action(retry(), now=NOW)
    done = verify(
        request_action(retry(), now=NOW - timedelta(hours=3)),
        observed_state=BatchState.COMPLETED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="ok",
        now=NOW,
    )

    found = unverified([stale, fresh, done], now=NOW, after=timedelta(hours=1))
    assert [r.requested_ts for r in found] == [stale.requested_ts]


# ── only safe actions are offered ────────────────────────────────────────────
def test_every_offered_action_actually_authorizes() -> None:
    """A console that draws a button and then refuses it teaches people that
    refusals are noise."""
    for state in [*BatchState, None]:
        for paused in (False, True):
            for action in offered(batch_state=state, feed_paused=paused):
                request = ActionRequest(
                    action=action,
                    target=BATCH,
                    actor=SAM,
                    reason="because",
                    assignee="mei@cinqcare.test",
                )
                authorize(
                    request,
                    environment=Environment.DEVELOPMENT,
                    batch_state=state,
                    feed_paused=paused,
                    now=NOW,
                )


def test_retry_is_not_offered_on_a_running_batch() -> None:
    """Retrying a running batch is how two writers end up in the same target
    table, and retrying a completed one is how a month loads twice."""
    assert OpsAction.RETRY not in offered(batch_state=BatchState.IN_PROGRESS)
    assert OpsAction.RETRY not in offered(batch_state=BatchState.COMPLETED)
    assert OpsAction.RETRY in offered(batch_state=BatchState.FAILED)


def test_resume_is_offered_only_on_a_paused_feed_and_pause_only_otherwise() -> None:
    running = offered(batch_state=BatchState.FAILED, feed_paused=False)
    paused = offered(batch_state=BatchState.FAILED, feed_paused=True)
    assert OpsAction.PAUSE in running and OpsAction.RESUME not in running
    assert OpsAction.RESUME in paused and OpsAction.PAUSE not in paused


def test_an_open_breaker_offers_no_production_action() -> None:
    available = offered(batch_state=BatchState.FAILED, breaker=Breaker(is_open=True, reason="held"))
    assert not any(action.mutates_production for action in available)
    # Bookkeeping is still possible — an operator must be able to write down
    # what they found while the breaker is open.
    assert OpsAction.NOTE in available


def test_bookkeeping_is_never_gated_on_batch_state() -> None:
    """Gating a note on state would mean an operator could not write down what
    they found while a batch was still running."""
    for action in (OpsAction.ACKNOWLEDGE, OpsAction.ASSIGN, OpsAction.NOTE):
        assert ALLOWED_STATES[action] == frozenset()


# ── the exception ────────────────────────────────────────────────────────────
def test_retrying_a_paused_feed_is_refused_with_the_reason_and_a_link() -> None:
    pause = CitationId(kind=CitationKind.FEED, subject=FEED)
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(),
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.FAILED,
            feed_paused=True,
            paused_reason="feed paused by J. Smith — mapping change pending",
            pause_citation=pause,
            now=NOW,
        )
    refusal = refused.value.refusal
    assert refusal.reason is RefusalReason.FEED_PAUSED
    assert "J. Smith" in refusal.detail
    assert refusal.route.startswith("/data/intake/feed/")


# ── the guardrail ────────────────────────────────────────────────────────────
def test_an_open_breaker_refuses_notifies_and_is_recordable() -> None:
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(),
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.FAILED,
            breaker=Breaker(is_open=True, reason="cluster on hold"),
            now=NOW,
        )
    refusal = refused.value.refusal
    assert refusal.reason is RefusalReason.BREAKER_OPEN
    assert refusal.notifies, "a breaker refusal must reach a human"


def test_only_the_breaker_pages_somebody() -> None:
    """Paging on a wrong-state refusal would train people to ignore the
    channel that also carries the breaker."""
    assert not RefusalReason.WRONG_STATE.notifies_a_human
    assert not RefusalReason.NO_APPROVAL_IDENTIFIER.notifies_a_human
    assert RefusalReason.BREAKER_OPEN.notifies_a_human


def test_a_retry_storm_is_rate_limited_per_target() -> None:
    """Three operators each retrying twice is still six retries at the
    cluster."""
    recent = [NOW - timedelta(minutes=m) for m in (1, 3, 5)]
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(),
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.FAILED,
            rate_limit=RateLimit(max_actions=3, window=timedelta(minutes=15)),
            recent_actions=recent,
            now=NOW,
        )
    assert refused.value.refusal.reason is RefusalReason.RATE_LIMITED


def test_actions_outside_the_window_do_not_count_toward_the_limit() -> None:
    old = [NOW - timedelta(minutes=m) for m in (60, 90, 120)]
    authorize(
        retry(),
        environment=Environment.DEVELOPMENT,
        batch_state=BatchState.FAILED,
        rate_limit=RateLimit(max_actions=3, window=timedelta(minutes=15)),
        recent_actions=old,
        now=NOW,
    )


def test_a_production_mutation_without_an_approval_identifier_is_refused() -> None:
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(),
            environment=Environment.PRODUCTION,
            batch_state=BatchState.FAILED,
            now=NOW,
        )
    assert refused.value.refusal.reason is RefusalReason.NO_APPROVAL_IDENTIFIER


def test_bookkeeping_needs_no_approval_identifier_even_in_production() -> None:
    """If acknowledging needed a change ticket, the requirement would mean
    nothing where it matters."""
    authorize(
        ActionRequest(action=OpsAction.ACKNOWLEDGE, target=BATCH, actor=SAM),
        environment=Environment.PRODUCTION,
        now=NOW,
    )


def test_an_agent_cannot_act_on_the_surface() -> None:
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(actor=AGENT),
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.FAILED,
            now=NOW,
        )
    assert refused.value.refusal.reason is RefusalReason.NOT_A_HUMAN


def test_every_action_but_acknowledgement_says_why() -> None:
    """A pause with no stated reason becomes a mystery nobody dares unpause."""
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(reason="  "),
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.FAILED,
            now=NOW,
        )
    assert refused.value.refusal.reason is RefusalReason.NO_REASON_GIVEN


def test_the_state_of_the_world_is_reported_before_the_callers_own_inputs() -> None:
    """Telling somebody their approval identifier is missing when the feed is
    paused sends them to raise a ticket for an action refused anyway."""
    with pytest.raises(RefusedError) as refused:
        authorize(
            retry(reason="", approval_identifier=""),
            environment=Environment.PRODUCTION,
            batch_state=BatchState.FAILED,
            feed_paused=True,
            paused_reason="paused",
            now=NOW,
        )
    assert refused.value.refusal.reason is RefusalReason.FEED_PAUSED


# ── the don't ────────────────────────────────────────────────────────────────
def test_no_type_here_can_carry_a_command() -> None:
    """ "Offer free-form commands or raw SQL anywhere." The way to mean it is a
    request type with nowhere to put one."""
    from dataclasses import fields

    names = {f.name for f in fields(ActionRequest)}
    assert not (names & {"command", "sql", "query", "script", "parameters", "payload"})


# ── the preview ──────────────────────────────────────────────────────────────
def test_the_preview_says_the_scope_before_the_button_is_pressed() -> None:
    """An operator about to reprocess is entitled to know whether that is 200
    rows or 22 million BEFORE they press it."""
    shown = preview(
        retry(resume_from=Layer.SILVER_RAW),
        environment=Environment.PRODUCTION,
        scope_records=22_000_000,
        scope_stages=[Layer.SILVER_RAW],
        estimated_minutes=45,
    )
    text = shown.explain()
    assert "Re-run 1244 from silver_raw" in text
    assert "22,000,000 rows re-enter" in text
    assert "approval identifier is required" in text


def test_a_development_preview_does_not_demand_an_approval() -> None:
    shown = preview(retry(), environment=Environment.DEVELOPMENT)
    assert not shown.requires_approval_identifier


# ── the thread ───────────────────────────────────────────────────────────────
def test_the_thread_carries_a_handoff() -> None:
    issue = Issue(issue_id="I-1", feed_id=FEED, target=BATCH, opened_ts=NOW)
    assert issue.status is StatusWord.NEEDS_ATTENTION

    acknowledged = apply_to_issue(
        issue,
        request_action(
            ActionRequest(action=OpsAction.ACKNOWLEDGE, target=BATCH, actor=SAM), now=NOW
        ),
    )
    assert acknowledged.acknowledged_by == SAM.subject
    assert acknowledged.status is StatusWord.NEEDS_REVIEW

    assigned = apply_to_issue(
        acknowledged,
        request_action(
            ActionRequest(
                action=OpsAction.ASSIGN, target=BATCH, actor=SAM, reason="Mei owns Fidelis."
            ),
            now=NOW,
        ),
        text="mei@cinqcare.test",
    )
    assert assigned.assignee == "mei@cinqcare.test"
    assert len(assigned.thread) == 2
    assert "mei@cinqcare.test" in assigned.render()


def test_nothing_reaches_the_thread_without_a_record_behind_it() -> None:
    """An entry with no record behind it would be a claim rather than a fact."""
    import inspect

    signature = inspect.signature(apply_to_issue)
    assert list(signature.parameters)[1] == "record"


# ── the recovery toolkit joins the same surface ──────────────────────────────
def test_every_action_has_a_row_in_the_allowed_state_matrix() -> None:
    """A new action costs a row. That toll is what stops an eleventh arriving
    as a text box."""
    assert set(ALLOWED_STATES) == set(OpsAction)


def test_every_action_says_what_success_looks_like() -> None:
    """CF-V2-E12-03 — the fourth table. `verify` reads its `expected` from
    here, and an action missing a row is one the verifier cannot ever move
    past REQUESTED."""
    from cinqflow.core.operations.actions import EXPECTED_STATES

    assert set(EXPECTED_STATES) == set(OpsAction)
    for action, expected in EXPECTED_STATES.items():
        assert expected, f"{action.value} has an empty success set — verify() refuses those"


def test_every_action_names_the_permission_it_costs() -> None:
    """CF-V2-E12-03 — the third table an action pays into. An action missing
    from PERMISSION_FOR would be one the server cannot gate, which is how an
    eleventh action arrives ungated rather than as a text box."""
    from cinqflow.core.operations.actions import PERMISSION_FOR

    assert set(PERMISSION_FOR) == set(OpsAction)
    for action, permission in PERMISSION_FOR.items():
        assert permission.changes_things, (
            f"{action.value} is gated on {permission.value}, which Read-Only holds — "
            "every operations action must cost a permission that changes things"
        )


def test_the_recovery_actions_all_reload_data_and_all_need_an_approval() -> None:
    recoveries = {
        OpsAction.RESTART_FROM_STAGE,
        OpsAction.REPROCESS_BATCH,
        OpsAction.REPROCESS_FAILED_ONLY,
        OpsAction.BACKDATE,
    }
    assert all(action.reloads_data for action in recoveries)
    assert all(action.mutates_production for action in recoveries)


def test_bookkeeping_never_reloads_data_and_never_needs_an_approval() -> None:
    """Requiring an approval identifier to write a note would make the surface
    unusable and teach people to route around it."""
    for action in (OpsAction.ACKNOWLEDGE, OpsAction.ASSIGN, OpsAction.NOTE):
        assert not action.reloads_data
        assert not action.mutates_production


def test_a_paused_feed_offers_no_action_that_would_reload_data() -> None:
    available = offered(batch_state=BatchState.FAILED, feed_paused=True)
    assert not any(action.reloads_data for action in available)
    assert OpsAction.RESUME in available


def test_a_verification_checks_the_state_it_read_not_the_prose_it_was_given() -> None:
    """An operator typing 'looks fine' over a batch that is still FAILED is the
    green tick with extra steps."""
    record = request_action(retry(), now=NOW)
    outcome = verify(
        record,
        observed_state=BatchState.FAILED,
        expected=frozenset({BatchState.COMPLETED}),
        outcome="looks fine to me",
        now=NOW,
    )
    assert outcome.phase is ActionPhase.FAILED
    assert not outcome.is_complete
    assert outcome.observed_state is BatchState.FAILED


def test_verifying_against_no_expected_state_is_refused() -> None:
    """A verification that accepts any outcome as success is decoration."""
    with pytest.raises(ActionError, match="what success looks like"):
        verify(
            request_action(retry(), now=NOW),
            observed_state=BatchState.COMPLETED,
            expected=frozenset(),
            outcome="fine",
            now=NOW,
        )


def test_a_refusal_becomes_a_row_rather_than_an_exception_that_vanished() -> None:
    """ "the system refuses, notifies a human, and RECORDS the refusal"."""
    request = retry()
    with pytest.raises(RefusedError) as raised:
        authorize(
            request,
            environment=Environment.DEVELOPMENT,
            batch_state=BatchState.IN_PROGRESS,
            now=NOW,
        )
    row = refused_action(request, raised.value.refusal, now=NOW)
    assert row.phase is ActionPhase.REFUSED
    assert row.status is StatusWord.NEEDS_ATTENTION
    assert not row.is_complete
    assert "refused" in row.explain()
