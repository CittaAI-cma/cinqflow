"""CF-V1-E8-03 — dependencies, holds, and the release nobody has to perform.

    "Given claims processing depends on the month's enrollment batch, when
     enrollment completes, then claims start automatically; the run history
     shows the dependency chain that gated them."
    "Given enrollment fails at Silver Raw, when the claims schedule fires, then
     claims hold in Waiting-on-Upstream with the blocking batch linked,
     Operations is notified once (not per held run), and everything resumes
     when enrollment recovers."
    — CF-V1-E8-03

The acceptance criteria ARE these tests. The one that matters most is
`test_the_hold_releases_itself_with_nobody_clearing_anything`: it asserts a
property of the DESIGN, not of a code path — that between "held" and "running"
the only thing that changed was an upstream batch row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType, BatchState, Layer, StatusWord
from cinqflow.core.scheduling import (
    DEPENDS_ON_KEY,
    BatchLike,
    CircularDependencyError,
    DependencyGraph,
    DependencyHoldError,
    HoldReason,
    ReleaseDecision,
    decide,
    guard_start,
    notice_for,
    picture,
    recovered,
)
from cinqflow.ports.control_tables import BatchControl, StageStatus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 6, 0, tzinfo=UTC)
ENGINEER = Actor(subject="dev-engineer@cinqcare.test", actor_type=ActorType.HUMAN)

ENROLLMENT = "centene-medicare-enrollment"
CLAIMS = "centene-medicare-claims"
REFERENCE = "cms-reference-data"
PERIOD = "2026-08"


def batch(
    feed_id: str,
    business_date: str,
    state: BatchState,
    *,
    batch_id: str | None = None,
    minutes: int = 0,
) -> BatchControl:
    return BatchControl(
        batch_id=batch_id or f"{feed_id}-{business_date}",
        feed_id=feed_id,
        feed_version=1,
        business_date=business_date,
        state=state,
        started_ts=NOW + timedelta(minutes=minutes),
    )


def feed(
    feed_id: str, *, depends_on: tuple[str, ...] = (), published: bool = True
) -> GovernedObject:
    state = LifecycleState.PUBLISHED if published else LifecycleState.DRAFT
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=feed_id,
        version=1,
        lifecycle_state=state,
        created_by=ENGINEER,
        created_ts=NOW,
        body={DEPENDS_ON_KEY: list(depends_on)},
        approved_by=Actor(subject="dev-platform@cinqcare.test", actor_type=ActorType.HUMAN)
        if published
        else None,
        approved_ts=NOW if published else None,
    )


CHAIN = DependencyGraph(upstreams={CLAIMS: (ENROLLMENT,), ENROLLMENT: (REFERENCE,)})
SIMPLE = DependencyGraph(upstreams={CLAIMS: (ENROLLMENT,)})


# ── the graph ────────────────────────────────────────────────────────────────
def test_the_graph_is_read_off_published_feeds_only() -> None:
    """A draft feed's dependency is not approved configuration.

    Letting one gate production would mean a person could stop a live feed by
    saving a draft — the exact inversion of "the engine reads published
    metadata and nothing else".
    """
    graph = DependencyGraph.from_feeds(
        [
            feed(CLAIMS, depends_on=(ENROLLMENT,)),
            feed("draft-feed", depends_on=(CLAIMS,), published=False),
        ]
    )
    assert graph.upstream_of(CLAIMS) == (ENROLLMENT,)
    assert graph.upstream_of("draft-feed") == ()


def test_a_cycle_is_refused_when_the_graph_is_built_not_when_a_run_deadlocks() -> None:
    with pytest.raises(CircularDependencyError) as refused:
        DependencyGraph(upstreams={CLAIMS: (ENROLLMENT,), ENROLLMENT: (CLAIMS,)})
    assert "wait for each other" in str(refused.value)


def test_a_feed_may_not_be_its_own_upstream() -> None:
    with pytest.raises(CircularDependencyError):
        DependencyGraph(upstreams={CLAIMS: (CLAIMS,)})


def test_the_order_is_upstreams_first_and_deterministic() -> None:
    assert CHAIN.order() == (REFERENCE, ENROLLMENT, CLAIMS)


def test_blast_radius_is_everything_a_failure_here_would_stop() -> None:
    """What Operations needs the moment a batch fails: not "one feed is down"
    but "these two will not run tonight"."""
    assert CHAIN.blast_radius(REFERENCE) == (ENROLLMENT, CLAIMS)
    assert CHAIN.blast_radius(CLAIMS) == ()


# ── the happy path ───────────────────────────────────────────────────────────
def test_claims_start_when_enrollment_completes() -> None:
    ruling = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.COMPLETED)],
    )
    assert ruling.may_run
    assert ruling.batch_state is BatchState.RECEIVED
    assert "nothing upstream is holding it" in ruling.explain()


def test_a_feed_with_no_dependencies_always_runs() -> None:
    ruling = decide(feed_id=REFERENCE, business_date=PERIOD, graph=DependencyGraph(), batches=[])
    assert ruling.may_run


# ── the exception: enrollment fails at Silver Raw ────────────────────────────
def test_claims_hold_with_the_blocking_batch_and_the_layer_named() -> None:
    failed = batch(ENROLLMENT, PERIOD, BatchState.FAILED, batch_id="ENR-8842")
    ruling = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[failed],
        stages={
            "ENR-8842": [
                StageStatus(
                    batch_id="ENR-8842",
                    stage=Layer.SILVER_RAW,
                    state=BatchState.FAILED,
                    started_ts=NOW,
                )
            ]
        },
    )
    assert not ruling.may_run
    blocker = ruling.blockers[0]
    assert blocker.reason is HoldReason.UPSTREAM_FAILED
    assert blocker.batch_id == "ENR-8842"
    assert blocker.layer is Layer.SILVER_RAW
    assert "failed at silver_raw" in blocker.explain()
    assert ruling.status_word is StatusWord.NEEDS_ATTENTION


def test_a_hold_that_will_clear_is_waiting_and_a_hold_that_will_not_is_blocked() -> None:
    """WAITING_DEPENDENCY and BLOCKED mean different things, and the Wave-0
    vocabulary already carried both."""
    loading = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.IN_PROGRESS)],
    )
    assert loading.batch_state is BatchState.WAITING_DEPENDENCY

    broken = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.FAILED)],
    )
    assert broken.batch_state is BatchState.BLOCKED


def test_an_upstream_that_never_arrived_is_missing_not_failed() -> None:
    ruling = decide(feed_id=CLAIMS, business_date=PERIOD, graph=SIMPLE, batches=[])
    assert ruling.blockers[0].reason is HoldReason.UPSTREAM_NOT_ARRIVED
    assert ruling.status_word is StatusWord.MISSING


# ── the chain ────────────────────────────────────────────────────────────────
def test_a_two_hop_hold_names_the_root_cause_and_the_path_to_it() -> None:
    """Claims waits on enrollment; enrollment waits on the reference load.

    Reporting only "waiting on enrollment" sends an operator to a feed that is
    itself blameless.
    """
    ruling = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=CHAIN,
        batches=[batch(REFERENCE, PERIOD, BatchState.FAILED, batch_id="REF-1")],
    )
    blocker = ruling.blockers[0]
    assert blocker.feed_id == REFERENCE
    assert blocker.batch_id == "REF-1"
    assert blocker.chain == (CLAIMS, ENROLLMENT, REFERENCE)
    assert ruling.chain == (CLAIMS, ENROLLMENT, REFERENCE)


# ── sequential processing within one feed ────────────────────────────────────
def test_a_months_file_never_processes_before_the_prior_month_completes() -> None:
    ruling = decide(
        feed_id=ENROLLMENT,
        business_date="2026-08",
        graph=DependencyGraph(),
        batches=[batch(ENROLLMENT, "2026-07", BatchState.IN_PROGRESS)],
    )
    assert ruling.blockers[0].reason is HoldReason.PRIOR_PERIOD_INCOMPLETE
    assert ruling.blockers[0].business_date == "2026-07"


def test_every_unfinished_earlier_period_is_reported_not_only_the_newest() -> None:
    """A feed three months behind should say so. Naming only the most recent
    gap would let an operator fix one month and be surprised twice more."""
    ruling = decide(
        feed_id=ENROLLMENT,
        business_date="2026-08",
        graph=DependencyGraph(),
        batches=[
            batch(ENROLLMENT, "2026-05", BatchState.FAILED),
            batch(ENROLLMENT, "2026-06", BatchState.COMPLETED),
            batch(ENROLLMENT, "2026-07", BatchState.FAILED),
        ],
    )
    assert [b.business_date for b in ruling.blockers] == ["2026-05", "2026-07"]


def test_a_restarted_period_is_judged_on_the_restart_not_the_first_attempt() -> None:
    """Otherwise every downstream feed sits behind a batch already fixed —
    the "held work nobody released" failure this module exists to avoid."""
    ruling = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[
            batch(ENROLLMENT, PERIOD, BatchState.FAILED, batch_id="first", minutes=0),
            batch(ENROLLMENT, PERIOD, BatchState.COMPLETED, batch_id="retry", minutes=30),
        ],
    )
    assert ruling.may_run


# ── the pause, reported in the same place ────────────────────────────────────
def test_a_paused_feed_reports_its_pause_beside_its_dependencies() -> None:
    """The operator asking "why has this not run?" wants one answer, not two
    screens. CF-V1-E3-04's pause is a second axis and is reported here."""
    ruling = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.COMPLETED)],
        suspended=frozenset({CLAIMS}),
    )
    assert not ruling.may_run
    assert ruling.blockers[0].reason is HoldReason.FEED_SUSPENDED


