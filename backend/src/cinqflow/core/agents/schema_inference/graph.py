"""CF-V1-E5-02 — the schema-inference agent's shape, as data.

    "I want the platform to propose field names, types, formats and
     nullability with per-field confidence and cited precedents, so that
     approving a data contract takes minutes instead of a morning."
    — CF-V1-E5-02

    ground(NO MODEL) -> infer(small) -> assemble(NO MODEL)

TWO OF THE THREE NODES CALL NO MODEL, and that ratio is the story's whole
argument. `ground` computes everything the evidence already determines — the
profiler's type arithmetic and the glossary's synonym match — and `assemble`
checks the model's output back against that grounding. The model is asked
exactly one question: what to do with the columns nobody could settle by
counting.

THREE CONSEQUENCES, EACH TESTED SEPARATELY:

  1. A feed whose columns are all deterministic costs ZERO tokens. The agent
     notices it has nothing to ask and skips the model entirely.
  2. The eval number is honest. `Acceptance` reports the deterministic and
     inferred shares apart, so a 94% contract built from 90% arithmetic cannot
     be read as a claim about the model.
  3. "Ungroundable column -> needs your input, never silently typed" is
     enforced in `assemble`, by the PLATFORM. A proposed column whose grounding
     the platform cannot find is dropped to `needs_input` — the model does not
     get to decide what counts as evidence, exactly as in CF-V0-E16-10.

R2 · config_proposal. The agent writes one row to `proposals.proposal` and
nothing else. Approval creates a DRAFT contract authored by the approver, which
then travels E11-01's lifecycle — so the agent's output enters the world at the
same door a hand-typed draft does.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass

#: The audit `agent`, the budget key, and the `proposals.agent` column.
AGENT = "schema-inference"

#: R2 — a config proposal. A human approves, always. Confidence routes WITHIN
#: this class and can never leave it (`RiskClass.at_confidence` ignores its
#: argument on purpose).
RISK_CLASS = RiskClass.R2

CAPABILITY = "propose_schema_contract"

NODE_GROUND = "ground"
NODE_INFER = "infer"
NODE_ASSEMBLE = "assemble"

NODES: tuple[str, ...] = (NODE_GROUND, NODE_INFER, NODE_ASSEMBLE)

#: The nodes that must never reach a model. Asserted by a test that walks the
#: implementation's AST, not by this comment.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_GROUND, NODE_ASSEMBLE})

#: What a BA sees when the platform will not guess. The exact string, so a
#: screen, a test and the eval all mean the same thing by it.
NEEDS_YOUR_INPUT = "needs your input"

#: Canonical names the platform will accept from the model. Anything else is a
#: name it invented, and a contract column named something no vocabulary
#: contains is a column nobody can map. Empty means "no constraint" — the
#: caller supplies the canonical model's field names as grounding.
NAME_PATTERN = r"^[a-z][a-z0-9_]{1,62}$"

INFER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["columns"],
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_name", "confidence"],
                "properties": {
                    # The source column this is about. The platform matches it
                    # back to the profile; a name the profile does not contain
                    # is a column the model invented.
                    "source_name": {"type": "string"},
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "string",
                            "int64",
                            "decimal",
                            "date",
                            "timestamp_utc",
                            "bool",
                            "uuid",
                            "json",
                        ],
                    },
                    "nullable": {"type": "boolean"},
                    "is_phi": {"type": "boolean"},
                    "date_format": {"type": "string"},
                    "glossary_id": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    # The model's own escape hatch. Setting this is a CORRECT
                    # answer, and the prompt says so — an agent with no way to
                    # decline has only one way to respond to a column it cannot
                    # read, and that way is a guess.
                    "needs_input": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

#: Below this, a column is routed to "needs your input" no matter what the
#: model said about it. A threshold in the platform rather than the prompt,
#: because a model asked to self-censor at a number will report that number.
CONFIDENCE_FLOOR = 0.6
