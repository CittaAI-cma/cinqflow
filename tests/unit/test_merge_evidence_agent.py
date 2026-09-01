"""CF-V3-E9-03 wired — the merge evidence agent, the gateway and the schema.

LANE 1. Scripted model, no credentials. This suite proves MACHINERY: `gather`
reaches no model, the schema has no field a decision could occupy, a
successful call returns a narrative grounded in exactly the fields it was
given, and a gateway failure degrades to a card with no narrative rather than
blocking the steward's screen. It proves nothing about narrative QUALITY —
that is Lane 3's job, on real scenarios, once credentials exist.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)."
    — docs/architecture/plates/13-three-lane-ai-testing.md
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.agents.merge_evidence.graph import DETERMINISTIC_NODES, NARRATE_SCHEMA
from cinqflow.core.agents.merge_evidence.prompts import TEMPLATES
from cinqflow.core.identity.merge import (
    DemographicComparison,
    FieldComparison,
    MergePlan,
    SatelliteRepoint,
)
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.intelligence.agents.merge_evidence import MergeEvidenceAgent
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from tests.support.ast_checks import assert_deterministic_nodes

pytestmark = [pytest.mark.unit, pytest.mark.lane1]

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN)

PLAN = MergePlan(
    merged_away_member_id="C2",
    survivor_member_id="C1",
    repoints=(
        SatelliteRepoint(
            entity="Members_Addresses", record_id="A1", from_member_id="C2", to_member_id="C1"
        ),
        SatelliteRepoint(
            entity="Members_Addresses", record_id="A2", from_member_id="C2", to_member_id="C1"
        ),
    ),
    collapses=(),
)

COMPARISON = DemographicComparison(
    fields={
        "first_name": FieldComparison.MATCH,
        "last_name": FieldComparison.MATCH,
        "date_of_birth": FieldComparison.MATCH,
    }
)


def _published(obj):  # type: ignore[no-untyped-def]
    return replace(
        obj, lifecycle_state=LifecycleState.PUBLISHED, approved_by=STEWARD, approved_ts=NOW
    )


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=BA, now=NOW)))
    return metadata


def _narrate_answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "narrative": "All three demographic fields match between the two profiles.",
        "grounded_fields": ["first_name", "last_name", "date_of_birth"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _agent(store: MemMetadataDb, llm: ScriptedLlm) -> MergeEvidenceAgent:
    gateway = LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    return MergeEvidenceAgent(llm=gateway)


# ── the shape carries no decision field ──────────────────────────────────────


def test_the_schema_has_no_field_a_decision_could_occupy() -> None:
    """R4: there is no confidence at which this agent may propose anything,
    so there is no field for it to name one."""
    assert set(NARRATE_SCHEMA["properties"]) == {"narrative", "grounded_fields"}
    assert NARRATE_SCHEMA["additionalProperties"] is False


def test_the_agent_declares_r4() -> None:
    from cinqflow.core.agents.merge_evidence.graph import RISK_CLASS

    assert RISK_CLASS is RiskClass.R4
    assert RISK_CLASS.automatable is False


# ── gather reaches no model ──────────────────────────────────────────────────


def test_gather_reaches_no_model() -> None:
    from cinqflow.intelligence.agents import merge_evidence as wired

    assert_deterministic_nodes(wired, {"_gather"})
    assert {"gather"} == DETERMINISTIC_NODES


def test_gather_never_carries_a_raw_demographic_value() -> None:
    agent = _agent(MemMetadataDb(), ScriptedLlm())
    bundle = agent._gather(PLAN, COMPARISON)
    assert "Jane" not in bundle and "Doe" not in bundle
    assert "match" in bundle


# ── a successful narration ───────────────────────────────────────────────────


def test_prepare_returns_a_card_grounded_in_what_it_was_given(store: MemMetadataDb) -> None:
    llm = ScriptedLlm(responder=lambda prompt, task: _narrate_answer())
    agent = _agent(store, llm)
    card = agent.prepare(plan=PLAN, comparison=COMPARISON, caller=STEWARD, now=NOW)

    assert card.model_called
    assert card.narrative
    assert set(card.grounded_fields) <= set(COMPARISON.fields)
    assert card.merged_away_member_id == "C2"
    assert card.survivor_member_id == "C1"
    assert card.plan is PLAN
    assert card.comparison is COMPARISON


def test_prepare_calls_the_small_model_not_the_large_one(store: MemMetadataDb) -> None:
    llm = ScriptedLlm(responder=lambda prompt, task: _narrate_answer())
    agent = _agent(store, llm)
    agent.prepare(plan=PLAN, comparison=COMPARISON, caller=STEWARD, now=NOW)
    assert llm.calls
    assert all(task is TaskClass.SMALL for _, task in llm.calls)


def test_a_grounded_field_the_model_never_saw_is_dropped() -> None:
    """The platform decides what counts as grounded, same rule
    `mapping_suggestion`/`schema_inference` enforce: a name outside what
    `gather` handed the model is discarded, never trusted at face value."""
    store = MemMetadataDb()
    for template in TEMPLATES:
        store.save(_published(template.as_governed(author=BA, now=NOW)))
    llm = ScriptedLlm(
        responder=lambda prompt, task: _narrate_answer(grounded_fields=["ssn", "first_name"])
    )
    agent = _agent(store, llm)
    card = agent.prepare(plan=PLAN, comparison=COMPARISON, caller=STEWARD, now=NOW)
    assert "ssn" not in card.grounded_fields
    assert "first_name" in card.grounded_fields


# ── degradation: the card survives a gateway failure ────────────────────────


def test_a_manual_path_failure_degrades_to_a_card_with_no_narrative(store: MemMetadataDb) -> None:
    """The evidence card's deterministic half (the plan, the comparison) is
    exactly what a steward needs even when the model cannot be reached — this
    agent's failure mode is "less helpful", never "blocks the review"."""

    class _Failing:
        def complete(self, **kwargs: object) -> object:
            raise ManualPathRequiredError("endpoint unreachable")

    agent = MergeEvidenceAgent(
        llm=LlmGateway(
            llm=_Failing(),  # type: ignore[arg-type]
            phi_scrub=PatternPhiScrub(),
            metadata_db=store,
            observability=NoopObservability(),
            budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
            routing=Routing(small="small-model", large="large-model"),
            clock=lambda: NOW,
        )
    )
    card = agent.prepare(plan=PLAN, comparison=COMPARISON, caller=STEWARD, now=NOW)
    assert not card.model_called
    assert card.narrative == ""
    assert card.grounded_fields == ()
    # The deterministic half survives regardless.
    assert card.plan is PLAN
    assert card.comparison is COMPARISON
