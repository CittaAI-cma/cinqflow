"""The ONE contract suite for the intelligence-plane pins.

    llm · phi_scrub · vector · agent_runtime

These four carry most of the platform's refusals, and the refusals are what
make the plane safe rather than merely capable. So the suite is written mostly
as negatives: the attempt is made, and the refusal is asserted.

    "no model credentials exist outside the LLM gateway"
    "PHI is scrubbed before ANY prompt; the scrub-then-prompt ordering has its
     own test"
    "retrieval applies the caller's RBAC scopes BEFORE any similarity computation"
    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2 (replay)"
    — docs/architecture/INVARIANTS.md
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.prompts import PromptSection, PromptTemplate
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.ports.agent_runtime import AgentRuntimeError, AgentRuntimePort, Edge, GraphSpec
from cinqflow.ports.llm import (
    Completion,
    CompletionFailedError,
    Embedding,
    LlmPort,
    TaskClass,
    UndeclaredEndpointError,
)
from cinqflow.ports.phi_scrub import PhiScrubPort
from cinqflow.ports.vector import Chunk, VectorPort

from .conftest import adapters_for

pytestmark = [pytest.mark.contract, pytest.mark.lane1]


# ── llm ──────────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("llm"))
def llm(request: pytest.FixtureRequest, make: Callable[..., Any]) -> LlmPort:
    return make(request.param)


def test_every_completion_carries_what_audit_and_metering_require(llm: LlmPort) -> None:
    """ "100% of model calls carry prompt hash, model version, cost and caller
    identity in the audit log."

    Three of those four are on the RETURN VALUE rather than left for the caller
    to record separately — a caller that forgets is a call that escaped audit.
    """
    completion = llm.complete(prompt="explain this feed", task_class=TaskClass.SMALL)
    assert completion.prompt_hash
    assert completion.model_version
    assert isinstance(completion.cost_usd, Decimal)
    assert completion.prompt_tokens >= 0


def test_the_same_prompt_gives_the_same_hash(llm: LlmPort) -> None:
    """The hash joins a call record to a registry version. A hash that varied
    would make "reproducible from registry version plus model version"
    untrue."""
    first = llm.complete(prompt="identical", task_class=TaskClass.SMALL)
    second = llm.complete(prompt="identical", task_class=TaskClass.LARGE)
    assert first.prompt_hash == second.prompt_hash


def test_routing_is_by_task_class_not_by_model_name(llm: LlmPort) -> None:
    """A call site naming a model is a call site that must be edited when the
    tenant's model catalogue differs."""
    small = llm.complete(prompt="classify", task_class=TaskClass.SMALL)
    large = llm.complete(prompt="classify", task_class=TaskClass.LARGE)
    assert small.model != large.model


def test_a_mock_reports_zero_cost_and_does_not_pretend_otherwise(llm: LlmPort) -> None:
    """A mock that invented a cost would let a cost assertion pass in Lane 1,
    and cost caps are asserted in CI like accuracy."""
    completion = llm.complete(prompt="anything", task_class=TaskClass.SMALL)
    assert completion.cost_usd == Decimal("0")


def test_the_lane_one_adapter_declares_no_real_endpoint(llm: LlmPort) -> None:
    """Lanes 1 and 2 hold NO live credentials, so a misclassified test fails
    loudly instead of quietly reaching production."""
    for endpoint in llm.declared_endpoints():
        assert endpoint.startswith("mock://"), f"{endpoint} is reachable from Lane 1"


def test_an_undeclared_endpoint_is_refused_at_the_adapter(llm: LlmPort) -> None:
    """ "only endpoints declared in the connection profile may be called"

    At the ADAPTER, because that is the only layer that can see an endpoint at
    all. A policy document cannot enforce this; a constructor can.
    """
    from cinqflow.ports.llm import UndeclaredEndpointError

    with pytest.raises(UndeclaredEndpointError):
        llm.check_endpoint("https://an-endpoint-nobody-declared.example.com/v1")


def test_a_schema_constrained_response_is_shape_valid_but_content_free(llm: LlmPort) -> None:
    """A mock that invented plausible content would let an ungrounded answer
    pass a machinery test — which is exactly the failure Lane 1 must not hide."""
    import json

    schema = {
        "type": "object",
        "properties": {"claims": {"type": "array"}, "confidence": {"type": "number"}},
    }
    completion = llm.complete(prompt="p", task_class=TaskClass.LARGE, response_schema=schema)
    parsed = json.loads(completion.text)
    assert parsed == {"claims": [], "confidence": 0}


