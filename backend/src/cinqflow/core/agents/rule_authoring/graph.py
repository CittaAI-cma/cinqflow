"""CF-V1-E7-01 — the NL-rule agent's shape, as data.

    "NL rule authoring — plain English → SQL/PySpark + business-language
     explanation + confidence; both texts stored"
    "the 110 legacy rules are the labeled golden set, so accuracy is
     measurable from day one"
    — CF-V1-E7-01

    ground(NO MODEL) -> author(small) -> assemble(NO MODEL)

The fifth Wave-1 R2 agent, on the shape CF-V1-E5-02 established. What is
different here is WHAT THE MODEL IS ALLOWED TO PRODUCE: not SQL, not PySpark,
not a predicate — a CHECK, which is a kind from `core.rules.CheckKind` plus
scalar parameters. The platform renders all three notations from it.

That single decision is what the story's "unsafe logic blocked from
publication" (CF-V1-E7-04) becomes a type property rather than a filter, and it
is why there is no code path from this agent's output to a query string.

TWO THINGS THE PLATFORM SETTLES BEFORE THE MODEL IS ASKED:

  1. THE COLUMN. If the BA's sentence names something the contract has, or a
     spelling the glossary records as a synonym for one, the platform resolves
     it and the model is not asked to spell a column name. Same rule as
     CF-V1-E5-02 and CF-V1-E6-02: the model picks the concept, the estate
     spells the name.
  2. AN IDENTICAL RULE. If a published rule on this feed states the same thing
     in the same words, it IS the answer — proposing it again would ask a
     steward to re-approve what they signed.

R2 · config_proposal. One row to `proposals.proposal`, nothing else.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass
from cinqflow.core.rules import CheckKind, Comparison, Dimension

AGENT = "rule-authoring"
RISK_CLASS = RiskClass.R2
CAPABILITY = "propose_dq_rule"

NODE_GROUND = "ground"
NODE_AUTHOR = "author"
NODE_ASSEMBLE = "assemble"

NODES: tuple[str, ...] = (NODE_GROUND, NODE_AUTHOR, NODE_ASSEMBLE)
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_GROUND, NODE_ASSEMBLE})

#: What a BA sees when the platform will not turn their sentence into a rule.
#: The exact string, so a screen, a test and CF-V1-E7-04's queue agree.
NEEDS_TECHNICAL_REVIEW = "needs technical review"

AUTHOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["rules"],
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["stated", "confidence"],
                "properties": {
                    # The BA's sentence, copied back so the platform can match
                    # the answer to the request when several are asked at once.
                    "stated": {"type": "string"},
                    "name": {"type": "string"},
                    "check_kind": {"type": "string", "enum": [k.value for k in CheckKind]},
                    # THE NUMBER of the column in the grounding's list. A number
                    # survives the PHI scrubber; a name does not always. The
                    # lesson CF-V1-E6-02 paid for.
                    "column_ref": {"type": "integer", "minimum": 1},
                    "column": {"type": "string"},
                    "other_column_ref": {"type": "integer", "minimum": 1},
                    "allowed": {"type": "array", "items": {"type": "string"}},
                    "case_sensitive": {"type": "boolean"},
                    "pattern": {"type": "string"},
                    "minimum": {"type": "string"},
                    "maximum": {"type": "string"},
                    "comparison": {"type": "string", "enum": [c.value for c in Comparison]},
                    "within_days": {"type": "integer", "minimum": 1},
                    "reference_table": {"type": "string"},
                    "reference_column": {"type": "string"},
                    "dimension": {"type": "string", "enum": [d.value for d in Dimension]},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "glossary_id": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    # The escape hatch, and a CORRECT answer. CF-V1-E7-04's
                    # queue is fed from here: a rule the vocabulary cannot
                    # express must say so rather than be approximated.
                    "unsupported": {"type": "boolean"},
                    "unsupported_reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

#: Below this a rule goes to technical review whatever the model claimed.
#:
#: The highest floor of the three Wave-1 agents, and the reason is the blast
#: radius. A mis-typed column is caught by the next load; a mis-mapped one
#: lands a value in the wrong field; a wrong DQ rule at Critical QUARANTINES
#: every row that breaks it — a rule nobody checked can empty a roster.
CONFIDENCE_FLOOR = 0.80
