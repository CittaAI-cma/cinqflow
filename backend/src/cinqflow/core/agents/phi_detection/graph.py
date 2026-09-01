"""CF-V1-E5-03 — the PHI-detection agent's shape, as data.

    "I want the platform to detect PHI and healthcare code sets in every
     column at contract time, so that masking and rule suggestions are driven
     by what the data actually holds instead of by what somebody remembered."
    — CF-V1-E5-03

    classify(NO MODEL) -> name(small) -> confirm(NO MODEL)

THE ONE THING THAT MAKES THIS AGENT DIFFERENT FROM EVERY OTHER ONE:

    IT IS NEVER SHOWN A VALUE.

Not a masked value, not an example, not a top-N frequency — the grounding for
this agent is column names, integer statistics, pattern hit-rates and glossary
definitions, and nothing else. `tests/contract/test_phi_detection_agent.py`
asserts it by putting a synthetic SSN in the profile and checking it does not
appear anywhere in the assembled prompt.

The reason is not belt-and-braces on the gateway's scrubber. It is that this
agent's whole job is deciding which columns hold protected data, and an agent
that must read protected data to decide whether data is protected has a
bootstrapping problem it can only lose. Everything it needs was already
computed: `01`/`02` in a column is a distinct count of 2, `1841293990` is
"every value passes the NPI checksum", and a name is a name.

WHAT THE MODEL IS ACTUALLY FOR. After the glossary and the arithmetic have
run, what is left is columns with an unhelpful name, no glossary term and no
decisive shape — `SUBSCR_REL_CD`, `AUX_ID_2`, `PROV_SPEC`. Those are already
protected by precaution; the model's job is to say WHAT they are, so a steward
reviewing forty flagged columns has forty sentences instead of forty shrugs.
It cannot unprotect any of them.

R2 · config_proposal. One row in `proposals.proposal`, and approval produces a
DRAFT contract carrying the flags — authored by the approver, so E11-01's
universal negative applies unchanged.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass
from cinqflow.core.patterns import CodeSet
from cinqflow.core.phi import PhiKind

#: The audit `agent`, the budget key, and the `proposals.agent` column.
AGENT = "phi-detection"

#: R2. A proposal, always — and note what this class does NOT permit even at
#: confidence 1.0: applying a masking change. Masking is R4-adjacent and the
#: flags travel through a human-approved contract, never from here.
RISK_CLASS = RiskClass.R2

CAPABILITY = "propose_phi_classification"

NODE_CLASSIFY = "classify"
NODE_NAME = "name"
NODE_CONFIRM = "confirm"

NODES: tuple[str, ...] = (NODE_CLASSIFY, NODE_NAME, NODE_CONFIRM)

#: Nodes that must never reach a model. Asserted by a test that walks the
#: implementation's AST.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_CLASSIFY, NODE_CONFIRM})

#: What a steward sees on a column the platform protected without identifying.
PROTECTED_PENDING_REVIEW = "protected — pending your review"

#: Below this, the model's naming is recorded but the column stays flagged for
#: a steward. Note what the floor does NOT do: it never lowers protection. A
#: low-confidence answer costs a steward's attention, never a flag.
CONFIDENCE_FLOOR = 0.6

NAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["columns"],
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_name", "is_phi", "confidence"],
                "properties": {
                    "source_name": {"type": "string"},
                    # The model may set this TRUE. Setting it false is refused
                    # by `core.phi.merge_inference` and recorded — the schema
                    # permits the value so that the refusal is a governance
                    # event with an audit row, rather than a validation error
                    # the model retries its way around.
                    "is_phi": {"type": "boolean"},
                    "phi_kind": {"type": "string", "enum": [k.value for k in PhiKind]},
                    "code_set": {"type": "string", "enum": [c.value for c in CodeSet]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}
