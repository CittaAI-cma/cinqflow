"""CF-V2-E12-01 — the morning question, and the spreadsheet made unbuildable.

    "Given twelve feeds expected today, ten received, one missing, one at risk,
     when an operator opens the home page, then the counters read 12/10/1/1,
     the missing UHC file leads the attention list with 'expected 6:00 AM — not
     received', and one click opens its investigation view."
    "Given the control tables are momentarily unreachable, when the page loads,
     then it shows its last refreshed time and a clear 'data may be stale'
     banner — never silently stale numbers."
    — CF-V2-E12-01

The test that carries the design is
`test_a_counter_with_no_query_behind_it_cannot_be_constructed`: the don't says
no hand-maintained figures, and the way to mean it is a type that refuses one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.model.vocabulary import BatchState, StatusWord
from cinqflow.core.operations import (
    ArrivalCondition,
    Counter,
    Expectation,
    UntraceableNumberError,
    build_board,
    domains,
    impact_of,
    resolve_due,
    stale_board,
)
from cinqflow.core.registry.operations import ServiceLevel
from cinqflow.core.scheduling import DependencyGraph
from cinqflow.ports.control_tables import BatchControl

pytestmark = pytest.mark.unit

DAY = "2026-08-30"
SIX_AM = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)  # 06:00 America/New_York
NOW = datetime(2026, 8, 30, 11, 0, tzinfo=UTC)  # an hour past due

UHC = "uhc-optum-ny-roster"
ENROLLMENT = "centene-medicare-enrollment"
CLAIMS = "centene-medicare-claims"


def expect(feed_id: str, *, domain: str = "Enrollment", due: datetime = SIX_AM) -> Expectation:
    return Expectation(
        feed_id=feed_id,
        domain=domain,
        business_date=DAY,
        due_ts=due,
        grace_minutes=30,
        escalate_after_minutes=120,
    )


def batch(feed_id: str, state: BatchState = BatchState.COMPLETED) -> BatchControl:
    return BatchControl(
        batch_id=f"B-{feed_id}",
        feed_id=feed_id,
        feed_version=1,
        business_date=DAY,
        state=state,
        started_ts=SIX_AM,
    )


# ── the happy path, figure for figure ────────────────────────────────────────
def test_twelve_expected_ten_received_one_missing_one_at_risk() -> None:
    """The story's own arithmetic."""
    received = [expect(f"feed-{i:02d}") for i in range(10)]
    missing = expect(UHC)
    at_risk = expect("at-risk-feed", due=NOW - timedelta(minutes=10))

    board = build_board(
        business_date=DAY,
        expectations=[*received, missing, at_risk],
        batches=[batch(e.feed_id) for e in received],
        now=NOW,
    )
    assert board.totals == (12, 10, 1, 1)


def test_the_missing_file_leads_the_attention_list_with_the_storys_sentence() -> None:
    board = build_board(
        business_date=DAY,
        expectations=[expect(UHC)],
        batches=[],
        now=NOW,
    )
    (item,) = board.attention
    assert item.feed_id == UHC
    assert "not received" in item.headline
    assert item.status is StatusWord.MISSING


def test_one_click_opens_the_investigation_view() -> None:
    """`CitationId.route` — the platform's own address space, so this needed no
    link scheme of its own."""
    board = build_board(business_date=DAY, expectations=[expect(UHC)], batches=[], now=NOW)
    assert board.attention[0].route.startswith("/data/intake/feed/")

    with_batch = build_board(
        business_date=DAY,
        expectations=[expect(UHC)],
        batches=[batch(UHC, BatchState.FAILED)],
        now=NOW,
    )
    assert with_batch.attention[0].route.startswith("/operations/control/batch/")


def test_a_quiet_morning_says_so() -> None:
    board = build_board(
        business_date=DAY,
        expectations=[expect(UHC)],
        batches=[batch(UHC)],
        now=NOW,
    )
    assert board.attention == ()
    assert "Nothing needs you this morning" in board.explain()


# ── the don't ────────────────────────────────────────────────────────────────
def test_a_counter_with_no_query_behind_it_cannot_be_constructed() -> None:
    """THE DON'T, AS A TYPE.

    "Show any number that cannot be traced to a control-table query — no
    hand-maintained figures anywhere." `daily_status.xlsx` made unbuildable
    rather than discouraged.
    """
    with pytest.raises(UntraceableNumberError) as refused:
        Counter(label="expected", value=12, derived_from="   ")
    assert "spreadsheet this screen replaces" in str(refused.value)


