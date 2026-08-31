"""LANE 3 — CF-V2-E12-05's gate. The ONLY place a quality claim is made.

    "Measurable — Cause hypothesis accepted by operators in >= 80% of
     enriched alerts, measured monthly."
    — CF-V2-E12-05, acceptance criteria

THE GAP THIS FILE IS HONEST ABOUT. "Accepted by operators, measured monthly"
is a PRODUCTION TELEMETRY metric — it counts real operators clicking accept
or reject on a real alert, over a month of real traffic. No pytest run,
against any lane, computes it: `tests/contract/test_alert_enrichment_agent
.py`'s own docstring says so directly ("It proves NOTHING about hypothesis
QUALITY; that is Lane 3's >= 80% ... gate, measured monthly"), and this file
does not pretend otherwise by inventing a stand-in percentage from a hand-
built set of alerts graded against itself — exactly the number `test_lane3
_phi_detection.py`'s own docstring calls out as meaningless. That telemetry
belongs on the `/ai/acceptance` surface CF-V1-E16-03 already built, fed by
real operator decisions once this ships — not fabricated here.

WHAT IS GRADED INSTEAD, AND WHY IT IS THE RIGHT SUBSTITUTE UNTIL THAT
TELEMETRY EXISTS. Every "don't" in this story IS testable against the real
model today, and each one is a precondition an operator-acceptance number
would be meaningless without: an alert whose cause is fabricated cannot
possibly earn a HONEST acceptance rate, whatever the number says. So this
file grades exactly what `_compose`'s own two rules state —

    "Enrich, never replace, the existing alert channels."
    "cause: under investigation ... enrichment never fabricates a reason."

— across several distinct real alert groupings, against the REAL endpoint.

Skips, visibly, until an endpoint is configured (`.env`, see
`conftest.LANE_3_REQUIREMENTS`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core import sla as sla_core
from cinqflow.core.agents.alert_enrichment.graph import CAUSE_UNDER_INVESTIGATION
from cinqflow.core.agents.alert_enrichment.prompts import TEMPLATES
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.intelligence.agents.alert_enrichment import AlertEnrichmentAgent, EnrichedAlert
from cinqflow.intelligence.evals import citation_fidelity
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.authn import Principal, Role, Scopes

pytestmark = [pytest.mark.evaluation, pytest.mark.lane3]

NOW = datetime(2026, 8, 30, 7, 0, tzinfo=UTC)
BA = Actor(subject="dev-ops@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya")

#: A hypothesis carrying no citation must NEVER ship — `_compose`'s own
#: refusal, at Lane 1. This confirms the real model's raw output does not
#: find a path around it: any accepted-but-uncited cause is one instance,
#: not a percentage.
MAX_UNCITED_ACCEPTED_CAUSE = 0


#: Distinct, REALISTIC groupings — a single feed running late, a genuine
#: multi-feed shared-cause outage (the story's own worked example: "five ADT
#: feeds miss the same arrival window"), and a feed with no history at all —
#: not scenarios purpose-built to make the gate easy.
def _groups() -> tuple[tuple[str, tuple[sla_core.SlaAlert, ...]], ...]:
    single = (
        sla_core.SlaAlert(
            feed_id="acme-837-medical",
            cycle_date=date(2026, 8, 30),
            severity=sla_core.AlertSeverity.WARNING,
            summary="acme-837-medical: expected 6:00 AM — not received",
            citations=(CitationId(CitationKind.FEED, "acme-837-medical"),),
            group_key="2026-08-30T06:00-single",
        ),
    )
    shared = tuple(
        sla_core.SlaAlert(
            feed_id=feed_id,
            cycle_date=date(2026, 8, 30),
            severity=sla_core.AlertSeverity.CRITICAL,
            summary=f"{feed_id}: expected 5:00 AM — not received",
            citations=(CitationId(CitationKind.FEED, feed_id),),
            group_key="2026-08-30T05:00-shared",
        )
        for feed_id in ("payer-a-adt", "payer-b-adt", "payer-c-adt", "payer-d-adt", "payer-e-adt")
    )
    ghost = (
        sla_core.SlaAlert(
            feed_id="ghost-feed-no-history",
            cycle_date=date(2026, 8, 30),
            severity=sla_core.AlertSeverity.WARNING,
            summary="ghost-feed-no-history: expected 7:00 AM — not received",
            citations=(CitationId(CitationKind.FEED, "ghost-feed-no-history"),),
            group_key="2026-08-30T07:00-ghost",
        ),
    )
    return (
        ("2026-08-30T06:00-single", single),
        ("2026-08-30T05:00-shared", shared),
        ("2026-08-30T07:00-ghost", ghost),
    )


@pytest.fixture
def agent(lane3_llm: Any) -> AlertEnrichmentAgent:
    from cinqflow.adapters.local.secrets import DotenvSecrets
    from cinqflow.installer.profile import load
    from cinqflow.intelligence.wiring import budget_from, routing_from

    store = MemMetadataDb()
    control = MemStoreControlTables()
    reviewer = Actor(subject="reviewer@cinqcare.test", actor_type=ActorType.HUMAN, display_name="R")
    for template in TEMPLATES:
        obj = template.as_governed(author=BA)
        reviewed, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=BA)
        approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=reviewer)
        published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=reviewer)
        store.save(published)
    profile = load("profiles/local.yaml")
    secrets = DotenvSecrets()
    gateway = LlmGateway(
        llm=lane3_llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=budget_from(profile),
        routing=routing_from(profile, secrets),
    )
    tools = ToolContext(
        principal=Principal(
            subject="ops@cinqcare.test",
            display_name="Priya Nair",
            roles=frozenset({Role.OPERATIONS}),
            scopes=Scopes(feeds=frozenset({"*"}), domains=frozenset({"*"})),
        ),
        control=control,
        metadata=store,
        agent="alert-enrichment",
        now=NOW,
    )
    return AlertEnrichmentAgent(llm=gateway, tools=tools, runtime=InProcAgentRuntime())


def _enrich_all(agent: AlertEnrichmentAgent) -> list[tuple[str, EnrichedAlert]]:
    return [
        (group_key, agent.enrich(group_key, alerts, caller=BA, run_id=f"eval-{group_key}", now=NOW))
        for group_key, alerts in _groups()
    ]


def test_a_cause_never_ships_without_its_own_citation(agent: AlertEnrichmentAgent) -> None:
    """`_compose`'s refusal, confirmed against the real model: an accepted
    (non-fallback) cause with zero `cause_citations` would be exactly the
    fabricated-reason defect the story's own don't names."""
    violations: list[str] = []
    for group_key, enriched in _enrich_all(agent):
        if enriched.manual_path or enriched.cause == CAUSE_UNDER_INVESTIGATION:
            continue
        if not enriched.cause_citations:
            violations.append(f"{group_key}: {enriched.cause!r} shipped with no citation")
    assert len(violations) <= MAX_UNCITED_ACCEPTED_CAUSE, "\n".join(violations)


def test_every_citation_an_enriched_alert_carries_resolves(agent: AlertEnrichmentAgent) -> None:
    """100%, the same `CF-V0-E16-10` resolvability bar every other agent's
    grounding is held to."""
    failures: list[str] = []
    for group_key, enriched in _enrich_all(agent):
        fidelity = citation_fidelity(tuple(str(c) for c in enriched.citations))
        if not fidelity.passes:
            failures.append(f"{group_key}: unresolvable {fidelity.unresolvable}")
    assert not failures, "\n".join(failures)


def test_an_alert_never_arrives_with_an_empty_headline(agent: AlertEnrichmentAgent) -> None:
    """'Enrich, never replace' means the alert is never WORSE than the plain
    one it augments — every group still names its affected feeds and states
    a cause, even the fallback one."""
    for group_key, enriched in _enrich_all(agent):
        assert enriched.facts, f"{group_key}: no facts — an enriched alert must not read as empty"
        assert enriched.cause.strip(), f"{group_key}: no cause at all, not even the fallback"
        assert enriched.feed_ids, f"{group_key}: no affected feeds named"


def test_a_feed_with_no_history_gets_an_honest_fallback_not_a_guess(
    agent: AlertEnrichmentAgent,
) -> None:
    """The exception path, against the real model: nothing in `retrieve`
    grounds a hypothesis for a feed with no history, so `hypothesise` must
    either skip its call or the model must decline — `compose` then falls
    back to `CAUSE_UNDER_INVESTIGATION` rather than the model inventing a
    plausible-sounding cause it has no evidence for."""
    ghost = next(e for key, e in _enrich_all(agent) if key.endswith("-ghost"))
    if not ghost.manual_path and ghost.cause != CAUSE_UNDER_INVESTIGATION:
        assert ghost.cause_citations, (
            f"a confident cause for a feed with no history must still be grounded — got "
            f"{ghost.cause!r} with no citations backing it"
        )


def test_cost_and_latency_stay_within_budget(agent: AlertEnrichmentAgent) -> None:
    """One hypothesise call at most per group — the graph's own linear shape
    (group, retrieve, compose deterministic; hypothesise the only model
    call) bounds this independent of what the model says."""
    import time

    group_key, alerts = _groups()[1]  # the five-feed shared-cause group
    started = time.monotonic()
    agent.enrich(group_key, alerts, caller=BA, run_id="eval-budget", now=NOW)
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 15_000, f"one enrichment took {elapsed_ms:.0f}ms"
