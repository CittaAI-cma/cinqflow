"""The deterministic half of CF-V1-E5-02. No model, no I/O, no guessing.

    "The facts come from computation. The AI only ever *interprets* them."
    — memory/07-runbooks/RB-04-onboard-a-feed.md, step 1

This module answers every question the evidence already settles, and it runs
BEFORE the model is considered:

  • the TYPE, wherever `ColumnProfile.narrowest_type` determines one;
  • the CANONICAL NAME, wherever a glossary term claims the source column as a
    synonym — `BG-004` records that `DOB`, `Patient_dob` and `MemberDateOfBirth`
    are all Member Date of Birth, written by the client's own analysts;
  • the PHI FLAG, which comes from the glossary and NOWHERE ELSE on the way
    down (a model may raise one, never clear one).

Note what is deliberately NOT decided here: NULLABILITY. A sample cannot
establish a NOT NULL constraint — absence of nulls in 200 rows is not evidence
that a column cannot be null, and a constraint guessed that way quarantines
real members. Null counts travel as evidence; the constraint arrives with the
key columns the approver declares.

What is left over is the model's question, and it is usually small. That is the
whole economics of the story: the columns a payer names sensibly cost nothing,
and the tokens go to `MBR_DOB_DT` and `SUBSCR_REL_CD`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from cinqflow.core.agents.schema_inference.graph import NEEDS_YOUR_INPUT
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.profiling import ColumnProfile, FileProfile
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import TypeName


@dataclass(frozen=True)
class GroundedColumn:
    """One column, with whatever the evidence determined and what it did not.

    `settled` is the load-bearing field: True means nothing needs asking, and
    the agent's cost is proportional to how many of these are False.
    """

    source_name: str
    position: int
    #: None where the profiler found more than one type fitting, or none.
    type: TypeName | None = None
    #: The canonical name a glossary term supplies, if one claims this column.
    name: str | None = None
    nullable: bool = True
    is_phi: bool = False
    glossary_id: str | None = None
    date_format: str | None = None
    precision: int | None = None
    scale: int | None = None
    citations: tuple[CitationId, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def settled(self) -> bool:
        """Both halves determined: a type from the arithmetic AND a name from
        the glossary. Either one missing is a question for the model."""
        return self.type is not None and self.name is not None

    @property
    def missing(self) -> tuple[str, ...]:
        gaps = []
        if self.type is None:
            gaps.append("type")
        if self.name is None:
            gaps.append("name")
        return tuple(gaps)


@dataclass(frozen=True)
class Vocabulary:
    """The canonical names a proposal may choose from, with their terms.

    THIS IS GROUNDING, NOT A HINT. A model asked to name `DOB` with no target
    vocabulary in front of it invents `dob` — a defensible name, and not the
    one this estate uses. A BA doing the same job opens the canonical model
    browser and picks from the list. Showing the same list is what makes the
    proposal conform to a naming convention instead of to a model's habits,
    and it is what "cited glossary precedents" means in CF-V1-E5-02.
    """

    entries: tuple[tuple[str, str, str], ...] = ()  # (term, canonical name, glossary id)

    def as_text(self, limit: int = 200) -> str:
        shown = self.entries[:limit]
        # The CANONICAL NAME leads, and is labelled. A line reading
        # "Member Date of Birth -> date_of_birth" invites a model to answer
        # with the left-hand side, which is a term and not a column name.
        lines = [
            f"  {glossary_id}: column `{name}` — the business term is {term!r}"
            for term, name, glossary_id in shown
        ]
        if len(self.entries) > len(shown):
            # No silent truncation: a model told "choose from this list" and
            # shown a third of it would decline perfectly nameable columns.
            lines.append(f"  ... and {len(self.entries) - len(shown)} more terms not listed here")
        return "\n".join(lines)


@dataclass(frozen=True)
class Grounding:
    """Everything known before a model is consulted."""

    feed_id: str
    profile_id: str
    columns: tuple[GroundedColumn, ...]
    vocabulary: Vocabulary = Vocabulary()

    @property
    def settled(self) -> tuple[GroundedColumn, ...]:
        return tuple(c for c in self.columns if c.settled)

    @property
    def open_questions(self) -> tuple[GroundedColumn, ...]:
        """What the model is asked about. If this is empty, no model is called
        — and a feed of well-named columns therefore costs zero tokens."""
        return tuple(c for c in self.columns if not c.settled)

    @property
    def needs_no_model(self) -> bool:
        return not self.open_questions

    def column(self, source_name: str) -> GroundedColumn | None:
        for candidate in self.columns:
            if candidate.source_name == source_name:
                return candidate
        return None

    def as_prompt_grounding(self) -> str:
        """The GROUNDING section, assembled as text.

        Only the open questions and their evidence: sending the settled columns
        too would pay tokens for answers the platform already has, and would
        give the model the opportunity to disagree with arithmetic.
        """
        lines = [
            f"Feed: {self.feed_id}",
            f"Profile: profile:{self.profile_id} (computed facts — do not contradict these)",
            "",
            "Columns needing a decision:",
        ]
        for column in self.open_questions:
            lines.append(f"- source column {column.source_name!r} (position {column.position})")
            lines.append(f"  missing: {', '.join(column.missing)}")
            for line in column.evidence:
                lines.append(f"  {line}")
            if column.glossary_id:
                lines.append(f"  glossary: {column.glossary_id} (PHI: {column.is_phi})")
        if self.settled:
            lines += [
                "",
                "Already settled by computation (for naming consistency only — "
                "do not restate them):",
                "  " + ", ".join(f"{c.source_name}->{c.name}" for c in self.settled),
            ]
        if self.vocabulary.entries:
            lines += [
                "",
                "The canonical vocabulary. Prefer a name from this list where one fits the "
                "column, and cite its glossary id:",
                self.vocabulary.as_text(),
            ]
        return "\n".join(lines)


def ground(profile: FileProfile, *, feed_id: str, glossary: Glossary) -> Grounding:
    """Compute everything the profile and the glossary already decide.

    Deliberately takes the FULL profile and returns citations per column, so
    every fact the agent later interprets has an address the reviewer can open
    (`profile:<id>#<column>`, `term:<slug>`).
    """
    return Grounding(
        feed_id=feed_id,
        profile_id=profile.profile_id,
        columns=tuple(_ground_column(profile, column, glossary) for column in profile.columns),
        vocabulary=_vocabulary(glossary),
    )


def _vocabulary(glossary: Glossary) -> Vocabulary:
    """Every term that supplies a canonical name, sorted for reproducibility."""
    entries = {
        (term.term, name, term.glossary_id)
        for term in glossary.terms
        if (name := _canonical_name(term)) is not None
    }
    return Vocabulary(entries=tuple(sorted(entries, key=lambda e: e[2])))


def _ground_column(
    profile: FileProfile, column: ColumnProfile, glossary: Glossary
) -> GroundedColumn:
    terms = glossary.for_column(column.name)
    citations: list[CitationId] = [profile.citation_for(column.name)]
    evidence = [
        f"rows {column.row_count}, populated {column.populated_count}, "
        f"nulls {column.null_count}, distinct {column.distinct_count}"
        + ("" if column.distinct_is_exact else "+ (exact count exceeded)"),
        "types fitting every value: "
        + (
            ", ".join(
                f"{c.type.value} {c.matched}/{c.considered}"
                for c in column.type_candidates
                if c.is_total
            )
            or "none but string"
        ),
    ]
    if column.date_formats:
        evidence.append(
            "date formats seen: "
            + ", ".join(f"{d.label} x{d.matched}" for d in column.date_formats)
        )
    if column.examples:
        evidence.append("examples: " + ", ".join(column.examples))

    term = _single(terms)
    if term is not None:
        citations.append(CitationId(kind=CitationKind.TERM, subject=term.slug))
        evidence.append(f"glossary {term.glossary_id} — {term.term}: {term.definition}")
    elif terms:
        # More than one term claims this spelling. That is a real ambiguity in
        # the client's own glossary, and resolving it silently would pick a
        # business meaning on somebody's behalf.
        evidence.append(
            f"{NEEDS_YOUR_INPUT}: {len(terms)} glossary terms claim this column — "
            + ", ".join(f"{t.glossary_id} ({t.term})" for t in terms)
        )

    return GroundedColumn(
        source_name=column.name,
        position=column.position,
        type=column.narrowest_type,
        name=_canonical_name(term),
        # ALWAYS NULLABLE HERE. Nullability is a business constraint, not a
        # sample statistic, and it is set where the human's decision actually
        # is: the key columns declared at approval become NOT NULL, and
        # nothing else does.
        #
        # The obvious alternative — "no nulls in the sample, so propose NOT
        # NULL" — is wrong in the expensive direction. `nullable=False` makes
        # the pipeline QUARANTINE every row arriving with that field empty, so
        # a constraint inferred from 200 rows starts dropping real members the
        # first month a payer omits a middle name. And it over-fits: in a
        # synthetic sample every last name is distinct, which says nothing
        # about last names. The null COUNT is reported as evidence; the
        # constraint is a decision.
        nullable=True,
        # From the glossary and nowhere else on the way down. A model may raise
        # this flag later; `merge` refuses to let it clear one.
        is_phi=bool(term and term.is_phi),
        glossary_id=term.glossary_id if term else None,
        date_format=column.date_formats[0].label if column.date_formats else None,
        precision=column.observed_precision,
        scale=column.observed_scale,
        citations=tuple(citations),
        evidence=tuple(evidence),
    )


def _single(terms: tuple[GlossaryTerm, ...]) -> GlossaryTerm | None:
    """Exactly one term, or none. Two terms claiming one column is a question,
    not a coin toss."""
    return terms[0] if len(terms) == 1 else None


def _canonical_name(term: GlossaryTerm | None) -> str | None:
    """The corrected column name the client's canonical model uses.

    Falls back to the term's slug with underscores — `Member Date of Birth`
    becomes `member_date_of_birth` — which is the same rule the ODS model
    follows, so a term with no corrected column still lands on the name a
    reviewer expects.
    """
    if term is None:
        return None
    for candidate in term.mapped_columns_corrected:
        return candidate.strip().lower()
    return term.slug.replace("-", "_") or None


def merge(
    grounded: GroundedColumn,
    proposed: dict[str, Any],
    *,
    glossary: Glossary | None = None,
) -> tuple[GroundedColumn, tuple[str, ...]]:
    """Fold one model answer into the grounded column, refusing what it may not say.

    Returns the merged column and the list of REFUSALS — things the model said
    that the platform declined to take. Refusals are returned rather than
    logged and forgotten, because "the agent tried to clear a PHI flag" is a
    governance event and needs to reach the review screen.

    THE MODEL PICKS THE CONCEPT; THE PLATFORM SPELLS THE NAME. Where the answer
    cites a `glossary_id`, the canonical name and the PHI flag are read from
    that term rather than from the model's free text — the same rule that makes
    the Pipeline Insight Agent take identifiers from routing rather than from a
    model's output. Left to spell it, a model returns "Member Date of Birth"
    where the estate says `date_of_birth`, and a whole class of naming variance
    disappears the moment the platform does the spelling.
    """
    refusals: list[str] = []
    cited = _cited_term(proposed.get("glossary_id"), glossary)

    #: THE PHI RULE. A glossary-flagged column stays flagged whatever the model
    #: says — "never downgrade a glossary-flagged PHI field" (CF-V1-E5-03's
    #: don't, enforced here where the downgrade would first become possible).
    proposed_phi = bool(proposed.get("is_phi", False))
    if grounded.is_phi and not proposed_phi:
        refusals.append(
            f"{grounded.source_name}: the agent proposed clearing the PHI flag that glossary "
            f"{grounded.glossary_id} sets. Refused — clearing a PHI flag needs steward "
            "approval, and no model has it."
        )
    is_phi = grounded.is_phi or proposed_phi or bool(cited and cited.is_phi)

    # The TYPE the arithmetic determined wins over the model's. The model is
    # asked only about columns where it did not determine one.
    chosen_type: TypeName | None
    if grounded.type is not None:
        chosen_type = grounded.type
        if proposed.get("type") and proposed["type"] != grounded.type.value:
            refusals.append(
                f"{grounded.source_name}: the agent proposed {proposed['type']}, but every "
                f"value in the sample fits {grounded.type.value}. The computation wins."
            )
    else:
        chosen_type = TypeName(proposed["type"]) if proposed.get("type") else None

    # Precedence: what the glossary already settled, then the term the model
    # CITED (spelled by the platform), then its own free text.
    name = grounded.name or _canonical_name(cited) or (proposed.get("name") or None)
    if cited is not None and proposed.get("name") and _canonical_name(cited) != proposed["name"]:
        refusals.append(
            f"{grounded.source_name}: the agent cited {cited.glossary_id} but wrote "
            f"{proposed['name']!r}. Using the term's own column name "
            f"{_canonical_name(cited)!r} — the estate's vocabulary spells it, not the model."
        )
    return (
        replace(
            grounded,
            type=chosen_type,
            name=name,
            is_phi=is_phi,
            nullable=bool(proposed.get("nullable", grounded.nullable)),
            date_format=proposed.get("date_format") or grounded.date_format,
            glossary_id=grounded.glossary_id
            or (cited.glossary_id if cited else None)
            or (proposed.get("glossary_id") or None),
        ),
        tuple(refusals),
    )


def _cited_term(glossary_id: Any, glossary: Glossary | None) -> GlossaryTerm | None:
    """The term the model cited, if it is real.

    A cited id that names no term is discarded silently rather than refused:
    it is a model reaching for grounding it does not have, which the confidence
    floor and the "needs your input" path already handle.
    """
    if not glossary_id or glossary is None:
        return None
    return glossary.get(str(glossary_id))
