"""CF-V1-E6-03 — the mapping object, and the transform taxonomy as DATA.

    "Manual mapping editor — full transform taxonomy (cast, split, lookup,
     conditional, default, null handling)"
    "Humans must be able to do by hand everything the AI proposes — the editor
     is the fallback and the correction surface."
    — CF-V1-E6-03

BUILT BEFORE CF-V1-E6-02, DELIBERATELY, AND THE STORY SAYS WHY. The AI mapping
agent proposes mappings; if it ships first it must invent a vocabulary to
propose INTO, and the manual editor then arrives to reproduce whatever the
agent happened to emit. Building the taxonomy first makes the story's own claim
— "humans must be able to do by hand everything the AI proposes" — true by
construction rather than by later reconciliation.

THE TAXONOMY IS HARVESTED, NOT INVENTED. It comes from the client's own
source-to-Silver-Raw workbooks, which already carry a status vocabulary
(`✔ DIRECT`, `~ TRANSFORM`, `⇒ SPLIT`, `∑ DERIVED`, `✘ NO MAP`, `AUDIT`) and
1,531 transform notes. Reading them settled three things a designed-from-scratch
taxonomy gets wrong:

  • CONSTANT is real and common. The enrollment workbook's `Values` column
    carries `D0284`, `"Primary"`, `FIDELIS` — a target field populated by a
    literal, with no source at all.
  • LOOKUP is real and finite. The claims workbook ships a `reference` sheet of
    70 code translations (`Physician_type`, `Denial_reason`).
  • UNMAPPED IS A DECISION, NOT AN ABSENCE. `CCLF_to_SilverRaw_Mapping` has a
    whole `NO MAP Fields` sheet whose last column is `Reason`. The client's own
    analysts already record why a field has no target. A line that is silently
    missing and a line explicitly unmapped are different facts, and only the
    second one can be reviewed.

A TRANSFORM IS DATA, NEVER CODE. There is no `expression` field anywhere in
this module, and no string on a mapping is ever evaluated. The kinds are a
closed enum and their parameters are scalars, which is the same guarantee
`rules_from_governed` makes for DQ rules: a registry row cannot change what
code runs, so nobody can smuggle logic past an approver by editing a
configuration field. The engine is handed a `Transform` and matches on its
kind; there is no path from a mapping body to an interpreter.

MAPPING RUNS IN CORE, SO IT RUNS IDENTICALLY ON BOTH PLANES. `apply` is pure —
no I/O, no engine — for the reason the date normaliser is: dual rendering means
the local plane and the cluster must agree byte-for-byte, and the only way to
guarantee that is for the semantics to live in one place neither of them owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.registry.canonical import CanonicalModel
from cinqflow.core.registry.contract import SchemaContract
from cinqflow.core.schema_spec import TypeName


class MappingError(RuntimeError):
    """A mapping that could not be built, or could not be run."""


class TransformShapeError(MappingError):
    """A transform carrying parameters its kind has no use for, or missing one
    it cannot work without.

    Refused at construction rather than at execution. A LOOKUP with no table is
    not a mapping that fails at 3am — it is a mapping that should never have
    been storable.
    """


class UnlistedCodeError(MappingError):
    """A LOOKUP met a code its table does not contain, and the mapping says to
    reject the row rather than guess.

    Carried as an attributed drop, exactly like a cast failure: the row leaves
    the pipeline with a reason naming the column and the value's ABSENCE from
    the table. Never the value itself — an unlisted code in an error message is
    a payer's data in a log.
    """


@unique
class TransformKind(StrEnum):
    """How a target field is populated. The whole vocabulary.

    Closed on purpose. A new kind costs an entry in `_SHAPE`, a branch in
    `apply`, a control in the editor and a line in the agent's prompt — and
    that toll is what stops the taxonomy growing an `expression` escape hatch
    the first time something does not fit.
    """

    #: Copy, possibly under a different name. The client's `✔ DIRECT`, and by
    #: far the most common line in every workbook they have written.
    DIRECT = "direct"
    #: Convert to the target's type. `Members.Part_A_Begin_Date datetime ->
    #: patient.part_a_enrollment_begin_date DATE. Type cast.`
    CAST = "cast"
    #: One part of a delimited source value. The client's `⇒ SPLIT`.
    SPLIT = "split"
    #: Several sources joined. Rare in the corpus but real, and the inverse of
    #: SPLIT — omitting it would make one direction of a two-way mapping
    #: unexpressible.
    CONCAT = "concat"
    #: A finite code table. `CMS race code decode applies for CCLF-sourced
    #: members`; the workbook's `reference` sheet is 70 rows of exactly this.
    LOOKUP = "lookup"
    #: When/then over the source value. The scope columns
    #: (`IP H, IP L, OP H...`) are this.
    CONDITIONAL = "conditional"
    #: A literal, with no source at all. The enrollment workbook's `Values`
    #: column: `D0284`, `"Primary"`, `FIDELIS`.
    CONSTANT = "constant"


@unique
class NullPolicy(StrEnum):
    """What happens when the source is absent or empty. A SECOND AXIS.

    Deliberately not folded into `TransformKind`. Every kind needs a null
    answer — a CAST of an empty string and a LOOKUP of a missing code are the
    same question asked twice — and modelling it as a kind would multiply the
    taxonomy by four while making "what does this mapping do about nulls?"
    unanswerable without reading each line's kind first.
    """

    #: Null in, null out. The default, and correct for a nullable target.
    PASS_THROUGH = "pass_through"  # noqa: S105 - a null policy, not a credential
    #: Use `default_value`. The client's "set TRUE for all non-voided claims
    #: on initial load" is this.
    SUBSTITUTE = "substitute"
    #: Try the next source column. `OP service date -> service_date (date).
    #: COALESCE with service_from_date`, verbatim from the workbook.
    COALESCE = "coalesce"
    #: The row cannot be built without it. Attributed, never silently dropped.
    REJECT_ROW = "reject_row"


@unique
class UnlistedCode(StrEnum):
    """What a LOOKUP does with a code its table does not list.

    A SEPARATE DECISION FROM `NullPolicy`, because "the payer sent nothing" and
    "the payer sent a code we have never seen" are different events and want
    different answers. The 70-row `reference` sheet in the client's claims
    workbook will meet its 71st code; what happens then is a decision somebody
    should make once, visibly, rather than discover.
    """

    #: The row is attributed and quarantined. The safe answer for a code that
    #: drives clinical or financial meaning.
    REJECT_ROW = "reject_row"
    #: Keep the payer's own code. Honest, and readable downstream as "not
    #: translated" rather than as a translation that happens to be wrong.
    PASS_THROUGH = "pass_through"  # noqa: S105 - a lookup policy, not a credential
    #: Use `default_value` — an explicit `unknown` bucket.
    SUBSTITUTE = "substitute"


#: Which parameters each kind REQUIRES and which it MAY carry. Data, so the
#: shape rules are readable in one place and a new kind is a row rather than a
#: new branch of a growing conditional.
_SHAPE: dict[TransformKind, tuple[frozenset[str], frozenset[str]]] = {
    TransformKind.DIRECT: (frozenset(), frozenset()),
    TransformKind.CAST: (frozenset({"target_type"}), frozenset({"date_format"})),
    TransformKind.SPLIT: (frozenset({"separator", "part"}), frozenset({"target_type"})),
    TransformKind.CONCAT: (frozenset(), frozenset({"separator"})),
    TransformKind.LOOKUP: (
        frozenset({"lookup"}),
        frozenset({"on_unlisted", "default_value", "target_type"}),
    ),
    TransformKind.CONDITIONAL: (frozenset({"cases"}), frozenset({"default_value"})),
    TransformKind.CONSTANT: (frozenset({"literal"}), frozenset({"target_type"})),
}


@dataclass(frozen=True)
class Case:
    """One branch of a CONDITIONAL: when the source equals any of `when_in`,
    the value is `then`.

    Equality against a listed set, never a predicate. The moment a case can
    hold an expression, a mapping can hold code — and the approver of a
    mapping is reviewing configuration, not reading a language.
    """

    when_in: tuple[str, ...]
    then: str

    def matches(self, value: str) -> bool:
        return value in self.when_in


@dataclass(frozen=True)
class Transform:
    """What to do with the source value(s). Parameters only — never code.

    Validated on construction against `_SHAPE`, so a transform that could not
    run cannot be stored, let alone approved.
    """

    kind: TransformKind = TransformKind.DIRECT
    target_type: TypeName | None = None
    date_format: str | None = None
    separator: str | None = None
    part: int | None = None
    #: Source value -> target value. A tuple of pairs rather than a dict so the
    #: transform stays hashable and its order survives the JSONB round trip —
    #: a reviewer comparing two versions of a lookup needs stable ordering.
    lookup: tuple[tuple[str, str], ...] = ()
    on_unlisted: UnlistedCode = UnlistedCode.REJECT_ROW
    cases: tuple[Case, ...] = ()
    literal: str | None = None
    default_value: str | None = None

    def __post_init__(self) -> None:
        required, optional = _SHAPE[self.kind]
        present = {
            name
            for name in ("target_type", "date_format", "separator", "part", "literal")
            if getattr(self, name) is not None
        }
        if self.lookup:
            present.add("lookup")
        if self.cases:
            present.add("cases")
        if self.default_value is not None:
            present.add("default_value")

        if missing := required - present:
            raise TransformShapeError(
                f"a {self.kind.value} transform needs {', '.join(sorted(missing))} and none "
                "was given — a transform that cannot run should not be storable"
            )
        if extra := present - required - optional:
            raise TransformShapeError(
                f"a {self.kind.value} transform has no use for {', '.join(sorted(extra))}. "
                "Carrying it would put a parameter on the review screen that changes nothing, "
                "which is how a reviewer learns to stop reading them."
            )
        if self.kind is TransformKind.SPLIT and (self.part is None or self.part < 1):
            raise TransformShapeError("split parts are 1-based; part 0 is not a part")
        if self.kind is TransformKind.LOOKUP:
            if self.on_unlisted is UnlistedCode.SUBSTITUTE and self.default_value is None:
                raise TransformShapeError(
                    "a lookup that substitutes for unlisted codes needs a default_value — "
                    "otherwise 'substitute' means 'silently null', which is the outcome the "
                    "setting exists to avoid"
                )
            if len({code for code, _ in self.lookup}) != len(self.lookup):
                raise TransformShapeError(
                    "a lookup table lists the same source code twice. Which translation wins "
                    "would then depend on ordering, and nobody reviewing it would know."
                )

    @property
    def is_deterministic_rename(self) -> bool:
        """DIRECT with nothing else. The line a reviewer can approve at a glance."""
        return self.kind is TransformKind.DIRECT

    def describe(self) -> str:
        """One line of plain English. What the editor shows and what the
        approval packet's diff reads like — an approver comparing two mapping
        versions should not have to parse JSON."""
        match self.kind:
            case TransformKind.DIRECT:
                return "copied as-is"
            case TransformKind.CAST:
                fmt = f" using format {self.date_format}" if self.date_format else ""
                kind = self.target_type.value if self.target_type else "?"
                return f"cast to {kind}{fmt}"
            case TransformKind.SPLIT:
                return f"split on {self.separator!r}, part {self.part}"
            case TransformKind.CONCAT:
                return f"joined with {self.separator!r}" if self.separator else "joined"
            case TransformKind.LOOKUP:
                return (
                    f"translated through a {len(self.lookup)}-code table; an unlisted code "
                    f"{_UNLISTED_WORDS[self.on_unlisted]}"
                )
            case TransformKind.CONDITIONAL:
                return f"{len(self.cases)} when/then case(s)"
            case TransformKind.CONSTANT:
                return f"always {self.literal!r}"


_UNLISTED_WORDS: dict[UnlistedCode, str] = {
    UnlistedCode.REJECT_ROW: "quarantines the row",
    UnlistedCode.PASS_THROUGH: "is kept untranslated",
    UnlistedCode.SUBSTITUTE: "becomes the default",
}


@unique
class LineStatus(StrEnum):
    """What a target field's mapping IS. Computed, never declared.

    The client's workbooks carry this as a typed column, and typed columns
    drift from their rows — a line whose sources were deleted keeps saying
    `MAPPED` until somebody notices. So it is a property of the line's
    contents, which makes the two impossible to disagree. The one thing a
    human must still declare is `platform_supplied`, because only a person
    knows that `batch_id` is the pipeline's rather than the payer's.
    """

    MAPPED = "mapped"
    CONSTANT = "constant"
    #: The client's `AUDIT` status: `batch_id`, `record_hash`, `is_deleted` —
    #: written by the pipeline, correctly absent from every source.
    PLATFORM_SUPPLIED = "platform_supplied"
    #: Nothing populates it, and the reason is REQUIRED.
    UNMAPPED = "unmapped"


@dataclass(frozen=True)
class MappingLine:
    """One target field, and where its value comes from.

    Keyed by the TARGET, not the source. A source column may feed two targets
    and a target may draw on two sources, but a target field has exactly one
    answer to "what populates this?" — and that is the question a reviewer, a
    lineage graph and a row-loss investigation all ask.
    """

    target_entity: str
    target_field: str
    source_columns: tuple[str, ...] = ()
    transform: Transform = field(default_factory=Transform)
    null_policy: NullPolicy = NullPolicy.PASS_THROUGH
    default_value: str | None = None
    #: Written by the pipeline, not read from the source. The client's `AUDIT`.
    platform_supplied: bool = False
    #: REQUIRED when nothing populates the field. See the module docstring: the
    #: client's own `NO MAP Fields` sheet has a `Reason` column, because a field
    #: with no source is a decision somebody made and should have to defend.
    unmapped_reason: str = ""
    #: The business term this line realises, where one does. Carried so the
    #: mapping's lineage reaches the glossary and `core.impact` can tell a
    #: steward that changing `BG-004` touches eleven mappings.
    glossary_id: str | None = None
    notes: str = ""
    confidence: float | None = None
    #: Where the proposal that produced this line got its idea. Empty for a
    #: hand-authored line, which is itself worth seeing on the screen.
    citations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.target_field.strip():
            raise MappingError("a mapping line with no target field maps nothing")
        # Widened to `object` deliberately: the annotation says tuple, and a
        # type checker is therefore certain this cannot happen — but the
        # callers that matter are JSON bodies and hand-written fixtures, which
        # a type checker never sees. A string is iterable, so
        # `source_columns="facility_code"` quietly becomes thirteen one-letter
        # column names and maps from none of them, with no error anywhere.
        # Refused rather than coerced: guessing that a bare string meant one
        # column is how a mapping ends up reading a column nobody named.
        columns: object = self.source_columns
        if isinstance(columns, str):
            raise MappingError(
                f"{self.address}: source_columns is a string, not a list of columns. "
                f"A bare string iterates letter by letter — write ({columns!r},)."
            )
        if self.status is LineStatus.UNMAPPED and not self.unmapped_reason.strip():
            raise MappingError(
                f"{self.address} has no source and no reason. An unmapped field is a "
                "DECISION — the client's own workbooks record why, and a blank here is the "
                "difference between 'we looked and there is nothing' and 'nobody got to it'."
            )
        if self.null_policy is NullPolicy.SUBSTITUTE and self.default_value is None:
            raise MappingError(
                f"{self.address} substitutes on null with nothing to substitute — which "
                "means it passes null through while claiming not to"
            )
        if self.null_policy is NullPolicy.COALESCE and len(self.source_columns) < 2:
            raise MappingError(
                f"{self.address} coalesces over one source. Coalescing needs somewhere to "
                "fall back to; with one column it is pass-through under another name."
            )
        if self.transform.kind is TransformKind.CONSTANT and self.source_columns:
            raise MappingError(
                f"{self.address} is a constant AND reads {', '.join(self.source_columns)}. "
                "One of those is a lie, and a reviewer cannot tell which."
            )
        if self.transform.kind is TransformKind.CONCAT and len(self.source_columns) < 2:
            raise MappingError(f"{self.address} concatenates fewer than two columns")

    @property
    def address(self) -> str:
        return f"{self.target_entity}.{self.target_field}"

    @property
    def status(self) -> LineStatus:
        if self.platform_supplied:
            return LineStatus.PLATFORM_SUPPLIED
        if self.transform.kind is TransformKind.CONSTANT:
            return LineStatus.CONSTANT
        if self.source_columns:
            return LineStatus.MAPPED
        return LineStatus.UNMAPPED

    @property
    def is_mapped(self) -> bool:
        """Something populates this field. Counted for coverage."""
        return self.status is not LineStatus.UNMAPPED

    @property
    def reads_from(self) -> str:
        """The primary source, for the common one-source case."""
        return self.source_columns[0] if self.source_columns else ""

    def describe(self) -> str:
        """The whole line in one sentence, for the editor and the diff."""
        if self.status is LineStatus.UNMAPPED:
            return f"{self.address}: not mapped — {self.unmapped_reason}"
        if self.status is LineStatus.PLATFORM_SUPPLIED:
            return f"{self.address}: supplied by the pipeline, not read from the source"
        if self.status is LineStatus.CONSTANT:
            return f"{self.address}: {self.transform.describe()}"
        sources = ", ".join(self.source_columns)
        nulls = _NULL_WORDS[self.null_policy]
        return f"{self.address}: from {sources}, {self.transform.describe()}; {nulls}"


_NULL_WORDS: dict[NullPolicy, str] = {
    NullPolicy.PASS_THROUGH: "an empty source stays empty",
    NullPolicy.SUBSTITUTE: "an empty source becomes the default",
    NullPolicy.COALESCE: "an empty source falls back to the next column",
    NullPolicy.REJECT_ROW: "an empty source quarantines the row",
}


#: What `apply` returns when the mapping says the row cannot be built. A
#: sentinel rather than an exception at the value level, so the caller decides
#: whether one unusable field fails a row or attributes it — and a distinct
#: object rather than None, because None is a perfectly good mapped value.
class _Reject:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "REJECT_ROW"


REJECT = _Reject()


def apply(line: MappingLine, row: dict[str, str]) -> object:
    """Run ONE mapping line over ONE source row. Pure, and therefore identical
    on both planes.

    Returns the mapped value, `None` for a legitimately empty field, or
    `REJECT` when the line's own policy says the row cannot be built. Casting
    is deliberately NOT done here — `core.registry.contract.cast_value` already
    owns type conversion for the whole platform, and a second caster would be a
    second definition of what `19900101` means.
    """
    if line.status is LineStatus.PLATFORM_SUPPLIED:
        return None
    if line.transform.kind is TransformKind.CONSTANT:
        return line.transform.literal

    raw = _read(line, row)
    if raw is None:
        match line.null_policy:
            case NullPolicy.REJECT_ROW:
                return REJECT
            case NullPolicy.SUBSTITUTE:
                return line.default_value
            case _:
                return None
    return _transform(line.transform, raw, row, line)


def _read(line: MappingLine, row: dict[str, str]) -> str | None:
    """The source value, honouring COALESCE. None when nothing populated it."""
    coalescing = line.null_policy is NullPolicy.COALESCE
    columns = line.source_columns if coalescing else line.source_columns[:1]
    if line.transform.kind is TransformKind.CONCAT:
        columns = line.source_columns
    for column in columns:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return None


def _transform(transform: Transform, raw: str, row: dict[str, str], line: MappingLine) -> object:
    match transform.kind:
        case TransformKind.DIRECT | TransformKind.CAST:
            # CAST returns the STRING; `cast_value` converts it against the
            # contract column, so the mapping declares intent and the contract
            # owns the arithmetic. Two casters would be two answers.
            return raw
        case TransformKind.CONCAT:
            separator = transform.separator or ""
            parts = [(row.get(c) or "").strip() for c in line.source_columns]
            return separator.join(p for p in parts if p)
        case TransformKind.SPLIT:
            pieces = raw.split(transform.separator or "")
            index = (transform.part or 1) - 1
            return pieces[index].strip() if index < len(pieces) else None
        case TransformKind.LOOKUP:
            for code, translated in transform.lookup:
                if code == raw:
                    return translated
            match transform.on_unlisted:
                case UnlistedCode.REJECT_ROW:
                    return REJECT
                case UnlistedCode.PASS_THROUGH:
                    return raw
                case UnlistedCode.SUBSTITUTE:
                    return transform.default_value
        case TransformKind.CONDITIONAL:
            for case in transform.cases:
                if case.matches(raw):
                    return case.then
            return transform.default_value
        case TransformKind.CONSTANT:  # pragma: no cover - handled in `apply`
            return transform.literal


# ── the mapping, as a whole ──────────────────────────────────────────────────


@unique
class FindingSeverity(StrEnum):
    """Whether a validation finding stops a mapping being submitted."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class MappingFinding:
    """One thing wrong, or worth knowing, about a mapping.

    Three strings for the same reason `ChecklistItem` has three: a finding that
    only names a field gets a placeholder typed into it.
    """

    key: str
    address: str
    severity: FindingSeverity
    what: str
    why_it_matters: str
    how_to_fix: str

    @property
    def blocks(self) -> bool:
        return self.severity is FindingSeverity.BLOCKING


