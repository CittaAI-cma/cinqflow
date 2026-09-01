"""CF-V2-E12-05 — the alert-enrichment agent's shape, as data.

    "group -> retrieve -> hypothesise -> compose"
    "Three of `alert_enrichment`'s four nodes call no model, and `compose` is
     deterministic on purpose: `E12-05`'s exception path requires that when
     no supported hypothesis exists the alert still ships, carrying 'cause:
     under investigation'. A composer that needs the model cannot degrade."
    — platformdata/wave2.md §4.5

R0 — IT EXPLAINS; IT NEVER PROPOSES. Read that as a HARDER constraint than R2,
not a lighter one: an R2 agent may write exactly one thing, a `Proposal`, and
everything else is refused. This agent may write NOTHING — there is no
`core.proposals.submit` call anywhere in `intelligence.agents.alert_enrichment`,
full stop, and a test asserts the absence over the import graph rather than
trusting a docstring's word for it.

LINEAR, NOT BRANCHING, UNLIKE `fingerprint_match`. `fingerprint_match`
branches because its `retrieve` sometimes finds nothing worth a sentence and
its `narrate` step is genuinely SKIPPABLE — the graph itself has two shapes.
There is no equivalent fork here: every alert group gets an attempt at an
explanation, so the edge spec (assembled one layer up, in
`intelligence.agents.alert_enrichment.AlertEnrichmentAgent.graph`, for the
`layers` reason `fingerprint_match.graph` already documents — core sits below
`cinqflow.ports` in `.importlinter` and may not import `Edge`/`GraphSpec`) is
four nodes in a straight line. What varies run to run is not WHICH nodes
execute but what `hypothesise` itself has to work with — and whether it even
attempts a model call is that NODE's own business, not the edge spec's. It
may skip its own call entirely when `retrieve` found nothing worth grounding
a hypothesis in; either way, `compose` runs next regardless, because it
sits on every path through this graph.

THIS AGENT PROCESSES A GROUP, NOT ONE ALERT. `core.sla.grouped` already
performs the grouping — "five feeds missing an identical window is ONE
upstream fault, not five feed incidents" — deterministically, before this
agent is ever called. `workers.sla.SlaWorker.sweep` is the caller that
produces the `(group_key, tuple[SlaAlert, ...])` pairs this agent's `group`
node adapts into its own state shape. Recomputing that partition here would
be a second implementation of the same rule, free to drift from the first.

R0 · READ-ONLY, STRUCTURALLY. `RETRIEVE_TOOLS` is fixed and hardcoded — never
model-planned — so there is no plan step where a model could ask for a write.
Every one of the three is a certified read-only catalogue entry, chosen for
exactly what a lateness CAUSE needs: history for "is this feed chronically
late", the reliability score for which of the six signals is weakest, and any
open incident that might already explain the batch behind the missing file.
`lookup_reference` is deliberately NOT here — `fingerprint_match.graph`'s own
note on it applies just as much: a glossary hit is near-universal and would
count as "grounding" for almost every alert, which is a worse failure than
honestly calling the alert un-grounded.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass

#: The audit `agent`, and the budget key.
AGENT = "alert-enrichment"

#: R0 — observe and explain. Not configurable, not raised by confidence: a
#: cause hypothesis reported with high confidence is still an OBSERVATION,
#: and this agent has no path to anything else.
RISK_CLASS = RiskClass.R0

#: The fixed `audit.agent_action.action` value for every row this agent
#: writes. There is no `CAPABILITY` constant the way an R2 agent has one,
#: because there is nothing here to approve — only tool refusals and a
#: hypothesis the platform discarded.
ACTION = "explain_alert_group"

NODE_GROUP = "group"
NODE_RETRIEVE = "retrieve"
NODE_HYPOTHESISE = "hypothesise"
NODE_COMPOSE = "compose"

NODES: tuple[str, ...] = (NODE_GROUP, NODE_RETRIEVE, NODE_HYPOTHESISE, NODE_COMPOSE)

#: The three nodes that must never reach a model. Asserted by a test that
#: walks the implementation's AST, not by this comment. `hypothesise` is the
#: one exception — and even it may skip its own call, from within its own
#: body, when `retrieve` found nothing to ground a hypothesis in.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_GROUP, NODE_RETRIEVE, NODE_COMPOSE})

#: `retrieve`'s WHOLE tool surface — fixed and hardcoded, never model-planned.
RETRIEVE_TOOLS: tuple[str, ...] = ("get_sla_history", "get_reliability_score", "list_incidents")

#: How many feeds in one group `retrieve` fetches history and a score for. A
#: bound, not a tuning knob — a shared upstream fault can span a whole
#: domain, and the point is "enough feeds to ground a shared-cause
#: hypothesis", not an exhaustive per-feed report the reliability screen
#: already gives.
MAX_GROUP_FEEDS = 5

#: The literal fallback cause. `compose` writes exactly this string when
#: `hypothesise` produced nothing supported by a real citation — no synonym,
#: no rephrasing, so a grep for this constant is a complete audit of "which
#: alerts shipped unexplained."
CAUSE_UNDER_INVESTIGATION = "under investigation"

HYPOTHESISE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["cause", "citations"],
    "properties": {
        "cause": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

__all__ = [
    "ACTION",
    "AGENT",
    "CAUSE_UNDER_INVESTIGATION",
    "DETERMINISTIC_NODES",
    "HYPOTHESISE_SCHEMA",
    "MAX_GROUP_FEEDS",
    "NODES",
    "NODE_COMPOSE",
    "NODE_GROUP",
    "NODE_HYPOTHESISE",
    "NODE_RETRIEVE",
    "RETRIEVE_TOOLS",
    "RISK_CLASS",
]
