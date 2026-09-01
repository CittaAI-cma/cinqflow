"""CF-V3-E9-03 — the merge evidence card: R4, informational only, never a proposal.

    "for each potential merge ... an AI-prepared evidence card — demographics
     side by side, source history, prior identity events — and a preview of
     exactly which records would repoint or separate, with the decision
     itself always mine"
    "Execute any merge or split automatically, at any confidence, ever — this
     is a human decision by policy, not by threshold."
    — CF-V3-E9-03

WHY TWO NODES, NOT FOUR LIKE FINGERPRINT-MATCH. That agent's `retrieve` node
exists because a novel incident needs the platform to go FIND precedent it
does not already hold. This agent needs no retrieval at all: by the time it
runs, `core.identity.merge.plan_merge` has already computed the ENTIRE
preview (every repoint, every collapse) and `core.identity.merge.
compare_demographics` has already computed the entire field-by-field
comparison. There is nothing left to look up — only something left to
NARRATE. `gather` packages what core already computed into prompt text;
`narrate` is the one model call.

WHY THIS SCHEMA HAS NO DECISION FIELD, NOT EVEN A CONFIDENCE ONE. Every other
agent's response schema in this codebase has a `confidence` — R2's floor-gate
mechanism depends on the model reporting one. R4 has no floor to gate: there
is no confidence at which this agent may propose anything, so there is no
field for it to name one. `NARRATE_SCHEMA` accepts exactly `narrative` (the
plain-English text) and `grounded_fields` (which of the fields the platform
handed it the narrative actually leaned on, for the citation-style
verification other agents use `citations` for). Nothing else survives
`additionalProperties: false` — a model that tried to add `recommendation` or
`should_merge` would have its ENTIRE completion rejected by the gateway's
schema validation, not silently stripped of the extra field.

THIS AGENT NEVER SUBMITS A `core.proposals.Proposal`, STRUCTURALLY.
`Proposal.__post_init__` already refuses construction at a risk class whose
`automatable` is `False` (`NotAutomatableError`) — R4's is. So even if this
module's implementation tried to submit one, core would refuse it before the
object existed. What this agent actually returns
(`intelligence.agents.merge_evidence.EvidenceCard`) is a plain, unstored
value handed straight to the steward's own screen — there is no lifecycle
state for it to occupy, because it is not a decision awaiting one.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass

AGENT = "merge-evidence"

#: R4 — phi_consequential, human-always. Not automatable at any confidence,
#: which is why nothing below has a confidence field.
RISK_CLASS = RiskClass.R4

CAPABILITY = "narrate_merge_evidence"

NODE_GATHER = "gather"
NODE_NARRATE = "narrate"
NODES: tuple[str, ...] = (NODE_GATHER, NODE_NARRATE)

#: `gather` reaches no model — proven by `tests.support.ast_checks
#: .assert_deterministic_nodes` against the wired implementation, the same
#: way every other agent's deterministic half is proven.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_GATHER})

NARRATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["narrative", "grounded_fields"],
    "properties": {
        "narrative": {"type": "string"},
        # Which compared fields (from core.identity.merge.DemographicComparison)
        # the narrative actually leaned on — the same discipline every other
        # agent's `citations` field enforces, renamed because there is no
        # CitationId here to resolve: the "evidence" is core's own already-
        # computed comparison, not a retrieved document.
        "grounded_fields": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

__all__ = [
    "AGENT",
    "CAPABILITY",
    "DETERMINISTIC_NODES",
    "NARRATE_SCHEMA",
    "NODES",
    "NODE_GATHER",
    "NODE_NARRATE",
    "RISK_CLASS",
]
