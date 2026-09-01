"""CF-V1-E7-04 — a rule in technical review cannot be approved.

    "any rule whose generated logic falls below the confidence threshold, or
     that needs constructs the safe subset does not allow, [is] routed
     automatically to a technical reviewer ... so that uncertain logic never
     reaches a pipeline silently"
    "100% of below-threshold rules routed (zero silent publications), verified
     in testing"
    — CF-V1-E7-04

ROUTING HELD; PUBLICATION DID NOT. `core.rules.review.guard_publication` was
written for exactly this seam — "this keeps a rule that was LATER questioned
out of production" — and had no caller anywhere in `src/`. A sentence the
generator refused to write was routed to the queue, appeared on the queue
screen, and could still be approved by the ordinary governance route.

A SECOND, SEPARATE BUG IN THE SAME STORY: the agent wrote
`payload["needs_technical_review"]` and the serializer read
`payload["needs_steward_review"]`, so every routed rule arrived at the API as
an empty list. The measurable was reported as met by a field that was always
empty.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.intelligence.demo import plane, rule_authoring_for

pytestmark = pytest.mark.contract

BA = {"authorization": "Bearer dev-ba@cinqcare.test"}
STEWARD = {"authorization": "Bearer dev-steward@cinqcare.test"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    store, control = plane()
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        rule_authoring_factory=rule_authoring_for,
    )
    with TestClient(app) as test_client:
        yield test_client


def _feed(client: TestClient) -> str:
    return client.get("/api/feeds", headers=BA).json()[0]["feed_id"]


# ── the routing reaches the API at all ───────────────────────────────────────


def test_a_routed_rule_is_reported_as_routed(client: TestClient) -> None:
    """The key mismatch. `needs_technical_review` was ALWAYS empty, whatever
    the agent decided, because the serializer read a key nothing wrote."""
    feed = _feed(client)
    body = client.post(
        f"/api/feeds/{feed}/author-rules",
        json={"stated": ["Members must be valid"]},
        headers=BA,
    ).json()
    assert body["needs_technical_review"], (
        "the agent routed this sentence and the API reported nothing — "
        "'zero silent publications' measured by an always-empty list"
    )


def test_the_two_review_lists_stay_separate(client: TestClient) -> None:
    """A steward DECIDES whether a column is PHI; a technical reviewer
    CORRECTS logic. Folding them into one field would send a rule nobody can
    express to a person who cannot write it either."""
    feed = _feed(client)
    body = client.post(
        f"/api/feeds/{feed}/author-rules", json={"stated": ["Members must be valid"]}, headers=BA
    ).json()
    assert body["needs_technical_review"]
    assert body["needs_steward_review"] == []


def test_the_queue_screen_shows_it(client: TestClient) -> None:
    feed = _feed(client)
    client.post(
        f"/api/feeds/{feed}/author-rules", json={"stated": ["Members must be valid"]}, headers=BA
    )
    queue = client.get(f"/api/feeds/{feed}/rule-reviews", headers=BA).json()
    assert queue["open_count"] >= 1
    assert queue["unrouted"] == [], "a below-threshold rule that reached nobody"


# ── and it blocks the approval ───────────────────────────────────────────────


def _rule_set_stating(client: TestClient, feed: str, sentence: str) -> None:
    """A PARSEABLE rule set whose rule states `sentence`.

    Built through `rule_as_governed` rather than by writing a body by hand,
    because the gate reads the set with `rules_from_governed` and a
    hand-written body would be testing the loader instead of the gate. The
    demo plane's own seeded rules predate the stored-`check` shape and no
    route can read them — which is precisely why the gate tolerates that and
    why this test supplies a set it CAN read.
    """
    from datetime import UTC, datetime

    from cinqflow.core.model.governed import Actor, ObjectType
    from cinqflow.core.model.vocabulary import ActorType
    from cinqflow.core.rules import Check, CheckKind, Dimension, RuleSpec, rule_as_governed

    store = client.app.state.metadata_db
    author = Actor(subject="ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="BA")
    existing = store.history(ObjectType.DQ_RULE, feed)
    version = max((o.version for o in existing), default=0) + 1
    store.save(
        rule_as_governed(
            feed,
            (
                RuleSpec(
                    rule_id="DQ-900",
                    name="first name populated",
                    stated=sentence,
                    check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
                    dimension=Dimension.COMPLETENESS,
                ),
            ),
            author=author,
            version=version,
            created_ts=datetime.now(UTC),
        )
    )


def test_a_rule_set_with_an_open_review_cannot_be_approved(client: TestClient) -> None:
    """The gate `guard_publication` was written for, finally called."""
    feed = _feed(client)
    sentence = "Members must be valid"
    client.post(f"/api/feeds/{feed}/author-rules", json={"stated": [sentence]}, headers=BA)
    queue = client.get(f"/api/feeds/{feed}/rule-reviews", headers=BA).json()
    assert queue["open_count"] >= 1, "nothing was routed, so there is no gate to test"

    _rule_set_stating(client, feed, sentence)
    client.post(f"/api/objects/dq_rule/{feed}/submit", json={}, headers=BA)
    refused = client.post(
        f"/api/objects/dq_rule/{feed}/approve",
        json={"comment": "looks fine to me"},
        headers=STEWARD,
    )
    assert refused.status_code == 409, (
        f"a rule whose sentence is in technical review was approved: "
        f"{refused.status_code} {refused.text[:200]}"
    )
    detail = refused.json()["detail"]
    assert "technical review" in detail
    assert "DQ-900" in detail, "the refusal must name which rule is blocked"


def test_the_refusal_is_recorded(client: TestClient) -> None:
    """Every refusal leaves a row — that is the only evidence the gate ever
    bound anything."""
    feed = _feed(client)
    sentence = "Members must be valid"
    client.post(f"/api/feeds/{feed}/author-rules", json={"stated": [sentence]}, headers=BA)
    _rule_set_stating(client, feed, sentence)
    client.post(f"/api/objects/dq_rule/{feed}/submit", json={}, headers=BA)
    client.post(f"/api/objects/dq_rule/{feed}/approve", json={"comment": "x"}, headers=STEWARD)
    trail = client.get("/api/audit", headers=STEWARD).json()
    assert any(entry["action"] == "refused:technical_review_open" for entry in trail), (
        "the attempt left no row"
    )


def test_a_rule_set_with_no_open_review_is_unaffected(client: TestClient) -> None:
    """A steward approving hand-written rules never meets this gate."""
    feed = _feed(client)
    _rule_set_stating(client, feed, "Member first name must be populated")
    client.post(f"/api/objects/dq_rule/{feed}/submit", json={}, headers=BA)
    response = client.post(
        f"/api/objects/dq_rule/{feed}/approve", json={"comment": "read it"}, headers=STEWARD
    )
    assert response.status_code != 409 or "technical review" not in response.json()["detail"]
