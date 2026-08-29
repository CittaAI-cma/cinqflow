"""CF-V0-E16-01 — the gateway, and the four things it refuses.

    "PHI is scrubbed before ANY prompt; the scrub-then-prompt ordering has its
     own test"
    — docs/architecture/INVARIANTS.md, intelligence

The ordering test below is asserted INDEPENDENTLY of what either component
does: a recorder sits behind both pins and the assertion is about the SEQUENCE
of calls, not about masked text. That matters because "we scrub before we
prompt" is exactly the kind of property that survives review and dies to a
refactor which reorders two lines.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.intelligence import CALL_PIPELINE, Budget, CallStage, Routing
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.llm import BudgetExhaustedError, TaskClass
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.prompts import PromptSection, PromptTemplate
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.ports.metadata_db import MetadataDbPort

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")
REVIEWER = Actor(subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")
CALLER = Actor(subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        }
    },
}


def _publish(store: MetadataDbPort, template: PromptTemplate) -> None:
    obj = template.as_governed(author=AUTHOR)
    reviewed, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=AUTHOR)
    approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=REVIEWER)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=REVIEWER)
    store.save(published)


def _template(**overrides: Any) -> PromptTemplate:
    base: dict[str, Any] = {
        "prompt_id": "insight.answer",
        "version": 1,
        "task_class": TaskClass.LARGE,
        "sections": {
            PromptSection.IDENTITY: "You explain CINQFLOW pipelines.",
            PromptSection.TASK: "Answer from the grounding only.",
            PromptSection.CONSTRAINTS: "Cite every claim. Never propose a write.",
        },
    }
    base.update(overrides)
    return PromptTemplate(**base)


class Recorder:
    """One log for both pins, so the ORDER is observable."""

    def __init__(self) -> None:
        self.events: list[str] = []


class WatchedScrub(PatternPhiScrub):
    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def scrub(self, text: str) -> Any:
        self._recorder.events.append("scrub")
        return super().scrub(text)


class WatchedLlm(ScriptedLlm):
    def __init__(self, recorder: Recorder, **kw: Any) -> None:
        super().__init__(**kw)
        self._recorder = recorder

    def complete(self, **kw: Any) -> Any:
        self._recorder.events.append("complete")
        return super().complete(**kw)


def _gateway(
    *,
    store: MemMetadataDb,
    llm: Any,
    scrub: Any = None,
    budget: Budget | None = None,
    estimate: Decimal = Decimal("0.01"),
) -> LlmGateway:
    return LlmGateway(
        llm=llm,
        phi_scrub=scrub or PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=budget or Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        estimate_usd=estimate,
        clock=lambda: datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
    )


# ── the ordering guarantee ───────────────────────────────────────────────────


def test_phi_is_scrubbed_before_any_prompt_reaches_a_model() -> None:
    recorder = Recorder()
    store = MemMetadataDb()
    _publish(store, _template())
    gateway = _gateway(
        store=store,
        llm=WatchedLlm(recorder, responder=lambda p, t: "ok"),
        scrub=WatchedScrub(recorder),
    )

    gateway.complete(
        agent="pipeline-insight",
        run_id="run-1",
        prompt_id="insight.answer",
        caller=CALLER,
        context="member MBR-123456 was quarantined",
        input_text="why?",
    )

    assert recorder.events[0] == "scrub"
    assert recorder.events.index("complete") > recorder.events.index("scrub"), (
        "a prompt assembled before the scrub is a PHI disclosure that already happened"
    )


def test_the_trace_shows_all_six_stages_in_the_documented_order() -> None:
    store = MemMetadataDb()
    _publish(store, _template())
    result = _gateway(store=store, llm=ScriptedLlm(responder=lambda p, t: "ok")).complete(
        agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
    )
    assert result.stages == CALL_PIPELINE
    assert list(result.stages) == sorted(result.stages)
    assert result.stages[1] is CallStage.PHI_SCRUB
    assert result.stages[2] is CallStage.PROMPT_ASSEMBLY


def test_what_reaches_the_model_carries_no_detected_phi() -> None:
    store = MemMetadataDb()
    _publish(store, _template())
    llm = ScriptedLlm(responder=lambda p, t: "ok")
    result = _gateway(store=store, llm=llm).complete(
        agent="pipeline-insight",
        run_id="run-1",
        prompt_id="insight.answer",
        caller=CALLER,
        context="contact jane@example.com about 123-45-6789",
    )
    sent, _ = llm.calls[0]
    assert "jane@example.com" not in sent
    assert "123-45-6789" not in sent
    assert "<EMAIL_ADDRESS>" in sent
    assert set(result.scrubbed_entities) == {"EMAIL_ADDRESS", "US_SSN"}


# ── the ledger ───────────────────────────────────────────────────────────────


def test_every_call_is_logged_with_prompt_hash_model_version_cost_and_caller() -> None:
    store = MemMetadataDb()
    _publish(store, _template())
    result = _gateway(store=store, llm=ScriptedLlm(responder=lambda p, t: "ok")).complete(
        agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
    )

    (row,) = store.read_agent_actions(run_id="run-1")
    assert row.outcome is ActionOutcome.COMPLETED
    assert row.prompt_hash == result.prompt.prompt_hash
    assert row.prompt_ref == "insight.answer@v1"
    assert row.model and row.model_version
    assert row.actor.subject == CALLER.subject
    assert row.actor.actor_type is ActorType.HUMAN
    assert row.risk_class == "R0"


def test_an_unpublished_prompt_never_reaches_a_model() -> None:
    from cinqflow.core.prompts import PromptError

    store = MemMetadataDb()
    store.save(_template().as_governed(author=AUTHOR))  # left in Draft
    llm = ScriptedLlm(responder=lambda p, t: "ok")
    with pytest.raises(PromptError, match="published prompts only"):
        _gateway(store=store, llm=llm).complete(
            agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
        )
    assert llm.calls == []


# ── budgets ──────────────────────────────────────────────────────────────────


def test_the_budget_is_checked_before_the_call_not_after() -> None:
    store = MemMetadataDb()
    _publish(store, _template())
    llm = ScriptedLlm(responder=lambda p, t: "ok")
    gateway = _gateway(
        store=store,
        llm=llm,
        budget=Budget(per_run_usd=Decimal("0.02"), per_agent_per_day_usd=Decimal("5")),
        estimate=Decimal("0.03"),
    )
    with pytest.raises(BudgetExhaustedError, match="per-run cap"):
        gateway.complete(
            agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
        )
    assert llm.calls == [], "a budget checked after the call is a report, not a control"


def test_a_budget_refusal_is_recorded_so_operations_can_see_it() -> None:
    store = MemMetadataDb()
    _publish(store, _template())
    gateway = _gateway(
        store=store,
        llm=ScriptedLlm(responder=lambda p, t: "ok"),
        budget=Budget(per_run_usd=Decimal("0.02"), per_agent_per_day_usd=Decimal("5")),
        estimate=Decimal("0.03"),
    )
    with pytest.raises(BudgetExhaustedError):
        gateway.complete(
            agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
        )
    (row,) = store.read_agent_actions(run_id="run-1")
    assert row.outcome is ActionOutcome.REFUSED_BUDGET
    assert row.outcome.is_refusal
    assert "per-run cap" in row.detail


def test_a_budget_of_zero_is_refused_as_a_disabled_feature() -> None:
    with pytest.raises(ValueError, match="disabled feature"):
        Budget(per_run_usd=Decimal("0"), per_agent_per_day_usd=Decimal("5"))


def test_a_per_run_cap_above_the_daily_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="never bind"):
        Budget(per_run_usd=Decimal("6"), per_agent_per_day_usd=Decimal("5"))


def test_spend_accumulates_across_runs_for_the_day() -> None:
    store = MemMetadataDb()
    _publish(store, _template())
    llm = ScriptedLlm(responder=lambda p, t: "ok")

    def costed(**kw: Any) -> Any:
        completion = ScriptedLlm.complete(llm, **kw)
        return type(completion)(**{**completion.__dict__, "cost_usd": Decimal("0.10")})

    llm.complete = costed  # type: ignore[method-assign]
    gateway = _gateway(store=store, llm=llm)
    for index in range(2):
        gateway.complete(
            agent="pipeline-insight",
            run_id=f"run-{index}",
            prompt_id="insight.answer",
            caller=CALLER,
        )
    assert gateway.spent_today("pipeline-insight") == Decimal("0.20")


# ── parse or reject ──────────────────────────────────────────────────────────


def test_a_schema_valid_response_is_parsed() -> None:
    store = MemMetadataDb()
    _publish(store, _template(response_schema=ANSWER_SCHEMA))
    payload = json.dumps({"claims": [{"text": "21,820 rows loaded"}]})
    result = _gateway(store=store, llm=ScriptedLlm(responder=lambda p, t: payload)).complete(
        agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
    )
    assert result.value == {"claims": [{"text": "21,820 rows loaded"}]}
    assert result.repairs == 0


def test_an_invalid_response_gets_exactly_one_repair_naming_the_paths() -> None:
    store = MemMetadataDb()
    _publish(store, _template(response_schema=ANSWER_SCHEMA))
    attempts: list[str] = []

    def responder(prompt: str, task: TaskClass) -> str:
        attempts.append(prompt)
        if len(attempts) == 1:
            return json.dumps({"claims": [{}]})
        return json.dumps({"claims": [{"text": "repaired"}]})

    result = _gateway(store=store, llm=ScriptedLlm(responder=responder)).complete(
        agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
    )
    assert result.repairs == 1
    assert len(attempts) == 2
    assert "$.claims[0].text: required, and absent" in attempts[1], (
        "a retry told only 'invalid' is a second sample from the same distribution"
    )


def test_a_second_failure_degrades_to_the_manual_path_and_is_recorded() -> None:
    store = MemMetadataDb()
    _publish(store, _template(response_schema=ANSWER_SCHEMA))
    llm = ScriptedLlm(responder=lambda p, t: "not json at all")
    with pytest.raises(ManualPathRequiredError, match="manual path is unaffected"):
        _gateway(store=store, llm=llm).complete(
            agent="pipeline-insight", run_id="run-1", prompt_id="insight.answer", caller=CALLER
        )
    outcomes = [row.outcome for row in store.read_agent_actions(run_id="run-1")]
    assert outcomes == [
        ActionOutcome.FAILED_SCHEMA,
        ActionOutcome.FAILED_SCHEMA,
        ActionOutcome.ESCALATED_TO_MANUAL,
    ]
    assert len(llm.calls) == 2, "one bounded retry — a loop that can run twice can run forever"
