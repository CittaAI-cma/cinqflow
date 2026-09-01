"""The deterministic half of CF-V1-E6-02. No model, no I/O, no guessing.

    "The facts come from computation. The AI only ever *interprets* them."

This module answers every mapping question the estate has already answered, and
it runs BEFORE the model is considered:

  • the GLOSSARY's synonym set. `BG-004` records that `DOB`, `Patient_dob` and
    `MemberDateOfBirth` all carry Member Date of Birth; the canonical column it
    names is the target, and no model is asked.
  • THIS FEED'S OWN PUBLISHED MAPPING. Already approved by a named human. A
    proposal that re-proposed it would ask a steward to re-sign what they
    signed in March.

What is left over is the model's question, and it is usually small — which is
the economics of the story: the columns the client's own vocabulary already
names cost nothing, and the tokens go to `SUBSCR_REL_CD`.

THE AGENT IS NOT SHOWN A SINGLE VALUE, and unlike CF-V1-E5-03 the reason is not
only protection — it is that VALUES DO NOT HELP HERE. Schema inference must see
`19900101` to know a column is a date. Mapping is a name-to-concept decision:
the evidence is column names, business definitions, the canonical field list
and prior approved mappings. A grounding carrying what it does not need is a
grounding that leaks for nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.mapping import FeedMapping, LineStatus
from cinqflow.core.registry.canonical import CanonicalModel
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm


def identifier(name: str) -> str:
    """`[name]`. EVERY identifier in this grounding is written this way.

    A MITIGATION, NOT THE GUARANTEE — the guarantee is `TargetVocabulary`'s
    numbering, because no rendering makes an arbitrary name safe. Brackets stop
    the recogniser reading the line as prose and cut the false-positive rate on
    the client's real corpus from two-in-seventy-three to one; the number is
    what makes the remaining one harmless.

    THE SCRUBBER STANDS BETWEEN THIS TEXT AND THE MODEL, and it is right to. It
    is tuned for 100% recall on a payer's values, so over a page of identifiers
    it produces false positives — and a false positive here is not a masked
    value, it is a NAME THE MODEL IS ASKED TO COPY BACK AND CANNOT.

    Two forms were tried against the client's real Fidelis claims workbook
    before this one:

      • `entity.field` — Presidio reads `a.b` as a hostname. It rewrote
        `claim_header.source_claim_id` to `claim_<URL>urce_claim_id`, and
        `MBR_DOB->members.date_of_birth` to `<LOCATION>` entire. Every one of
        the agent's ninety proposals was then refused by the platform as "not
        in the canonical model", because the model was faithfully copying back
        what it had been shown.
      • `entity / field` — better, and still wrong twice in seventy-three:
        `id_qualifier` and `date_of_birth` come back as `<PERSON>`.

    Bracketed, ninety of ninety source columns and seventy-two of seventy-three
    canonical fields survive. The last one does not and cannot: `date_of_birth`
    sitting beside the sentence "Date of birth of the member." is a person to
    any NER worth having, and every layout tried — definition first, definition
    on its own line, quoted, colon-separated — leaves it redacted. That is the
    scrubber being CORRECT about a string that happens to be a field name.

    So the rendering stops being the guarantee. See `TargetVocabulary`.

    A reviewer's screen still shows `entity.field` — see
    `Exemplar.as_review_line`. Nothing stands between that string and a person.
    """
    return f"[{name}]"


def address_for_a_prompt(entity: str, field: str) -> str:
    """One canonical target, as the prompt writes it. See `identifier`."""
    return f"{identifier(entity)} {identifier(field)}"


@dataclass(frozen=True)
class Exemplar:
    """A mapping decision somebody already approved, for the same source
    spelling, on a DIFFERENT feed.

        "new payer's MBR_DOB maps like Fidelis date_of_birth did"

    EVIDENCE, NOT AN ANSWER. Two payers can spell two different concepts the
    same way, so an exemplar is shown to the model with the feed it came from
    and the person who approved it, and the model decides whether it applies.
    Auto-applying it would make the platform confidently wrong the first time
    two payers disagree about what `STATUS` means.

    `approved_by` travels on the OBJECT because it is what makes this precedent
    rather than similarity — a reviewer reading "Ola approved this on the
    Fidelis feed" can go and ask Ola. It is deliberately NOT in `as_line`: the
    model needs to know the precedent was approved, and it does not need to
    know by whom. Putting a colleague's identifier into a prompt buys nothing
    and sends a staff email to a vendor.

    (The gateway's scrubber found this before I did — it redacted the address
    out of the prompt, and the test asserting the name arrived failed. The
    scrubber was right and the design was wrong.)
    """

    feed_id: str
    source_column: str
    target_entity: str
    target_field: str
    transform: str
    approved_by: str = ""
    version: int = 1

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.MAPPING, subject=self.feed_id, version=self.version)

    def as_line(self) -> str:
        """The prompt's rendering. No identity — see the class docstring, and
        no DOT between entity and field — see `address_for_a_prompt`."""
        address = address_for_a_prompt(self.target_entity, self.target_field)
        return (
            f"  {identifier(self.source_column)} -> {address} ({self.transform}) "
            f"on feed {identifier(self.feed_id)}, approved"
        )

    def as_review_line(self) -> str:
        """The REVIEWER's rendering, where the identity is the whole point —
        and where the ordinary dotted address is fine, because no scrubber
        stands between this string and a person."""
        who = f", approved by {self.approved_by}" if self.approved_by else ""
        return (
            f"{self.source_column} -> {self.target_entity}.{self.target_field} "
            f"on feed {self.feed_id}{who}"
        )


@dataclass(frozen=True)
class GroundedColumn:
    """One source column, with whatever the estate already decided about it."""

    source_column: str
    position: int
    #: Set where the glossary or this feed's own published mapping settled it.
    target_entity: str | None = None
    target_field: str | None = None
    glossary_id: str | None = None
    is_phi: bool = False
    #: How it was settled, for the screen and for the eval's split: one of
    #: "glossary", "published_mapping", or "" while open.
    settled_by: str = ""
    exemplars: tuple[Exemplar, ...] = ()
    citations: tuple[CitationId, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def settled(self) -> bool:
        return self.target_field is not None


@dataclass(frozen=True)
class TargetVocabulary:
    """The canonical fields a proposal may choose from.

    THIS IS GROUNDING, NOT A HINT — the same rule CF-V1-E5-02 learned the hard
    way. A model asked where `MBR_DOB` goes, with no target list in front of
    it, invents `patient.dob`: a defensible name, and not one this estate has.
    A BA doing the same job opens the canonical browser and picks from the
    list, and showing the same list is what "cited exemplars" means.

    THE LIST IS NUMBERED, AND THE NUMBER IS THE ANSWER. That is what makes this
    agent robust to its own scrubber rather than dependent on it.

    The scrubber sits between this text and the model and is tuned for total
    recall on a payer's values, so over a page of identifiers it produces false
    positives. `date_of_birth`, beside the sentence "Date of birth of the
    member.", comes back as `<PERSON>` under every rendering tried — correctly,
    for a string that happens to also be the estate's single most important
    field name.

    With names as the answer, that one redaction cost the platform every
    proposal for the whole feed: the model copied back what it was shown and
    the platform refused all ninety as "not in the canonical model". With a
    NUMBER as the answer, it costs a little context on one line — the
    definition is still there, and it is the more informative half anyway — and
    the platform resolves the exact target regardless.

    A redacted name is now a legibility problem. It used to be a correctness
    one.
    """

    entries: tuple[tuple[str, str, str], ...] = ()  # (entity, field, definition)

    def target(self, ref: int) -> tuple[str, str] | None:
        """The (entity, field) a number names. 1-based, as the prompt shows."""
        if 1 <= ref <= len(self.entries):
            entity, field, _ = self.entries[ref - 1]
            return entity, field
        return None

    def as_text(self, limit: int = 300) -> str:
        shown = self.entries[:limit]
        lines = [
            f"  {index} = {address_for_a_prompt(entity, field)}"
            + (f" — {definition}" if definition else "")
            for index, (entity, field, definition) in enumerate(shown, start=1)
        ]
        if len(self.entries) > len(shown):
            # No silent truncation: a model told "choose from this list" and
            # shown a third of it declines perfectly mappable columns.
            lines.append(f"  ... and {len(self.entries) - len(shown)} more fields not listed here")
        return "\n".join(lines)


@dataclass(frozen=True)
class Grounding:
    """Everything known before a model is consulted."""

    feed_id: str
    columns: tuple[GroundedColumn, ...] = ()
    vocabulary: TargetVocabulary = TargetVocabulary()

    @property
    def settled(self) -> tuple[GroundedColumn, ...]:
        return tuple(c for c in self.columns if c.settled)

    @property
    def open_questions(self) -> tuple[GroundedColumn, ...]:
        """What the model is asked about. If this is empty, NO MODEL IS CALLED
        — so a feed the glossary already names costs zero tokens."""
        return tuple(c for c in self.columns if not c.settled)

    @property
    def needs_no_model(self) -> bool:
        return not self.open_questions

    def column(self, source_column: str) -> GroundedColumn | None:
        for candidate in self.columns:
            if candidate.source_column == source_column:
                return candidate
        return None

    def batches(self, size: int) -> tuple[Grounding, ...]:
        """One grounding per batch of open questions.

        Every batch keeps the SETTLED columns and the full target vocabulary —
        they are what make the answers consistent with each other and with the
        estate, and a batch shown a third of the model would decline perfectly
        mappable columns. Only the questions are split.

        Returns an empty tuple when there is nothing to ask, so the caller's
        "no open questions means no model call" branch stays the one place that
        decision is made.
        """
        open_questions = self.open_questions
        if not open_questions:
            return ()
        settled = self.settled
        return tuple(
            Grounding(
                feed_id=self.feed_id,
                columns=settled + open_questions[start : start + size],
                vocabulary=self.vocabulary,
            )
            for start in range(0, len(open_questions), size)
        )

    def as_prompt_grounding(self) -> str:
        """The GROUNDING section, assembled as text. Names, definitions and
        precedents — and not one value from anybody's file."""
        lines = [
            f"Feed: {self.feed_id}",
            "",
            "Source columns needing a target:",
        ]
        for column in self.open_questions:
            lines.append(
                f"- source column {identifier(column.source_column)} (position {column.position})"
            )
            for line in column.evidence:
                lines.append(f"  {line}")
            if column.exemplars:
                lines.append("  precedents — the same column name, mapped and approved before:")
                lines.extend(exemplar.as_line() for exemplar in column.exemplars)
        if self.settled:
            lines += [
                "",
                "Already settled by the estate's own vocabulary and approved mappings "
                "(for consistency only — do not restate them):",
                "  "
                + ", ".join(
                    f"{identifier(c.source_column)} -> "
                    f"{address_for_a_prompt(c.target_entity or '', c.target_field or '')}"
                    for c in self.settled
                ),
            ]
        if self.vocabulary.entries:
            lines += [
                "",
                "The canonical target model, numbered. Answer with `target_ref` — the "
                "NUMBER of the line you chose. A target that is not in this list does not "
                "exist:",
                self.vocabulary.as_text(),
            ]
        return "\n".join(lines)