# ── the gateway degrades, it does not crash ─────────────────────────────────
#
#     W2-37 — a timed-out or unaffordable model call used to CRASH every
#     calling agent instead of taking the manual path. `_call` wrapped only
#     the budget PRE-CHECK in try/except, and re-raised `BudgetExhaustedError`
#     bare — a sibling of `ManualPathRequiredError`, not that error itself —
#     and the actual `.complete()` call (both in `_call` and in the one
#     bounded repair attempt) had NO try/except at all. Every calling agent
#     (mapping_suggestion, fingerprint_match, alert_enrichment) catches only
#     `ManualPathRequiredError`, so both failures propagated straight through
#     them.


_GATEWAY_TEST_BA = Actor(subject="dev-ops@cinqcare.test", actor_type=ActorType.HUMAN)
_GATEWAY_TEST_NOW = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)

_GATEWAY_TEST_TEMPLATE = PromptTemplate(
    prompt_id="test.gateway-degrade",
    version=1,
    task_class=TaskClass.SMALL,
    sections={
        PromptSection.IDENTITY: "You are a test prompt used only by the gateway contract suite.",
        PromptSection.TASK: "Say hello.",
        PromptSection.CONSTRAINTS: "Never fabricate a citation.",
    },
)


class _RaisingLlm:
    """A fake `llm` port whose `.complete()` always raises the given
    exception — the shape a FIXED adapter takes once it has translated a
    vendor failure at the boundary (Part 1 of W2-37's fix). Used here to
    prove the gateway's own normalisation (Part 2) in isolation, with no
    vendor SDK involved."""

    def __init__(self, to_raise: Exception) -> None:
        self._to_raise = to_raise
        self.calls = 0

    def complete(
        self,
        *,
        prompt: str,
        task_class: TaskClass,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Completion:
        self.calls += 1
        raise self._to_raise

    def embed(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        raise NotImplementedError

    def declared_endpoints(self) -> frozenset[str]:
        return frozenset({"mock://scripted"})


def _store_with_gateway_test_template() -> MemMetadataDb:
    store = MemMetadataDb()
    published = replace(
        _GATEWAY_TEST_TEMPLATE.as_governed(author=_GATEWAY_TEST_BA, now=_GATEWAY_TEST_NOW),
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=_GATEWAY_TEST_BA,
        approved_ts=_GATEWAY_TEST_NOW,
    )
    store.save(published)
    return store


def _gateway(llm: LlmPort, store: MemMetadataDb, *, per_run_usd: str = "0.25") -> LlmGateway:
    return LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal(per_run_usd), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: _GATEWAY_TEST_NOW,
    )


def test_a_transport_failure_degrades_to_the_manual_path() -> None:
    """A timeout or connection failure translated by the adapter into
    `CompletionFailedError` must reach the caller as `ManualPathRequiredError`
    — never as the raw transport exception, which no calling agent catches."""
    store = _store_with_gateway_test_template()
    llm = _RaisingLlm(CompletionFailedError("simulated network timeout"))
    gateway = _gateway(llm, store)

    with pytest.raises(ManualPathRequiredError):
        gateway.complete(
            agent="test-agent",
            run_id="R-transport",
            prompt_id="test.gateway-degrade",
            caller=_GATEWAY_TEST_BA,
            input_text="hello",
        )

    actions = store.read_agent_actions(agent="test-agent")
    assert any(a.outcome is ActionOutcome.FAILED_COMPLETION for a in actions), (
        "a transport failure must leave an audit row Operations can query, distinct "
        "from a budget refusal or a schema failure"
    )


def test_a_budget_exhaustion_degrades_to_the_manual_path_not_bare_budget_exhausted() -> None:
    """`BudgetExhaustedError` is DESIGNED FOR, not a freak accident — it must
    degrade exactly like a transport failure or a schema failure, not
    propagate as itself past every `except ManualPathRequiredError`."""
    store = _store_with_gateway_test_template()
    llm = _RaisingLlm(AssertionError("the model must not be called once the budget refuses"))
    # per_run_usd deliberately below the gateway's own default estimate_usd
    # (0.01), so the very FIRST call is refused before it is even made.
    gateway = _gateway(llm, store, per_run_usd="0.001")

    with pytest.raises(ManualPathRequiredError):
        gateway.complete(
            agent="test-agent",
            run_id="R-budget",
            prompt_id="test.gateway-degrade",
            caller=_GATEWAY_TEST_BA,
            input_text="hello",
        )

    assert llm.calls == 0
    actions = store.read_agent_actions(agent="test-agent")
    assert any(a.outcome is ActionOutcome.REFUSED_BUDGET for a in actions)


def test_an_undeclared_endpoint_still_propagates_unchanged_through_the_gateway() -> None:
    """The third sibling of `LlmError` is a genuine misconfiguration, not
    something to degrade past — it must keep failing loudly, exactly as
    today, rather than being folded into `ManualPathRequiredError`."""
    store = _store_with_gateway_test_template()
    llm = _RaisingLlm(UndeclaredEndpointError("https://not-declared.example.test/v1"))
    gateway = _gateway(llm, store)

    with pytest.raises(UndeclaredEndpointError):
        gateway.complete(
            agent="test-agent",
            run_id="R-endpoint",
            prompt_id="test.gateway-degrade",
            caller=_GATEWAY_TEST_BA,
            input_text="hello",
        )


# ── phi_scrub ────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("phi_scrub"))
def scrubber(request: pytest.FixtureRequest, make: Callable[..., Any]) -> PhiScrubPort:
    return make(request.param)


@pytest.mark.parametrize(
    "text",
    [
        "member 123-45-6789 called",
        "dob 1990-01-01 on file",
        "email arun.menon@cinqcare.test",
        "call 555-867-5309",
        "id MBR000042 in the roster",
    ],
)
def test_phi_is_detected__the_gate_is_recall_not_precision(
    scrubber: PhiScrubPort, text: str
) -> None:
    """The gate is 100% RECALL. Missing PHI is the failure that matters: a
    false positive costs a masked field, a false negative costs a disclosure."""
    assert scrubber.detect(text), f"undetected PHI in {text!r}"


def test_scrubbing_removes_the_value_and_keeps_the_shape(scrubber: PhiScrubPort) -> None:
    result = scrubber.scrub("member 123-45-6789 born 1990-01-01")
    assert "123-45-6789" not in result.text
    assert "1990-01-01" not in result.text
    assert result.was_scrubbed is True


def test_a_finding_never_carries_the_value_it_found(scrubber: PhiScrubPort) -> None:
    """Carrying the detected value would put PHI into logs and error messages —
    the two classic leak routes — via the very type built to prevent it."""
    (finding, *_) = scrubber.detect("member 123-45-6789")
    assert not hasattr(finding, "value")
    assert not hasattr(finding, "text")
    assert (finding.start, finding.end) == (7, 18)


def test_clean_text_is_left_exactly_alone(scrubber: PhiScrubPort) -> None:
    clean = "batch 8842 completed at 03:14 with 175 quarantined by DQ-002"
    result = scrubber.scrub(clean)
    assert result.text == clean
    assert result.was_scrubbed is False


# ── vector ───────────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("vector"))
def vector(request: pytest.FixtureRequest, make: Callable[..., Any]) -> VectorPort:
    return make(request.param)


def test_wave_0_provisions_the_store_and_leaves_it_empty(vector: VectorPort) -> None:
    """ "pgvector stays provisioned and empty, exactly as specified."

    The knowledge plane is Wave 1. Provisioning now and asserting empty is the
    honest way to say the seat exists and the capability does not.
    """
    assert vector.count() == 0


def test_scope_filters_the_candidate_set_before_similarity(vector: VectorPort) -> None:
    """ "Apply a scope filter to results rather than to the query" is a
    documented don't. Filtering results is the version that leaks: the row was
    fetched, and every future path that forgets the filter exposes it."""
    in_scope = Chunk(
        chunk_id="c1",
        text="Fidelis downstate roster",
        citation=CitationId(kind=CitationKind.TERM, subject="roster"),
        metadata={"domain": "enrollments"},
    )
    out_of_scope = Chunk(
        chunk_id="c2",
        text="Fidelis downstate roster",
        citation=CitationId(kind=CitationKind.TERM, subject="roster"),
        metadata={"domain": "claims"},
    )
    vector.index([in_scope, out_of_scope], [(1.0, 0.0), (1.0, 0.0)])

    found = vector.retrieve((1.0, 0.0), scope_filter={"domain": "enrollments"})
    assert [s.chunk.chunk_id for s in found] == ["c1"]


def test_a_chunk_cannot_exist_without_a_citation() -> None:
    """A chunk that cannot be cited cannot ground a claim, and an ungrounded
    claim is a defect class."""
    with pytest.raises(TypeError):
        Chunk(chunk_id="c", text="t")  # type: ignore[call-arg]


# ── agent_runtime ────────────────────────────────────────────────────────────
@pytest.fixture(params=adapters_for("agent_runtime"))
def runtime(request: pytest.FixtureRequest, make: Callable[..., Any]) -> AgentRuntimePort:
    return make(request.param)


def _linear_graph() -> GraphSpec:
    """The Wave-0 shape: route -> plan_tools -> execute -> answer."""
    return GraphSpec(
        name="pipeline_insight",
        nodes={
            "route": lambda s: {"intent": "explain_run"},
            "plan_tools": lambda s: {"tools": ["get_batch", "get_reconciliation"]},
            "execute": lambda s: {"results": {"batch": s["intent"]}},
            "answer": lambda s: {"answer": f"{len(s['tools'])} tools"},
        },
        edges=(
            Edge("route", "plan_tools"),
            Edge("plan_tools", "execute"),
            Edge("execute", "answer"),
        ),
        entrypoint="route",
        terminal="answer",
    )


def test_a_graph_runs_and_every_node_is_traced(runtime: AgentRuntimePort) -> None:
    """ "each node's entry and exit is traced, and the run is reproducible from
    the prompt hashes alone" — CF-V0-E16-11, happy path"""
    run = runtime.execute(_linear_graph(), {})
    assert [t.node for t in run.traces] == ["route", "plan_tools", "execute", "answer"]
    assert run.state["answer"] == "2 tools"
    assert run.completed is True


def test_the_same_graph_and_state_produce_the_same_run(runtime: AgentRuntimePort) -> None:
    """The Lane-1 runner is deterministic. A machinery test that sometimes
    passes teaches the team to re-run CI."""
    first = runtime.execute(_linear_graph(), {})
    second = runtime.execute(_linear_graph(), {})
    assert [t.node for t in first.traces] == [t.node for t in second.traces]
    assert first.state == second.state


def test_a_conditional_edge_is_taken_when_its_key_is_truthy(runtime: AgentRuntimePort) -> None:
    graph = GraphSpec(
        name="branching",
        nodes={
            "start": lambda s: {"needs_tools": s.get("needs_tools", False)},
            "with_tools": lambda s: {"path": "tools"},
            "without_tools": lambda s: {"path": "direct"},
        },
        edges=(
            Edge("start", "with_tools", when="needs_tools"),
            Edge("start", "without_tools"),
            Edge("with_tools", "without_tools"),
        ),
        entrypoint="start",
        terminal="without_tools",
    )
    assert runtime.execute(graph, {"needs_tools": True}).state["path"] == "direct"
    assert runtime.execute(graph, {}).state["path"] == "direct"


def test_a_cycle_is_bounded_and_refused_by_name_rather_than_hanging(
    runtime: AgentRuntimePort,
) -> None:
    """An unbounded graph would hang an interactive request.

    The Wave-0 agent has a p95 < 6s latency gate, so "eventually times out
    somewhere" is not good enough: the refusal names the graph, at the runtime,
    before a user is left waiting.
    """
    graph = GraphSpec(
        name="looping",
        nodes={
            "a": lambda s: {"n": s.get("n", 0) + 1},
            "b": lambda s: {},
            "done": lambda s: {},
        },
        edges=(Edge("a", "b"), Edge("b", "a")),  # never reaches `done`
        entrypoint="a",
        terminal="done",
    )
    with pytest.raises(AgentRuntimeError, match="looping"):
        runtime.execute(graph, {})


def test_an_edge_to_an_unknown_node_is_refused_at_declaration(
    runtime: AgentRuntimePort,
) -> None:
    """Graphs are data, so they are validated when declared rather than
    discovered when run."""
    with pytest.raises(ValueError, match="unknown node"):
        GraphSpec(
            name="broken",
            nodes={"a": lambda s: {}},
            edges=(Edge("a", "nowhere"),),
            entrypoint="a",
            terminal="a",
        )


def test_the_runtime_holds_no_credentials_and_offers_no_model_call(
    runtime: AgentRuntimePort,
) -> None:
    """ "never_owns: [model_call, prompt_text, tool_whitelist, risk_class,
    lifecycle_state]"

    CF-V0-E16-11's guardrail is true by construction: a node attempting a model
    call outside the gateway fails because there is nothing here to
    authenticate with.
    """
    for forbidden in ("complete", "llm", "model", "api_key", "prompt", "tools", "whitelist"):
        assert not hasattr(runtime, forbidden), f"agent_runtime exposes {forbidden}"
