"""CF-V1-E6-02 — the mapping-suggestion agent, wired.

    "AI source→target mapping with confidence + exemplars from the golden
     workbooks; UNMAPPED flagged, never guessed"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

The graph is data in `core/agents/mapping_suggestion/`; these are the node
implementations, which touch pins. Five properties are enforced here and tested
independently:

  1. `_ground` and `_assemble` reach NO model. A test walks this module's AST
     and asserts neither body reaches the gateway.
  2. A feed whose columns the glossary already names calls no model AT ALL.
  3. The PLATFORM decides what counts as a target. A proposed
     `target_entity.target_field` the canonical model does not have is
     DISCARDED, and one below the confidence floor becomes UNMAPPED whatever
     the model claimed.
  4. THE MODEL PICKS THE CONCEPT; THE PLATFORM SPELLS THE NAME. Where an answer
     cites a `glossary_id`, the canonical entity and field are read from that
     term, never from the model's free text.
  5. THE PROPOSAL IS ALWAYS STORABLE. Every line is built through
     `core.mapping.MappingLine`, so a declined column MUST carry a reason — the
     type refuses a blank one — and `core.mapping.validate` runs before the
     proposal is written, turning a PHI-laundering suggestion into a refusal
     rather than into a line somebody could approve.

Nothing here writes anything but a proposal. There is no code path from this
module to `metadata_db.save`, and the only object it constructs is a `Proposal`
at R2.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cinqflow.core.agents.mapping_suggestion.graph import (
    AGENT,
    BATCH_SIZE,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    NO_CONFIDENT_TARGET,
    RISK_CLASS,
    SUGGEST_SCHEMA,
)
from cinqflow.core.agents.mapping_suggestion.grounding import (
    GroundedColumn,
    Grounding,
    TargetVocabulary,
    ground,
)
from cinqflow.core.citations import CitationId
from cinqflow.core.mapping import (
    FeedMapping,
    MappingFinding,
    MappingLine,
    Transform,
    TransformKind,
    blocking,
    validate,
)
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.proposals import Proposal, submit
from cinqflow.core.registry.canonical import CanonicalModel
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.registry.glossary import Glossary
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.intelligence.retrieval import (
    RetrievalResult,
    RetrievalService,
    as_fenced_grounding,
    ground_for_feed,
)
from cinqflow.ports.metadata_db import MetadataDbPort

#: The actor a proposal is created by. AI, always.
AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="Mapping suggestion")


@dataclass(frozen=True)
class SuggestedLine:
    """One proposed mapping line, with where the idea came from.

    `settled_by` is not decoration. The eval reports the deterministic and
    inferred shares apart, and a screen shows a steward which lines a model
    touched — a mapping that is 90% glossary lookups is not evidence that a
    model is good at mapping.
    """

    line: MappingLine
    source_column: str
    confidence: float
    settled_by: str  # "glossary" | "published_mapping" | "inference"
    rationale: str
    like_feed_id: str | None = None

    @property
    def is_unmapped(self) -> bool:
        return not self.line.is_mapped

    def as_record(self) -> dict[str, Any]:
        from cinqflow.core.mapping import line_to_dict

        return {
            "source_column": self.source_column,
            "confidence": self.confidence,
            "settled_by": self.settled_by,
            "rationale": self.rationale,
            "like_feed_id": self.like_feed_id,
            "target_entity": self.line.target_entity,
            "target_field": self.line.target_field,
            "unmapped": self.is_unmapped,
            "unmapped_reason": self.line.unmapped_reason,
            "line": line_to_dict(self.line),
        }


@dataclass(frozen=True)
class SuggestionResult:
    """What the agent produced, and what it refused to produce."""

    proposal: Proposal
    lines: tuple[SuggestedLine, ...]
    refusals: tuple[str, ...]
    findings: tuple[MappingFinding, ...]
    model_called: bool
    cost_usd: Decimal = Decimal("0")
    #: The gateway escalated to the manual path — the model was called and
    #: could not produce a schema-valid answer after its repair.
    #:
    #: A FIRST-CLASS FIELD, because without it a degraded run is
    #: indistinguishable from a careful one: every column comes back UNMAPPED
    #: with a reason, `model_called` is True, and the only difference is that
    #: the answers are missing rather than declined. The Lane-3 gate reported
    #: "0 of 90, all declined and explained, cost $0" for a run that had spent
    #: real money on two calls — a sentence that reads like an honest agent
    #: being careful and was in fact a broken one.
    manual_path: bool = False

    @property
    def unmapped(self) -> tuple[SuggestedLine, ...]:
        return tuple(line for line in self.lines if line.is_unmapped)

    @property
    def mapping(self) -> FeedMapping:
        """The proposal as a mapping, for previewing and for validation."""
        feed_id = self.proposal.feed_id or ""
        return FeedMapping(feed_id=feed_id, lines=tuple(s.line for s in self.lines))

    @property
    def deterministic_keys(self) -> frozenset[str]:
        return frozenset(s.source_column for s in self.lines if s.settled_by != "inference")


@dataclass
class MappingSuggestionAgent:
    """One gateway, one store, no credentials of its own."""

    llm: LlmGateway
    metadata: MetadataDbPort
    confidence_floor: float = CONFIDENCE_FLOOR
    #: CF-V1-E6-02 · CF-V1-E16-05. "Retrieve precedents through the platform
    #: retrieval service (scope-filtered, hybrid, cited) — never from a
    #: private store; the reviewer opens the same citations the model reasoned
    #: from." The glossary and this feed's own published mappings stay a
    #: DIRECT read, and that is not a contradiction: they are not a private
    #: store, they are the governed objects themselves, read at their source
    #: with a version. Retrieval adds what those cannot reach — the payer's
    #: companion guide, a runbook, a closed incident's narrative.
    retrieval: RetrievalService | None = None

    def propose(
        self,
        contract: SchemaContract,
        *,
        feed_id: str,
        glossary: Glossary,
        model: CanonicalModel,
        caller: Actor,
        published_mappings: tuple[FeedMapping, ...] = (),
        approvers: dict[str, str] | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> SuggestionResult:
        """A contract in, one `proposals.proposal` row out.

        `published_mappings` are APPROVED mappings only, and the caller
        filters. Grounding a suggestion in somebody's unreviewed draft would
        launder an unapproved decision into a second feed, where it would
        arrive wearing the authority of precedent.
        """
        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)

        grounding = self._ground(
            contract,
            feed_id=feed_id,
            glossary=glossary,
            model=model,
            published_mappings=published_mappings,
            approvers=approvers or {},
        )
        # CF-V1-E16-06. Retrieved by the column names the deterministic half
        # could NOT settle — a companion guide earns its tokens on
        # `SUBSCR_REL_CD`, never on `member_id`.
        retrieved = ground_for_feed(
            self.retrieval,
            text=" ".join(column.source_column for column in grounding.open_questions) or feed_id,
            feed_id=feed_id,
            run_id=run,
        )
        answers, extras, called, unreached, prompt_hash, cost = self._suggest(
            grounding, retrieved=retrieved, caller=caller, run_id=run
        )
        lines, refusals, findings = self._assemble(
            grounding,
            answers,
            extras,
            unreached,
            model=model,
            glossary=glossary,
            contract=contract,
            feed_id=feed_id,
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
                    "key": "source_column",
                    "contract_version": contract.version,
                    "records": [line.as_record() for line in lines],
                    "refusals": list(refusals),
                    "unmapped": [line.source_column for line in lines if line.is_unmapped],
                },
                created_by=AGENT_ACTOR,
                created_ts=stamp,
                confidence=_overall_confidence(lines),
                grounding_citations=_citations(grounding) + retrieved.grounding.citations,
                prompt_hash=prompt_hash,
            ),
            now=stamp,
        )
        stored = self.metadata.record_proposal(proposal)
        for refusal in refusals:
            self._record(run, caller, ActionOutcome.REFUSED_NOT_WHITELISTED, refusal, stamp)
        return SuggestionResult(
            proposal=stored,
            lines=lines,
            refusals=refusals,
            findings=findings,
            model_called=called,
            cost_usd=cost,
            manual_path=bool(unreached),
        )

    # ── node 1 · ground (NO MODEL) ───────────────────────────────────────────

    def _ground(
        self,
        contract: SchemaContract,
        *,
        feed_id: str,
        glossary: Glossary,
        model: CanonicalModel,
        published_mappings: tuple[FeedMapping, ...],
        approvers: dict[str, str],
    ) -> Grounding:
        """Deterministic. This node reaches no model, and a test proves it."""
        return ground(
            contract,
            feed_id=feed_id,
            glossary=glossary,
            model=model,
            published_mappings=published_mappings,
            approvers=approvers,
        )

    # ── node 2 · suggest (small) ─────────────────────────────────────────────

    def _suggest(
        self,
        grounding: Grounding,
        *,
        retrieved: RetrievalResult,
        caller: Actor,
        run_id: str,
    ) -> tuple[
        dict[str, dict[str, Any]],
        tuple[dict[str, Any], ...],
        bool,
        frozenset[str],
        str,
        Decimal,
    ]:
        """The model calls — one per BATCH, and only when there is something to ask.

        A feed whose columns the client's own glossary already names costs ZERO
        tokens. Not a cheap call: none.

        BATCHED, AND THE BATCHING IS FAILURE ISOLATION rather than tuning. One
        call for the client's ninety-column claims extract failed twice on the
        real endpoint — an empty completion, then a timeout — and in both cases
        the whole run produced nothing. At `BATCH_SIZE` a failed batch costs
        its own columns and no more: the rest still yield suggestions, and the
        failed ones become UNMAPPED saying why.

        The failed batches' COLUMN NAMES are returned, not a bare flag, so a
        column that nothing was proposed for can say which silence it was: the
        model declining, or the model never being reached. Those look identical
        on a review screen and mean opposite things.
        """
        batches = grounding.batches(BATCH_SIZE)
        if not batches:
            return {}, (), False, frozenset(), "", Decimal("0")

        answers: dict[str, dict[str, Any]] = {}
        extras: list[dict[str, Any]] = []
        prompt_hash = ""
        cost = Decimal("0")
        unreached: set[str] = set()

        for batch in batches:
            try:
                result = self.llm.complete(
                    agent=AGENT,
                    run_id=run_id,
                    prompt_id="mapping-suggestion.suggest",
                    caller=caller,
                    context=batch.as_prompt_grounding() + as_fenced_grounding(retrieved),
                    input_text="\n".join(c.source_column for c in batch.open_questions),
                )
            except ManualPathRequiredError:
                # This batch degrades to the manual path. Its columns become
                # UNMAPPED with a reason naming that; the others are unaffected.
                unreached.update(c.source_column for c in batch.open_questions)
                continue

            cost += result.cost_usd
            # The hash of the LAST batch, and every batch assembles the same
            # template — so it identifies the prompt version, which is what a
            # reviewer and the eval set need it for.
            prompt_hash = result.prompt.prompt_hash
            raw = result.value if isinstance(result.value, dict) else {}
            for entry in raw.get("mappings", ()):
                if not isinstance(entry, dict):
                    continue
                column = str(entry.get("source_column", ""))
                # FIRST WINS, AND THE REST ARE SURFACED. A source column really
                # can populate two targets — the client's own workbook sends
                # `claim_id` to both `claim_header` and `claim_line` — and this
                # agent proposes ONE target per column (see the graph's scope
                # note). A dict comprehension would have kept the LAST
                # silently, which is the worst of the three options: a decision
                # made by iteration order, invisible to everyone.
                if column in answers:
                    extras.append(entry)
                    continue
                answers[column] = entry

        return answers, tuple(extras), True, frozenset(unreached), prompt_hash, cost

    # ── node 3 · assemble (NO MODEL) ─────────────────────────────────────────

    def _assemble(
        self,
        grounding: Grounding,
        answers: dict[str, dict[str, Any]],
        extras: tuple[dict[str, Any], ...] = (),
        unreached: frozenset[str] = frozenset(),
        *,
        model: CanonicalModel,
        glossary: Glossary,
        contract: SchemaContract,
        feed_id: str,
    ) -> tuple[tuple[SuggestedLine, ...], tuple[str, ...], tuple[MappingFinding, ...]]:
        """Deterministic. The PLATFORM decides what counted as a target.

        Four checks, in this order:

          1. a `source_column` the contract does not have is DISCARDED — a
             mapping line with no source column cannot be run, tested or
             approved;
          2. the target must exist in the canonical model, and where the answer
             cites a glossary term the platform reads the name from the term;
          3. confidence below the floor becomes UNMAPPED whatever the model
             said, because a threshold inside a prompt is one the model reports
             rather than one the platform enforces;
          4. `core.mapping.validate` runs over the whole result, and any
             BLOCKING finding — a PHI-laundering line above all — is turned
             into a refusal and the offending line into UNMAPPED. A proposal
             that could not be saved is a proposal somebody would try to
             approve.
        """
        refusals: list[str] = []
        lines: list[SuggestedLine] = []

        for extra in extras:
            # Not discarded quietly. A second target for a column this agent
            # already placed is a real thing the client's workbooks do, and it
            # is work a person must now finish in the editor.
            refusals.append(
                f"{extra.get('source_column')!r} was given a second target "
                f"({extra.get('target_entity')}.{extra.get('target_field')}). This agent "
                "proposes ONE target per source column; a column that genuinely populates "
                "two fields is completed in the mapping editor."
            )

        known = {c.source_column for c in grounding.columns}
        for invented in sorted(set(answers) - known):
            refusals.append(
                f"{invented!r} is not a column of this feed's contract. Discarded — a mapping "
                "line with no source column cannot be run, tested or approved."
            )

        if unreached:
            refusals.append(
                f"The model could not be reached for {len(unreached)} column(s) — it returned "
                "nothing matching the response schema, twice. Those columns are UNMAPPED "
                "because the run was broken, NOT because the agent declined to guess: "
                f"{', '.join(sorted(unreached))}. The rest of this proposal is unaffected, and "
                "the manual editor always was."
            )

        for grounded in grounding.columns:
            if grounded.settled:
                lines.append(_from_the_estate(grounded))
                continue
            lines.append(
                self._from_inference(
                    grounded,
                    answers.get(grounded.source_column),
                    refusals,
                    model,
                    glossary,
                    grounding.vocabulary,
                    unreached=grounded.source_column in unreached,
                )
            )

        lines, findings = self._refuse_blocking(lines, refusals, contract, model, feed_id)
        return tuple(lines), tuple(refusals), findings

    def _from_inference(
        self,
        grounded: GroundedColumn,
        answer: dict[str, Any] | None,
        refusals: list[str],
        model: CanonicalModel,
        glossary: Glossary,
        vocabulary: TargetVocabulary,
        *,
        unreached: bool = False,
    ) -> SuggestedLine:
        if answer is None:
            return _unmapped(
                grounded,
                confidence=0.0,
                reason=(
                    "The model could not answer in the required shape for this batch, so "
                    f"nothing was proposed for {grounded.source_column!r}. A FAILED RUN, not "
                    "a decision — re-run it before reading anything into this."
                    if unreached
                    else (
                        f"No proposal was returned for {grounded.source_column!r}. "
                        f"{NO_CONFIDENT_TARGET}"
                    )
                ),
            )

        confidence = float(answer.get("confidence", 0.0))
        rationale = str(answer.get("rationale", ""))

        if answer.get("unmapped"):
            # The model declined. A CORRECT answer, recorded as the model's
            # own rather than as a platform override.
            return _unmapped(
                grounded,
                confidence=confidence,
                reason=str(answer.get("unmapped_reason") or rationale or NO_CONFIDENT_TARGET),
                rationale=rationale,
            )

        target = _resolve_target(answer, model, glossary, grounded, refusals, vocabulary=vocabulary)
        if target is None:
            return _unmapped(
                grounded,
                confidence=confidence,
                reason=(
                    f"The proposed target is not in the canonical model. {NO_CONFIDENT_TARGET}"
                ),
                rationale=rationale,
            )
        if confidence < self.confidence_floor:
            return _unmapped(
                grounded,
                confidence=confidence,
                reason=(
                    f"{rationale or 'A target was proposed'} — but confidence "
                    f"{confidence:.2f} is below the platform's floor of "
                    f"{self.confidence_floor:.2f}, so this is for you to decide. The "
                    f"suggestion was {target[0]}.{target[1]}."
                ),
                rationale=rationale,
            )

        entity, field, glossary_id = target
        return SuggestedLine(
            line=MappingLine(
                target_entity=entity,
                target_field=field,
                source_columns=(grounded.source_column,),
                transform=Transform(),
                glossary_id=glossary_id,
                notes=_transform_note(str(answer.get("transform", "direct")), rationale),
                confidence=confidence,
                citations=tuple(str(c) for c in grounded.citations),
            ),
            source_column=grounded.source_column,
            confidence=confidence,
            settled_by="inference",
            rationale=rationale,
            like_feed_id=str(answer.get("like_feed_id") or "") or None,
        )

    def _refuse_blocking(
        self,
        lines: list[SuggestedLine],
        refusals: list[str],
        contract: SchemaContract,
        model: CanonicalModel,
        feed_id: str,
    ) -> tuple[list[SuggestedLine], tuple[MappingFinding, ...]]:
        """Run CF-V1-E6-03's validator over the agent's own output.

        The one that matters is `phi_laundering`: a suggestion that carries a
        protected source column into a target field nothing flags takes the
        value out of the masking policy without breaking a rule anywhere. It is
        exactly the kind of mistake a plausible-sounding mapping makes, and it
        must not reach a queue where somebody could approve it — so the line
        becomes UNMAPPED, with the finding's own sentence as its reason.
        """
        mapping = FeedMapping(feed_id=feed_id, lines=tuple(s.line for s in lines))
        findings = validate(mapping, contract=contract, model=model)
        blocked = {f.address: f for f in blocking(findings)}
        if not blocked:
            return lines, findings

        rebuilt: list[SuggestedLine] = []
        for suggested in lines:
            finding = blocked.get(suggested.line.address)
            if finding is None or not suggested.line.is_mapped:
                rebuilt.append(suggested)
                continue
            refusals.append(
                f"{suggested.source_column} -> {suggested.line.address}: {finding.what}. "
                f"{finding.why_it_matters} Left unmapped for a human."
            )
            rebuilt.append(
                SuggestedLine(
                    line=MappingLine(
                        target_entity=suggested.line.target_entity,
                        target_field=suggested.line.target_field,
                        unmapped_reason=f"{finding.what}. {finding.how_to_fix}",
                        glossary_id=suggested.line.glossary_id,
                        confidence=suggested.confidence,
                        citations=suggested.line.citations,
                    ),
                    source_column=suggested.source_column,
                    confidence=suggested.confidence,
                    settled_by=suggested.settled_by,
                    rationale=suggested.rationale,
                    like_feed_id=suggested.like_feed_id,
                )
            )
        return rebuilt, findings

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


def _resolve_target(
    answer: dict[str, Any],
    model: CanonicalModel,
    glossary: Glossary,
    grounded: GroundedColumn,
    refusals: list[str],
    *,
    vocabulary: TargetVocabulary,
) -> tuple[str, str, str | None] | None:
    """THE MODEL PICKS THE CONCEPT; THE PLATFORM SPELLS THE NAME.

    Three sources, in this order and for these reasons:

      1. a cited GLOSSARY TERM that names exactly one table and one column —
         the estate's own vocabulary, and on CF-V1-E5-02's gate this one rule
         was the difference between 80% and 100%;
      2. `target_ref`, the NUMBER of a line in the grounding's target list.
         Preferred over the names because a number survives the PHI scrubber
         and a name does not always — see `TargetVocabulary`;
      3. the proposed entity and field names, which must resolve in the
         canonical model or there is no target at all.
    """
    cited_id = str(answer.get("glossary_id") or "")
    cited = glossary.get(cited_id) if cited_id else None
    if cited is not None and len(cited.mapped_tables) == 1 and cited.mapped_columns_corrected:
        table, column = cited.mapped_tables[0], cited.mapped_columns_corrected[0]
        proposed = str(answer.get("target_field") or "")
        if proposed and proposed.lower() != column.lower():
            refusals.append(
                f"{grounded.source_column}: the agent cited {cited.glossary_id} but wrote "
                f"{proposed!r}. Using the term's own column name {column!r} — the estate's "
                "vocabulary spells it, not the model."
            )
        return table, column, cited.glossary_id

    ref = answer.get("target_ref")
    if isinstance(ref, int):
        chosen = vocabulary.target(ref)
        if chosen is None:
            refusals.append(
                f"{grounded.source_column}: the agent chose target #{ref}, and the list it "
                f"was shown has {len(vocabulary.entries)} entries. Discarded."
            )
            return None
        entity_name, field_name = chosen
        found = model.entity(entity_name)
        field = found.field(field_name) if found is not None else None
        if found is not None and field is not None:
            return found.name, field.name, field.glossary_id
        # The vocabulary is BUILT from the canonical model, so a number that
        # resolves to a field the model does not have means the two were built
        # from different inputs — a platform defect, not a model error.
        return entity_name, field_name, None

    entity_name = str(answer.get("target_entity") or "")
    field_name = str(answer.get("target_field") or "")
    if not entity_name or not field_name:
        return None
    entity = model.entity(entity_name)
    field = entity.field(field_name) if entity is not None else None
    if entity is None or field is None:
        refusals.append(
            f"{grounded.source_column}: the agent proposed {entity_name}.{field_name}, which "
            "the canonical model does not have. Discarded — a mapping to a field nobody has "
            "designed or deployed has nowhere to land."
        )
        return None
    return entity.name, field.name, field.glossary_id


def _from_the_estate(grounded: GroundedColumn) -> SuggestedLine:
    """A line the glossary or an approved mapping already decided."""
    rationale = (
        "The client's own glossary names this column."
        if grounded.settled_by == "glossary"
        else "Already mapped and approved on this feed; carried forward unchanged."
    )
    return SuggestedLine(
        line=MappingLine(
            target_entity=grounded.target_entity or "",
            target_field=grounded.target_field or "",
            source_columns=(grounded.source_column,),
            glossary_id=grounded.glossary_id,
            notes=rationale,
            confidence=1.0,
            citations=tuple(str(c) for c in grounded.citations),
        ),
        source_column=grounded.source_column,
        confidence=1.0,
        settled_by=grounded.settled_by,
        rationale=rationale,
    )


def _unmapped(
    grounded: GroundedColumn, *, confidence: float, reason: str, rationale: str = ""
) -> SuggestedLine:
    """A column the platform will not map.

        "UNMAPPED flagged, never guessed"

    The target address is preserved where the model proposed one, so a steward
    filling the gap is confirming a suggestion rather than starting over. And
    the REASON is required by `MappingLine` itself — an agent that declines has
    to say why, which is why "never guessed" is enforced by a type rather than
    by a convention.
    """
    return SuggestedLine(
        line=MappingLine(
            target_entity=grounded.target_entity or "unassigned",
            target_field=grounded.source_column,
            unmapped_reason=reason or NO_CONFIDENT_TARGET,
            glossary_id=grounded.glossary_id,
            confidence=confidence,
            citations=tuple(str(c) for c in grounded.citations),
        ),
        source_column=grounded.source_column,
        confidence=confidence,
        settled_by="inference",
        rationale=rationale or reason,
    )


def _transform_note(kind: str, rationale: str) -> str:
    """THE AGENT PROPOSES A COLUMN PAIRING; IT NEVER PROPOSES A TRANSFORM'S
    PARAMETERS.

    Every kind above DIRECT needs parameters — a separator, a 70-code lookup
    table, a set of when/then cases — and those are business rules with
    consequences a steward has to read. A model filling them in from a column
    name would be inventing them, and inventing them plausibly, which is worse
    than leaving them blank.

    So the proposed line is always DIRECT and the model's intent travels as a
    NOTE the BA completes in the editor. That is the honest division of labour
    in this story: the hard part of mapping is knowing that `MBR_DOB` is a date
    of birth, and the platform's own taxonomy is where the split character goes.
    """
    try:
        named = TransformKind(kind)
    except ValueError:
        return rationale
    if named is TransformKind.DIRECT:
        return rationale
    suggestion = (
        f"The agent suggests this needs a {named.value} transform. Its parameters are for you "
        "to set in the editor — they are business rules, not something to infer from a name."
    )
    return f"{rationale} {suggestion}".strip()


def _overall_confidence(lines: tuple[SuggestedLine, ...]) -> float:
    """The WEAKEST line's confidence, not the mean.

    A mapping is approved as a whole, and averaging lets forty glossary lookups
    hide one column the agent barely placed.
    """
    return min((line.confidence for line in lines), default=0.0)


def _citations(grounding: Grounding) -> tuple[CitationId, ...]:
    seen: dict[str, CitationId] = {}
    for column in grounding.columns:
        for citation in column.citations:
            seen.setdefault(str(citation), citation)
    return tuple(seen.values())


__all__ = [
    "AGENT_ACTOR",
    "SUGGEST_SCHEMA",
    "MappingSuggestionAgent",
    "SuggestedLine",
    "SuggestionResult",
]