def test_every_counter_on_a_real_board_names_its_source() -> None:
    board = build_board(business_date=DAY, expectations=[expect(UHC)], batches=[], now=NOW)
    assert all(counter.derived_from.strip() for counter in board.counters)


# ── ranked by business impact, not by timestamp ──────────────────────────────
def test_the_roster_eleven_feeds_wait_on_outranks_a_standalone_extract() -> None:
    """Sorting by time puts the overnight batch nobody consumes above the
    roster eleven feeds wait for, every single morning."""
    graph = DependencyGraph(upstreams={CLAIMS: (ENROLLMENT,), "gold-quality": (CLAIMS,)})
    board = build_board(
        business_date=DAY,
        expectations=[
            # Four hours late, and nothing depends on it.
            expect("standalone-extract", due=NOW - timedelta(hours=4)),
            # Forty-five minutes late, and two feeds wait on it.
            expect(ENROLLMENT, due=NOW - timedelta(minutes=45)),
        ],
        batches=[],
        graph=graph,
        now=NOW,
    )
    assert [item.feed_id for item in board.attention] == [ENROLLMENT, "standalone-extract"]


def test_the_ranking_shows_its_reasoning() -> None:
    """An operator who cannot see why will re-sort by time, and the ranking has
    bought nothing."""
    graph = DependencyGraph(upstreams={CLAIMS: (ENROLLMENT,)})
    board = build_board(
        business_date=DAY,
        expectations=[expect(ENROLLMENT)],
        batches=[],
        graph=graph,
        now=NOW,
    )
    assert "1 downstream feed(s) wait on it" in board.attention[0].why
    assert CLAIMS in board.attention[0].why


@pytest.mark.parametrize("minutes", [0, 1, 30, 240, 1440, 100_000])
def test_lateness_can_never_outweigh_a_single_blocked_feed(minutes: int) -> None:
    """THE INVARIANT, over the whole range.

    A roster eleven feeds wait on outranks a standalone extract four hours
    late — and it still does at nine hours, which is the part a
    minutes-per-point scheme gets wrong by mid-morning.
    """
    from cinqflow.core.operations import _BLOCKED_FEED_POINTS, late_points

    assert late_points(minutes) < _BLOCKED_FEED_POINTS


def test_impact_is_computed_from_facts_nobody_types() -> None:
    graph = DependencyGraph(upstreams={CLAIMS: (ENROLLMENT,)})
    board = build_board(
        business_date=DAY, expectations=[expect(ENROLLMENT)], batches=[], now=NOW, graph=graph
    )
    score, why = impact_of(board.rows[0], graph)
    assert score > 0 and why


def test_the_order_is_stable_between_refreshes() -> None:
    """A list that reshuffles under the cursor is a list people stop clicking."""
    expectations = [expect(f"tie-{i}") for i in range(5)]
    first = build_board(business_date=DAY, expectations=expectations, batches=[], now=NOW).attention
    second = build_board(
        business_date=DAY, expectations=list(reversed(expectations)), batches=[], now=NOW
    ).attention
    assert [i.feed_id for i in first] == [i.feed_id for i in second]


# ── a batch that arrived and broke ───────────────────────────────────────────
def test_a_received_file_whose_batch_failed_is_on_the_attention_list() -> None:
    """Worse than a file that did not arrive — somebody may already be reading
    its half-loaded output."""
    board = build_board(
        business_date=DAY,
        expectations=[expect(UHC)],
        batches=[batch(UHC, BatchState.FAILED)],
        now=NOW,
    )
    assert board.totals == (1, 1, 0, 0)
    assert board.attention[0].feed_id == UHC
    assert "batch is failed" in board.attention[0].why


def test_a_received_row_reports_its_batchs_word_not_merely_received() -> None:
    """ "Received" stops being the useful fact the moment loading starts."""
    board = build_board(
        business_date=DAY,
        expectations=[expect(UHC)],
        batches=[batch(UHC, BatchState.IN_PROGRESS)],
        now=NOW,
    )
    assert board.rows[0].status is StatusWord.PROCESSING


# ── the seven words, and the fourth counter ──────────────────────────────────
def test_at_risk_is_a_counter_and_needs_attention_is_the_word() -> None:
    """Adding an eighth status word to make the two match would break the
    lexicon test to save a subtraction."""
    assert ArrivalCondition.AT_RISK.status_word is StatusWord.NEEDS_ATTENTION
    assert all(condition.status_word in set(StatusWord) for condition in ArrivalCondition)


def test_grace_separates_at_risk_from_missing() -> None:
    inside = expect("f", due=NOW - timedelta(minutes=20))
    outside = expect("f", due=NOW - timedelta(minutes=40))
    assert inside.condition(arrived=False, now=NOW) is ArrivalCondition.AT_RISK
    assert outside.condition(arrived=False, now=NOW) is ArrivalCondition.MISSING