@dataclass(frozen=True)
class FeedMapping:
    """Every target field for one feed, and how each is populated."""

    feed_id: str
    version: int = 1
    contract_version: int | None = None
    lines: tuple[MappingLine, ...] = ()

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.MAPPING, subject=self.feed_id, version=self.version)

    @property
    def mapped(self) -> tuple[MappingLine, ...]:
        return tuple(line for line in self.lines if line.is_mapped)

    @property
    def unmapped(self) -> tuple[MappingLine, ...]:
        """Every field somebody decided has no source, with their reasons.

        A first-class list rather than a filter at each call site: this is what
        a reviewer reads first, and CF-V1-E6-02 writes into.
        """
        return tuple(line for line in self.lines if not line.is_mapped)

    @property
    def coverage(self) -> tuple[int, int]:
        """(mapped, total). Two integers, so a reader can recompute the rate."""
        return len(self.mapped), len(self.lines)

    @property
    def source_columns(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for line in self.lines:
            for column in line.source_columns:
                seen.setdefault(column, None)
        return tuple(seen)

    @property
    def glossary_ids(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for line in self.lines:
            if line.glossary_id:
                seen.setdefault(line.glossary_id, None)
        return tuple(sorted(seen))

    def line(self, target_entity: str, target_field: str) -> MappingLine | None:
        for candidate in self.lines:
            if (
                candidate.target_entity.lower() == target_entity.lower()
                and candidate.target_field.lower() == target_field.lower()
            ):
                return candidate
        return None

    def apply_to(self, row: dict[str, str]) -> tuple[dict[str, object], MappingLine | None]:
        """Map one source row. Returns the mapped row, or the line that
        rejected it.

        The FIRST rejecting line wins, for the reason `_first_broken_rule`
        gives: a row can only be dropped once, and attributing it to three
        lines would treble-count it and break the ledger's balance."""
        mapped: dict[str, object] = {}
        for line in self.lines:
            value = apply(line, row)
            if value is REJECT:
                return {}, line
            if line.is_mapped:
                mapped[line.target_field] = value
        return mapped, None


def validate(
    mapping: FeedMapping,
    *,
    contract: SchemaContract | None = None,
    model: CanonicalModel | None = None,
) -> tuple[MappingFinding, ...]:
    """Check a mapping against both of its ends.

    BOTH ends, deliberately. A mapping is the only object in the platform that
    references a source and a target at once, so it is the only place where
    "this reads a column the contract does not have" and "this writes a field
    nobody has deployed" can be caught at authoring time rather than at 3am on
    the first delivery.

    Either end may be omitted — a BA drafting against a feed whose contract is
    still in review needs to be able to save — and what is checkable is checked.
    """
    findings: list[MappingFinding] = []
    findings.extend(_duplicate_targets(mapping))
    if contract is not None:
        findings.extend(_source_findings(mapping, contract))
    if model is not None:
        findings.extend(_target_findings(mapping, model))
    if contract is not None and model is not None:
        findings.extend(_phi_findings(mapping, contract, model))
    return tuple(findings)


def _duplicate_targets(mapping: FeedMapping) -> list[MappingFinding]:
    seen: dict[str, int] = {}
    for line in mapping.lines:
        seen[line.address.lower()] = seen.get(line.address.lower(), 0) + 1
    return [
        MappingFinding(
            key="duplicate_target",
            address=address,
            severity=FindingSeverity.BLOCKING,
            what=f"{address} is mapped {count} times",
            why_it_matters=(
                "Which line wins would depend on ordering, so the field's value would be "
                "decided by something nobody approved."
            ),
            how_to_fix="Delete the duplicates, or merge them into one line with a transform.",
        )
        for address, count in sorted(seen.items())
        if count > 1
    ]


def _source_findings(mapping: FeedMapping, contract: SchemaContract) -> list[MappingFinding]:
    available = {c.name for c in contract.columns} | set(contract.source_columns)
    findings: list[MappingFinding] = []
    for line in mapping.lines:
        for column in line.source_columns:
            if column in available:
                continue
            findings.append(
                MappingFinding(
                    key="unknown_source",
                    address=line.address,
                    severity=FindingSeverity.BLOCKING,
                    what=f"reads {column!r}, which the contract does not have",
                    why_it_matters=(
                        "The column would be empty on every row, so the field would silently "
                        "arrive null — which reads downstream as missing data rather than as "
                        "a broken mapping."
                    ),
                    how_to_fix=(
                        f"Pick a column the contract declares, or add {column!r} to the "
                        "contract and have it approved."
                    ),
                )
            )
    return findings


def _target_findings(mapping: FeedMapping, model: CanonicalModel) -> list[MappingFinding]:
    findings: list[MappingFinding] = []
    for line in mapping.lines:
        entity = model.entity(line.target_entity)
        if entity is None:
            findings.append(
                MappingFinding(
                    key="unknown_entity",
                    address=line.address,
                    severity=FindingSeverity.BLOCKING,
                    what=f"{line.target_entity!r} is not an entity in the canonical model",
                    why_it_matters=(
                        "There is nowhere for these values to land, so the mapping cannot run."
                    ),
                    how_to_fix="Choose an entity from the canonical browser.",
                )
            )
            continue
        target = entity.field(line.target_field)
        if target is None:
            findings.append(
                MappingFinding(
                    key="unknown_field",
                    address=line.address,
                    severity=FindingSeverity.BLOCKING,
                    what=f"{line.target_entity} has no field {line.target_field!r}",
                    why_it_matters="There is nowhere for these values to land.",
                    how_to_fix="Choose a field from the canonical browser.",
                )
            )
            continue
        if not target.deployed and line.is_mapped:
            # ADVISORY, not blocking. The client has designed twenty entities
            # and deployed one; refusing to map against the rest would make the
            # mapping studio unusable until Wave 3. What matters is that
            # nobody is surprised.
            findings.append(
                MappingFinding(
                    key="target_not_deployed",
                    address=line.address,
                    severity=FindingSeverity.ADVISORY,
                    what="the target field is designed but not yet provisioned",
                    why_it_matters=(
                        "The mapping is valid and cannot run yet — this is the roadmap "
                        "showing through, not a mistake."
                    ),
                    how_to_fix="Nothing now. It becomes runnable when the entity is deployed.",
                )
            )
    return findings


def _phi_findings(
    mapping: FeedMapping, contract: SchemaContract, model: CanonicalModel
) -> list[MappingFinding]:
    """THE ONE THAT ONLY A MAPPING CAN CATCH.

    CF-V1-E5-03 flags a source column as PHI; CF-V4-E2-03 masks canonical
    fields the glossary flags. A mapping that carries a protected source column
    into a target field nothing flags moves the value from inside the masking
    policy to outside it — and every screen downstream then shows it in clear,
    with no rule broken anywhere. Neither end can see this on its own, because
    the mapping IS the crossing.
    """
    phi_sources = {c.name for c in contract.columns if c.is_phi} | {
        c.reads_from for c in contract.columns if c.is_phi
    }
    findings: list[MappingFinding] = []
    for line in mapping.lines:
        carried = sorted(set(line.source_columns) & phi_sources)
        if not carried:
            continue
        entity = model.entity(line.target_entity)
        target = entity.field(line.target_field) if entity else None
        if target is None or target.is_phi:
            continue
        findings.append(
            MappingFinding(
                key="phi_laundering",
                address=line.address,
                severity=FindingSeverity.BLOCKING,
                what=(
                    f"carries protected column(s) {', '.join(carried)} into a target field "
                    "nothing marks as PHI"
                ),
                why_it_matters=(
                    "Masking reads the TARGET's flag. Landing protected values in an "
                    "unflagged field takes them out of the masking policy without breaking "
                    "any rule — every screen downstream would show them in clear."
                ),
                how_to_fix=(
                    "Map to a field the glossary flags, or have a steward flag this one "
                    "and say why the value is no longer protected."
                ),
            )
        )
    return findings


def blocking(findings: tuple[MappingFinding, ...]) -> tuple[MappingFinding, ...]:
    return tuple(f for f in findings if f.blocks)


# ── the mapping as a governed object ─────────────────────────────────────────
#
# Same lifecycle as everything else (ADR-0006), routed to the DATA STEWARD by
# `core.lifecycle.APPROVAL_ROUTING`. The body keys are the ones
# `core.impact.REFERENCES` already declares for a MAPPING — `feed_id`,
# `contract_id`, `glossary_ids` — so lineage works the moment the object is
# stored, with no second declaration to keep in step.


def mapping_body(
    mapping: FeedMapping, *, business_consumers: tuple[str, ...] = ()
) -> dict[str, Any]:
    """The governed body, without the envelope.

    Separate from `mapping_as_governed` because CF-V1-E6-02's approval path
    needs the body alone — `core.proposals.apply` supplies the author, the
    version and the DRAFT state, and it is the approver who authors it. A
    caller forced to invent a placeholder actor just to reach a dict would be
    one edit away from that placeholder ending up on a real row.
    """
    return {
        "feed_id": mapping.feed_id,
        "contract_id": mapping.feed_id if mapping.contract_version else None,
        "contract_version": mapping.contract_version,
        "glossary_ids": list(mapping.glossary_ids),
        "business_consumers": list(business_consumers),
        "lines": [line_to_dict(line) for line in mapping.lines],
    }


def mapping_as_governed(
    mapping: FeedMapping,
    *,
    author: Actor,
    created_ts: datetime | None = None,
    business_consumers: tuple[str, ...] = (),
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.MAPPING,
        object_id=mapping.feed_id,
        version=mapping.version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=created_ts or datetime.now(UTC),
        body=mapping_body(mapping, business_consumers=business_consumers),
    )


def from_governed(obj: GovernedObject) -> FeedMapping:
    if obj.object_type is not ObjectType.MAPPING:
        raise MappingError(f"{obj.object_type} is not a mapping")
    return FeedMapping(
        feed_id=obj.object_id,
        version=obj.version,
        contract_version=obj.body.get("contract_version"),
        lines=tuple(line_from_dict(raw) for raw in obj.body.get("lines", ())),
    )


def line_to_dict(line: MappingLine) -> dict[str, Any]:
    return {
        "target_entity": line.target_entity,
        "target_field": line.target_field,
        "source_columns": list(line.source_columns),
        "transform": transform_to_dict(line.transform),
        "null_policy": line.null_policy.value,
        "default_value": line.default_value,
        "platform_supplied": line.platform_supplied,
        "unmapped_reason": line.unmapped_reason,
        "glossary_id": line.glossary_id,
        "notes": line.notes,
        "confidence": line.confidence,
        "citations": list(line.citations),
        # DERIVED, and written anyway. The status is computed on the way back
        # in — this copy exists so a human reading the stored JSON, or a query
        # counting unmapped fields, does not have to re-implement the rule.
        "status": line.status.value,
    }


def line_from_dict(raw: dict[str, Any]) -> MappingLine:
    return MappingLine(
        target_entity=str(raw.get("target_entity", "")),
        target_field=str(raw.get("target_field", "")),
        source_columns=tuple(raw.get("source_columns", ())),
        transform=transform_from_dict(raw.get("transform") or {}),
        null_policy=NullPolicy(raw.get("null_policy", NullPolicy.PASS_THROUGH.value)),
        default_value=raw.get("default_value"),
        platform_supplied=bool(raw.get("platform_supplied", False)),
        unmapped_reason=str(raw.get("unmapped_reason", "")),
        glossary_id=raw.get("glossary_id"),
        notes=str(raw.get("notes", "")),
        confidence=raw.get("confidence"),
        citations=tuple(raw.get("citations", ())),
    )


def transform_to_dict(transform: Transform) -> dict[str, Any]:
    return {
        "kind": transform.kind.value,
        "target_type": transform.target_type.value if transform.target_type else None,
        "date_format": transform.date_format,
        "separator": transform.separator,
        "part": transform.part,
        "lookup": [list(pair) for pair in transform.lookup],
        "on_unlisted": transform.on_unlisted.value,
        "cases": [{"when_in": list(c.when_in), "then": c.then} for c in transform.cases],
        "literal": transform.literal,
        "default_value": transform.default_value,
    }


def transform_from_dict(raw: dict[str, Any]) -> Transform:
    target_type = raw.get("target_type")
    return Transform(
        kind=TransformKind(raw.get("kind", TransformKind.DIRECT.value)),
        target_type=TypeName(target_type) if target_type else None,
        date_format=raw.get("date_format"),
        separator=raw.get("separator"),
        part=raw.get("part"),
        lookup=tuple((str(pair[0]), str(pair[1])) for pair in raw.get("lookup", ())),
        on_unlisted=UnlistedCode(raw.get("on_unlisted", UnlistedCode.REJECT_ROW.value)),
        cases=tuple(
            Case(when_in=tuple(c.get("when_in", ())), then=str(c.get("then", "")))
            for c in raw.get("cases", ())
        ),
        literal=raw.get("literal"),
        default_value=raw.get("default_value"),
    )
