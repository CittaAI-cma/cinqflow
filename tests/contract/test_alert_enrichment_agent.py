"""CF-V2-E12-05 — the alert-enrichment agent, wired.

LANE 1. Scripted model, no credentials. This suite proves MACHINERY: that
`group`, `retrieve` and `compose` reach no model, that `hypothesise` may skip
its own call when there is nothing to ground a hypothesis in, that a
hypothesise failure still ships a complete answer, that an uncited hypothesis
is refused before dispatch, and — the sharpest test for an R0 agent — that
this module contains NO code path reaching `core.proposals.submit` or
`metadata.record_proposal`. It proves NOTHING about hypothesis QUALITY; that
is Lane 3's `>= 80% operator-accepted causes` gate, measured monthly.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)."
    — docs/architecture/plates/13-three-lane-ai-testing.md
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core import sla as sla_core
from cinqflow.core.agents.alert_enrichment.graph import (
    CAUSE_UNDER_INVESTIGATION,
    DETERMINISTIC_NODES,
    NODE_COMPOSE,
    NODE_GROUP,
    NODE_RETRIEVE,
    RETRIEVE_TOOLS,
    RISK_CLASS,
)
from cinqflow.core.agents.alert_enrichment.prompts import TEMPLATES
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.model.llm import CompletionFailedError, TaskClass
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.intelligence.action_gateway import ActionGateway
from cinqflow.intelligence.agents.alert_enrichment import AlertEnrichmentAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.control_tables import SlaCycle as SlaCycleRow
from tests.support.ast_checks import (
    assert_deterministic_nodes,
    assert_graph_module_imports_no_runtime,
)

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

FEED_A = "acme-837-medical"
FEED_B = "beta-834-dental"
GHOST_FEED = "ghost-feed-no-history"
NOW = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)
BA = Actor(subject="dev-ops@cinqcare.test", actor_type=ActorType.HUMAN)


def _feed_citation(feed_id: str) -> CitationId:
    return CitationId(CitationKind.FEED, feed_id)


def _alert(
    feed_id: str,
    *,
    group_key: str = "2026-08-30T06:00",
    severity: sla_core.AlertSeverity = sla_core.AlertSeverity.WARNING,
) -> sla_core.SlaAlert:
    return sla_core.SlaAlert(
        feed_id=feed_id,
        cycle_date=date(2026, 8, 30),
        severity=severity,
        summary=f"{feed_id}: expected 6:00 AM — not received",
        citations=(_feed_citation(feed_id),),
        group_key=group_key,
    )


def _seed_sla_history(
    control: MemStoreControlTables, feed_id: str, *, breached: int, total: int
) -> None:
    for i in range(total):
        control.upsert_sla_instance(
            SlaCycleRow(
                feed_id=feed_id,
                cycle_date=date(2026, 8, 1) + timedelta(days=i),
                expected_ts=datetime(2026, 8, 1, 6, 0, tzinfo=UTC) + timedelta(days=i),
                sla_status="Breached" if i < breached else "On-Time",
                batch_id=f"B-{feed_id}-{i}",
            )
        )


@pytest.fixture
def control() -> MemStoreControlTables:
    return MemStoreControlTables()


def _published(obj):  # type: ignore[no-untyped-def]
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=BA,
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=BA, now=NOW)))
    return metadata


def _tools(store: MemMetadataDb, control: MemStoreControlTables) -> ToolContext:
    return ToolContext(
        principal=Principal(
            subject="platform@cinqflow",
            display_name="platform",
            scopes=Scopes(feeds=frozenset({"*"})),
        ),
        control=control,
        metadata=store,
        agent="alert-enrichment",
        now=NOW,
    )


def _agent(
    store: MemMetadataDb, control: MemStoreControlTables, llm: ScriptedLlm
) -> AlertEnrichmentAgent:
    gateway = LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    return AlertEnrichmentAgent(
        llm=gateway, tools=_tools(store, control), runtime=InProcAgentRuntime()
    )


def _hypothesis_answer(**overrides: object) -> str:
    payload: dict[str, object] = {
        "cause": "a shared upstream Mirth interface outage across these feeds",
        "citations": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _never_called(prompt: str, task: TaskClass) -> str:
    raise AssertionError("the model must not be called when retrieve found nothing to ground on")


# ── the deterministic half reaches no model ──────────────────────────────────


def test_the_deterministic_nodes_never_reach_the_gateway() -> None:
    """Factored into `tests.support.ast_checks` — the sixth agent to need this
    check is the first one that does not write a sixth copy of it."""
    from cinqflow.intelligence.agents import alert_enrichment as wired

    assert_deterministic_nodes(wired, {"_group", "_retrieve", "_compose"})
    assert {NODE_GROUP, NODE_RETRIEVE, NODE_COMPOSE} == DETERMINISTIC_NODES


def test_the_graph_package_imports_no_runtime() -> None:
    assert_graph_module_imports_no_runtime("src/cinqflow/core/agents/alert_enrichment/graph.py")


# ── the sharpest test: R0 writes NOTHING, ever ───────────────────────────────


def test_this_agent_never_reaches_proposals_or_record_proposal() -> None:
    """R0 is a HARDER constraint than R2, not a lighter one. There is no
    `core.proposals.submit` call anywhere in this agent because there is no
    `cinqflow.core.proposals` import anywhere in it — asserted over the
    import graph, which a renamed call site cannot quietly slip past — and
    then belt-and-braces over every ATTRIBUTE CALL's own name, structurally
    (an AST `ast.Call` node, never the raw source text: a docstring merely
    quoting 'proposals.submit' as prose, the way this very module's own
    docstring does two paragraphs up, must not trip a text-grep version of
    this same test)."""
    import cinqflow.core.agents.alert_enrichment.graph as graph_mod
    import cinqflow.core.agents.alert_enrichment.prompts as prompts_mod
    import cinqflow.intelligence.agents.alert_enrichment as wired

    for module in (wired, graph_mod, prompts_mod):
        tree = ast.parse(inspect.getsource(module))
        imported = {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        } | {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
        assert not any(
            m == "cinqflow.core.proposals" or m.startswith("cinqflow.core.proposals.")
            for m in imported
        ), f"{module.__name__} imports proposals machinery — an R0 agent may never reach it"

        called_attrs = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "submit" not in called_attrs, f"{module.__name__} calls something named submit(...)"
        assert "record_proposal" not in called_attrs, (
            f"{module.__name__} calls record_proposal(...)"
        )


def test_the_r0_whitelist_refuses_every_write_verb() -> None:
    """Mirrors `conformance.kit._law_checks`'s own `law:r0-read-only` style:
    asserted mechanically, not trusted from a docstring."""
    gateway = ActionGateway(whitelist=frozenset(RETRIEVE_TOOLS), risk_class=RISK_CLASS.name)
    for verb in ("retry_batch", "pause_feed", "edit_mapping", "delete_audit", "retry", "approve"):
        assert not gateway.permit(verb), f"{verb} must be refused — this agent runs at R0"


# ── the group node adapts core.sla.grouped, it does not recompute it ────────


def test_enrich_refuses_an_empty_group(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    llm = ScriptedLlm(_never_called)
    with pytest.raises(ValueError):
        _agent(store, control, llm).enrich("k", (), caller=BA, run_id="R-empty", now=NOW)


def test_group_adapts_core_sla_grouped_output_directly(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """The group this agent explains comes straight out of `core.sla.grouped`
    — the same function `workers.sla.SlaWorker.sweep` calls — not a second
    implementation of the same partition."""
    cycles = (
        sla_core.Cycle(feed_id=FEED_A, cycle_date=date(2026, 8, 30), expected_ts=NOW),
        sla_core.Cycle(feed_id=FEED_B, cycle_date=date(2026, 8, 30), expected_ts=NOW),
    )
    raised = sla_core.alerts_for(cycles, NOW + timedelta(hours=1))
    key, members = next(iter(sla_core.grouped(raised)))
    assert len(members) == 2, "both feeds missed the identical window — ONE group"

    llm = ScriptedLlm(lambda p, t: _hypothesis_answer())
    result = _agent(store, control, llm).enrich(key, members, caller=BA, run_id="R-group", now=NOW)
    assert set(result.feed_ids) == {FEED_A, FEED_B}
    assert len(result.facts) == 2
    assert len(result.citations) == 2


# ── no grounding: the model is never even asked ──────────────────────────────


def test_no_grounding_falls_back_without_calling_the_model(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """`retrieve` found nothing for a feed with no history, no reliability
    data and no incidents — `hypothesise` must not spend a call producing
    something `compose` would only have to discard."""
    llm = ScriptedLlm(_never_called)
    alert = _alert(GHOST_FEED)
    result = _agent(store, control, llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-ghost", now=NOW
    )

    assert result.model_called is False
    assert result.cause == CAUSE_UNDER_INVESTIGATION
    assert result.manual_path is True
    assert result.cause_citations == ()
    assert llm.calls == []
    # The group's OWN evidence — the alert's own citation — still ships,
    # even though the CAUSE did not.
    assert result.citations == (_feed_citation(GHOST_FEED),)


# ── hypothesise failing still ships a complete answer ────────────────────────


def test_hypothesise_failing_still_produces_a_complete_answer(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """THE DEFECT THE LANE-3 GATE FOUND ELSEWHERE, guarded here too: a broken
    run must not read as a careful one — and it must not raise, either."""
    _seed_sla_history(control, FEED_A, breached=5, total=10)
    llm = ScriptedLlm(lambda p, t: "{ not json")
    alert = _alert(FEED_A)

    result = _agent(store, control, llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-fail", now=NOW
    )

    assert result.model_called is True
    assert result.manual_path is True
    assert result.cause == CAUSE_UNDER_INVESTIGATION
    assert result.cause_citations == ()
    assert any("could not produce a valid cause hypothesis" in r for r in result.refusals)


class _RaisingLlm:
    """A fake `llm` port whose `.complete()` always raises the given
    exception — the shape the gateway is handed once Part 1 of W2-37's fix
    has translated a real vendor failure at the adapter boundary. Used here
    to prove the AGENT degrades correctly, not just the gateway in
    isolation."""

    def __init__(self, to_raise: Exception) -> None:
        self._to_raise = to_raise
        self.calls: list[tuple[str, TaskClass]] = []

    def complete(
        self,
        *,
        prompt,
        task_class,
        response_schema=None,
        max_tokens=2048,
        temperature=0.0,
    ):
        self.calls.append((prompt, task_class))
        raise self._to_raise

    def embed(self, texts):
        raise NotImplementedError

    def declared_endpoints(self):
        return frozenset({"mock://scripted"})


def test_a_transport_failure_and_a_budget_exhaustion_both_still_degrade_the_alert(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """W2-37 — THE BUG THIS REGRESSION GUARDS: a real network timeout, or a
    per-run budget exhausted on the very first call, used to CRASH
    `enrich()` with the raw transport exception or a bare
    `BudgetExhaustedError` instead of taking the manual path — because the
    gateway caught neither and re-raised the budget refusal as itself, a
    SIBLING of `ManualPathRequiredError`, not that error. Both must now
    degrade exactly like the schema failure `test_hypothesise_failing_
    still_produces_a_complete_answer` already guards."""
    _seed_sla_history(control, FEED_A, breached=5, total=10)
    alert = _alert(FEED_A)

    # -- a transport failure: the call is made, and fails in flight ---------
    transport_llm = _RaisingLlm(CompletionFailedError("simulated network timeout"))
    transport_result = _agent(store, control, transport_llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-transport", now=NOW
    )
    assert transport_result.model_called is True
    assert transport_result.manual_path is True
    assert transport_result.cause == CAUSE_UNDER_INVESTIGATION
    assert transport_result.cause_citations == ()
    assert any("could not produce a valid cause hypothesis" in r for r in transport_result.refusals)

    # -- a budget exhaustion: refused before the call is ever made ----------
    budget_llm = _RaisingLlm(AssertionError("must not be called once the budget refuses"))
    budget_gateway = LlmGateway(
        llm=budget_llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        # Deliberately below the gateway's own default `estimate_usd`
        # (0.01), so the very FIRST call is refused before it is made.
        budget=Budget(per_run_usd=Decimal("0.001"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    budget_agent = AlertEnrichmentAgent(
        llm=budget_gateway, tools=_tools(store, control), runtime=InProcAgentRuntime()
    )
    budget_result = budget_agent.enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-budget", now=NOW
    )
    assert budget_result.model_called is True
    assert budget_result.manual_path is True
    assert budget_result.cause == CAUSE_UNDER_INVESTIGATION
    assert budget_llm.calls == []


def test_a_blank_cause_from_the_model_still_falls_back(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    """The model may honestly say nothing — that is not a schema failure,
    and `compose` must still ship a complete answer."""
    _seed_sla_history(control, FEED_A, breached=5, total=10)
    llm = ScriptedLlm(lambda p, t: _hypothesis_answer(cause=""))
    alert = _alert(FEED_A)

    result = _agent(store, control, llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-blank", now=NOW
    )

    assert result.model_called is True
    assert result.cause == CAUSE_UNDER_INVESTIGATION
    assert result.manual_path is True


# ── the platform, not the model, decides what a hypothesis is worth ─────────


def test_a_grounded_and_cited_hypothesis_survives(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    _seed_sla_history(control, FEED_A, breached=5, total=10)
    citation = str(_feed_citation(FEED_A))
    llm = ScriptedLlm(lambda p, t: _hypothesis_answer(citations=[citation]))
    alert = _alert(FEED_A)

    result = _agent(store, control, llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-grounded", now=NOW
    )

    assert result.model_called is True
    assert result.manual_path is False
    assert result.cause == "a shared upstream Mirth interface outage across these feeds"
    assert citation in {str(c) for c in result.cause_citations}
    assert result.refusals == ()


def test_an_uncited_hypothesis_is_refused_before_dispatch(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    _seed_sla_history(control, FEED_A, breached=5, total=10)
    llm = ScriptedLlm(lambda p, t: _hypothesis_answer(citations=["feed:not-a-real-feed"]))
    alert = _alert(FEED_A)

    result = _agent(store, control, llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-uncited", now=NOW
    )

    assert result.cause == CAUSE_UNDER_INVESTIGATION
    assert result.manual_path is True
    assert result.cause_citations == ()
    assert any("was discarded" in r for r in result.refusals)
    assert any("no valid citation" in r for r in result.refusals)


def test_every_refusal_leaves_an_agent_action_row(
    store: MemMetadataDb, control: MemStoreControlTables
) -> None:
    _seed_sla_history(control, FEED_A, breached=5, total=10)
    llm = ScriptedLlm(lambda p, t: _hypothesis_answer(citations=["feed:not-a-real-feed"]))
    alert = _alert(FEED_A)

    _agent(store, control, llm).enrich(
        alert.group_key, (alert,), caller=BA, run_id="R-audit", now=NOW
    )

    actions = store.read_agent_actions(agent="alert-enrichment")
    assert any(a.outcome is ActionOutcome.ESCALATED_TO_MANUAL for a in actions)
    assert any("no valid citation" in a.detail for a in actions)
