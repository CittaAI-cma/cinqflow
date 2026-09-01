"""CF-V1-E7-03 through the API — configuration, and the two gates at approval.

    "Given the tested DOB rule, when the steward sets Silver Raw / Quarantine /
     threshold 1% and approves, then the rule publishes with version 1 … and
     its configuration is visible on the feed profile."
    "Given a steward tries to publish a Stop-pipeline rule with no alert
     recipient, when they approve, then publication is blocked with the reason:
     a rule that can stop production must page a human."
    — CF-V1-E7-03

`tests/unit/test_rule_policy.py` proves the ladder and the gates. This proves
the routes cannot be talked past them — and, the test that matters, that the
gate bites at APPROVE and leaves a row when it does.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api.app import RULE_EVIDENCE_KEY, create_app
from cinqflow.core.model.governed import Actor, AuditEntry, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.rules import Check, CheckKind, RuleSpec, rule_as_governed

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
BA = "dev-ba@cinqcare.test"
STEWARD = "dev-steward@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"

DOB = "DQ-026"
NAME = "DQ-002"

RULES = (
    RuleSpec(
        rule_id=DOB,
        name="Member date of birth is populated",
        stated="Member date of birth must be populated",
        check=Check(kind=CheckKind.NOT_NULL, column="date_of_birth"),
    ),
    RuleSpec(
        rule_id=NAME,
        name="Member first name is populated",
        stated="Member first name must be populated",
        check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
    ),
)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _policy(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "rule_id": DOB,
        "layer": "silver_raw",
        "on_failure": "quarantine",
        "threshold_percent": "1",
        "execution_order": 10,
    }
    body.update(overrides)
    return body


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    author = Actor(subject=BA, actor_type=ActorType.HUMAN)
    rules = rule_as_governed(FEED, RULES, author=author, created_ts=NOW)
    # CF-V1-E7-02's saved preview, on the body it is evidence about.
    memory.save(
        replace(
            rules,
            body={
                **rules.body,
                RULE_EVIDENCE_KEY: {
                    "sample_rows": 10_000,
                    "previews": [
                        {"rule_id": DOB, "tested": 10_000, "failed": 13},
                        {"rule_id": NAME, "tested": 10_000, "failed": 4},
                    ],
                },
            },
        )
    )
    return memory


@pytest.fixture
def client(store: MemMetadataDb) -> Iterator[TestClient]:
    with TestClient(create_app(authn=StaticAuthn(), metadata_db=store)) as test_client:
        yield test_client


# ── the ladder is served, not hard-coded in the client ───────────────────────
def test_the_six_rungs_arrive_with_their_plain_language(client: TestClient) -> None:
    """A copy of these sentences in a dropdown is a copy that drifts from what
    the engine does."""
    body = client.get(f"/api/feeds/{FEED}/rule-policies", headers=_as(STEWARD)).json()
    assert [rung["value"] for rung in body["ladder"]] == [
        "information",
        "warning",
        "manual_review",
        "quarantine",
        "reject",
        "stop_pipeline",
    ]
    stop = next(rung for rung in body["ladder"] if rung["value"] == "stop_pipeline")
    assert "paged" in stop["in_plain_language"]
    assert stop["needs_a_person"] is True


# ── configuring ──────────────────────────────────────────────────────────────
def test_a_configuration_saves_and_shows_on_the_feed_profile(client: TestClient) -> None:
    saved = client.put(f"/api/feeds/{FEED}/rule-policies", json=[_policy()], headers=_as(BA))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 2, "a policy change writes the NEXT version"
    assert body["lifecycle_state"] == "draft"
    described = body["policies"][0]["describes"]
    assert "runs at silver_raw" in described
    assert "quarantined with its reason" in described
    assert "above 1% of rows" in described


def test_a_layer_the_engine_does_not_reach_is_refused_with_the_list(
    client: TestClient,
) -> None:
    refused = client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(layer="gold")],
        headers=_as(BA),
    )
    assert refused.status_code == 400
    assert "never executes" in refused.text


def test_a_policy_for_a_rule_this_feed_does_not_have_is_refused(
    client: TestClient,
) -> None:
    """It would sit in the body forever and read on the feed profile as
    protection that is not there."""
    refused = client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(rule_id="DQ-999")],
        headers=_as(BA),
    )
    assert refused.status_code == 400
    assert "not a rule of this feed" in refused.text


def test_an_unknown_rung_is_refused_with_the_ladder_named(client: TestClient) -> None:
    refused = client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(on_failure="catastrophic")],
        headers=_as(BA),
    )
    assert refused.status_code == 400
    assert "information -> warning" in refused.text


def test_two_rules_sharing_a_slot_save_but_are_not_approvable(
    client: TestClient,
) -> None:
    """Save is permissive; approval is not — the same split CF-V1-E3-02 made
    for the feed envelope."""
    saved = client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(execution_order=10), _policy(rule_id=NAME, execution_order=10)],
        headers=_as(BA),
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["is_approvable"] is False
    assert any(f["key"] == "ambiguous_order" for f in body["findings"])


def test_a_softening_between_versions_is_named_on_the_response(
    client: TestClient,
) -> None:
    client.put(f"/api/feeds/{FEED}/rule-policies", json=[_policy()], headers=_as(BA))
    softened = client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(on_failure="warning", threshold_percent=None)],
        headers=_as(BA),
    ).json()
    assert softened["softened"] == [DOB]


# ── the gate at approval ─────────────────────────────────────────────────────
def _submit(store: MemMetadataDb) -> None:
    """Move the latest rule set into review so a steward can approve it.

    Written straight to the store rather than through the submit route: the BA
    who authors is a different person from the steward who approves, and this
    test is about the policy gate, not about who may submit.
    """
    latest = store.get(ObjectType.DQ_RULE, FEED)
    moved = replace(latest, lifecycle_state=LifecycleState.PENDING_REVIEW)
    store.record_transition(
        moved,
        AuditEntry(
            object_type=ObjectType.DQ_RULE,
            object_id=FEED,
            version=moved.version,
            action="transition:pending_review",
            actor=Actor(subject=BA, actor_type=ActorType.HUMAN),
            occurred_ts=NOW,
        ),
    )


def test_a_stop_pipeline_rule_with_no_recipient_is_blocked_at_approval(
    client: TestClient, store: MemMetadataDb
) -> None:
    """THE STORY'S OWN EXCEPTION, through the route."""
    client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(on_failure="stop_pipeline", threshold_percent=None)],
        headers=_as(BA),
    )
    _submit(store)
    refused = client.post(
        f"/api/objects/dq_rule/{FEED}/approve",
        json={"comment": "Looks right to me."},
        headers=_as(STEWARD),
    )
    assert refused.status_code == 403
    assert "must page a human" in refused.text


