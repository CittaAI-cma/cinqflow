"""CF-V0-E2-01 — the permission matrix, tested exhaustively.

A permission matrix tested by clicking is a permission matrix nobody tests. So
the decision is pure, and every role is checked against every action here.
"""

from __future__ import annotations

import pytest

from cinqflow.core.security import Action, may, visible_feeds
from cinqflow.ports.authn import Principal, Role, Scopes

pytestmark = pytest.mark.unit


def _principal(*roles: Role, feeds: tuple[str, ...] = ("*",)) -> Principal:
    return Principal(
        subject="user@cinqcare.test",
        display_name="A User",
        roles=frozenset(roles),
        scopes=Scopes(domains=frozenset({"*"}), feeds=frozenset(feeds)),
    )


ENGINEER = _principal(Role.ENGINEER)
READ_ONLY = _principal(Role.READ_ONLY)
ADMIN = _principal(Role.ADMINISTRATOR)
NOBODY = Principal(subject="nobody@cinqcare.test", display_name="Unassigned")


# ── the guardrail that ships before any edit screen exists ───────────────────
@pytest.mark.parametrize("action", [a for a in Action if a.changes_things])
def test_a_read_only_user_is_refused_every_action_that_changes_anything(
    action: Action,
) -> None:
    """ "Give Read-Only users full visibility but NO BUTTONS THAT CHANGE
    ANYTHING" — and the server refuses too, not just the menu.

    Parametrised across every mutating action, so a NEW action is refused by
    default rather than permitted by omission.
    """
    decision = may(READ_ONLY, action)
    assert decision.allowed is False
    assert decision.reason


def test_a_read_only_user_can_see_everything_and_ask_the_agent() -> None:
    """ "full visibility". Read-Only is not a lesser view of the data — it is
    the same view without the buttons."""
    assert may(READ_ONLY, Action.VIEW).allowed is True
    assert may(READ_ONLY, Action.ASK_AGENT).allowed is True


def test_an_engineer_may_run_and_retry_but_not_approve() -> None:
    """ "Separate create, approve, publish and operate rights" — the MVP's
    segregation requirement. The person who builds a feed does not sign it
    off."""
    assert may(ENGINEER, Action.RUN_PIPELINE).allowed is True
    assert may(ENGINEER, Action.RETRY_BATCH).allowed is True
    assert may(ENGINEER, Action.APPROVE).allowed is False
    assert may(ENGINEER, Action.PUBLISH).allowed is False


def test_an_administrator_manages_access_and_cannot_approve_either() -> None:
    """The person who GRANTS permissions being able to use them all is how
    segregation of duty quietly dies."""
    assert may(ADMIN, Action.MANAGE_USERS).allowed is True
    assert may(ADMIN, Action.APPROVE).allowed is False
    assert may(ADMIN, Action.EDIT_FEED).allowed is False


def test_nobody_in_a_group_gets_a_clear_message_not_a_broken_app() -> None:
    """ "they see a clear 'no access assigned — contact your administrator'
    page ... they are NEVER shown a broken or empty application."

    Which requires the refusal to carry that exact sentence, so the page can
    render the reason rather than inventing one.
    """
    decision = may(NOBODY, Action.VIEW)
    assert decision.allowed is False
    assert decision.reason == "no access assigned — contact your administrator"


# ── scope filters the decision, and does not leak ────────────────────────────
def test_an_out_of_scope_feed_is_refused_without_revealing_it_exists() -> None:
    """ "then access is denied, NOTHING IS REVEALED, and the attempt is logged."

    The refusal must be indistinguishable from "no such feed" — otherwise the
    denial itself tells the caller which feeds are real.
    """
    scoped = _principal(Role.ENGINEER, feeds=("ga-enrollment",))
    decision = may(scoped, Action.EDIT_FEED, feed_id="fidelis-downstate-roster")
    assert decision.allowed is False
    assert decision.reason == "out of scope"
    assert "fidelis" not in decision.reason


def test_an_in_scope_feed_is_permitted() -> None:
    scoped = _principal(Role.ENGINEER, feeds=("ga-enrollment",))
    assert may(scoped, Action.EDIT_FEED, feed_id="ga-enrollment").allowed is True


def test_out_of_scope_feeds_are_invisible_in_lists() -> None:
    """ "an out-of-scope feed is invisible in lists, search and exports alike"

    The filter is applied where the list is BUILT — never to a response that
    was already assembled, because the row was fetched and every future path
    that forgets exposes it.
    """
    analyst = _principal(Role.READ_ONLY, feeds=("ga-enrollment",))
    assert visible_feeds(analyst, ("ga-enrollment", "fidelis-downstate-roster")) == (
        "ga-enrollment",
    )


def test_a_user_with_no_access_sees_no_feeds_at_all() -> None:
    assert visible_feeds(NOBODY, ("ga-enrollment",)) == ()


# ── the matrix, in full ──────────────────────────────────────────────────────
@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("action", list(Action))
def test_every_role_against_every_action_has_a_definite_answer(role: Role, action: Action) -> None:
    """No combination falls through to an accidental default.

    This is the test that makes the matrix a matrix rather than a pile of
    conditionals — and it is why a NEW action is denied by default: it will not
    appear in any role's set until someone adds it deliberately.
    """
    decision = may(_principal(role), action)
    assert isinstance(decision.allowed, bool)
    if not decision.allowed:
        assert decision.reason, f"{role}/{action} denied without a reason"


def test_a_decision_is_truthy_so_call_sites_read_naturally() -> None:
    """`if may(...)` rather than `if may(...).allowed` — because the version
    that is easy to write must also be the version that is correct."""
    assert bool(may(ENGINEER, Action.VIEW)) is True
    assert bool(may(READ_ONLY, Action.EDIT_FEED)) is False
