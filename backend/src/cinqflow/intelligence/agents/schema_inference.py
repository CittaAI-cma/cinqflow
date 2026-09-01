"""CF-V1-E5-02 — the schema-inference agent, wired.

    "eval red until >= 90% fields accepted without correction on feeds with
     existing human schemas · ungroundable column -> 'needs your input', never
     silently typed · contract emitted machine-enforceable · corrections
     captured to eval set"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

The graph is data in `core/agents/schema_inference/`; these are the node
implementations, which touch pins. Four properties are enforced here and tested
independently:

  1. `_ground` and `_assemble` reach NO model. A test walks this module's AST
     and asserts neither body reaches the gateway.
  2. A feed whose columns are all settled by computation calls no model AT ALL
     — not a cheap call, not a validation call. Zero.
  3. The platform, not the model, decides what counts as grounded: a proposed
     column whose `source_name` is not in the profile is DISCARDED, and one
     below the confidence floor is routed to "needs your input" whatever the
     model claimed about it.
  4. Nothing here writes anything but a proposal. There is no code path from
     this module to `metadata_db.save`, and the only object it constructs is a
     `Proposal` at R2.

PHI is scrubbed before the prompt by the gateway (stage 2 of six, order checked
at runtime) — this module never assembles a prompt itself, which is what makes
that guarantee hold for this agent for free.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cinqflow.core.agents.document_evidence import DocumentConflict, column_count_conflicts
from cinqflow.core.agents.schema_inference.graph import (
    AGENT,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    INFER_SCHEMA,
    NEEDS_YOUR_INPUT,
    RISK_CLASS,
)
from cinqflow.core.agents.schema_inference.grounding import (
    GroundedColumn,
    Grounding,
    ground,
    merge,
)
from cinqflow.core.citations import CitationId
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.profiling import FileProfile
from cinqflow.core.proposals import Proposal, submit
from cinqflow.core.registry.glossary import Glossary
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.intelligence.retrieval import (
    RetrievalResult,
    RetrievalService,
    as_fenced_grounding,
    ground_for_feed,
)
from cinqflow.ports.metadata_db import MetadataDbPort

#: The actor a proposal is created by. AI, always — `Proposal.__post_init__`
#: refuses any other actor type, so a human path cannot borrow this class.
AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="Schema inference")


@dataclass(frozen=True)
class ProposedColumn:
    """One column of the proposed contract, with where each part came from.

    `settled_by` is not decoration: the eval reports the deterministic and
    inferred shares apart, and a screen shows a BA which lines a model touched.
    """

    source_name: str
    position: int
    name: str | None
    type: str | None
    nullable: bool
    is_phi: bool
    glossary_id: str | None
    date_format: str | None
    precision: int | None
    scale: int | None
    confidence: float
    settled_by: str  # "computation" | "inference"
    needs_input: bool
    rationale: str
    citations: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "position": self.position,
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "is_phi": self.is_phi,
            "glossary_id": self.glossary_id,
            "date_format": self.date_format,
            "precision": self.precision,
            "scale": self.scale,
            "confidence": self.confidence,
            "settled_by": self.settled_by,
            "needs_input": self.needs_input,
            "rationale": self.rationale,
            "citations": list(self.citations),
        }


@dataclass(frozen=True)
class InferenceResult:
    """What the agent produced, and what it refused to produce."""

    proposal: Proposal
    columns: tuple[ProposedColumn, ...]
    refusals: tuple[str, ...]
    model_called: bool
    cost_usd: Decimal = Decimal("0")
    #: CF-V1-E16-06's exception path. Empty where no companion guide was
    #: uploaded, and empty where the guide agrees with the file — a conflict
    #: is never asserted from the ABSENCE of a claim.
    conflicts: tuple[DocumentConflict, ...] = ()

    @property
    def needs_input(self) -> tuple[ProposedColumn, ...]:
        return tuple(c for c in self.columns if c.needs_input)

    @property
    def deterministic_keys(self) -> frozenset[str]:
        return frozenset(c.source_name for c in self.columns if c.settled_by == "computation")


@dataclass
class SchemaInferenceAgent:
    """One gateway, one store, no credentials of its own.

    Takes the metadata store to WRITE ITS PROPOSAL and to read the glossary —
    and to nothing else. `metadata_db.save` is never called from here; a
    proposal is the only thing this agent can leave behind.
    """

    llm: LlmGateway
    metadata: MetadataDbPort
    confidence_floor: float = CONFIDENCE_FLOOR
    #: CF-V1-E5-02 · CF-V1-E16-05 · CF-V1-E16-06. "Ground every proposal
    #: through the platform retrieval service — glossary terms and precedent
    #: contracts arrive as cited chunks, and the citations are shown beside
    #: the proposal." Optional because the DETERMINISTIC half of this agent is
    #: the half that carries most of its evidence, and a deployment with no
    #: vector pin still proposes honestly — it simply cites fewer sources.
    retrieval: RetrievalService | None = None

    def propose(
        self,
        profile: FileProfile,
        *,
        feed_id: str,
        glossary: Glossary,
        caller: Actor,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> InferenceResult:
        """Profile in, one `proposals.proposal` row out.

        `caller` is the human who asked — carried into the audit row so a model
        call always names the person it was made on behalf of, never only the
        agent.
        """
        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)

        grounding = self._ground(profile, feed_id=feed_id, glossary=glossary)
        # CF-V1-E16-06. The payer's own companion guide, if one has been
        # uploaded and PUBLISHED for this feed, retrieved by the column names
        # the deterministic half could not settle — which is exactly the set
        # a specification is useful for. A guide nobody uploaded costs one
        # lexical lookup and returns nothing.
        retrieved = ground_for_feed(
            self.retrieval,
            text=" ".join(column.source_name for column in grounding.open_questions)
            or profile.source_key,
            feed_id=feed_id,
            run_id=run,
        )
        answers, called, prompt_hash, cost = self._infer(
            grounding, retrieved=retrieved, caller=caller, run_id=run
        )
        columns, refusals = self._assemble(grounding, answers, glossary)
        # CF-V1-E16-06. Checked AFTER the model has answered and BEFORE the
        # proposal is written, because the proposal is the artifact a reviewer
        # reads and `record_proposal` deliberately never rewrites a payload —
        # a conflict attached afterwards would be a conflict that never
        # persisted. The model is not asked to adjudicate: it is the party
        # least able to check which of two numbers the file actually has.
        conflicts = column_count_conflicts(
            chunks=tuple((chunk.citation, chunk.text) for chunk in retrieved.grounding.chunks),
            sample_columns=len(profile.columns),
            sample_citation=profile.citation,
        )

        proposal = submit(
            Proposal(
                proposal_id=str(uuid.uuid4()),
                agent=AGENT,
                capability=CAPABILITY,
                risk_class=RISK_CLASS,
                run_id=run,
                feed_id=feed_id,
                payload={
                    "key": "source_name",
                    "profile_id": profile.profile_id,
                    "source_key": profile.source_key,
                    "records": [column.as_record() for column in columns],
                    "refusals": list(refusals),
                    "needs_input": [c.source_name for c in columns if c.needs_input],
                    # Both sources, both cited, and which one the platform
                    # proceeded on. "Sample evidence wins by DEFAULT" — the
                    # guide is recorded, never discarded, because a truncated
                    # delivery and a wrong guide look identical from here.
                    "document_conflicts": [conflict.as_record() for conflict in conflicts],
                },
                created_by=AGENT_ACTOR,
                created_ts=stamp,
                confidence=_overall_confidence(columns),
                # Deterministic citations FIRST, retrieved ones after: a
                # reviewer reading top-down sees what the platform computed
                # before what a document claimed, which is the order
                # E16-06's own conflict rule puts them in ("sample evidence
                # wins by default").
                grounding_citations=_citations(grounding) + retrieved.grounding.citations,
                prompt_hash=prompt_hash,
            ),
            now=stamp,
        )
        stored = self.metadata.record_proposal(proposal)
        for refusal in refusals:
            self._record(run, caller, ActionOutcome.REFUSED_NOT_WHITELISTED, refusal, stamp)
        return InferenceResult(
            proposal=stored,
            columns=columns,
            refusals=refusals,
            model_called=called,
            cost_usd=cost,
            conflicts=conflicts,
        )

    # ── node 1 · ground (NO MODEL) ───────────────────────────────────────────

    def _ground(self, profile: FileProfile, *, feed_id: str, glossary: Glossary) -> Grounding:
        """Deterministic. This node reaches no model, and a test proves it."""
        return ground(profile, feed_id=feed_id, glossary=glossary)

    # ── node 2 · infer (small) ───────────────────────────────────────────────

    def _infer(
        self,
        grounding: Grounding,
        *,
        retrieved: RetrievalResult,
        caller: Actor,
        run_id: str,
    ) -> tuple[dict[str, dict[str, Any]], bool, str, Decimal]:
        """The one model call — and only when there is something to ask.

        A feed whose columns the profiler and the glossary both settled costs
        ZERO tokens. Not a cheap call: none. That is the deterministic-first
        rule showing up on an invoice rather than in a docstring.
        """
        if grounding.needs_no_model:
            return {}, False, "", Decimal("0")

        try:
            result = self.llm.complete(
                agent=AGENT,
                run_id=run_id,
                prompt_id="schema-inference.infer",
                caller=caller,
                context=grounding.as_prompt_grounding() + as_fenced_grounding(retrieved),
                input_text="\n".join(
                    f"{c.source_name}: " + " | ".join(c.evidence) for c in grounding.open_questions
                ),
            )
        except ManualPathRequiredError:
            # The feature degrades to its manual path: every open column
            # becomes "needs your input", which is precisely the screen a BA
            # would have used if this agent did not exist.
            return {}, True, "", Decimal("0")

        raw = result.value if isinstance(result.value, dict) else {}
        answers = {
            str(entry.get("source_name", "")): entry
            for entry in raw.get("columns", ())
            if isinstance(entry, dict)
        }
        return answers, True, result.prompt.prompt_hash, result.cost_usd

    # ── node 3 · assemble (NO MODEL) ─────────────────────────────────────────

    def _assemble(
        self, grounding: Grounding, answers: dict[str, dict[str, Any]], glossary: Glossary
    ) -> tuple[tuple[ProposedColumn, ...], tuple[str, ...]]:
        """Deterministic. The PLATFORM decides what counted as grounded.

        Three checks, in this order and for these reasons:

          1. A `source_name` the profile does not contain is DISCARDED — a
             column the model invented cannot be mapped, tested or approved,
             and letting it through would put a phantom field in a contract.
          2. `merge` refuses anything that contradicts the arithmetic, and
             refuses a PHI downgrade outright.
          3. Confidence below the floor becomes "needs your input" whatever the
             model said, because a threshold inside the prompt is a threshold
             the model reports rather than one the platform enforces.
        """
        refusals: list[str] = []
        columns: list[ProposedColumn] = []

        for invented in sorted(set(answers) - {c.source_name for c in grounding.columns}):
            refusals.append(
                f"{invented!r} is not a column in the profiled file. Discarded — a contract "
                "field with no source column cannot be mapped, tested or approved."
            )

        for grounded in grounding.columns:
            if grounded.settled:
                columns.append(_from_computation(grounded))
                continue
            columns.append(
                self._from_inference(
                    grounded, answers.get(grounded.source_name), refusals, glossary
                )
            )

        return tuple(columns), tuple(refusals)

    def _from_inference(
        self,
        grounded: GroundedColumn,
        answer: dict[str, Any] | None,
        refusals: list[str],
        glossary: Glossary,
    ) -> ProposedColumn:
        if answer is None:
            return _needs_input(
                grounded,
                confidence=0.0,
                rationale=(
                    f"The evidence does not determine this column's {', '.join(grounded.missing)}, "
                    "and no proposal was returned for it."
                ),
            )

        merged, merge_refusals = merge(grounded, answer, glossary=glossary)
        refusals.extend(merge_refusals)
        confidence = float(answer.get("confidence", 0.0))
        rationale = str(answer.get("rationale", ""))

        if answer.get("needs_input"):
            # The model declined. That is a CORRECT answer and is recorded as
            # the model's own, not as a platform override.
            return _needs_input(merged, confidence=confidence, rationale=rationale)
        if confidence < self.confidence_floor:
            return _needs_input(
                merged,
                confidence=confidence,
                rationale=(
                    f"{rationale} (confidence {confidence:.2f} is below the platform's "
                    f"floor of {self.confidence_floor:.2f}, so this is for you to decide)"
                ).strip(),
            )
        if merged.type is None or merged.name is None:
            return _needs_input(
                merged,
                confidence=confidence,
                rationale=(f"{rationale} (no {', '.join(merged.missing)} was proposed)").strip(),
            )
        return _proposed(merged, confidence=confidence, rationale=rationale, settled_by="inference")

    # ── audit ────────────────────────────────────────────────────────────────

    def _record(
        self, run_id: str, caller: Actor, outcome: ActionOutcome, detail: str, now: datetime
    ) -> None:
        self.metadata.append_agent_action(
            AgentAction(
                run_id=run_id,
                agent=AGENT,
                action=CAPABILITY,
                outcome=outcome,
                actor=caller,
                occurred_ts=now,
                risk_class=RISK_CLASS.name,
                detail=detail,
            )
        )


# ── projections ──────────────────────────────────────────────────────────────
def _from_computation(grounded: GroundedColumn) -> ProposedColumn:
    return _proposed(
        grounded,
        confidence=1.0,
        rationale=(
            "Every value in the sample fits this type, and the client's glossary names this column."
        ),
        settled_by="computation",
    )


def _proposed(
    grounded: GroundedColumn, *, confidence: float, rationale: str, settled_by: str
) -> ProposedColumn:
    return ProposedColumn(
        source_name=grounded.source_name,
        position=grounded.position,
        name=grounded.name,
        type=grounded.type.value if grounded.type else None,
        nullable=grounded.nullable,
        is_phi=grounded.is_phi,
        glossary_id=grounded.glossary_id,
        date_format=grounded.date_format,
        precision=grounded.precision,
        scale=grounded.scale,
        confidence=confidence,
        settled_by=settled_by,
        needs_input=False,
        rationale=rationale,
        citations=tuple(str(c) for c in grounded.citations),
    )


def _needs_input(grounded: GroundedColumn, *, confidence: float, rationale: str) -> ProposedColumn:
    """A column the platform will not type.

        "ungroundable column -> 'needs your input', never silently typed"

    Note what is preserved: the type or name that WAS determined still travels,
    so a BA filling the gap is answering one question rather than re-doing the
    column. Blanking the whole row would throw away arithmetic nobody disputes.
    """
    return ProposedColumn(
        source_name=grounded.source_name,
        position=grounded.position,
        name=grounded.name,
        type=grounded.type.value if grounded.type else None,
        nullable=grounded.nullable,
        is_phi=grounded.is_phi,
        glossary_id=grounded.glossary_id,
        date_format=grounded.date_format,
        precision=grounded.precision,
        scale=grounded.scale,
        confidence=confidence,
        settled_by="inference",
        needs_input=True,
        rationale=rationale or NEEDS_YOUR_INPUT,
        citations=tuple(str(c) for c in grounded.citations),
    )


def _overall_confidence(columns: tuple[ProposedColumn, ...]) -> float:
    """The WEAKEST column's confidence, not the mean.

    A contract is approved as a whole, and averaging lets forty easy columns
    hide one the agent barely guessed at. The number a reviewer needs is how
    confident the agent is about the part it is least sure of.
    """
    return min((c.confidence for c in columns), default=0.0)


def _citations(grounding: Grounding) -> tuple[CitationId, ...]:
    seen: dict[str, CitationId] = {}
    for column in grounding.columns:
        for citation in column.citations:
            seen.setdefault(str(citation), citation)
    return tuple(seen.values())


#: Re-exported so callers do not need the schema module to validate a payload.
__all__ = [
    "AGENT_ACTOR",
    "INFER_SCHEMA",
    "InferenceResult",
    "ProposedColumn",
    "SchemaInferenceAgent",
]
