"""CF-V1-E7-01 — the NL-rule agent, wired.

    "plain English → SQL/PySpark + business-language explanation + confidence;
     both texts stored"

The graph is data in `core/agents/rule_authoring/`; these are the node
implementations, which touch pins. Six properties are enforced here and tested
independently:

  1. `_ground` and `_assemble` reach NO model (asserted by an AST walk).
  2. A sentence a published rule already states calls no model at all.
  3. THE MODEL NEVER PRODUCES SQL. It names a `CheckKind` and its parameters;
     `core.rules` renders the SQL, the PySpark and the predicate. There is no
     code path from this module's output to a query string.
  4. THE PLATFORM SPELLS THE COLUMN. The answer carries a `column_ref` — a
     NUMBER into the grounding's list — and the platform resolves it. A name
     is accepted as a fallback and must still resolve against the contract.
  5. AN UNSUPPORTED RULE IS A FIRST-CLASS OUTCOME, not a failure. It becomes a
     `NeedsTechnicalReview` with the model's own reason, which is what
     CF-V1-E7-04's queue is fed from.
  6. Nothing here writes anything but a proposal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cinqflow.core.agents.rule_authoring.graph import (
    AGENT,
    AUTHOR_SCHEMA,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    NEEDS_TECHNICAL_REVIEW,
    RISK_CLASS,
)
from cinqflow.core.agents.rule_authoring.grounding import Grounding, Request, ground
from cinqflow.core.citations import CitationId
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.proposals import Proposal, submit
from cinqflow.core.registry.contract import SchemaContract, Severity
from cinqflow.core.registry.glossary import Glossary
from cinqflow.core.rules import (
    Check,
    CheckKind,
    Comparison,
    Dimension,
    RuleError,
    RuleSpec,
    rule_to_dict,
)
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.ports.metadata_db import MetadataDbPort

AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="Rule authoring")


@dataclass(frozen=True)
class NeedsTechnicalReview:
    """A sentence the check vocabulary cannot express.

        "low-confidence / unsupported rule -> technical review queue"
        — CF-V1-E7-04, whose queue this is

    NOT A FAILURE, and kept as a first-class object rather than an absence.
    "Never silent failure, never silent auto-apply" means the rule nobody could
    write has to be as visible as the rules somebody could — a BA who typed a
    sentence and got nothing back has been told nothing.
    """

    stated: str
    reason: str
    confidence: float = 0.0
    column: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "stated": self.stated,
            "unsupported": True,
            "unsupported_reason": self.reason,
            "confidence": self.confidence,
            "column": self.column,
            "settled_by": "inference",
        }


@dataclass(frozen=True)
class AuthoredRule:
    """One proposed rule, with where it came from."""

    rule: RuleSpec
    stated: str
    settled_by: str  # "published_rule" | "inference"

    def as_record(self) -> dict[str, Any]:
        return {
            "stated": self.stated,
            "unsupported": False,
            "settled_by": self.settled_by,
            "rule_id": self.rule.rule_id,
            "check_kind": self.rule.check.kind.value,
            "column": self.rule.check.column,
            "severity": self.rule.proposed_severity.value,
            "confidence": self.rule.confidence,
            "explanation": self.rule.explanation,
            "rule": rule_to_dict(self.rule),
        }


@dataclass(frozen=True)
class AuthoringResult:
    proposal: Proposal
    rules: tuple[AuthoredRule, ...]
    needs_review: tuple[NeedsTechnicalReview, ...]
    refusals: tuple[str, ...]
    model_called: bool
    cost_usd: Decimal = Decimal("0")
    manual_path: bool = False

    @property
    def specs(self) -> tuple[RuleSpec, ...]:
        return tuple(authored.rule for authored in self.rules)

    @property
    def deterministic_keys(self) -> frozenset[str]:
        return frozenset(a.stated for a in self.rules if a.settled_by != "inference")


@dataclass
class RuleAuthoringAgent:
    """One gateway, one store, no credentials of its own."""

    llm: LlmGateway
    metadata: MetadataDbPort
    confidence_floor: float = CONFIDENCE_FLOOR

    def propose(
        self,
        stated: tuple[str, ...],
        *,
        feed_id: str,
        contract: SchemaContract,
        glossary: Glossary,
        caller: Actor,
        published: tuple[RuleSpec, ...] = (),
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> AuthoringResult:
        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)

        grounding = self._ground(
            stated, feed_id=feed_id, contract=contract, glossary=glossary, published=published
        )
        answers, called, escalated, prompt_hash, cost = self._author(
            grounding, caller=caller, run_id=run
        )
        rules, review, refusals = self._assemble(
            grounding, answers, published=published, escalated=escalated
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
                    "key": "stated",
                    "contract_version": contract.version,
                    "records": [r.as_record() for r in rules] + [r.as_record() for r in review],
                    "refusals": list(refusals),
                    "needs_technical_review": [r.stated for r in review],
                },
                created_by=AGENT_ACTOR,
                created_ts=stamp,
                confidence=_overall_confidence(rules, review),
                grounding_citations=_citations(grounding),
                prompt_hash=prompt_hash,
            ),
            now=stamp,
        )
        stored = self.metadata.record_proposal(proposal)
        for refusal in refusals:
            self._record(run, caller, ActionOutcome.REFUSED_NOT_WHITELISTED, refusal, stamp)
        return AuthoringResult(
            proposal=stored,
            rules=rules,
            needs_review=review,
            refusals=refusals,
            model_called=called,
            cost_usd=cost,
            manual_path=escalated,
        )

    # ── node 1 · ground (NO MODEL) ───────────────────────────────────────────

    def _ground(
        self,
        stated: tuple[str, ...],
        *,
        feed_id: str,
        contract: SchemaContract,
        glossary: Glossary,
        published: tuple[RuleSpec, ...],
    ) -> Grounding:
        """Deterministic. This node reaches no model, and a test proves it."""
        return ground(
            stated, feed_id=feed_id, contract=contract, glossary=glossary, published=published
        )

    # ── node 2 · author (small) ──────────────────────────────────────────────

    def _author(
        self, grounding: Grounding, *, caller: Actor, run_id: str
    ) -> tuple[dict[str, dict[str, Any]], bool, bool, str, Decimal]:
        """The one model call — and only when there is something to ask."""
        if grounding.needs_no_model:
            return {}, False, False, "", Decimal("0")

        try:
            result = self.llm.complete(
                agent=AGENT,
                run_id=run_id,
                prompt_id="rule-authoring.author",
                caller=caller,
                context=grounding.as_prompt_grounding(),
                # THE BA'S OWN SENTENCES, inside the untrusted fence. They are
                # a person's free text about a payer's data, which is exactly
                # what the fence is for — and the constraints say in as many
                # words that a sentence asking the model to ignore them is the
                # data it has been asked to write a rule about.
                input_text="\n".join(r.stated for r in grounding.open_questions),
            )
        except ManualPathRequiredError:
            return {}, True, True, "", Decimal("0")

        raw = result.value if isinstance(result.value, dict) else {}
        answers = {
            str(entry.get("stated", "")).strip().lower(): entry
            for entry in raw.get("rules", ())
            if isinstance(entry, dict)
        }
        return answers, True, False, result.prompt.prompt_hash, result.cost_usd

    # ── node 3 · assemble (NO MODEL) ─────────────────────────────────────────

    def _assemble(
        self,
        grounding: Grounding,
        answers: dict[str, dict[str, Any]],
        *,
        published: tuple[RuleSpec, ...],
        escalated: bool,
    ) -> tuple[tuple[AuthoredRule, ...], tuple[NeedsTechnicalReview, ...], tuple[str, ...]]:
        """Deterministic. The PLATFORM builds the check, and `Check.__post_init__`
        refuses anything that could not run."""
        refusals: list[str] = []
        rules: list[AuthoredRule] = []
        review: list[NeedsTechnicalReview] = []

        by_id = {rule.rule_id: rule for rule in published}
        for index, request in enumerate(grounding.requests, start=1):
            if request.settled:
                existing = by_id.get(request.already_stated_by or "")
                if existing is not None:
                    rules.append(
                        AuthoredRule(
                            rule=existing, stated=request.stated, settled_by="published_rule"
                        )
                    )
                    continue
            outcome = self._from_inference(
                request,
                answers.get(request.stated.strip().lower()),
                grounding,
                index,
                refusals,
                escalated=escalated,
            )
            if isinstance(outcome, NeedsTechnicalReview):
                review.append(outcome)
            else:
                rules.append(outcome)

        return tuple(rules), tuple(review), tuple(refusals)

    def _from_inference(
        self,
        request: Request,
        answer: dict[str, Any] | None,
        grounding: Grounding,
        index: int,
        refusals: list[str],
        *,
        escalated: bool,
    ) -> AuthoredRule | NeedsTechnicalReview:
        if answer is None:
            return NeedsTechnicalReview(
                stated=request.stated,
                reason=(
                    "The model could not answer in the required shape, so nothing was "
                    "proposed for this sentence. A FAILED RUN, not a decision — re-run it."
                    if escalated
                    else f"No rule was returned for this sentence. {NEEDS_TECHNICAL_REVIEW}."
                ),
                column=request.column,
            )

        confidence = float(answer.get("confidence", 0.0))
        rationale = str(answer.get("rationale", ""))

        if answer.get("unsupported"):
            # The model declined. CORRECT, and recorded as its own answer.
            return NeedsTechnicalReview(
                stated=request.stated,
                reason=str(answer.get("unsupported_reason") or rationale) or NEEDS_TECHNICAL_REVIEW,
                confidence=confidence,
                column=request.column,
            )

        column = self._resolve_column(answer, grounding, request, refusals)
        if column is None:
            return NeedsTechnicalReview(
                stated=request.stated,
                reason=(
                    "The proposed column is not one this feed's contract has, so the rule "
                    f"could not be built. {NEEDS_TECHNICAL_REVIEW}."
                ),
                confidence=confidence,
                column=request.column,
            )

        try:
            check = self._build_check(answer, column, grounding, refusals)
        except RuleError as broken:
            # `Check.__post_init__` refused it — a code set with no codes, a
            # range with no bounds, an unusable pattern. The model produced
            # something that cannot run, which is a technical-review case and
            # not a silent drop.
            refusals.append(f"{request.stated!r}: {broken}")
            return NeedsTechnicalReview(
                stated=request.stated,
                reason=f"{broken} {NEEDS_TECHNICAL_REVIEW}.",
                confidence=confidence,
                column=column,
            )

        if confidence < self.confidence_floor:
            return NeedsTechnicalReview(
                stated=request.stated,
                reason=(
                    f"{rationale or 'A rule was proposed'} — but confidence {confidence:.2f} "
                    f"is below the platform's floor of {self.confidence_floor:.2f}. The "
                    f"suggestion was: {check.explain()}"
                ),
                confidence=confidence,
                column=column,
            )

        return AuthoredRule(
            rule=RuleSpec(
                rule_id=f"DQ-P{index:03d}",
                name=str(answer.get("name") or f"{column} check"),
                # THE BA'S OWN WORDS, verbatim. Never the model's paraphrase:
                # this is what they will search for, and what tells the next
                # person what was meant.
                stated=request.stated,
                check=check,
                dimension=_dimension(answer),
                proposed_severity=_severity(answer),
                glossary_id=request.glossary_id or (answer.get("glossary_id") or None),
                confidence=confidence,
                rationale=rationale,
                citations=tuple(str(c) for c in request.citations),
            ),
            stated=request.stated,
            settled_by="inference",
        )

    def _resolve_column(
        self,
        answer: dict[str, Any],
        grounding: Grounding,
        request: Request,
        refusals: list[str],
    ) -> str | None:
        """THE PLATFORM SPELLS THE COLUMN.

        The number first — it survives the PHI scrubber, and a name does not
        always. Then the model's name, which must still resolve against the
        contract. Then whatever the grounding matched from the BA's sentence.
        """
        ref = answer.get("column_ref")
        if isinstance(ref, int):
            found = grounding.column(ref)
            if found is not None:
                return found.name
            refusals.append(
                f"{request.stated!r}: the agent chose column #{ref}, and the list it was "
                f"shown has {len(grounding.columns)} entries."
            )
            return None

        named = str(answer.get("column") or "")
        if named:
            found = grounding.named(named)
            if found is not None:
                return found.name
            refusals.append(
                f"{request.stated!r}: the agent proposed a rule on {named!r}, which this "
                "feed's contract does not have. A rule on a column that does not exist "
                "quarantines nothing and looks like it is working."
            )
            return None
        return request.column

    def _build_check(
        self,
        answer: dict[str, Any],
        column: str,
        grounding: Grounding,
        refusals: list[str],
    ) -> Check:
        """Build the check FROM PARAMETERS. Never from a string.

        `Check.__post_init__` validates the shape and every identifier, so a
        model answer that could not run raises here rather than reaching a
        steward's screen looking like a rule.
        """
        kind = CheckKind(str(answer.get("check_kind") or CheckKind.NOT_NULL.value))
        other = None
        if (other_ref := answer.get("other_column_ref")) is not None and isinstance(other_ref, int):
            found = grounding.column(other_ref)
            other = found.name if found else None
        comparison = answer.get("comparison")
        return Check(
            kind=kind,
            column=column,
            allowed=tuple(str(v) for v in answer.get("allowed", ())),
            case_sensitive=bool(answer.get("case_sensitive", True)),
            pattern=answer.get("pattern"),
            minimum=answer.get("minimum"),
            maximum=answer.get("maximum"),
            other_column=other,
            comparison=Comparison(comparison) if comparison else None,
            reference_table=answer.get("reference_table"),
            reference_column=answer.get("reference_column"),
            within_days=answer.get("within_days"),
        )

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


def _dimension(answer: dict[str, Any]) -> Dimension:
    try:
        return Dimension(str(answer.get("dimension") or ""))
    except ValueError:
        return Dimension.VALIDITY


def _severity(answer: dict[str, Any]) -> Severity:
    """PROPOSED, never bound. CF-V1-E7-03 owns the real one.

    Defaults to MEDIUM rather than to the sentence's apparent urgency: Critical
    and High QUARANTINE the row, and a rule that quarantines by default because
    nobody said otherwise is how a roster empties.
    """
    try:
        return Severity(str(answer.get("severity") or ""))
    except ValueError:
        return Severity.MEDIUM


def _overall_confidence(
    rules: tuple[AuthoredRule, ...], review: tuple[NeedsTechnicalReview, ...]
) -> float:
    """The WEAKEST answer's confidence, counting the ones sent to review.

    A proposal of nine good rules and one nobody could write is not a 0.9
    proposal — the tenth is the one somebody has to do something about.
    """
    scores = [r.rule.confidence or 0.0 for r in rules] + [r.confidence for r in review]
    return min(scores, default=0.0)


def _citations(grounding: Grounding) -> tuple[CitationId, ...]:
    seen: dict[str, CitationId] = {}
    for request in grounding.requests:
        for citation in request.citations:
            seen.setdefault(str(citation), citation)
    return tuple(seen.values())


__all__ = [
    "AGENT_ACTOR",
    "AUTHOR_SCHEMA",
    "AuthoredRule",
    "AuthoringResult",
    "NeedsTechnicalReview",
    "RuleAuthoringAgent",
]