def ground(
    contract: SchemaContract,
    *,
    feed_id: str,
    glossary: Glossary,
    model: CanonicalModel,
    published_mappings: tuple[FeedMapping, ...] = (),
    approvers: dict[str, str] | None = None,
) -> Grounding:
    """Compute everything the estate has already decided about these columns.

    `published_mappings` are the APPROVED ones only — the caller filters, and a
    test asserts that a draft mapping never becomes an exemplar. A suggestion
    grounded in somebody's unreviewed draft would launder an unapproved
    decision into a second feed, where it would arrive wearing the authority of
    precedent.
    """
    own, others = _split_by_feed(published_mappings, feed_id)
    return Grounding(
        feed_id=feed_id,
        columns=tuple(
            _ground_column(column.reads_from, index, glossary, own, others, approvers or {})
            for index, column in enumerate(contract.columns)
        ),
        vocabulary=_vocabulary(model),
    )


def _split_by_feed(
    mappings: tuple[FeedMapping, ...], feed_id: str
) -> tuple[FeedMapping | None, tuple[FeedMapping, ...]]:
    own = next((m for m in mappings if m.feed_id == feed_id), None)
    return own, tuple(m for m in mappings if m.feed_id != feed_id)


def _vocabulary(model: CanonicalModel) -> TargetVocabulary:
    """Every canonical field, with its definition. DEPLOYED FIRST.

    Order matters because the list is truncated for long models: a BA can map
    to a designed-but-unprovisioned field and the platform allows it with an
    advisory, but a target that exists today is the better default, and burying
    it below three hundred designed ones would invert that.
    """
    deployed = [
        (entity.name, field.name, field.definition)
        for entity in model.entities
        for field in entity.fields
        if field.deployed
    ]
    designed = [
        (entity.name, field.name, field.definition)
        for entity in model.entities
        for field in entity.fields
        if not field.deployed
    ]
    return TargetVocabulary(entries=tuple(deployed + designed))


