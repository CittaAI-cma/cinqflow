"""The exact shape a schema-constrained model is asked to produce.

Deliberately separate from `workflow/models.py`: these are the LLM's output
contract (what OpenAI Structured Outputs enforces at generation time via
`response_format`), not the persisted artifact shape. Two differences from the
persisted models matter:

- No numeric range keywords (`ge`/`le` on confidence): OpenAI's structured-output
  schema converter does not support JSON Schema range constraints, so encoding
  them here would make every real request fail schema validation before the
  model ever runs. Range/evidence rules are enforced exactly where they already
  were - the deterministic `_assemble`/`_validate` nodes in each graph - which
  stays the universal safety net regardless of which provider is configured.
- No fields the model must never populate (e.g. `FieldCandidate.rejected_target`,
  `.reason` - those are computed by `_validate` after the fact). Asking the model
  for fields only code should set would just invite it to invent values for them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ClaimKind = Literal["observed_fact", "governed_knowledge", "inference", "recommendation"]


class LlmClaim(BaseModel):
    kind: ClaimKind
    field: str
    value: str
    confidence: float
    evidence: list[str] = Field(default_factory=list)


SignalKind = Literal["risk", "unknown"]


class LlmSignal(BaseModel):
    """A risk or unknown, in the four-slot shape the review screen renders.
    No `severity` - that is assigned by `_assemble` from `kind`, the same
    "the model never populates a field only code should set" rule this
    module's docstring states for `FieldCandidate.rejected_target`/`.reason`."""

    kind: SignalKind
    claim: str
    basis: str
    check: str
    consequence: str


#: The role vocabulary the model may use for a column (PR-6). Wider than the
#: profiler's hint set: `business_attribute` (descriptive, neither key nor
#: quantity nor fixed category) and `derived` (computed from other columns) are
#: things only reading the names and knowledge can tell.
ColumnRole = Literal[
    "identifier",
    "measure",
    "dimension",
    "date",
    "business_attribute",
    "technical",
    "derived",
    "unclassified",
]
COLUMN_ROLES: frozenset[str] = frozenset(
    {
        "identifier",
        "measure",
        "dimension",
        "date",
        "business_attribute",
        "technical",
        "derived",
        "unclassified",
    }
)
Importance = Literal["high", "medium", "low"]
IMPORTANCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})


class LlmColumnRole(BaseModel):
    """One observed column, classified against its profiler hint. `role` and
    `importance` are plain `str` here, not the Literals: an out-of-vocabulary
    value must reach `_assemble` (which records and corrects it) rather than
    fail schema validation and take every other role down with it."""

    name: str
    role: str
    importance: str
    reason: str = ""


class InterpretationResponse(BaseModel):
    """`interpret_file`'s contract - mirrors `prompts/interpret_file_v3.md` exactly."""

    claims: list[LlmClaim] = Field(default_factory=list)
    signals: list[LlmSignal] = Field(default_factory=list)
    column_roles: list[LlmColumnRole] = Field(default_factory=list)


#: The model may only ever claim one of these three - `invalid` is assigned
#: later, by `_validate`, when a proposed target turns out not to exist.
MappingFieldStatus = Literal["candidate", "ambiguous", "unknown"]


class LlmTransformArg(BaseModel):
    key: str
    value: str


class LlmTransform(BaseModel):
    """`args` is a list of pairs, not a dict: OpenAI's Structured Outputs strict
    mode rejects open-ended `additionalProperties` schemas like `dict[str, str]`
    produces - every object schema must declare a fixed `properties`/`required`
    set. `recommend_mapping._validate` converts it back to a dict when
    building the persisted `Transform`."""

    op: str
    args: list[LlmTransformArg] = Field(default_factory=list)


class LlmFieldCandidate(BaseModel):
    source: str
    target: str | None = None
    #: What the column means, independent of whether a legal target exists for
    #: it. Never checked against the canonical model and never becomes a
    #: target itself - it is understanding, not a decision.
    concept: str | None = None
    transform: LlmTransform | None = None
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    status: MappingFieldStatus


class MappingProposalResponse(BaseModel):
    """`recommend_mapping`'s contract - mirrors `prompts/recommend_mapping_v1.md`."""

    fields: list[LlmFieldCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