# ── the guardrail: no override ───────────────────────────────────────────────
def test_starting_a_held_run_is_refused_and_there_is_no_force_flag() -> None:
    """ "No overrides outside the governed exception flow" — and a keyword
    argument is not a governed exception flow."""
    held = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.FAILED)],
    )
    with pytest.raises(DependencyHoldError):
        guard_start(held)

    import inspect

    assert "force" not in inspect.signature(guard_start).parameters


# ── the release nobody performs ──────────────────────────────────────────────
def test_the_hold_releases_itself_with_nobody_clearing_anything() -> None:
    """THE DESIGN CLAIM, ASSERTED.

    Between held and running, the only thing that changed is one upstream batch
    row. No sweeper ran, no flag was cleared, no operator acted — because a
    hold is a computed answer and not stored state.
    """

    def ask(rows: list[BatchControl]) -> ReleaseDecision:
        return decide(feed_id=CLAIMS, business_date=PERIOD, graph=SIMPLE, batches=rows)

    before = ask([batch(ENROLLMENT, PERIOD, BatchState.FAILED)])
    after = ask([batch(ENROLLMENT, PERIOD, BatchState.COMPLETED)])

    assert not before.may_run
    assert after.may_run
    assert recovered(before, after)


# ── notifying once ───────────────────────────────────────────────────────────
def test_the_same_hold_produces_the_same_dedupe_key_every_evaluation() -> None:
    """Notify-once, by the producer. `queue.message` already makes a repeated
    dedupe_key return the existing message."""
    rows = [batch(ENROLLMENT, PERIOD, BatchState.FAILED, batch_id="ENR-1")]
    keys = {
        decide(feed_id=CLAIMS, business_date=PERIOD, graph=SIMPLE, batches=rows).notice_key
        for _ in range(24)
    }
    assert len(keys) == 1