def _ground_column(
    source_column: str,
    position: int,
    glossary: Glossary,
    own: FeedMapping | None,
    others: tuple[FeedMapping, ...],
    approvers: dict[str, str],
) -> GroundedColumn:
    citations: list[CitationId] = []
    evidence: list[str] = []

    # 1 · this feed's OWN approved mapping. Not evidence — the decision.
    existing = _own_line(own, source_column)
    if existing is not None and own is not None:
        return GroundedColumn(
            source_column=source_column,
            position=position,
            target_entity=existing.target_entity,
            target_field=existing.target_field,
            glossary_id=existing.glossary_id,
            settled_by="published_mapping",
            citations=(own.citation,),
            evidence=(f"already mapped and approved in {own.feed_id}@v{own.version}",),
        )

    # 2 · the client's own vocabulary.
    terms = glossary.for_column(source_column)
    term = terms[0] if len(terms) == 1 else None
    if term is not None:
        citations.append(CitationId(kind=CitationKind.TERM, subject=term.slug))
        evidence.append(f"glossary {term.glossary_id} — {term.term}: {term.definition}")
        target = _canonical_target(term)
        if target is not None:
            entity, field = target
            return GroundedColumn(
                source_column=source_column,
                position=position,
                target_entity=entity,
                target_field=field,
                glossary_id=term.glossary_id,
                is_phi=term.is_phi,
                settled_by="glossary",
                citations=tuple(citations),
                evidence=tuple(evidence),
            )
    elif terms:
        # More than one term claims this spelling — a real ambiguity in the
        # client's own glossary, and resolving it silently would pick a
        # business meaning on somebody's behalf.
        evidence.append(
            f"{len(terms)} glossary terms claim this column: "
            + ", ".join(f"{t.glossary_id} ({t.term})" for t in terms)
        )

    exemplars = _exemplars(source_column, others, approvers)
    citations.extend(exemplar.citation for exemplar in exemplars)
    if not evidence and not exemplars:
        evidence.append("no glossary term names this column, and no other feed has mapped it")

    return GroundedColumn(
        source_column=source_column,
        position=position,
        glossary_id=term.glossary_id if term else None,
        is_phi=bool(term and term.is_phi),
        exemplars=exemplars,
        citations=tuple(citations),
        evidence=tuple(evidence),
    )


