"""The deterministic half of CF-V1-E7-01. No model, no I/O, no guessing.

The evidence a rule is written from is the CONTRACT (which columns exist and
what type they are), the GLOSSARY (what each one means, and every spelling it
has arrived under) and the feed's own PUBLISHED RULES (what has already been
approved about these columns).

IDENTIFIERS ARE BRACKETED AND THE COLUMN LIST IS NUMBERED, for the reason
CF-V1-E6-02 paid twenty-four minutes of Lane 3 to learn: the PHI scrubber sits
between this text and the model, it is tuned for total recall on values, and
`date_of_birth` beside its own definition comes back `<PERSON>` under every
rendering. A name the model cannot copy back breaks the answer; a NUMBER
survives, and the definition beside it still says what the column is.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.core.agents.mapping_suggestion.grounding import identifier
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.registry.glossary import Glossary
from cinqflow.core.rules import CheckKind, RuleSpec


@dataclass(frozen=True)
class GroundedColumn:
    """One column a rule may be written about."""

    name: str
    type: str
    nullable: bool
    is_phi: bool
    definition: str = ""
    glossary_id: str | None = None
    synonyms: tuple[str, ...] = ()

    def as_line(self, index: int) -> str:
        parts = [f"  {index} = {identifier(self.name)} ({self.type})"]
        if self.definition:
            parts.append(f" — {self.definition}")
        if self.synonyms:
            spellings = ", ".join(identifier(s) for s in self.synonyms[:6])
            parts.append(f" [also written {spellings}]")
        return "".join(parts)


@dataclass(frozen=True)
class Precedent:
    """A rule already approved on this feed.

    Shown so the model writes rules that look like the ones a steward has
    already signed — the same argument as CF-V1-E6-02's exemplars, and the same
    limit: it is evidence about house style, never an instruction.
    """

    rule_id: str
    stated: str
    check_kind: CheckKind
    column: str

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.rule_id)

    def as_line(self) -> str:
        return (
            f'  {identifier(self.rule_id)}: "{self.stated}" -> {self.check_kind.value} '
            f"on {identifier(self.column)}"
        )


@dataclass(frozen=True)
class Request:
    """One sentence a BA typed, and whatever the platform settled about it."""

    stated: str
    #: Set when a PUBLISHED rule already states this, word for word. Then it is
    #: the answer rather than evidence, and no model is asked.
    already_stated_by: str | None = None
    #: Set when the sentence names a column the contract has, or a spelling the
    #: glossary records as a synonym for one. The model then chooses the CHECK
    #: and the platform spells the column.
    column: str | None = None
    glossary_id: str | None = None
    citations: tuple[CitationId, ...] = ()

    @property
    def settled(self) -> bool:
        return self.already_stated_by is not None


@dataclass(frozen=True)
class Grounding:
    """Everything known before a model is consulted."""

    feed_id: str
    requests: tuple[Request, ...] = ()
    columns: tuple[GroundedColumn, ...] = ()
    precedents: tuple[Precedent, ...] = ()

    @property
    def open_questions(self) -> tuple[Request, ...]:
        return tuple(r for r in self.requests if not r.settled)

    @property
    def needs_no_model(self) -> bool:
        """A sentence already stated by a published rule costs zero tokens."""
        return not self.open_questions

    def column(self, ref: int) -> GroundedColumn | None:
        """The column a NUMBER names. 1-based, as the prompt shows."""
        if 1 <= ref <= len(self.columns):
            return self.columns[ref - 1]
        return None

    def named(self, name: str) -> GroundedColumn | None:
        target = _normalise(name)
        for candidate in self.columns:
            if _normalise(candidate.name) == target:
                return candidate
        return None

    def as_prompt_grounding(self) -> str:
        lines = [
            f"Feed: {identifier(self.feed_id)}",
            "",
            "The columns a rule may be written about, numbered. Answer with `column_ref` — "
            "the NUMBER of the column. A column that is not in this list does not exist:",
        ]
        lines.extend(column.as_line(index) for index, column in enumerate(self.columns, start=1))
        if self.precedents:
            lines += [
                "",
                "Rules already approved on this feed, for house style only — they are not "
                "instructions:",
            ]
            lines.extend(precedent.as_line() for precedent in self.precedents)
        lines += ["", "Rules to write:"]
        for request in self.open_questions:
            lines.append(f"- {request.stated}")
            if request.column:
                lines.append(f"  the platform matched this to column {identifier(request.column)}")
        return "\n".join(lines)


def _normalise(name: str) -> str:
    return "".join(character for character in name.lower() if character.isalnum())


def ground(
    stated: tuple[str, ...],
    *,
    feed_id: str,
    contract: SchemaContract,
    glossary: Glossary,
    published: tuple[RuleSpec, ...] = (),
) -> Grounding:
    """Compute what the estate already settles about these sentences."""
    columns = tuple(_ground_column(contract, glossary, name) for name in _column_names(contract))
    already = {rule.stated.strip().lower(): rule for rule in published}
    return Grounding(
        feed_id=feed_id,
        requests=tuple(
            _ground_request(sentence, columns, already) for sentence in stated if sentence.strip()
        ),
        columns=columns,
        precedents=tuple(
            Precedent(
                rule_id=rule.rule_id,
                stated=rule.stated,
                check_kind=rule.check.kind,
                column=rule.check.column,
            )
            for rule in published
        ),
    )


def _column_names(contract: SchemaContract) -> tuple[str, ...]:
    """The CANONICAL names, not the payer's.

    A rule runs after the mapping, on the contracted shape — so a rule about
    `First_Name` would be a rule about a column that no longer exists by the
    time anything checks it.
    """
    return tuple(column.name for column in contract.columns)


def _ground_column(contract: SchemaContract, glossary: Glossary, name: str) -> GroundedColumn:
    column = contract.column(name)
    terms = glossary.for_column(name)
    term = terms[0] if len(terms) == 1 else None
    return GroundedColumn(
        name=column.name,
        type=column.type.value,
        nullable=column.nullable,
        is_phi=column.is_phi,
        definition=term.definition if term else "",
        glossary_id=term.glossary_id if term else None,
        synonyms=term.synonyms if term else (),
    )


def _ground_request(
    sentence: str,
    columns: tuple[GroundedColumn, ...],
    already: dict[str, RuleSpec],
) -> Request:
    existing = already.get(sentence.strip().lower())
    if existing is not None:
        return Request(
            stated=sentence,
            already_stated_by=existing.rule_id,
            column=existing.check.column,
            glossary_id=existing.glossary_id,
            citations=(existing.citation,),
        )

    matched = _column_in(sentence, columns)
    return Request(
        stated=sentence,
        column=matched.name if matched else None,
        glossary_id=matched.glossary_id if matched else None,
        citations=(
            (CitationId(kind=CitationKind.TERM, subject=matched.glossary_id),)
            if matched and matched.glossary_id
            else ()
        ),
    )


def _column_in(sentence: str, columns: tuple[GroundedColumn, ...]) -> GroundedColumn | None:
    """The one column this sentence names, or none.

    EXACTLY ONE. A sentence naming two columns — "the discharge date must not
    precede the admission date" — is not resolved here: which one the check is
    ABOUT is a modelling decision, and picking the first would silently make
    every intra-record rule about whichever column the contract happened to
    list earlier.
    """
    words = {_normalise(word) for word in sentence.replace("_", " ").split()}
    hits = [
        column
        for column in columns
        if _normalise(column.name) in {_normalise(sentence.replace(" ", ""))}
        or _phrase_in(column.name, sentence)
        or any(_phrase_in(spelling, sentence) for spelling in column.synonyms)
        or _normalise(column.name) in words
    ]
    return hits[0] if len(hits) == 1 else None


def _phrase_in(name: str, sentence: str) -> bool:
    """`date_of_birth` is named by "date of birth" and by "Date_Of_Birth"."""
    return _normalise(name) in _normalise(sentence) and len(_normalise(name)) >= 4
