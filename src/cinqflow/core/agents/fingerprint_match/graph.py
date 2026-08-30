"""CF-V2-E12-04 — the fingerprint-match agent's shape, as data.

    "a novel fingerprint that matches nothing -> retrieve the nearest prior
     narratives, and propose a draft runbook (R2, human approves)"
    "Auto-apply any fix in this story."
    "bounded auto-retry for the transient class is R1 and explicitly out of
     scope for this story ... the whitelist must not contain a write tool
     when the wave ships"
    — platformdata/wave2.md §2.2, §4.5

    gather(NO MODEL) -> retrieve(NO MODEL) -> narrate(small) -> draft(large)
                                            \\-------------------------^

THIS AGENT HAS EXACTLY ONE JOB: the NOVEL case. `core.operations.fingerprint`
already does everything deterministic — `signature()`, `match_guide()`,
`fingerprint_batch()` — and `workers.incidents.IncidentWorker.on_batch_failed`
already opens the incident and computes `.kind` from it, at R0, with no model
anywhere near it. When `.kind` is KNOWN, that machinery has already answered
the question and this agent has nothing to add — it is never invoked, and
`intelligence.agents.fingerprint_match.FingerprintMatchAgent.propose` refuses
to run the graph at all rather than let a KNOWN incident anywhere near a
model. When `.kind` is NOVEL, "the platform recognises nothing" is the honest
answer computation already gave, and what is left is the question computation
cannot answer: what a candidate fix might look like, for a human to judge.

WHY FOUR NODES AND A BRANCH, WHEN MAPPING-SUGGESTION NEEDED THREE AND NO
BRANCH. `gather` and `retrieve` are both deterministic, exactly like
`ground`/`assemble` elsewhere — but here the deterministic half sits FIRST and
UNBROKEN, because there is no cheap settled case to skip past: every incident
this agent sees is already known-novel, so there is always something to
retrieve and always a guide to draft. What varies is only whether `retrieve`
found anything worth writing a sentence about:

  - `has_grounding` — another still-open incident carries this EXACT
    fingerprint: several operators have already hit it, unresolved.
    `narrate` (small model) turns that into one sentence a human reads
    before the draft.
  - `novel` — no such sibling exists. `narrate` would have nothing true to
    say about precedent, so it is skipped and `draft` works from the
    evidence bundle alone.

`has_grounding` is a NEAR-MISS SIBLING, deliberately, not a reference hit.
Every `ErrorCategory` and `Layer` has a platform-generated glossary
definition, so a reference query built from the category alone matches
something on almost every incident — counting that as grounding would run
`narrate` on nearly every draft to say, in effect, nothing. Reference hits
still reach the model: they fold into `draft`'s context on BOTH branches,
unconditionally; what the branch decides is only whether there is a genuine
"this has already happened elsewhere and stayed open" sentence to write.

Both branches converge on `draft`, unconditionally, because a draft is the
one thing this agent is FOR: "the novel case gets a draft" is true of every
run that reaches this graph, with or without precedent to lean on.

RETRIEVAL HERE IS THE CERTIFIED READ-ONLY CATALOGUE, HONESTLY, NOT THE
SEMANTIC KNOWLEDGE PLANE CF-V2-E16-04/E16-07 STILL OWE. Those stories give a
FUTURE version of `retrieve` an embedded corpus of closed incident narratives
to search. Today `core/retrieval` is lexical-only, and there is no closed
narrative corpus at all yet — an incident's narrative is not even writable
until `E16-07`'s embed-on-close hook exists. So `retrieve` calls exactly what
IS certified: `list_incidents` for a sibling still open with the identical
fingerprint, and `lookup_reference` for the platform's own terminology. When
E16-04/07 land, `retrieve`'s TOOLS gain a real semantic option; this graph's
SHAPE — two deterministic nodes then a branch — does not need to change.

THE DON'TS, ENFORCED STRUCTURALLY:

  - "Auto-apply any fix" — `draft` never executes anything. `RecoveryGuide`
    has no executable field (see `core.operations.fingerprint`'s own
    docstring: "a guide a machine could run is a guide nobody reviews"), and
    whatever `remedy` the model names is a suggested `OpsAction` IDENTIFIER
    on the drafted guide — never called, only proposed, exactly like every
    other field here.
  - "bounded auto-retry ... is R1 and out of scope" — this agent has no
    retry action on its tool whitelist, and could not trigger one if it did:
    R2 may write exactly one thing, a `core.proposals.Proposal`, and nothing
    on `RETRIEVE_TOOLS` mutates anything.
  - "the whitelist must not contain a write tool" — `RETRIEVE_TOOLS` is a
    fixed, hardcoded tuple of three read-only catalogue entries, never
    model-chosen, so there is no plan step where a model could ask for a
    fourth.

WHERE THE EDGES ACTUALLY LIVE. `platformdata/wave2.md` §4.5 sketches
`EDGES = (Edge("gather", "retrieve"), ...)` inline in this file — but `Edge`
and `GraphSpec` are declared on `cinqflow.ports.agent_runtime`, and
`.importlinter`'s `layers` contract puts `cinqflow.core` BELOW
`cinqflow.ports`: core may not import a port, even an inert dataclass with no
I/O behind it. `pipeline_insight` already resolved this the same way — its
`core/agents/pipeline_insight/graph.py` declares no `Edge` at all, and
`intelligence/agents/pipeline_insight.py`'s `.graph()` method builds the real
`Edge`/`GraphSpec` objects from the NODE_* names declared here. This module
follows the same split: NODE NAMES and the `when` keys the branch reads
(`has_grounding`, `novel`) are the data core owns; the literal `Edge` tuple is
assembled one layer up, in `intelligence.agents.fingerprint_match.graph()`.

R2 · `draft_recovery_guide`. The agent writes ONE thing: a proposal that
becomes, on approval, a DRAFT `ObjectType.RUNBOOK` — the governed object
`core.operations.fingerprint.RecoveryGuide` has been waiting on since Wave 1,
and the one `recovery_guides()` reads back once a steward publishes it.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass

#: The audit `agent`, the budget key, and the `proposals.agent` column.
AGENT = "fingerprint-match"

#: R2 — it proposes a runbook; a human approves. Not configurable, and never
#: raised or lowered by confidence: a novel failure always gets a DRAFT, never
#: a published guide.
RISK_CLASS = RiskClass.R2

CAPABILITY = "draft_recovery_guide"

NODE_GATHER = "gather"
NODE_RETRIEVE = "retrieve"
NODE_NARRATE = "narrate"
NODE_DRAFT = "draft"

NODES: tuple[str, ...] = (NODE_GATHER, NODE_RETRIEVE, NODE_NARRATE, NODE_DRAFT)

#: The nodes that must never reach a model. Asserted by a test that walks the
#: implementation's AST, not by this comment.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_GATHER, NODE_RETRIEVE})

#: The two state keys `retrieve` sets and the branch reads. Mutually
#: exclusive, so which `Edge.when` fires never depends on declaration order —
#: see `platformdata/wave2.md` §4.5's own edge spec.
STATE_HAS_GROUNDING = "has_grounding"
STATE_NOVEL = "novel"

#: `retrieve`'s WHOLE tool surface — fixed and hardcoded, never model-planned.
#: All three are read-only catalogue entries (`core.tools.CATALOGUE`); nothing
#: here could be a write tool because nothing here is chosen at call time.
RETRIEVE_TOOLS: tuple[str, ...] = ("list_incidents", "get_incident", "lookup_reference")

#: How many same-fingerprint siblings `retrieve` pulls full detail for. A
#: bound, not a tuning knob — the point is "at least one, so the draft can say
#: 'this has already happened N times and stayed open'", not an exhaustive
#: history `core.operations.fingerprint.PriorIncident` already carries once
#: the guide is published.
MAX_NEAR_MISS = 3

NARRATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["narrative"],
    "properties": {
        "narrative": {"type": "string"},
        # Citation ids the narrative actually used, from what `retrieve`
        # handed it. Anything else is discarded before a human sees it — the
        # model does not get to decide what counts as evidence, same rule
        # `pipeline_insight._keep_only_grounded` enforces.
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "steps", "confidence"],
    "properties": {
        "title": {"type": "string"},
        # Prose for a human, exactly like a published `RecoveryGuide.steps` —
        # there is no field for something a machine could run.
        "steps": {"type": "array", "items": {"type": "string"}},
        # DELIBERATELY A PLAIN STRING, not a schema `enum` of `OpsAction`
        # values. An enum here would make the GATEWAY reject an off-catalogue
        # guess as an invalid completion — one bounded repair, then the WHOLE
        # draft escalates to the manual path over a single field. The prompt
        # states the closed vocabulary instead, and `_build_guide` is where an
        # answer outside it is discarded — title and steps survive either
        # way, exactly the discipline `mapping_suggestion._resolve_target`
        # applies to a proposed `target_entity`/`target_field`.
        "remedy": {"type": "string"},
        "is_transient": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "additionalProperties": False,
}

#: Below this, a proposed REMEDY is dropped whatever the model claimed — the
#: title and steps still reach the reviewer. Same number and the same
#: reasoning `mapping_suggestion.CONFIDENCE_FLOOR` states: a mis-typed column
#: is caught by the next load, a wrong remedy on a guide nobody has seen
#: before is a wrong action wearing the platform's authority the first time
#: anyone runs it.
CONFIDENCE_FLOOR = 0.75

__all__ = [
    "AGENT",
    "CAPABILITY",
    "CONFIDENCE_FLOOR",
    "DETERMINISTIC_NODES",
    "DRAFT_SCHEMA",
    "MAX_NEAR_MISS",
    "NARRATE_SCHEMA",
    "NODES",
    "NODE_DRAFT",
    "NODE_GATHER",
    "NODE_NARRATE",
    "NODE_RETRIEVE",
    "RETRIEVE_TOOLS",
    "RISK_CLASS",
    "STATE_HAS_GROUNDING",
    "STATE_NOVEL",
]