def test_nothing_is_ever_early() -> None:
    future = expect("f", due=NOW + timedelta(hours=2))
    assert future.minutes_late(NOW) == 0
    assert future.condition(arrived=False, now=NOW) is ArrivalCondition.EXPECTED


# ── never silently stale ─────────────────────────────────────────────────────
def test_an_unreachable_plane_keeps_the_numbers_and_says_they_are_old() -> None:
    """Zeros would read as "nothing expected today", which on a morning when
    the plane is down is the most dangerous thing this screen could say."""
    live = build_board(
        business_date=DAY,
        expectations=[expect(UHC), expect("other")],
        batches=[batch("other")],
        now=NOW,
    )
    stale = stale_board(live, now=NOW + timedelta(minutes=7))

    assert stale.totals == live.totals
    assert stale.freshness.may_be_stale
    banner = stale.freshness.banner(NOW + timedelta(minutes=7))
    assert "Data may be stale" in banner
    assert "7 minute(s) ago" in banner


def test_a_live_board_shows_no_banner() -> None:
    board = build_board(business_date=DAY, expectations=[expect(UHC)], batches=[], now=NOW)
    assert board.freshness.banner(NOW) == ""


# ── filtering by domain ──────────────────────────────────────────────────────
def test_filtering_recomputes_the_counters() -> None:
    """A board showing twelve expected and two rows is a screen whose header
    and body disagree — and the header is the half people quote in stand-up."""
    board = build_board(
        business_date=DAY,
        expectations=[
            expect("a", domain="Enrollment"),
            expect("b", domain="Claims"),
            expect("c", domain="Claims"),
        ],
        batches=[batch("b")],
        now=NOW,
    )
    claims = board.in_domain("Claims")
    assert claims.totals == (2, 1, 1, 0)
    assert domains(board) == ("Claims", "Enrollment")


# ── the SLA is the feed's own ────────────────────────────────────────────────
def test_the_expectation_comes_from_the_feeds_declared_service_level() -> None:
    """Not a global threshold: a roster due at 06:00 and a weekly claims
    extract are late at different speeds."""
    sla = ServiceLevel(
        expected_by_local_time="06:00",
        timezone="America/New_York",
        grace_minutes=45,
        escalate_after_minutes=180,
    )
    expectation = Expectation.from_service_level(
        feed_id=UHC,
        domain="Enrollment",
        business_date=DAY,
        service_level=sla,
        due_ts=SIX_AM,
    )
    assert expectation.grace_minutes == 45
    # 40 minutes late is MISSING under a 30-minute grace and AT RISK under 45.
    assert (
        expectation.condition(arrived=False, now=SIX_AM + timedelta(minutes=40))
        is ArrivalCondition.AT_RISK
    )


def test_the_offset_is_supplied_rather_than_assumed() -> None:
    """`ServiceLevel`'s own docstring warns that an offset is wrong for half the
    year. Taking it as a parameter is what keeps that honest."""
    due = resolve_due(
        business_day=datetime(2026, 8, 30, tzinfo=UTC),
        expected_by_local_time="06:00",
        offset=timedelta(hours=-4),  # EDT
    )
    assert due == datetime(2026, 8, 30, 10, 0, tzinfo=UTC)

    winter = resolve_due(
        business_day=datetime(2026, 1, 30, tzinfo=UTC),
        expected_by_local_time="06:00",
        offset=timedelta(hours=-5),  # EST
    )
    assert winter == datetime(2026, 1, 30, 11, 0, tzinfo=UTC)


# ── the page is opened hundreds of times a day ───────────────────────────────
def test_a_full_days_board_builds_far_inside_its_budget() -> None:
    """ "Load in under two seconds." The COMPUTE has to be a rounding error
    inside that, since the query and the render also have to fit."""
    import time

    expectations = [expect(f"feed-{i:03d}", domain=f"D{i % 5}") for i in range(500)]
    batches = [batch(e.feed_id) for e in expectations[:400]]
    graph = DependencyGraph(
        upstreams={f"feed-{i:03d}": (f"feed-{i - 1:03d}",) for i in range(1, 500)}
    )
    started = time.perf_counter()
    board = build_board(
        business_date=DAY,
        expectations=expectations,
        batches=batches,
        graph=graph,
        now=NOW,
    )
    elapsed = time.perf_counter() - started
    assert board.totals[0] == 500
    assert elapsed < 1.0, f"board build took {elapsed:.2f}s of a 2s page budget"