def test_a_hold_whose_root_cause_changes_produces_a_second_message() -> None:
    failed = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.FAILED, batch_id="ENR-1")],
    )
    absent = decide(feed_id=CLAIMS, business_date=PERIOD, graph=SIMPLE, batches=[])
    assert failed.notice_key != absent.notice_key


def test_a_hold_that_clears_itself_pages_nobody() -> None:
    """A team paged because last month's batch is still loading learns to
    ignore the channel that also carries the failures."""
    loading = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.IN_PROGRESS)],
    )
    assert notice_for(loading) is None

    broken = decide(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=SIMPLE,
        batches=[batch(ENROLLMENT, PERIOD, BatchState.FAILED, batch_id="ENR-1")],
    )
    notice = notice_for(broken)
    assert notice is not None
    assert notice.blocking_batch_ids == ("ENR-1",)
    assert notice.topic == "scheduling.hold"


# ── the picture ──────────────────────────────────────────────────────────────
def test_every_held_picture_names_a_root_cause() -> None:
    """A picture that shows a hold and no cause is the screen that sends an
    operator to ask an engineer — the workflow this epic exists to end."""
    drawn = picture(
        feed_id=CLAIMS,
        business_date=PERIOD,
        graph=CHAIN,
        batches=[batch(REFERENCE, PERIOD, BatchState.FAILED, batch_id="REF-1")],
    )
    assert drawn.is_self_explanatory
    assert {n.feed_id for n in drawn.nodes} == {CLAIMS, ENROLLMENT, REFERENCE}
    assert (ENROLLMENT, CLAIMS) in drawn.edges
    causes = [n.feed_id for n in drawn.nodes if n.is_root_cause]
    assert causes == [REFERENCE]


def test_the_picture_shows_the_blast_radius_even_when_nothing_is_wrong() -> None:
    """An engineer editing a feed's schedule needs to know what waits on it
    BEFORE they change it."""
    drawn = picture(
        feed_id=REFERENCE,
        business_date=PERIOD,
        graph=CHAIN,
        batches=[batch(REFERENCE, PERIOD, BatchState.COMPLETED)],
    )
    assert drawn.decision is not None and drawn.decision.may_run
    assert drawn.blast_radius == (ENROLLMENT, CLAIMS)


# ── the port satisfies the core protocol, structurally ───────────────────────
def test_the_real_control_row_satisfies_what_core_declares_it_reads() -> None:
    """core does not import the port — the port's own dataclass fits the
    Protocol core states. If that ever stops being true, this fails here rather
    than at a call site."""
    assert isinstance(batch(ENROLLMENT, PERIOD, BatchState.COMPLETED), BatchLike)
