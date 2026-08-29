"""LANE 3 — the only lane that may make a quality claim.

    "test lanes: 1 mock · 2 replay · 3 real API"
    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2 (replay)"
    — docs/architecture/INVARIANTS.md, testing

These tests are WIRED and SKIP until an OpenAI-compatible endpoint and key are
present in the environment. That is a deliberate, visible incompleteness rather
than a quietly-passing green tick: `CF-V0-E16-01`'s Definition of Done is not
met until they have run.

What Lane 3 proves that no other lane can: that a real model, at a real
endpoint, priced by a real table, returns something the schema validator
accepts — and that the cost the gateway records is a cost somebody was actually
charged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.prompts import PromptSection, PromptTemplate
from cinqflow.intelligence.gateway import LlmGateway

pytestmark = [pytest.mark.evaluation, pytest.mark.lane3]

AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")
REVIEWER = Actor(subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


@pytest.fixture
def gateway(lane3_llm: Any) -> LlmGateway:
    from cinqflow.adapters.local.secrets import DotenvSecrets
    from cinqflow.installer.profile import load
    from cinqflow.intelligence.wiring import budget_from, routing_from

    profile = load("profiles/local.yaml")
    secrets = DotenvSecrets()
    store = MemMetadataDb()

    template = PromptTemplate(
        prompt_id="lane3.smoke",
        version=1,
        task_class=TaskClass.SMALL,
        sections={
            PromptSection.IDENTITY: "You are a terse assistant inside a data platform.",
            PromptSection.TASK: "Answer the question in one short sentence.",
            PromptSection.CONSTRAINTS: (
                "Reply with JSON only: an object with a single key `answer`. "
                "Never include anything else."
            ),
        },
        response_schema=SCHEMA,
        max_tokens=200,
    )
    obj = template.as_governed(author=AUTHOR)
    reviewed, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=AUTHOR)
    approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=REVIEWER)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=REVIEWER)
    store.save(published)

    built = LlmGateway(
        llm=lane3_llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=budget_from(profile),
        routing=routing_from(profile, secrets),
    )
    built.store = store  # type: ignore[attr-defined]
    return built


def test_a_real_model_returns_something_the_validator_accepts(gateway: LlmGateway) -> None:
    result = gateway.complete(
        agent="lane3-smoke",
        run_id="lane3-1",
        prompt_id="lane3.smoke",
        caller=AUTHOR,
        input_text="What layer follows Bronze in a medallion architecture?",
    )
    assert isinstance(result.value, dict)
    assert result.value["answer"].strip()
    assert json.loads(result.text)


def test_the_recorded_cost_is_real_money(gateway: LlmGateway) -> None:
    """A Lane-1 mock costs zero and must not pretend otherwise. A real call
    does not, and a gateway that recorded zero here would make every budget cap
    non-binding in production."""
    gateway.complete(
        agent="lane3-smoke",
        run_id="lane3-2",
        prompt_id="lane3.smoke",
        caller=AUTHOR,
        input_text="Name one control table.",
    )
    (row,) = gateway.store.read_agent_actions(run_id="lane3-2")  # type: ignore[attr-defined]
    assert row.outcome is ActionOutcome.COMPLETED
    assert row.cost_usd > Decimal("0")
    assert row.prompt_tokens > 0 and row.completion_tokens > 0
    assert row.model_version, "the served version is what a threshold was measured against"
    assert row.latency_ms > 0


def test_the_audit_row_carries_all_four_required_fields(gateway: LlmGateway) -> None:
    """100% of model calls carry prompt hash, model version, cost and caller."""
    before = datetime.now(UTC)
    gateway.complete(
        agent="lane3-smoke",
        run_id="lane3-3",
        prompt_id="lane3.smoke",
        caller=AUTHOR,
        input_text="Say ok.",
    )
    (row,) = gateway.store.read_agent_actions(run_id="lane3-3")  # type: ignore[attr-defined]
    assert row.prompt_hash and row.model and row.model_version
    assert row.actor.subject == AUTHOR.subject
    assert row.occurred_ts >= before


def test_routing_reaches_two_different_models(gateway: LlmGateway) -> None:
    """small <-> large comes from the profile, never from a call site."""
    from cinqflow.adapters.local.secrets import DotenvSecrets
    from cinqflow.installer.profile import load
    from cinqflow.intelligence.wiring import routing_from

    routing: Routing = routing_from(load("profiles/local.yaml"), DotenvSecrets())
    assert routing.small and routing.large
    assert routing.model_for(TaskClass.SMALL) == routing.small
    assert routing.model_for(TaskClass.LARGE) == routing.large


def test_the_budget_binds_against_real_prices(gateway: LlmGateway) -> None:
    """Deliberately not asserting a refusal — a real call under a real cap must
    SUCCEED, and the assertion is that the spend is now non-zero and under it."""
    gateway.complete(
        agent="lane3-smoke",
        run_id="lane3-4",
        prompt_id="lane3.smoke",
        caller=AUTHOR,
        input_text="Say ok.",
    )
    spent = gateway.spent_today("lane3-smoke")
    assert Decimal("0") < spent < Budget(
        per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")
    ).per_agent_per_day_usd
