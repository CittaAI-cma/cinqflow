"""CF-V0-E16-10 — the Pipeline Insight Agent's shape, as data.

    route(small) -> plan_tools(small) -> execute(NO MODEL) -> answer(large)

Four nodes, one of them deterministic. The third node calls no model at all,
which is the point: tool execution is the platform's own governed surface, and
a model that could execute tools would be a model that could choose to execute
a tool nobody certified.

Two small-model calls and one large call means the gateway's routing table gets
a REAL CONSUMER in Wave 0, not only a canary — so "routing is by task from the
profile" is exercised rather than asserted.

This module declares the SHAPE and the CONTRACTS. The node implementations live
in `intelligence/agents/pipeline_insight.py`, because they touch pins; what
lives here is everything that can be reasoned about without one.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Any

#: The agent's name, used as the audit `agent` and the budget key.
AGENT = "pipeline-insight"

#: R0 — observe. Not configurable, not raised by confidence, not overridable
#: per environment. Wave 1 introduces R1/R2 agents; this one never changes.
RISK_CLASS = "R0"

NODE_ROUTE = "route"
NODE_PLAN_TOOLS = "plan_tools"
NODE_EXECUTE = "execute"
NODE_ANSWER = "answer"

#: The nodes, in order. The runtime walks edges; this is the reading order.
NODES: tuple[str, ...] = (NODE_ROUTE, NODE_PLAN_TOOLS, NODE_EXECUTE, NODE_ANSWER)

#: The one node that must never reach a model. Asserted by a test that walks
#: the implementation's AST, not by a comment.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_EXECUTE})


@unique
class Intent(StrEnum):
    """What the question is about. Chosen by the small model, from this list.

    A closed set, so an unroutable question becomes DECLINED rather than a
    creative interpretation. `DECLINED` is a first-class intent for exactly the
    questions Wave 0 must refuse by name.
    """

    EXPLAIN_FEED = "explain_feed"
    EXPLAIN_PLAN = "explain_plan"
    EXPLAIN_RUN = "explain_run"
    DEFINE_TERM = "define_term"
    DECLINED = "declined"


#: Which tools each intent is allowed to reach. A subset of the R0 whitelist,
#: narrowed further per intent — a question about a definition has no business
#: reading the error log, and a narrower reach is a smaller thing to audit.
INTENT_TOOLS: dict[Intent, tuple[str, ...]] = {
    Intent.EXPLAIN_FEED: ("get_feed", "get_schema_contract", "get_dq_rules"),
    Intent.EXPLAIN_PLAN: ("get_compiled_plan", "get_schema_contract", "get_dq_rules"),
    Intent.EXPLAIN_RUN: (
        "get_batch",
        "get_stage_status",
        "get_reconciliation",
        "get_drop_ledger",
        "list_errors",
        "get_quarantine_summary",
    ),
    Intent.DEFINE_TERM: ("lookup_reference",),
    Intent.DECLINED: (),
}

#: Questions Wave 0 declines BY NAME, with the story that will answer them.
#: Naming the future story is not politeness — it is what turns a refusal into
#: a roadmap answer instead of a dead end.
DECLINED_CAPABILITIES: dict[str, str] = {
    "member_level_data": (
        "Questions about individual members are answered by natural-language query over "
        "the data layers, which is CF-V4-E14-04 with full RBAC and masking underneath it. "
        "No Wave-0 tool can emit a member-level row."
    ),
    "write_action": (
        "Retrying a batch, pausing a feed or editing a mapping is a WRITE. This agent runs "
        "at R0 — it observes and explains, and no write tool is on its whitelist at any "
        "confidence. Wave 1's CF-V1-E16-06 introduces proposals a human approves."
    ),
    "free_form_sql": (
        "Composing SQL is not available to any agent in Wave 0. Text-to-tool, never "
        "text-to-SQL, until CF-V4-E14-04."
    ),
}

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent"],
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in Intent]},
        "feed_id": {"type": "string"},
        "batch_id": {"type": "string"},
        "term": {"type": "string"},
        "declined_capability": {
            "type": "string",
            "enum": list(DECLINED_CAPABILITIES),
        },
    },
    "additionalProperties": False,
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["calls"],
    "properties": {
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tool"],
                "properties": {
                    "tool": {"type": "string"},
                    "feed_id": {"type": "string"},
                    "batch_id": {"type": "string"},
                    "query": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

#: The answer contract. `claims` carry citations; `unanswered` names what the
#: grounding could not support. Thin results are never padded — an answer that
#: fills a gap with plausible prose is the failure mode this schema exists to
#: make structurally visible.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims", "confidence", "unanswered"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "citation_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "citation_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "unanswered": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