def _own_line(own: FeedMapping | None, source_column: str):  # type: ignore[no-untyped-def]
    if own is None:
        return None
    for line in own.lines:
        if line.status is LineStatus.MAPPED and source_column in line.source_columns:
            return line
    return None


def _exemplars(
    source_column: str, others: tuple[FeedMapping, ...], approvers: dict[str, str]
) -> tuple[Exemplar, ...]:
    """Approved mappings of the SAME source spelling on other feeds.

    Exact spelling, deliberately, and not a fuzzy match. A precedent's whole
    value is that it is the same decision about the same thing; "these two
    names are 80% similar" is not precedent, it is a guess wearing one — and
    the model is perfectly capable of noticing a near-match in the vocabulary
    list without the platform pre-committing to it.
    """
    found: list[Exemplar] = []
    for mapping in others:
        for line in mapping.lines:
            if line.status is not LineStatus.MAPPED or source_column not in line.source_columns:
                continue
            found.append(
                Exemplar(
                    feed_id=mapping.feed_id,
                    source_column=source_column,
                    target_entity=line.target_entity,
                    target_field=line.target_field,
                    transform=line.transform.kind.value,
                    approved_by=approvers.get(mapping.feed_id, ""),
                    version=mapping.version,
                )
            )
    return tuple(found)


def _canonical_target(term: GlossaryTerm) -> tuple[str, str] | None:
    """The (entity, field) a term names, if it names exactly one of each.

    Exactly one, deliberately. `BG-004 Member Date of Birth` names four tables
    — Members, Claim_IPHeader, Claim_Pharmacy and DailyCensus — and picking
    the first would map a claims column into the roster. Where the term is
    ambiguous about WHERE, the platform declines to settle and the model is
    asked, with the term's definition in front of it.
    """
    if len(term.mapped_tables) != 1 or not term.mapped_columns_corrected:
        return None
    return term.mapped_tables[0], term.mapped_columns_corrected[0]
