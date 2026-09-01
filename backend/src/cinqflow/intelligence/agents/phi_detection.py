"""CF-V1-E5-03 — the PHI-detection agent, wired.

    "eval red until 100% recall on glossary-flagged PHI · downgrade-by-AI
     refused · free-text treated PHI until a steward decides"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

The graph is data in `core/agents/phi_detection/`; these are the node
implementations, which touch pins. Four properties are enforced here and
tested independently:

  1. `_classify` and `_confirm` reach NO model. A test walks this module's AST.
  2. The scrubber is asked about values HERE — never in `core/`, which reaches
     no pin — and what crosses back is `ScrubEvidence`: entity names and two
     counts. The values do not leave this method.
  3. A file whose columns the glossary and the shapes account for calls no
     model at all.
  4. The recall gate is checked BEFORE the proposal is written. A
     classification that missed a glossary-flagged column is not recorded as a
     proposal a steward might approve; it raises, because the correct response
     to "the detector has a hole" is a broken build, not a queue entry.

Nothing here writes anything but a proposal. There is no path from this module
to `metadata_db.save`.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cinqflow.core.agents.phi_detection.graph import (
    AGENT,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    NAME_SCHEMA,
    RISK_CLASS,
)
from cinqflow.core.agents.phi_detection.grounding import Grounding, ground
from cinqflow.core.citations import CitationId
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.phi import (
    Classification,
    ColumnClassification,
    PhiClassificationError,
    ScrubEvidence,
    classify,
    masking_policy,
    merge_inference,
)
from cinqflow.core.profiling import ColumnProfile, FileProfile
from cinqflow.core.proposals import Proposal, submit
from cinqflow.core.registry.glossary import Glossary
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.phi_scrub import PhiScrubPort

AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="PHI detection")

#: How many of a column's recorded values the scrubber is shown. The profile
#: retains five examples and ten frequent values; scanning them all is cheap
#: and local, and there is nothing more to scan without re-reading the file.
SCRUB_SAMPLE = 15


class RecallGateFailedError(PhiClassificationError):
    """A classification that did not protect a column the glossary flags.

        "100% recall on glossary-flagged PHI (missing PHI is the failure that
         matters)"

    Raised rather than recorded. A proposal whose recall is below 1.0 is not a
    proposal with a caveat — it is a detector that has stopped working, and
    putting it in a review queue invites somebody to approve it on a Friday.
    """


@dataclass(frozen=True)
class DetectionResult:
    """What the agent classified, and what it refused."""

    proposal: Proposal
    classification: Classification
    refusals: tuple[str, ...]
    model_called: bool
    cost_usd: Decimal = Decimal("0")

    @property
    def phi_columns(self) -> tuple[ColumnClassification, ...]:
        return self.classification.phi_columns

    @property
    def needs_steward_review(self) -> tuple[ColumnClassification, ...]:
        return self.classification.needs_steward_review

    @property
    def deterministic_keys(self) -> frozenset[str]:
        return frozenset(
            c.source_name for c in self.classification.columns if c.basis.is_deterministic
        )


@dataclass
class PhiDetectionAgent:
    """One gateway, one scrubber, one store. No credentials of its own."""

    llm: LlmGateway
    scrub: PhiScrubPort
    metadata: MetadataDbPort
    confidence_floor: float = CONFIDENCE_FLOOR

    def propose(
        self,
        profile: FileProfile,
        *,
        feed_id: str,
        glossary: Glossary,
        caller: Actor,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> DetectionResult:
        """Profile in, one `proposals.proposal` row out."""
        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)

        classification = self._classify(profile, feed_id=feed_id, glossary=glossary)
        grounding = ground(classification)
        answers, called, prompt_hash, cost = self._name(grounding, caller=caller, run_id=run)
        final, refusals = self._confirm(classification, answers, glossary)

        policy = masking_policy(final)
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
                    "records": [_as_record(c) for c in final.columns],
                    "refusals": list(refusals),
                    "needs_steward_review": [c.source_name for c in final.needs_steward_review],
                    # The masking policy travels WITH the proposal rather than
                    # being recomputed downstream: "flags flow to masking" is a
                    # property of one document a steward approved, not of two
                    # code paths agreeing.
                    "masked_columns": list(policy.masked_columns),
                    "over_flagged": list(final.over_flagged(glossary)),
                },
                created_by=AGENT_ACTOR,
                created_ts=stamp,
                confidence=_overall_confidence(final),
                grounding_citations=_citations(final),
                prompt_hash=prompt_hash,
            ),
            now=stamp,
        )
        stored = self.metadata.record_proposal(proposal)
        for refusal in refusals:
            self._record(run, caller, ActionOutcome.REFUSED_NOT_WHITELISTED, refusal, stamp)
        return DetectionResult(
            proposal=stored,
            classification=final,
            refusals=refusals,
            model_called=called,
            cost_usd=cost,
        )

    # ── node 1 · classify (NO MODEL) ─────────────────────────────────────────

    def _classify(
        self, profile: FileProfile, *, feed_id: str, glossary: Glossary
    ) -> Classification:
        """The glossary, the arithmetic, and the scrubber. No model.

        The scrubber IS a pin, and calling it here rather than in `core/` is
        what keeps `core.phi.classify` a pure function of a dictionary — which
        is why the whole precedence table can be unit-tested with no Presidio
        installed.
        """
        evidence = {column.name: self._scrub_column(column) for column in profile.columns}
        return classify(profile, feed_id=feed_id, glossary=glossary, scrub=evidence)

    def _scrub_column(self, column: ColumnProfile) -> ScrubEvidence:
        """Run the scrubber over what the profile retained of one column.

        THE VALUES DO NOT LEAVE THIS METHOD. What crosses back is the set of
        entity names and two counts — `ScrubEvidence` has nowhere to put a
        value even if a future edit wanted to.
        """
        sample = _sample_values(column)
        if not sample:
            return ScrubEvidence()
        entities: set[str] = set()
        hits = 0
        for value in sample:
            findings = self.scrub.detect(value)
            if findings:
                hits += 1
                entities.update(f.entity_type for f in findings)
        return ScrubEvidence(
            entities=tuple(sorted(entities)),
            values_scanned=len(sample),
            values_with_entities=hits,
        )

    # ── node 2 · name (small) ────────────────────────────────────────────────

    def _name(
        self, grounding: Grounding, *, caller: Actor, run_id: str
    ) -> tuple[dict[str, dict[str, Any]], bool, str, Decimal]:
        """The one model call, and only when there is something to ask."""
        if grounding.needs_no_model:
            return {}, False, "", Decimal("0")

        try:
            result = self.llm.complete(
                agent=AGENT,
                run_id=run_id,
                prompt_id="phi-detection.name",
                caller=caller,
                context=grounding.as_prompt_grounding(),
                input_text=grounding.as_input(),
            )
        except ManualPathRequiredError:
            # Degrades to the manual path: every open column stays protected
            # and a steward names it. The flags are unaffected — which is the
            # point of computing them before the model is considered.
            return {}, True, "", Decimal("0")

        raw = result.value if isinstance(result.value, dict) else {}
        answers = {
            str(entry.get("source_name", "")): entry
            for entry in raw.get("columns", ())
            if isinstance(entry, dict)
        }
        return answers, True, result.prompt.prompt_hash, result.cost_usd

    # ── node 3 · confirm (NO MODEL) ──────────────────────────────────────────

    def _confirm(
        self,
        classification: Classification,
        answers: dict[str, dict[str, Any]],
        glossary: Glossary,
    ) -> tuple[Classification, tuple[str, ...]]:
        """Fold the answers in, then CHECK THE GATE before anything is stored."""
        refusals: list[str] = []
        known = {c.source_name for c in classification.columns}
        for invented in sorted(set(answers) - known):
            refusals.append(
                f"{invented!r} is not a column in the profiled file. Discarded — the "
                "platform decides what counts as a column, not the model."
            )

        merged: list[ColumnClassification] = []
        for column in classification.columns:
            answer = answers.get(column.source_name)
            if answer is None:
                merged.append(column)
                continue
            folded, column_refusals = merge_inference(
                column, answer, confidence_floor=self.confidence_floor
            )
            refusals.extend(column_refusals)
            merged.append(folded)

        final = Classification(
            feed_id=classification.feed_id,
            profile_id=classification.profile_id,
            columns=tuple(merged),
        )

        # THE GATE, checked here rather than in a test. `merge_inference`
        # already refuses every downgrade, so this can only fire if a future
        # edit introduces a path that clears a flag — which is exactly the
        # edit this line exists to stop.
        if missed := final.missed_phi(glossary):
            raise RecallGateFailedError(
                "the classification did not protect glossary-flagged columns: "
                + ", ".join(missed)
                + ". Missing PHI is the failure that matters, so nothing is proposed."
            )
        return final, tuple(refusals)

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


def _sample_values(column: ColumnProfile) -> tuple[str, ...]:
    """What the profile retained, deduplicated and bounded.

    Returns nothing for a redacted profile: a `without_values()` profile has
    no values to scan, and scanning the empty tuple silently would make a
    redacted classification look scrubbed when it was not.
    """
    if column.values_redacted:
        return ()
    seen: dict[str, None] = {}
    for value in (*column.examples, *(v for v, _ in column.top_values)):
        seen.setdefault(value, None)
    return tuple(seen)[:SCRUB_SAMPLE]


def _as_record(column: ColumnClassification) -> dict[str, Any]:
    return {
        "source_name": column.source_name,
        "position": column.position,
        "is_phi": column.is_phi,
        "phi_kind": column.phi_kind.value if column.phi_kind else None,
        "code_set": column.code_set.value if column.code_set else None,
        "basis": column.basis.value,
        "confidence": column.confidence,
        "needs_steward_review": column.needs_steward_review,
        "glossary_id": column.glossary_id,
        "rationale": column.rationale,
        "citations": [str(c) for c in column.citations],
    }


def _overall_confidence(classification: Classification) -> float:
    """The weakest column's, not the mean — as in CF-V1-E5-02.

    A classification is approved as a whole, and averaging lets forty
    glossary-certain columns hide one the platform is protecting because it
    has no idea what it is.
    """
    return min((c.confidence for c in classification.columns), default=0.0)


def _citations(classification: Classification) -> tuple[CitationId, ...]:
    seen: dict[str, CitationId] = {}
    for column in classification.columns:
        for citation in column.citations:
            seen.setdefault(str(citation), citation)
    return tuple(seen.values())


def deterministic_node_bodies() -> dict[str, str]:
    """Source of the nodes that must reach no model. For the AST test.

    Exposed as a function rather than left to the test to find, so that
    renaming a node breaks the test loudly instead of silently narrowing what
    it checks.
    """
    return {
        name: ast.dump(ast.parse(inspect.getsource(getattr(PhiDetectionAgent, name)).strip()))
        for name in ("_classify", "_confirm", "_scrub_column")
    }


__all__ = [
    "AGENT_ACTOR",
    "NAME_SCHEMA",
    "DetectionResult",
    "PhiDetectionAgent",
    "RecallGateFailedError",
]
