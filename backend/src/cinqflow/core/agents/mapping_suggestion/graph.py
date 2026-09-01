"""CF-V1-E6-02 — the mapping-suggestion agent's shape, as data.

    "AI source→target mapping with confidence + exemplars from the golden
     workbooks; UNMAPPED flagged, never guessed"
    "The core AI value: new payer's MBR_DOB maps like Fidelis date_of_birth
     did; benchmarked by blind re-derivation of live sources."
    — CF-V1-E6-02

    ground(NO MODEL) -> suggest(small) -> assemble(NO MODEL)

The same three-node shape CF-V1-E5-02 established, for the same reason: two of
the three nodes call no model, and the model is asked exactly one question —
what to do with the source columns nobody could settle from the estate's own
vocabulary and its own approved mappings.

WHAT COUNTS AS SETTLED IS A THREE-WAY DISTINCTION, AND IT IS THE STORY:

  1. A GLOSSARY SYNONYM settles the line. `BG-004` records that `DOB`,
     `Patient_dob` and `MemberDateOfBirth` are all Member Date of Birth —
     written by the client's own analysts, and the canonical column it names
     is the target. No model is consulted, and none should be.
  2. THIS FEED'S OWN PUBLISHED MAPPING settles the line. That is not evidence,
     it is the approved decision; re-proposing it would ask a steward to
     re-approve what they already signed. Only what CHANGED is proposed.
  3. ANOTHER FEED'S PUBLISHED MAPPING IS AN EXEMPLAR, NOT AN ANSWER. That
     Fidelis maps `DOB` to `members.date_of_birth` is strong evidence that
     UnitedHealth's `DOB` means the same thing — and it is not proof, because
     two payers can spell two different concepts the same way. So it is shown
     to the model as precedent, with the name of the person who approved it,
     and the model decides whether it applies.

THAT THIRD DISTINCTION IS ALSO WHAT MAKES THE BENCHMARK HONEST. "Blind
re-derivation of live sources" means proposing a mapping for a feed that
already has one and comparing. If the feed's own prior mapping were in the
grounding, the platform would be reading the answer key — so
`exclude_feed_ids` exists, the eval passes the feed under test, and a gate
measured any other way would be worthless.

UNMAPPED IS THE DECLINE, AND IT COSTS A SENTENCE. CF-V1-E6-03's `MappingLine`
refuses an unmapped field with no reason, so an agent that declines must say
why — the model's own uncertainty lands in the same Reason column the client's
`NO MAP Fields` sheet has always had. "Never guessed" is enforced by a type.

ONE TARGET PER SOURCE COLUMN — A SCOPE BOUNDARY, NOT AN OVERSIGHT. A source
field really can populate two canonical fields: the client's own Fidelis claims
workbook sends `claim_id` to both `claim_header.source_claim_id` and
`claim_line.source_claim_id`, and 12 of its 102 distinct decisions are a second
target for a column already mapped. `core.mapping` expresses that fan-out
perfectly well — it is keyed by target — and this AGENT does not propose it.

The reason is that the fan-out is a modelling decision about the canonical
side, not a reading of the payer's column: knowing that a claim id belongs on
both the header and every line is knowledge about the target schema's grain,
and a suggestion that guessed at grain would be guessing at exactly the thing
this platform asks a human to decide. So the agent proposes the primary target,
a second suggestion for the same column is REFUSED WITH A SENTENCE rather than
silently dropped, and the editor is where the fan-out is completed.

(The Lane-3 eval found this. The first version kept whichever entry the model
returned last — a decision made by iteration order and invisible to everyone.)

R2 · config_proposal. The agent writes one row to `proposals.proposal` and
nothing else.
"""

from __future__ import annotations

from typing import Any

from cinqflow.core.model.vocabulary import RiskClass

#: The audit `agent`, the budget key, and the `proposals.agent` column.
AGENT = "mapping-suggestion"

#: R2 — a config proposal. A human approves, always.
RISK_CLASS = RiskClass.R2

CAPABILITY = "propose_column_mapping"

NODE_GROUND = "ground"
NODE_SUGGEST = "suggest"
NODE_ASSEMBLE = "assemble"

NODES: tuple[str, ...] = (NODE_GROUND, NODE_SUGGEST, NODE_ASSEMBLE)

#: The nodes that must never reach a model. Asserted by a test that walks the
#: implementation's AST, not by this comment.
DETERMINISTIC_NODES: frozenset[str] = frozenset({NODE_GROUND, NODE_ASSEMBLE})

#: What the platform writes into `unmapped_reason` when the model declined and
#: said nothing useful. The exact string, so a screen, a test and the eval all
#: mean the same thing by it.
NO_CONFIDENT_TARGET = "no confident target — this one is for you to decide"

SUGGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["mappings"],
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_column", "confidence"],
                "properties": {
                    # The source column this is about. The platform matches it
                    # back to the contract; a name the contract does not have
                    # is a column the model invented.
                    "source_column": {"type": "string"},
                    # THE PRIMARY ANSWER: the NUMBER of the canonical target
                    # chosen from the grounding's numbered list. A number
                    # survives the PHI scrubber that stands between the prompt
                    # and the model; a name does not always — see
                    # `TargetVocabulary`. The names below are accepted as a
                    # fallback and are useful in the audit trail, but the
                    # platform resolves the ref first.
                    "target_ref": {"type": "integer", "minimum": 1},
                    "target_entity": {"type": "string"},
                    "target_field": {"type": "string"},
                    # The concept, cited. Where this names a real term, the
                    # PLATFORM reads the canonical column from it rather than
                    # from `target_field` — the estate's vocabulary spells the
                    # name, not the model.
                    "glossary_id": {"type": "string"},
                    "transform": {
                        "type": "string",
                        "enum": ["direct", "cast", "split", "concat", "lookup", "conditional"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    # The exemplar it reasoned from, if any. Asked for so the
                    # proposal can cite it — "maps like Fidelis did" is only
                    # useful to a reviewer if they can open the Fidelis one.
                    "like_feed_id": {"type": "string"},
                    # The model's own escape hatch, and a CORRECT answer. An
                    # agent with no way to decline has only one way to respond
                    # to a column it cannot place, and that way is a guess.
                    "unmapped": {"type": "boolean"},
                    "unmapped_reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

#: How many open columns go into one model call.
#:
#: NOT A TUNING KNOB — a failure-isolation boundary. Unlike CF-V1-E5-02, which
#: asks about the handful of columns arithmetic could not settle, this agent
#: asks about EVERY column a payer sends, and the client's Fidelis claims
#: extract has ninety. One call for ninety produced two failures in a row on
#: the real endpoint: first an empty completion (the answer did not fit the
#: token cap), then a request timeout when the cap was raised to fit it.
#:
#: Both were the same mistake — putting the whole feed on one round trip. At
#: twenty-five, each call is small enough to answer and a failed batch costs
#: twenty-five columns rather than the run: the rest still produce suggestions,
#: and the batch that failed becomes UNMAPPED with a reason saying so. A feed
#: that used to yield nothing now yields three quarters of an answer.
BATCH_SIZE = 25

#: Below this, a line becomes UNMAPPED whatever the model claimed about it. A
#: threshold in the platform rather than the prompt, because a model asked to
#: self-censor at a number will report that number.
#:
#: Higher than schema inference's 0.6, deliberately. A mis-typed column is
#: caught by the next load; a mis-mapped one silently lands a payer's copay in
#: the deductible field and reconciles perfectly while doing it.
CONFIDENCE_FLOOR = 0.75