def test_the_refusal_leaves_a_row(client: TestClient, store: MemMetadataDb) -> None:
    client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[_policy(on_failure="stop_pipeline", threshold_percent=None)],
        headers=_as(BA),
    )
    _submit(store)
    client.post(
        f"/api/objects/dq_rule/{FEED}/approve",
        json={"comment": "Looks right to me."},
        headers=_as(STEWARD),
    )
    actions = [entry.action for entry in store.read_audit(object_id=FEED)]
    assert "refused:rule_policy" in actions


def test_naming_a_person_lets_the_same_rule_through(
    client: TestClient, store: MemMetadataDb
) -> None:
    client.put(
        f"/api/feeds/{FEED}/rule-policies",
        json=[
            _policy(
                on_failure="stop_pipeline",
                threshold_percent=None,
                alert_recipient="sam.okafor@cinqcare.test",
            )
        ],
        headers=_as(BA),
    )
    _submit(store)
    approved = client.post(
        f"/api/objects/dq_rule/{FEED}/approve",
        json={"comment": "Stop-pipeline is right here; Sam is on call."},
        headers=_as(STEWARD),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["lifecycle_state"] == "approved"


def test_a_rule_set_with_no_policies_configured_still_approves(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Approving the sentences before configuring where they run is a
    legitimate order of work — a spec with no policy does not run."""
    _submit(store)
    approved = client.post(
        f"/api/objects/dq_rule/{FEED}/approve",
        json={"comment": "The sentences are right; we will configure next."},
        headers=_as(STEWARD),
    )
    assert approved.status_code == 200, approved.text


# ── the guardrail ────────────────────────────────────────────────────────────
def test_a_read_only_user_may_read_the_ladder_and_not_configure(
    client: TestClient,
) -> None:
    assert client.get(f"/api/feeds/{FEED}/rule-policies", headers=_as(READ_ONLY)).status_code == 200
    denied = client.put(
        f"/api/feeds/{FEED}/rule-policies", json=[_policy()], headers=_as(READ_ONLY)
    )
    assert denied.status_code == 403


def test_a_steward_who_configures_a_threshold_may_not_then_approve_it(
    client: TestClient, store: MemMetadataDb
) -> None:
    """THE LAW, WHERE THE STORY'S SUMMARY READS OTHERWISE.

    "the steward sets Silver Raw / Quarantine / threshold 1% and approves"
    describes an outcome, not two acts by one person. A steward who changes
    what a failing row costs should not be the only person who read the change
    — so configuring makes them the author, and a second approver is needed.
    """
    client.put(f"/api/feeds/{FEED}/rule-policies", json=[_policy()], headers=_as(STEWARD))
    _submit(store)
    refused = client.post(
        f"/api/objects/dq_rule/{FEED}/approve",
        json={"comment": "I set it, so I am signing it."},
        headers=_as(STEWARD),
    )
    assert refused.status_code == 403
    assert "never approves it" in refused.text


def test_an_unauthenticated_caller_sees_nothing(client: TestClient) -> None:
    assert client.get(f"/api/feeds/{FEED}/rule-policies").status_code == 401
    assert client.put(f"/api/feeds/{FEED}/rule-policies", json=[]).status_code == 401
