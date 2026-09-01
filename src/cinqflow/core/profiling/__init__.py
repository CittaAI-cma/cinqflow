"""CF-V1-E5-01 — the deterministic file profiler.

    "I want the platform to profile my sample file automatically — structure,
     delimiter, header, encoding, types, nulls, statistics, candidate keys and
     duplicates — so that I start from facts instead of from a guess."
    — CF-V1-E5-01

    "It handles BOMs, quoted delimiters, Excel typed cells and ragged rows by
     REPORTING, NEVER CRASHING; an unreadable file gets a plain-language
     explanation of what to ask the payer for."
    — memory/07-runbooks/RB-04-onboard-a-feed.md, step 1

    > The facts come from computation. The AI only ever *interprets* them.

THE DELIBERATE-FIRST RULE, MADE STRUCTURAL. Everything here is arithmetic over
bytes. There is no model call, no heuristic that cannot be shown its working,
and — importantly — NO FIELD NAMED `type`. A column carries `type_candidates`,
each with `matched` out of `considered`, and the reader can check the division.
CF-V1-E5-02's agent interprets that evidence into a contract and must cite it;
an agent grounded in a number it cannot verify is an agent guessing politely.

TWO READERS, ONE FILE, DIFFERENT JOBS. `core/parsers.parse` is STRICT: a ragged
row is a G2 structure failure and it raises, which is correct for a production
load. The profiler is TOLERANT: it reads the same bytes, survives everything,
and reports what the strict reader would refuse — `FileProfile.would_load` and
`Finding.blocks_ingestion` are that prediction. A BA should learn at step 1
that three rows are ragged, not at step 5 when the load fails.

REPRODUCIBILITY IS A FINGERPRINT, NOT A PROMISE. "Profiling statistics exactly
reproducible on re-run of the same file" is an acceptance criterion, so the
profile carries `fingerprint` — a digest over the FACTS. Two consequences that
are worth the small amount of care they cost:

  • The digest covers integers and enum names only. No timestamp, no float, no
    set iteration order. A wall-clock inside a reproducibility fingerprint
    makes reproducibility impossible by construction.
  • The digest EXCLUDES example values. A steward reading a PHI-masked packet
    and a BA reading the unmasked one must be provably looking at the same
    evidence — and they are, because `without_values()` does not move the
    fingerprint. This is what lets CF-V1-E4-03 block a submission for stale
    evidence without also blocking one for a redacted view of fresh evidence.

NO SILENT CAPS. The profiler is bounded — bytes read, distinct values held,
composite key pairs examined — and every bound that actually bit is reported in
the profile (`FileStructure.sampled`, `ColumnProfile.distinct_is_exact`,
`KeySearch.pairs_skipped`). A truncation nobody mentions reads as completeness.
"""

from __future__ import annotations

import csv
import hashlib
import json as _json
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum, unique
from io import StringIO
from typing import Any, Self

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.complex_format_profiling import (
    KNOWN_FIXED_WIDTH_LAYOUTS,
    FixedWidthColumn,
    FixedWidthLayout,
    FlattenProposal,
    StructurePath,
    ambiguous_boundaries,
    detect_fixed_width_boundaries,
    layout_from_reference,
    profile_structure,
    propose_flattening,
)
from cinqflow.core.parsers import cell_to_text
from cinqflow.core.patterns import BY_ID, PATTERNS, Pattern
from cinqflow.core.schema_spec import TypeName

#: Bumped when a computation changes. It is part of the fingerprint, so a
#: profiler that starts counting nulls differently produces visibly different
#: evidence rather than quietly disagreeing with a stored profile.
#:
#: 1.1.0 · CF-V1-E5-03 added `ColumnProfile.pattern_matches`. Every profile
#: computed by 1.0.0 therefore has a different id from the same file profiled
#: now — which is correct and is the reason the version is IN the fingerprint:
#: the older row is not wrong, it is evidence about less.
#: 1.2.0 · CF-V3-E5-05 added `structure_paths`, `flatten_proposals` and
#: `fixed_width_layout`, and two dispatch cases (`ndjson`, `fixed_width`)
#: that previously returned a `NO_PARSER` refusal.
PROFILER_VERSION = "1.2.0"

#: "A 50MB sample profiles in minutes with progress" — the story's bound, as a
#: default rather than a hope.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
#: Exact distinct sets up to here. Beyond it the column reports
#: `distinct_is_exact=False` and drops OUT of key candidacy — unknown, never
#: assumed non-unique.
DEFAULT_DISTINCT_CAP = 200_000
DEFAULT_MAX_COMPOSITE_PAIRS = 50
DEFAULT_PROGRESS_EVERY = 10_000
#: Rows kept in memory for the composite-key second look and for duplicate
#: EXAMPLES. Single-column uniqueness is exact arithmetic from the first pass
#: and needs no retention, so this bound never weakens a primary-key answer.
DEFAULT_RETAIN_ROWS = 100_000

EXAMPLE_VALUES = 5
TOP_VALUES = 10
FIRST_LINES = 5

#: CF-V1-E5-03's free-text bounds. Longer than the widest identifier or code
#: this estate carries (an eleven-character MBI), and various enough that the
#: column cannot be an enumeration.
FREE_TEXT_MIN_LENGTH = 40
FREE_TEXT_DISTINCT_RATIO = 0.8

#: What payers write when they mean null. A literal "NULL" in a CSV is a
#: four-character string, and a column that is 40% these is not 0% null — it is
#: a column whose nulls the loader will silently load as text.
NULL_LIKE_TOKENS = frozenset({"null", "n/a", "na", "none", "nil", "unknown", "\\n", "-", "?"})


# ── what the profiler found that is worth saying out loud ────────────────────
@unique
class Quirk(StrEnum):
    """Named so a screen can group them and a test can assert one.

    Every member here is a real thing this estate's files do. The story names
    four of them explicitly; the rest were cheap to detect once the tolerant
    reader existed, and each one is a support ticket that does not happen.
    """

    BYTE_ORDER_MARK = "byte_order_mark"
    QUOTED_DELIMITER = "quoted_delimiter"
    RAGGED_ROW = "ragged_row"
    TYPED_CELL = "typed_cell"
    EMBEDDED_NEWLINE = "embedded_newline"
    MIXED_LINE_ENDINGS = "mixed_line_endings"
    DUPLICATE_HEADER = "duplicate_header"
    EMPTY_HEADER_NAME = "empty_header_name"
    WHITESPACE_PADDING = "whitespace_padding"
    NULL_LIKE_TOKEN = "null_like_token"  # noqa: S105 - a quirk name, not a credential
    DUPLICATE_ROW = "duplicate_row"
    #: CF-V3-E5-05. One NDJSON line that did not parse as JSON — reported and
    #: skipped, never a crash: the same "tolerant reader" discipline the
    #: delimited profiler already holds for a ragged row.
    MALFORMED_JSON_LINE = "malformed_json_line"
    #: CF-V3-E5-05's own exception: a statistically-detected column that a
    #: named reference layout (`CCLF1`) would instead split further, because
    #: the fields either side of the boundary are always populated and leave
    #: no gap for whitespace scanning to find.
    AMBIGUOUS_FIXED_WIDTH_BOUNDARY = "ambiguous_fixed_width_boundary"


@dataclass(frozen=True)
class Finding:
    """One quirk, counted, located, and judged against the strict reader."""

    quirk: Quirk
    detail: str
    occurrences: int = 1
    first_lines: tuple[int, ...] = ()
    columns: tuple[str, ...] = ()
    #: Would `core/parsers.parse` refuse this file? The profiler's whole point
    #: is that this question is answered at step 1 rather than at step 5.
    blocks_ingestion: bool = False


@unique
class RefusalReason(StrEnum):
    EMPTY_FILE = "empty_file"
    UNDECODABLE = "undecodable"
    NO_HEADER = "no_header"
    NO_PARSER = "no_parser"
    UNREADABLE_WORKBOOK = "unreadable_workbook"


@dataclass(frozen=True)
class Refusal:
    """Why nothing could be profiled — in words a BA can forward.

        "an unreadable file gets a plain-language explanation of what to ask
         the payer for"

    `ask_the_payer` is a separate field rather than a sentence appended to the
    explanation, because it is the ONLY part the BA has to act on, and a screen
    should be able to show it alone.
    """

    reason: RefusalReason
    explanation: str
    ask_the_payer: str


# ── structure ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DelimiterEvidence:
    """Why the profiler believes the delimiter is what it says.

    Delimiter detection by counting characters in the header is wrong the
    moment a header contains a quoted comma. This counts CONSISTENCY: parse the
    first lines with each candidate and see which one yields the same field
    count every time. The losing candidates are reported too — a BA looking at
    a file that scored 8 for pipe and 8 for comma is looking at a genuinely
    ambiguous file, and should be told so.
    """

    delimiter: str
    fields_per_row: int
    consistent_rows: int
    rows_examined: int


@dataclass(frozen=True)
class FileStructure:
    file_format: str
    encoding: str
    declared_encoding: str
    byte_order_mark: str | None = None
    delimiter: str | None = None
    quote_char: str | None = None
    line_ending: str = ""
    header_line: int = 1
    column_count: int = 0
    data_rows: int = 0
    bytes_total: int = 0
    bytes_read: int = 0
    #: True when the byte budget stopped the read before the end of the file.
    sampled: bool = False
    delimiter_evidence: tuple[DelimiterEvidence, ...] = ()


# ── per-column facts ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TypeCandidate:
    """A type, and the count of values that fit it. Never a verdict.

    `matched` and `considered` are both carried so the share is checkable
    arithmetic rather than a rounded float somebody has to trust — and so the
    fingerprint contains integers, which reproduce exactly.
    """

    type: TypeName
    matched: int
    considered: int

    @property
    def is_total(self) -> bool:
        return self.considered > 0 and self.matched == self.considered

    @property
    def share(self) -> float:
        """For display only. Never fingerprinted, never compared for equality
        of evidence — that is what the two integers are for."""
        return self.matched / self.considered if self.considered else 0.0


@dataclass(frozen=True)
class PatternMatch:
    """How many values fitted one declared value shape. CF-V1-E5-03.

    Counted HERE rather than by the PHI detector, and that placement is the
    story's whole economics. The profiler already streams every value once; a
    detector sampling five example values would report a rate computed from
    five values and call it evidence. `matched`/`considered` over the whole
    sample is arithmetic a reviewer can check.

    Two integers and an id — no values, no floats — so this fingerprints
    exactly like `TypeCandidate` does, and a re-profile of unchanged bytes
    still collides onto the same row.
    """

    pattern_id: str
    matched: int
    considered: int

    @property
    def is_total(self) -> bool:
        return self.considered > 0 and self.matched == self.considered

    @property
    def share(self) -> float:
        """Display only. The two integers are what evidence is compared on."""
        return self.matched / self.considered if self.considered else 0.0

    @property
    def pattern(self) -> Pattern | None:
        return BY_ID.get(self.pattern_id)


@dataclass(frozen=True)
class DateFormatMatch:
    """Which date spelling arrived, and how often.

    Every label here is one `core.registry.contract.cast_value` actually
    accepts — asserted by a test, because a profiler that reports a format the
    caster rejects has told the BA their file is fine and the pipeline that it
    is not.
    """

    label: str
    matched: int


#: Genuine type containment: every value of the value is also a value of the
#: key. Only relations that hold for EVERY value belong here — "a date is
#: usually not a member id" is a probability, not a containment, and this table
#: is the boundary between the two.
_CONTAINS: dict[TypeName, frozenset[TypeName]] = {
    TypeName.DECIMAL: frozenset({TypeName.INT64}),
}

#: Reporting order for collapsed candidates. Fixed, so two runs agree.
_TYPE_ORDER: tuple[TypeName, ...] = (
    TypeName.UUID,
    TypeName.DATE,
    TypeName.TIMESTAMP_UTC,
    TypeName.BOOL,
    TypeName.INT64,
    TypeName.DECIMAL,
    TypeName.STRING,
)


@dataclass(frozen=True)
class ColumnProfile:
    """One column, computed. Nothing here is an opinion."""

    name: str
    position: int
    row_count: int = 0
    null_count: int = 0
    null_like_count: int = 0
    distinct_count: int = 0
    distinct_is_exact: bool = True
    min_length: int = 0
    max_length: int = 0
    padded_count: int = 0
    type_candidates: tuple[TypeCandidate, ...] = ()
    date_formats: tuple[DateFormatMatch, ...] = ()
    #: CF-V1-E5-03. Counts against the declared value shapes — never a verdict
    #: about what the column holds. Only shapes with at least one match are
    #: carried, so a forty-column file does not store 800 zeroes.
    pattern_matches: tuple[PatternMatch, ...] = ()
    #: Widest numeric shape observed, so a decimal contract can declare
    #: precision and scale — which `schema_spec.Column` REQUIRES it to.
    observed_precision: int | None = None
    observed_scale: int | None = None
    typed_cell_count: int = 0
    # ── value-bearing. Redacted by `without_values`, never fingerprinted. ────
    examples: tuple[str, ...] = ()
    top_values: tuple[tuple[str, int], ...] = ()
    min_value: str | None = None
    max_value: str | None = None
    values_redacted: bool = False

    @property
    def populated_count(self) -> int:
        return self.row_count - self.null_count

    @property
    def is_constant(self) -> bool:
        return self.populated_count > 0 and self.distinct_is_exact and self.distinct_count == 1

    @property
    def is_unique(self) -> bool | None:
        """No repeats AMONG POPULATED VALUES. None means unknown.

        Nulls are ignored here on purpose: "does this column repeat?" and "can
        this column be a key?" are different questions, and `KeyCandidate`
        answers the second one — where a null row disqualifies.

        None is UNKNOWN, and unknown is not False: the distinct cap bit, so
        nothing can be said. A column reported as non-unique when nobody
        counted it is how a real primary key gets discarded.
        """
        if not self.distinct_is_exact:
            return None
        return self.populated_count > 0 and self.distinct_count == self.populated_count

    @property
    def total_match_types(self) -> tuple[TypeName, ...]:
        """Every non-string type that fitted EVERY populated value, with
        CONTAINED types collapsed away.

        The distinction is worth the code. Two types fitting because one
        contains the other is not ambiguity — every whole number is also a
        decimal, so reporting both as rival candidates would make a plain
        integer column look undecidable. Two types fitting because the VALUES
        happen to satisfy both is real ambiguity, and stays.

        `19360201` is the case that decides this design: it is a valid date
        AND a valid integer, and neither type contains the other. The estate
        writes dates that way constantly — so the temptation is to prefer
        DATE, and the profiler must not, because an eight-digit member id
        exists too and this module is the one place that never guesses.
        CF-V1-E5-02 resolves it in a sentence, grounded in the column's name,
        its glossary synonyms and the format evidence below.
        """
        total = {
            c.type for c in self.type_candidates if c.is_total and c.type is not TypeName.STRING
        }
        return tuple(
            sorted(
                (t for t in total if not (_CONTAINS.get(t, frozenset()) & total)),
                key=_TYPE_ORDER.index,
            )
        )

    @property
    def narrowest_type(self) -> TypeName | None:
        """The one type the evidence determines, or None where it does not.

        Three cases, and each one is a real answer:

          • exactly one narrower type fitted everything -> that type;
          • NONE did -> `STRING`, which is a determination rather than a
            fallback: nothing narrower fits, so string is what the file says;
          • more than one did, or the column is entirely empty -> None, which
            is CF-V1-E5-02's "needs your input", reached deterministically and
            for free.

        Where this returns a type the inference agent has no question to ask
        and spends no tokens — the same bargain `Glossary.for_column` strikes
        for mapping.
        """
        if self.populated_count == 0:
            return None
        total = self.total_match_types
        if not total:
            return TypeName.STRING
        return total[0] if len(total) == 1 else None

    # ── CF-V1-E5-03 · what the shapes say, and what they do not ─────────────
    @property
    def total_pattern_matches(self) -> tuple[PatternMatch, ...]:
        """Shapes that fitted EVERY populated value. Reported in declaration
        order, so two runs list them identically."""
        return tuple(m for m in self.pattern_matches if m.is_total)

    @property
    def decisive_patterns(self) -> tuple[PatternMatch, ...]:
        """Shapes that fitted everything AND could not have done so by accident.

        This is the only pattern evidence that settles anything on its own. A
        column where this is non-empty is a column the platform can name by
        computation; everywhere else the shapes are a hint and the decision
        needs the glossary, the column's name, or a person.
        """
        return tuple(
            m
            for m in self.total_pattern_matches
            if (pattern := m.pattern) is not None and pattern.discriminating
        )

    @property
    def is_free_text(self) -> bool:
        """Long, various, untypeable prose — a `NOTES` column.

        The blueprint's rule for CF-V1-E5-03 is that free text is treated as
        PHI until a steward decides otherwise, so the platform has to be able
        to RECOGNISE free text rather than assert it. Three measured facts,
        all of which must hold:

          • nothing narrower than string fits, so it is not a code or a date;
          • the longest value is longer than any identifier or code in this
            estate (the widest is an eleven-character MBI);
          • values barely repeat, which is what separates prose from an
            enumeration of long-ish labels like `PRIMARY CARE PHYSICIAN`.

        Bounded on the low side too: a column of thirty distinct values that
        happen to be sixty characters long is a category list, not prose.
        """
        if self.populated_count == 0 or self.narrowest_type is not TypeName.STRING:
            return False
        if self.max_length <= FREE_TEXT_MIN_LENGTH:
            return False
        if not self.distinct_is_exact:
            # The distinct cap bit, which means the column has more than
            # 200,000 distinct values. Nothing enumerable is that various.
            return True
        return self.distinct_count / self.populated_count >= FREE_TEXT_DISTINCT_RATIO

    def without_values(self) -> Self:
        return replace(
            self,
            examples=(),
            top_values=(),
            min_value=None,
            max_value=None,
            values_redacted=True,
        )


# ── keys and duplicates ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class KeyCandidate:
    """A candidate key WITH ITS EVIDENCE.

    "candidate primary keys with uniqueness evidence" — so the counts travel
    with the claim. A key proposed without them is a suggestion; with them it
    is a finding somebody can check in one glance.
    """

    columns: tuple[str, ...]
    distinct_count: int
    populated_rows: int
    null_rows: int
    duplicate_values: int
    examples: tuple[tuple[str, tuple[int, ...]], ...] = ()
    values_redacted: bool = False

    @property
    def is_unique(self) -> bool:
        return self.null_rows == 0 and self.duplicate_values == 0 and self.populated_rows > 0

    def without_values(self) -> Self:
        return replace(self, examples=(), values_redacted=True)


@dataclass(frozen=True)
class KeySearch:
    """What was looked at, and what was not. Bounds, stated.

    A profile that silently examined 50 of 703 possible column pairs and
    reported "no composite key found" has told the reader something false.
    """

    single_columns_examined: int = 0
    composite_width: int = 2
    pairs_examined: int = 0
    pairs_skipped: int = 0
    rows_retained: int = 0
    excluded_columns: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class DuplicateRows:
    """Whole-row duplicates. Incident: the same member twice in one file."""

    duplicate_groups: int = 0
    duplicate_rows: int = 0
    first_lines: tuple[int, ...] = ()


# ── progress ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Progress:
    """Emitted on a fixed row cadence, so the sequence is reproducible too."""

    phase: str
    rows_read: int
    bytes_read: int
    bytes_total: int

    @property
    def share(self) -> float:
        return self.bytes_read / self.bytes_total if self.bytes_total else 1.0


ProgressCallback = Callable[[Progress], None]


# ── the profile ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FileProfile:
    """Everything computed about one file. The grounding for steps 2, 3 and 4."""

    source_key: str
    source_fingerprint: str
    structure: FileStructure
    profiler_version: str = PROFILER_VERSION
    readable: bool = True
    refusal: Refusal | None = None
    columns: tuple[ColumnProfile, ...] = ()
    findings: tuple[Finding, ...] = ()
    key_candidates: tuple[KeyCandidate, ...] = ()
    key_search: KeySearch = field(default_factory=KeySearch)
    duplicates: DuplicateRows = field(default_factory=DuplicateRows)
    values_redacted: bool = False
    #: CF-V3-E5-05 · nested formats only (`ndjson`). Empty for every other
    #: format — a flat CSV has no paths to count.
    structure_paths: tuple[StructurePath, ...] = ()
    flatten_proposals: tuple[FlattenProposal, ...] = ()
    #: CF-V3-E5-05 · `fixed_width` only. `None` for every other format.
    fixed_width_layout: FixedWidthLayout | None = None

    # ── identity ─────────────────────────────────────────────────────────────
    @property
    def fingerprint(self) -> str:
        """A digest over the FACTS — integers and names, never values, never
        a clock. Same bytes in, same fingerprint out, on any machine."""
        digest = hashlib.sha256()
        for line in self._canonical_facts():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        return "sha256-" + digest.hexdigest()[:32]

    @property
    def profile_id(self) -> str:
        """The profile IS its fingerprint.

        Re-profiling an unchanged file therefore writes the same row rather
        than a second one, which is what makes the replay proof a database
        fact instead of a test assertion.
        """
        return self.fingerprint

    def _canonical_facts(self) -> tuple[str, ...]:
        s = self.structure
        lines = [
            f"profiler={self.profiler_version}",
            f"source={self.source_fingerprint}",
            f"readable={self.readable}",
            f"refusal={self.refusal.reason.value if self.refusal else ''}",
            f"format={s.file_format}|encoding={s.encoding}|bom={s.byte_order_mark or ''}",
            f"delimiter={s.delimiter or ''}|quote={s.quote_char or ''}|le={s.line_ending}",
            f"cols={s.column_count}|rows={s.data_rows}|sampled={s.sampled}|read={s.bytes_read}",
        ]
        for column in self.columns:
            lines.append(
                f"col={column.position}:{column.name}|rows={column.row_count}"
                f"|null={column.null_count}|nulllike={column.null_like_count}"
                f"|distinct={column.distinct_count}:{column.distinct_is_exact}"
                f"|len={column.min_length}-{column.max_length}|pad={column.padded_count}"
                f"|typed={column.typed_cell_count}"
                f"|ps={column.observed_precision},{column.observed_scale}"
            )
            for candidate in column.type_candidates:
                lines.append(
                    f"  type={candidate.type.value}:{candidate.matched}/{candidate.considered}"
                )
            for fmt in column.date_formats:
                lines.append(f"  date={fmt.label}:{fmt.matched}")
            for shape in column.pattern_matches:
                lines.append(f"  shape={shape.pattern_id}:{shape.matched}/{shape.considered}")
        for finding in self.findings:
            lines.append(
                f"finding={finding.quirk.value}:{finding.occurrences}"
                f":{finding.blocks_ingestion}:{','.join(finding.columns)}"
            )
        for key in self.key_candidates:
            lines.append(
                f"key={'+'.join(key.columns)}|distinct={key.distinct_count}"
                f"|rows={key.populated_rows}|null={key.null_rows}|dup={key.duplicate_values}"
            )
        k = self.key_search
        lines.append(
            f"search={k.single_columns_examined}|{k.composite_width}"
            f"|{k.pairs_examined}|{k.pairs_skipped}|{k.rows_retained}"
            f"|{','.join(k.excluded_columns)}"
        )
        lines.append(f"duprows={self.duplicates.duplicate_groups}:{self.duplicates.duplicate_rows}")
        for path in self.structure_paths:
            lines.append(
                f"path={path.path}|docs={path.documents_with_path}:{path.documents_total}"
                f"|array={path.is_array}:{path.array_length_min}:{path.array_length_max}"
                f":{path.array_length_total}:{path.array_occurrences}"
            )
        if self.fixed_width_layout is not None:
            for fw_column in self.fixed_width_layout.columns:
                lines.append(f"fwcol={fw_column.start}-{fw_column.end}:{fw_column.name or ''}")
        return tuple(lines)

    @property
    def citation(self) -> CitationId:
        """`profile:<fingerprint>` — the address of these facts."""
        return CitationId(kind=CitationKind.PROFILE, subject=self.profile_id)

    def citation_for(self, column_name: str) -> CitationId:
        """`profile:<fingerprint>#<column>` — the address of ONE fact.

        This is what CF-V1-E5-02 must attach to every field it types: the
        inference is the agent's, the number under it is not, and a reader can
        open the number.
        """
        return CitationId(kind=CitationKind.PROFILE, subject=self.profile_id, fragment=column_name)

    # ── questions the wizard asks ────────────────────────────────────────────
    @property
    def would_load(self) -> bool:
        """Would the strict pipeline parser accept this file?

        Answered at step 1, which is the difference between a BA fixing a
        delivery with the payer this week and discovering it at publication.
        """
        return self.readable and not any(f.blocks_ingestion for f in self.findings)

    @property
    def blockers(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocks_ingestion)

    @property
    def primary_key_candidates(self) -> tuple[KeyCandidate, ...]:
        return tuple(k for k in self.key_candidates if k.is_unique)

    def column(self, name: str) -> ColumnProfile | None:
        for candidate in self.columns:
            if candidate.name == name:
                return candidate
        return None

    def without_values(self) -> Self:
        """The profile minus every value read out of the file.

        The don't this serves: "send sample data anywhere except storage the
        BA's role can access". A profile crossing into an approval packet a
        different role reads, or into a prompt, goes through here first — and
        the fingerprint is deliberately unchanged, so the redacted copy is
        still provably evidence of the same file.
        """
        return replace(
            self,
            columns=tuple(c.without_values() for c in self.columns),
            key_candidates=tuple(k.without_values() for k in self.key_candidates),
            values_redacted=True,
        )

    def as_evidence(self) -> dict[str, object]:
        """What an approval packet carries: the claim and its address."""
        return {
            "profile_id": self.profile_id,
            "profiler_version": self.profiler_version,
            "source_key": self.source_key,
            "source_fingerprint": self.source_fingerprint,
            "readable": self.readable,
            "would_load": self.would_load,
            "rows": self.structure.data_rows,
            "columns": self.structure.column_count,
            "sampled": self.structure.sampled,
            "blockers": [f.detail for f in self.blockers],
        }

    # ── persistence ──────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "profiler_version": self.profiler_version,
            "source_key": self.source_key,
            "source_fingerprint": self.source_fingerprint,
            "readable": self.readable,
            "values_redacted": self.values_redacted,
            "refusal": (
                None
                if self.refusal is None
                else {
                    "reason": self.refusal.reason.value,
                    "explanation": self.refusal.explanation,
                    "ask_the_payer": self.refusal.ask_the_payer,
                }
            ),
            "structure": {
                "file_format": self.structure.file_format,
                "encoding": self.structure.encoding,
                "declared_encoding": self.structure.declared_encoding,
                "byte_order_mark": self.structure.byte_order_mark,
                "delimiter": self.structure.delimiter,
                "quote_char": self.structure.quote_char,
                "line_ending": self.structure.line_ending,
                "header_line": self.structure.header_line,
                "column_count": self.structure.column_count,
                "data_rows": self.structure.data_rows,
                "bytes_total": self.structure.bytes_total,
                "bytes_read": self.structure.bytes_read,
                "sampled": self.structure.sampled,
                "delimiter_evidence": [
                    {
                        "delimiter": e.delimiter,
                        "fields_per_row": e.fields_per_row,
                        "consistent_rows": e.consistent_rows,
                        "rows_examined": e.rows_examined,
                    }
                    for e in self.structure.delimiter_evidence
                ],
            },
            "columns": [
                {
                    "name": c.name,
                    "position": c.position,
                    "row_count": c.row_count,
                    "null_count": c.null_count,
                    "null_like_count": c.null_like_count,
                    "distinct_count": c.distinct_count,
                    "distinct_is_exact": c.distinct_is_exact,
                    "min_length": c.min_length,
                    "max_length": c.max_length,
                    "padded_count": c.padded_count,
                    "typed_cell_count": c.typed_cell_count,
                    "observed_precision": c.observed_precision,
                    "observed_scale": c.observed_scale,
                    "type_candidates": [
                        {"type": t.type.value, "matched": t.matched, "considered": t.considered}
                        for t in c.type_candidates
                    ],
                    "date_formats": [
                        {"label": d.label, "matched": d.matched} for d in c.date_formats
                    ],
                    "pattern_matches": [
                        {
                            "pattern_id": m.pattern_id,
                            "matched": m.matched,
                            "considered": m.considered,
                        }
                        for m in c.pattern_matches
                    ],
                    "examples": list(c.examples),
                    "top_values": [[v, n] for v, n in c.top_values],
                    "min_value": c.min_value,
                    "max_value": c.max_value,
                    "values_redacted": c.values_redacted,
                }
                for c in self.columns
            ],
            "findings": [
                {
                    "quirk": f.quirk.value,
                    "detail": f.detail,
                    "occurrences": f.occurrences,
                    "first_lines": list(f.first_lines),
                    "columns": list(f.columns),
                    "blocks_ingestion": f.blocks_ingestion,
                }
                for f in self.findings
            ],
            "key_candidates": [
                {
                    "columns": list(k.columns),
                    "distinct_count": k.distinct_count,
                    "populated_rows": k.populated_rows,
                    "null_rows": k.null_rows,
                    "duplicate_values": k.duplicate_values,
                    "examples": [[v, list(lines)] for v, lines in k.examples],
                    "values_redacted": k.values_redacted,
                }
                for k in self.key_candidates
            ],
            "key_search": {
                "single_columns_examined": self.key_search.single_columns_examined,
                "composite_width": self.key_search.composite_width,
                "pairs_examined": self.key_search.pairs_examined,
                "pairs_skipped": self.key_search.pairs_skipped,
                "rows_retained": self.key_search.rows_retained,
                "excluded_columns": list(self.key_search.excluded_columns),
                "note": self.key_search.note,
            },
            "duplicates": {
                "duplicate_groups": self.duplicates.duplicate_groups,
                "duplicate_rows": self.duplicates.duplicate_rows,
                "first_lines": list(self.duplicates.first_lines),
            },
            "structure_paths": [
                {
                    "path": p.path,
                    "documents_with_path": p.documents_with_path,
                    "documents_total": p.documents_total,
                    "is_array": p.is_array,
                    "array_length_min": p.array_length_min,
                    "array_length_max": p.array_length_max,
                    "array_length_total": p.array_length_total,
                    "array_occurrences": p.array_occurrences,
                }
                for p in self.structure_paths
            ],
            "flatten_proposals": [
                {
                    "source_path": p.source_path,
                    "proposed_entity": p.proposed_entity,
                    "element_count_min": p.element_count_min,
                    "element_count_max": p.element_count_max,
                    "description": p.description,
                }
                for p in self.flatten_proposals
            ],
            "fixed_width_layout": (
                None
                if self.fixed_width_layout is None
                else {
                    "source": self.fixed_width_layout.source,
                    "columns": [
                        {
                            "start": c.start,
                            "end": c.end,
                            "name": c.name,
                            "confidence": c.confidence,
                        }
                        for c in self.fixed_width_layout.columns
                    ],
                }
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        """Rebuild a stored profile.

        Round-tripping matters more than it looks: the stored profile IS the
        evidence CF-V1-E4-03 re-checks at submission time, so a lossy read
        would let a stale-evidence gate compare a full profile against a
        partial one and call the difference a change.
        """
        s = raw["structure"]
        refusal = raw.get("refusal")
        return cls(
            source_key=raw["source_key"],
            source_fingerprint=raw["source_fingerprint"],
            profiler_version=raw.get("profiler_version", PROFILER_VERSION),
            readable=bool(raw.get("readable", True)),
            values_redacted=bool(raw.get("values_redacted", False)),
            refusal=(
                None
                if not refusal
                else Refusal(
                    reason=RefusalReason(refusal["reason"]),
                    explanation=refusal["explanation"],
                    ask_the_payer=refusal["ask_the_payer"],
                )
            ),
            structure=FileStructure(
                file_format=s["file_format"],
                encoding=s["encoding"],
                declared_encoding=s["declared_encoding"],
                byte_order_mark=s.get("byte_order_mark"),
                delimiter=s.get("delimiter"),
                quote_char=s.get("quote_char"),
                line_ending=s.get("line_ending", ""),
                header_line=int(s.get("header_line", 1)),
                column_count=int(s.get("column_count", 0)),
                data_rows=int(s.get("data_rows", 0)),
                bytes_total=int(s.get("bytes_total", 0)),
                bytes_read=int(s.get("bytes_read", 0)),
                sampled=bool(s.get("sampled", False)),
                delimiter_evidence=tuple(
                    DelimiterEvidence(
                        delimiter=e["delimiter"],
                        fields_per_row=int(e["fields_per_row"]),
                        consistent_rows=int(e["consistent_rows"]),
                        rows_examined=int(e["rows_examined"]),
                    )
                    for e in s.get("delimiter_evidence", ())
                ),
            ),
            columns=tuple(
                ColumnProfile(
                    name=c["name"],
                    position=int(c["position"]),
                    row_count=int(c["row_count"]),
                    null_count=int(c["null_count"]),
                    null_like_count=int(c.get("null_like_count", 0)),
                    distinct_count=int(c["distinct_count"]),
                    distinct_is_exact=bool(c.get("distinct_is_exact", True)),
                    min_length=int(c.get("min_length", 0)),
                    max_length=int(c.get("max_length", 0)),
                    padded_count=int(c.get("padded_count", 0)),
                    typed_cell_count=int(c.get("typed_cell_count", 0)),
                    observed_precision=c.get("observed_precision"),
                    observed_scale=c.get("observed_scale"),
                    type_candidates=tuple(
                        TypeCandidate(
                            type=TypeName(t["type"]),
                            matched=int(t["matched"]),
                            considered=int(t["considered"]),
                        )
                        for t in c.get("type_candidates", ())
                    ),
                    pattern_matches=tuple(
                        PatternMatch(
                            pattern_id=str(m["pattern_id"]),
                            matched=int(m["matched"]),
                            considered=int(m["considered"]),
                        )
                        for m in c.get("pattern_matches", ())
                        # A shape this build no longer declares is DROPPED
                        # rather than carried as an unreadable id: the
                        # profiler version in the fingerprint already says the
                        # evidence was computed by a different build.
                        if str(m["pattern_id"]) in BY_ID
                    ),
                    date_formats=tuple(
                        DateFormatMatch(label=d["label"], matched=int(d["matched"]))
                        for d in c.get("date_formats", ())
                    ),
                    examples=tuple(c.get("examples", ())),
                    top_values=tuple((v, int(n)) for v, n in c.get("top_values", ())),
                    min_value=c.get("min_value"),
                    max_value=c.get("max_value"),
                    values_redacted=bool(c.get("values_redacted", False)),
                )
                for c in raw.get("columns", ())
            ),
            findings=tuple(
                Finding(
                    quirk=Quirk(f["quirk"]),
                    detail=f["detail"],
                    occurrences=int(f.get("occurrences", 1)),
                    first_lines=tuple(f.get("first_lines", ())),
                    columns=tuple(f.get("columns", ())),
                    blocks_ingestion=bool(f.get("blocks_ingestion", False)),
                )
                for f in raw.get("findings", ())
            ),
            key_candidates=tuple(
                KeyCandidate(
                    columns=tuple(k["columns"]),
                    distinct_count=int(k["distinct_count"]),
                    populated_rows=int(k["populated_rows"]),
                    null_rows=int(k["null_rows"]),
                    duplicate_values=int(k["duplicate_values"]),
                    examples=tuple((v, tuple(lines)) for v, lines in k.get("examples", ())),
                    values_redacted=bool(k.get("values_redacted", False)),
                )
                for k in raw.get("key_candidates", ())
            ),
            key_search=KeySearch(
                single_columns_examined=int(
                    raw.get("key_search", {}).get("single_columns_examined", 0)
                ),
                composite_width=int(raw.get("key_search", {}).get("composite_width", 2)),
                pairs_examined=int(raw.get("key_search", {}).get("pairs_examined", 0)),
                pairs_skipped=int(raw.get("key_search", {}).get("pairs_skipped", 0)),
                rows_retained=int(raw.get("key_search", {}).get("rows_retained", 0)),
                excluded_columns=tuple(raw.get("key_search", {}).get("excluded_columns", ())),
                note=raw.get("key_search", {}).get("note", ""),
            ),
            duplicates=DuplicateRows(
                duplicate_groups=int(raw.get("duplicates", {}).get("duplicate_groups", 0)),
                duplicate_rows=int(raw.get("duplicates", {}).get("duplicate_rows", 0)),
                first_lines=tuple(raw.get("duplicates", {}).get("first_lines", ())),
            ),
            structure_paths=tuple(
                StructurePath(
                    path=p["path"],
                    documents_with_path=int(p["documents_with_path"]),
                    documents_total=int(p["documents_total"]),
                    is_array=bool(p.get("is_array", False)),
                    array_length_min=p.get("array_length_min"),
                    array_length_max=p.get("array_length_max"),
                    array_length_total=int(p.get("array_length_total", 0)),
                    array_occurrences=int(p.get("array_occurrences", 0)),
                )
                for p in raw.get("structure_paths", ())
            ),
            flatten_proposals=tuple(
                FlattenProposal(
                    source_path=p["source_path"],
                    proposed_entity=p["proposed_entity"],
                    element_count_min=int(p["element_count_min"]),
                    element_count_max=int(p["element_count_max"]),
                    description=p["description"],
                )
                for p in raw.get("flatten_proposals", ())
            ),
            fixed_width_layout=(
                None
                if not raw.get("fixed_width_layout")
                else FixedWidthLayout(
                    source=raw["fixed_width_layout"].get("source", "statistical"),
                    columns=tuple(
                        FixedWidthColumn(
                            start=int(c["start"]),
                            end=int(c["end"]),
                            name=c.get("name"),
                            confidence=float(c.get("confidence", 1.0)),
                        )
                        for c in raw["fixed_width_layout"].get("columns", ())
                    ),
                )
            ),
        )


# ── type recognisers ─────────────────────────────────────────────────────────
#
# Each one mirrors what `core.registry.contract.cast_value` will actually do,
# and a test asserts the correspondence in both directions. A profiler whose
# idea of a date is wider than the caster's tells the BA their file is fine and
# the pipeline that it is not.

#: A SIGNIFICANT LEADING ZERO MEANS IT IS NOT A NUMBER.
#:
#: `01` is a subscriber relationship code, `02134` is a Boston ZIP, `007` is a
#: plan code — and every one of them becomes a different, wrong value the
#: moment it is cast to an integer. This is one of the classic ways a
#: healthcare load corrupts data silently: nothing errors, the rows all load,
#: and a member's ZIP is now 2134.
#:
#: So the recognisers below refuse an integer part that is longer than one
#: digit and starts with a zero. `0`, `0.50` and `-0.75` are still numbers;
#: `01` and `02134` are codes, and the profiler says STRING.
_INT = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")
_DECIMAL = re.compile(r"^[+-]?(?:(?:0|[1-9]\d*)(?:\.\d*)?|\.\d+)$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1

_TRUE_TOKENS = frozenset({"true", "t", "y", "yes", "1"})
_FALSE_TOKENS = frozenset({"false", "f", "n", "no", "0"})

#: label -> (regex, field order). The labels are what a BA reads on the screen
#: and what a contract records in `date_formats`.
_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str], tuple[int, int, int]], ...] = (
    ("YYYYMMDD", re.compile(r"^(\d{4})(\d{2})(\d{2})$"), (1, 2, 3)),
    ("YYYY-MM-DD", re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), (1, 2, 3)),
    ("M/D/YYYY", re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), (3, 1, 2)),
)
#: The caster's plausibility window. `19000101` is a date; `10000101` is legacy
#: type debt (incident #8), and the profiler must not call it a date when the
#: pipeline will refuse it.
PLAUSIBLE_YEARS = range(1900, 2101)


def date_format_of(text: str) -> str | None:
    """The label of the first format this value fits, or None."""
    for label, pattern, (y, m, d) in _DATE_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        try:
            parsed = date(int(match[y]), int(match[m]), int(match[d]))
        except ValueError:
            continue
        if parsed.year in PLAUSIBLE_YEARS:
            return label
    return None


def _is_int64(text: str) -> bool:
    return bool(_INT.match(text)) and _INT64_MIN <= int(text) <= _INT64_MAX


def _is_decimal(text: str) -> bool:
    return bool(_DECIMAL.match(text))


def _is_timestamp(text: str) -> bool:
    if not _TIMESTAMP.match(text):
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _is_bool(text: str) -> bool:
    lowered = text.lower()
    return lowered in _TRUE_TOKENS or lowered in _FALSE_TOKENS


#: Evaluated in this order, and reported in this order — so two runs list a
#: column's candidates identically.
_RECOGNISERS: tuple[tuple[TypeName, Callable[[str], bool]], ...] = (
    (TypeName.INT64, _is_int64),
    (TypeName.DECIMAL, _is_decimal),
    (TypeName.DATE, lambda text: date_format_of(text) is not None),
    (TypeName.TIMESTAMP_UTC, _is_timestamp),
    (TypeName.BOOL, _is_bool),
    (TypeName.UUID, lambda text: bool(_UUID.match(text))),
)


# ── the accumulator ──────────────────────────────────────────────────────────
class _ColumnAccumulator:
    """One pass, bounded memory, exact counts within the declared cap."""

    __slots__ = (
        "_cap",
        "_distinct",
        "_distinct_overflowed",
        "_examples",
        "_frequency",
        "considered",
        "date_labels",
        "matches",
        "max_int_digits",
        "max_length",
        "max_scale",
        "max_value",
        "min_length",
        "min_value",
        "name",
        "null_count",
        "null_like_count",
        "padded_count",
        "pattern_hits",
        "position",
        "row_count",
        "typed_cell_count",
    )

    def __init__(self, name: str, position: int, distinct_cap: int) -> None:
        self.name = name
        self.position = position
        self.row_count = 0
        self.null_count = 0
        self.null_like_count = 0
        self.padded_count = 0
        self.typed_cell_count = 0
        self.considered = 0
        self.min_length = 0
        self.max_length = 0
        self.min_value: str | None = None
        self.max_value: str | None = None
        self.max_int_digits = 0
        self.max_scale = 0
        self.matches: dict[TypeName, int] = {name: 0 for name, _ in _RECOGNISERS}
        self.pattern_hits: dict[str, int] = {p.pattern_id: 0 for p in PATTERNS}
        self.date_labels: Counter[str] = Counter()
        self._distinct: set[str] = set()
        self._distinct_overflowed = False
        self._frequency: Counter[str] = Counter()
        self._examples: list[str] = []
        self._cap = distinct_cap

    def add(self, raw: str, *, was_typed: bool) -> None:
        self.row_count += 1
        if was_typed:
            self.typed_cell_count += 1
        text = raw.strip()
        if text != raw:
            self.padded_count += 1
        if not text:
            self.null_count += 1
            return

        if text.lower() in NULL_LIKE_TOKENS:
            self.null_like_count += 1

        self.considered += 1
        length = len(text)
        self.min_length = length if self.min_length == 0 else min(self.min_length, length)
        self.max_length = max(self.max_length, length)
        self.min_value = text if self.min_value is None else min(self.min_value, text)
        self.max_value = text if self.max_value is None else max(self.max_value, text)

        for type_name, recogniser in _RECOGNISERS:
            if recogniser(text):
                self.matches[type_name] += 1
        if label := date_format_of(text):
            self.date_labels[label] += 1
        if _is_decimal(text):
            self._widen_numeric(text)

        # CF-V1-E5-03. Every shape, every value — one pass, no sampling. A hit
        # rate computed from five example values would be a number nobody
        # should act on, and acting on it is the whole point.
        for pattern in PATTERNS:
            if pattern.matches(text):
                self.pattern_hits[pattern.pattern_id] += 1

        if not self._distinct_overflowed:
            if len(self._distinct) >= self._cap and text not in self._distinct:
                self._distinct_overflowed = True
            else:
                self._distinct.add(text)
        if len(self._frequency) < TOP_VALUES * 100 or text in self._frequency:
            self._frequency[text] += 1
        if len(self._examples) < EXAMPLE_VALUES and text not in self._examples:
            self._examples.append(text)

    def _widen_numeric(self, text: str) -> None:
        digits = text.lstrip("+-")
        whole, _, fraction = digits.partition(".")
        self.max_int_digits = max(self.max_int_digits, len(whole.lstrip("0")) or 1)
        self.max_scale = max(self.max_scale, len(fraction))

    def finish(self) -> ColumnProfile:
        candidates = [
            TypeCandidate(
                type=type_name,
                matched=self.matches[type_name],
                considered=self.considered,
            )
            for type_name, _ in _RECOGNISERS
            if self.matches[type_name]
        ]
        # STRING always fits, and saying so explicitly is what makes "no
        # narrower type fitted" a positive statement rather than an empty list.
        candidates.append(
            TypeCandidate(type=TypeName.STRING, matched=self.considered, considered=self.considered)
        )
        decimal_seen = self.matches[TypeName.DECIMAL] > 0
        return ColumnProfile(
            name=self.name,
            position=self.position,
            row_count=self.row_count,
            null_count=self.null_count,
            null_like_count=self.null_like_count,
            distinct_count=len(self._distinct),
            distinct_is_exact=not self._distinct_overflowed,
            min_length=self.min_length,
            max_length=self.max_length,
            padded_count=self.padded_count,
            typed_cell_count=self.typed_cell_count,
            type_candidates=tuple(candidates),
            date_formats=tuple(
                DateFormatMatch(label=label, matched=count)
                for label, count in sorted(self.date_labels.items())
            ),
            # Declaration order, and only the shapes something actually fitted.
            pattern_matches=tuple(
                PatternMatch(
                    pattern_id=pattern.pattern_id,
                    matched=self.pattern_hits[pattern.pattern_id],
                    considered=self.considered,
                )
                for pattern in PATTERNS
                if self.pattern_hits[pattern.pattern_id]
            ),
            observed_precision=(self.max_int_digits + self.max_scale) if decimal_seen else None,
            observed_scale=self.max_scale if decimal_seen else None,
            examples=tuple(self._examples),
            top_values=tuple(
                sorted(self._frequency.items(), key=lambda item: (-item[1], item[0]))[:TOP_VALUES]
            ),
            min_value=self.min_value,
            max_value=self.max_value,
        )


# ── reading ──────────────────────────────────────────────────────────────────
_BOMS: tuple[tuple[bytes, str, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig", "UTF-8"),
    (b"\xff\xfe", "utf-16", "UTF-16 (little-endian)"),
    (b"\xfe\xff", "utf-16", "UTF-16 (big-endian)"),
)

_DELIMITER_CANDIDATES = (",", "|", "\t", ";")
_DELIMITER_SAMPLE_LINES = 50


def _detect_bom(content: bytes) -> tuple[str | None, str | None]:
    for mark, codec, label in _BOMS:
        if content.startswith(mark):
            return label, codec
    return None, None


def _decode(content: bytes, declared: str) -> tuple[str, str, Refusal | None]:
    """Decode, or explain. Never decode with replacements.

    A mojibaked member name in an append-only layer cannot be corrected later,
    so the profiler refuses exactly where the parser refuses — and names the
    byte, because "not valid UTF-8" is not a sentence a payer can act on.
    """
    _, codec = _detect_bom(content)
    encoding = codec or declared
    try:
        return content.decode(encoding), encoding, None
    except UnicodeDecodeError as exc:
        offending = content[exc.start]
        try:
            as_latin1 = content[exc.start : exc.start + 1].decode("latin-1")
            hint = (
                f" In Western European (latin-1 / cp1252) that byte is {as_latin1!r}, which "
                "is the usual sign of an export saved with a regional default."
            )
        except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes every byte
            hint = ""
        return (
            "",
            encoding,
            Refusal(
                reason=RefusalReason.UNDECODABLE,
                explanation=(
                    f"The file is not valid {encoding}: byte {exc.start:,} is "
                    f"{offending:#04x}.{hint} Nothing was profiled, because reading it with "
                    "substitutions would put a corrupted name into a layer that cannot be "
                    "corrected afterwards."
                ),
                ask_the_payer=(
                    "Ask which character encoding the extract is saved in — UTF-8 or "
                    "Windows-1252 — and ask for a re-send in UTF-8 if they can."
                ),
            ),
        )


def _detect_line_ending(text: str) -> tuple[str, bool]:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and lf:
        return "mixed", True
    if crlf:
        return "\\r\\n", False
    return "\\n", False


def _detect_delimiter(
    text: str, *, declared: str | None
) -> tuple[str, tuple[DelimiterEvidence, ...]]:
    """Score each candidate by CONSISTENCY, quote-aware.

    Counting characters in the header line is the obvious approach and it is
    wrong for `"Smith, John",MBR000001` — the header of a file whose real
    delimiter is a comma looks identical to one whose delimiter is a pipe once
    a quoted comma is present. Parsing with each candidate and asking "did
    every row have the same number of fields?" is quote-aware for free,
    because `csv.reader` does the quoting.
    """
    lines = text.splitlines()[:_DELIMITER_SAMPLE_LINES]
    evidence: list[DelimiterEvidence] = []
    for candidate in _DELIMITER_CANDIDATES:
        counts = [
            len(row)
            for row in csv.reader(StringIO("\n".join(lines), newline=""), delimiter=candidate)
        ]
        if not counts:
            continue
        modal = Counter(counts).most_common(1)[0][0]
        evidence.append(
            DelimiterEvidence(
                delimiter=candidate,
                fields_per_row=modal,
                consistent_rows=sum(1 for c in counts if c == modal),
                rows_examined=len(counts),
            )
        )
    if declared:
        return declared, tuple(evidence)
    # More fields beats fewer; a tie goes to consistency, then to the fixed
    # candidate order — so the choice is reproducible rather than dict-ordered.
    best = max(
        evidence,
        key=lambda e: (e.fields_per_row > 1, e.consistent_rows, e.fields_per_row),
        default=None,
    )
    return (best.delimiter if best else ","), tuple(evidence)


def _rows(text: str, delimiter: str) -> Iterator[tuple[int, list[str]]]:
    reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter)
    for row in reader:
        yield reader.line_num, row


# ── the entry point ──────────────────────────────────────────────────────────
def profile_bytes(
    content: bytes,
    *,
    file_format: str,
    source_key: str = "",
    source_fingerprint: str = "",
    encoding: str = "utf-8",
    delimiter: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    distinct_cap: int = DEFAULT_DISTINCT_CAP,
    max_composite_pairs: int = DEFAULT_MAX_COMPOSITE_PAIRS,
    retain_rows: int = DEFAULT_RETAIN_ROWS,
    progress: ProgressCallback | None = None,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> FileProfile:
    """Profile a file's bytes. Returns a profile in EVERY case — never raises.

    An exception here would reach the wizard as a 500, and "something went
    wrong" is precisely the answer a BA cannot act on. An unreadable file comes
    back as `readable=False` with a `Refusal` that says what to ask the payer.
    """
    bytes_total = len(content)
    truncated = bytes_total > max_bytes
    window = content[:max_bytes] if truncated else content

    match file_format.lower():
        case "csv" | "txt" | "tsv" | "delimited" | "psv":
            return _profile_delimited(
                window,
                file_format=file_format.lower(),
                source_key=source_key,
                source_fingerprint=source_fingerprint,
                declared_encoding=encoding,
                delimiter="\t" if file_format.lower() == "tsv" else delimiter,
                bytes_total=bytes_total,
                truncated=truncated,
                distinct_cap=distinct_cap,
                max_composite_pairs=max_composite_pairs,
                retain_rows=retain_rows,
                progress=progress,
                progress_every=progress_every,
            )
        case "xlsx" | "xls" | "ods":
            return _profile_workbook(
                content,
                file_format=file_format.lower(),
                source_key=source_key,
                source_fingerprint=source_fingerprint,
                distinct_cap=distinct_cap,
                max_composite_pairs=max_composite_pairs,
                retain_rows=retain_rows,
                progress=progress,
                progress_every=progress_every,
            )
        case "ndjson" | "fhir_ndjson" | "hl7_json":
            return _profile_ndjson(
                window,
                file_format=file_format.lower(),
                source_key=source_key,
                source_fingerprint=source_fingerprint,
                declared_encoding=encoding,
                bytes_total=bytes_total,
                truncated=truncated,
            )
        case "fixed_width" | "fixed-width":
            return _profile_fixed_width(
                window,
                file_format=file_format.lower(),
                source_key=source_key,
                source_fingerprint=source_fingerprint,
                declared_encoding=encoding,
                bytes_total=bytes_total,
                truncated=truncated,
            )
        case _:
            return _refused(
                source_key,
                source_fingerprint,
                file_format,
                encoding,
                Refusal(
                    reason=RefusalReason.NO_PARSER,
                    explanation=(
                        f"The platform has no reader for {file_format!r} files yet. It reads "
                        "delimited files (csv, tsv, pipe-separated), spreadsheets, NDJSON "
                        "(including FHIR and HL7-derived JSON) and fixed-width files today."
                    ),
                    ask_the_payer=(
                        "Ask whether the extract can be delivered as a delimited file, a "
                        "spreadsheet, NDJSON or a fixed-width file, and raise the format with "
                        "the platform team if not."
                    ),
                ),
            )


def _refused(
    source_key: str,
    source_fingerprint: str,
    file_format: str,
    encoding: str,
    refusal: Refusal,
    *,
    bytes_total: int = 0,
) -> FileProfile:
    return FileProfile(
        source_key=source_key,
        source_fingerprint=source_fingerprint,
        readable=False,
        refusal=refusal,
        structure=FileStructure(
            file_format=file_format,
            encoding=encoding,
            declared_encoding=encoding,
            bytes_total=bytes_total,
        ),
    )


# ── CF-V3-E5-05 · nested NDJSON / FHIR / HL7-derived JSON ───────────────────


def _profile_ndjson(
    window: bytes,
    *,
    file_format: str,
    source_key: str,
    source_fingerprint: str,
    declared_encoding: str,
    bytes_total: int,
    truncated: bool,
) -> FileProfile:
    """One JSON document per line, tree-counted — never flattened here.

    Tolerant, exactly like the delimited reader: a line that will not parse
    is reported and skipped, not a crash and not a guess at what it meant.
    """
    text, encoding, refusal = _decode(window, declared_encoding)
    if refusal is not None:
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            declared_encoding,
            refusal,
            bytes_total=bytes_total,
        )
    bom, _ = _detect_bom(window)
    line_ending, mixed = _detect_line_ending(text)

    documents: list[dict[str, Any]] = []
    malformed_lines: list[int] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            parsed = _json.loads(stripped)
        except ValueError:
            malformed_lines.append(line_number)
            continue
        if isinstance(parsed, dict):
            documents.append(parsed)
        else:
            malformed_lines.append(line_number)

    findings: list[Finding] = []
    if mixed:
        findings.append(
            Finding(
                quirk=Quirk.MIXED_LINE_ENDINGS,
                detail="This file mixes \\r\\n and \\n line endings.",
            )
        )
    if malformed_lines:
        findings.append(
            Finding(
                quirk=Quirk.MALFORMED_JSON_LINE,
                detail=(
                    f"{len(malformed_lines)} line(s) did not parse as a JSON object and were "
                    "skipped — a strict reader would refuse this file."
                ),
                occurrences=len(malformed_lines),
                first_lines=tuple(malformed_lines[:FIRST_LINES]),
                blocks_ingestion=True,
            )
        )

    structure_paths = profile_structure(documents)
    top_level_fields = sum(1 for path in structure_paths if "." not in path.path)

    return FileProfile(
        source_key=source_key,
        source_fingerprint=source_fingerprint,
        structure=FileStructure(
            file_format=file_format,
            encoding=encoding,
            declared_encoding=declared_encoding,
            byte_order_mark=bom,
            line_ending=line_ending,
            column_count=top_level_fields,
            data_rows=len(documents),
            bytes_total=bytes_total,
            bytes_read=len(window),
            sampled=truncated,
        ),
        findings=tuple(findings),
        structure_paths=structure_paths,
        flatten_proposals=propose_flattening(structure_paths),
    )


# ── CF-V3-E5-05 · fixed-width boundary detection ─────────────────────────────


def _profile_fixed_width(
    window: bytes,
    *,
    file_format: str,
    source_key: str,
    source_fingerprint: str,
    declared_encoding: str,
    bytes_total: int,
    truncated: bool,
) -> FileProfile:
    """Statistical boundaries, plus every ambiguity a known reference layout
    (`CCLF1`) reveals — never one answer chosen silently."""
    text, encoding, refusal = _decode(window, declared_encoding)
    if refusal is not None:
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            declared_encoding,
            refusal,
            bytes_total=bytes_total,
        )
    bom, _ = _detect_bom(window)
    line_ending, mixed = _detect_line_ending(text)
    lines = [line for line in text.splitlines() if line]

    findings: list[Finding] = []
    if mixed:
        findings.append(
            Finding(
                quirk=Quirk.MIXED_LINE_ENDINGS,
                detail="This file mixes \\r\\n and \\n line endings.",
            )
        )

    lengths = Counter(len(line) for line in lines)
    if len(lengths) > 1:
        most_common_length, _ = lengths.most_common(1)[0]
        ragged = [
            number for number, line in enumerate(lines, start=1) if len(line) != most_common_length
        ]
        findings.append(
            Finding(
                quirk=Quirk.RAGGED_ROW,
                detail=(
                    f"{len(ragged)} of {len(lines)} line(s) are not the sample's most common "
                    f"length ({most_common_length} characters) — boundaries are detected "
                    "against the common length only."
                ),
                occurrences=len(ragged),
                first_lines=tuple(ragged[:FIRST_LINES]),
            )
        )

    layout = detect_fixed_width_boundaries(lines)

    for reference_name, reference_fields in KNOWN_FIXED_WIDTH_LAYOUTS.items():
        reference = layout_from_reference(reference_name, reference_fields)
        if reference.line_width != layout.line_width:
            continue  # a layout for a different-width file proves nothing here
        for ambiguity in ambiguous_boundaries(layout, reference):
            names = ", ".join(
                f"{c.name} ({c.start}-{c.end})" for c in ambiguity.reference_columns if c.name
            )
            findings.append(
                Finding(
                    quirk=Quirk.AMBIGUOUS_FIXED_WIDTH_BOUNDARY,
                    detail=(
                        f"Positions {ambiguity.statistical.start}-{ambiguity.statistical.end} "
                        "read as one column from this sample's whitespace alone, but the "
                        f"{reference_name} reference layout names "
                        f"{len(ambiguity.reference_columns)} separate fields there: {names}. "
                        "Both readings are shown — the profiler does not choose."
                    ),
                )
            )

    return FileProfile(
        source_key=source_key,
        source_fingerprint=source_fingerprint,
        structure=FileStructure(
            file_format=file_format,
            encoding=encoding,
            declared_encoding=declared_encoding,
            byte_order_mark=bom,
            line_ending=line_ending,
            column_count=len(layout.columns),
            data_rows=len(lines),
            bytes_total=bytes_total,
            bytes_read=len(window),
            sampled=truncated,
        ),
        findings=tuple(findings),
        fixed_width_layout=layout,
    )


def _profile_delimited(
    window: bytes,
    *,
    file_format: str,
    source_key: str,
    source_fingerprint: str,
    declared_encoding: str,
    delimiter: str | None,
    bytes_total: int,
    truncated: bool,
    distinct_cap: int,
    max_composite_pairs: int,
    retain_rows: int,
    progress: ProgressCallback | None,
    progress_every: int,
) -> FileProfile:
    if not window.strip():
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            declared_encoding,
            Refusal(
                reason=RefusalReason.EMPTY_FILE,
                explanation="The file contains no data — it is empty or only whitespace.",
                ask_the_payer=(
                    "Ask whether the extract ran and produced no rows, or failed. A genuine "
                    "zero-row delivery should still carry its header."
                ),
            ),
            bytes_total=bytes_total,
        )

    bom_label, _ = _detect_bom(window)
    text, encoding, refusal = _decode(window, declared_encoding)
    if refusal is not None:
        return _refused(
            source_key, source_fingerprint, file_format, encoding, refusal, bytes_total=bytes_total
        )

    findings: list[Finding] = []
    if bom_label:
        findings.append(
            Finding(
                quirk=Quirk.BYTE_ORDER_MARK,
                detail=(
                    f"The file begins with a {bom_label} byte-order mark — the marker Excel "
                    "writes when it saves as 'CSV UTF-8'. The platform consumes it, so the "
                    "first column is read correctly; it is reported because a payer who "
                    "starts sending one has changed their export tool."
                ),
            )
        )

    line_ending, mixed = _detect_line_ending(text)
    if mixed:
        findings.append(
            Finding(
                quirk=Quirk.MIXED_LINE_ENDINGS,
                detail=(
                    "Line endings are mixed within the file (both Windows and Unix). It reads "
                    "correctly, and it usually means the extract was assembled from more than "
                    "one process."
                ),
            )
        )

    chosen, evidence = _detect_delimiter(text, declared=delimiter)
    reader = _rows(text, chosen)
    try:
        header_line, header = next(reader)
    except StopIteration:  # pragma: no cover - the empty case is refused above
        header = []
        header_line = 1

    columns = [name.strip() for name in header]
    if not columns:
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            encoding,
            Refusal(
                reason=RefusalReason.NO_HEADER,
                explanation="The first line is empty, so there are no column names to read.",
                ask_the_payer=(
                    "Ask for a file with a header row, or for the record layout so the columns "
                    "can be declared on the feed instead."
                ),
            ),
            bytes_total=bytes_total,
        )

    findings.extend(_header_findings(columns))
    findings.extend(_quoted_delimiter_finding(text, chosen))

    accumulators = [
        _ColumnAccumulator(name, index, distinct_cap) for index, name in enumerate(columns)
    ]

    width = len(columns)
    short_rows: list[int] = []
    long_rows: list[int] = []
    embedded_newlines = 0
    row_digests: dict[str, list[int]] = {}
    rows_read = 0
    retained: list[list[str]] = []

    for line_number, row in reader:
        if not any(field_value.strip() for field_value in row):
            continue  # a trailing blank line is not a record
        rows_read += 1
        if len(row) < width:
            short_rows.append(line_number)
        elif len(row) > width:
            long_rows.append(line_number)
        padded = list(row[:width]) + [""] * max(0, width - len(row))
        for index, accumulator in enumerate(accumulators):
            value = padded[index]
            if "\n" in value or "\r" in value:
                embedded_newlines += 1
            accumulator.add(value, was_typed=False)
        digest = "\x1f".join(padded)
        row_digests.setdefault(digest, []).append(line_number)
        if len(retained) < retain_rows:
            retained.append(padded)
        if progress and rows_read % progress_every == 0:
            progress(
                Progress(
                    phase="scan",
                    rows_read=rows_read,
                    bytes_read=len(window),
                    bytes_total=bytes_total,
                )
            )

    findings.extend(_row_shape_findings(short_rows, long_rows, width, embedded_newlines))
    findings.extend(_value_findings(accumulators))

    column_profiles = tuple(accumulator.finish() for accumulator in accumulators)
    keys, search = _key_candidates(
        column_profiles, retained, max_composite_pairs=max_composite_pairs
    )
    duplicates = _duplicate_rows(row_digests)
    if duplicates.duplicate_rows:
        findings.append(
            Finding(
                quirk=Quirk.DUPLICATE_ROW,
                detail=(
                    f"{duplicates.duplicate_rows:,} row(s) are byte-identical to an earlier row "
                    f"in the same file, across {duplicates.duplicate_groups:,} group(s). The "
                    "same member delivered twice in one file is an ordinary delivery fault, so "
                    "it becomes an attributed drop rather than a failed batch."
                ),
                occurrences=duplicates.duplicate_rows,
                first_lines=duplicates.first_lines,
            )
        )

    if progress:
        progress(
            Progress(
                phase="done",
                rows_read=rows_read,
                bytes_read=len(window),
                bytes_total=bytes_total,
            )
        )

    return FileProfile(
        source_key=source_key,
        source_fingerprint=source_fingerprint,
        structure=FileStructure(
            file_format=file_format,
            encoding=encoding,
            declared_encoding=declared_encoding,
            byte_order_mark=bom_label,
            delimiter=chosen,
            quote_char='"',
            line_ending=line_ending,
            header_line=header_line,
            column_count=width,
            data_rows=rows_read,
            bytes_total=bytes_total,
            bytes_read=len(window),
            sampled=truncated,
            delimiter_evidence=evidence,
        ),
        columns=column_profiles,
        findings=tuple(findings),
        key_candidates=keys,
        key_search=search,
        duplicates=duplicates,
    )


def _profile_workbook(
    content: bytes,
    *,
    file_format: str,
    source_key: str,
    source_fingerprint: str,
    distinct_cap: int,
    max_composite_pairs: int,
    retain_rows: int,
    progress: ProgressCallback | None,
    progress_every: int,
) -> FileProfile:
    """A spreadsheet, whose cells arrive ALREADY TYPED.

    That is the quirk the story names: a member id stored as a number comes
    back as 1000042.0, and a date of birth comes back as a `datetime`. Both
    normalise to the same text the CSV path would produce — and the count of
    cells that needed normalising is reported per column, because a payer
    sending typed cells is why the same member fails to match across two feeds.
    """
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:  # pragma: no cover - declared in requirements
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            "n/a",
            Refusal(
                reason=RefusalReason.UNREADABLE_WORKBOOK,
                explanation=f"The spreadsheet reader is not installed on this deployment: {exc}",
                ask_the_payer="Nothing — this is a platform installation issue, not a file issue.",
            ),
            bytes_total=len(content),
        )

    try:
        from io import BytesIO

        workbook = CalamineWorkbook.from_filelike(BytesIO(content))
        sheet = workbook.get_sheet_by_index(0).to_python(skip_empty_area=True)
    except Exception as exc:
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            "n/a",
            Refusal(
                reason=RefusalReason.UNREADABLE_WORKBOOK,
                explanation=(
                    f"The file could not be opened as a spreadsheet ({exc}). A file named "
                    ".xlsx that is actually a CSV, an HTML export or a password-protected "
                    "workbook all look like this."
                ),
                ask_the_payer=(
                    "Ask whether the file is a genuine Excel workbook and whether it is "
                    "password-protected; if it is really a CSV, ask for the extension to match."
                ),
            ),
            bytes_total=len(content),
        )

    if not sheet:
        return _refused(
            source_key,
            source_fingerprint,
            file_format,
            "n/a",
            Refusal(
                reason=RefusalReason.EMPTY_FILE,
                explanation="The workbook's first sheet is empty.",
                ask_the_payer=(
                    "Ask which sheet holds the data — an extract with the data on a second "
                    "sheet is common, and the feed can be told which one to read."
                ),
            ),
            bytes_total=len(content),
        )

    columns = [str(cell).strip() for cell in sheet[0]]
    width = len(columns)
    findings = list(_header_findings(columns))
    accumulators = [
        _ColumnAccumulator(name, index, distinct_cap) for index, name in enumerate(columns)
    ]

    short_rows: list[int] = []
    long_rows: list[int] = []
    row_digests: dict[str, list[int]] = {}
    retained: list[list[str]] = []
    rows_read = 0

    for line_number, row in enumerate(sheet[1:], start=2):
        if not any(str(cell).strip() for cell in row):
            continue
        rows_read += 1
        if len(row) < width:
            short_rows.append(line_number)
        elif len(row) > width:
            long_rows.append(line_number)
        cells = list(row[:width]) + [""] * max(0, width - len(row))
        padded = [cell_to_text(cell) for cell in cells]
        for index, accumulator in enumerate(accumulators):
            accumulator.add(padded[index], was_typed=not isinstance(cells[index], str))
        digest = "\x1f".join(padded)
        row_digests.setdefault(digest, []).append(line_number)
        if len(retained) < retain_rows:
            retained.append(padded)
        if progress and rows_read % progress_every == 0:
            progress(
                Progress(
                    phase="scan",
                    rows_read=rows_read,
                    bytes_read=len(content),
                    bytes_total=len(content),
                )
            )

    findings.extend(_row_shape_findings(short_rows, long_rows, width, 0))
    findings.extend(_value_findings(accumulators))
    typed = [a for a in accumulators if a.typed_cell_count]
    if typed:
        findings.append(
            Finding(
                quirk=Quirk.TYPED_CELL,
                detail=(
                    f"{sum(a.typed_cell_count for a in typed):,} cell(s) arrived as spreadsheet "
                    "types rather than text — numbers, dates or booleans. They are normalised "
                    "the same way the pipeline will normalise them (a whole number loses its "
                    "'.0', so member id 1000042 does not become '1000042.0' and stop matching "
                    "the same member arriving in a CSV)."
                ),
                occurrences=sum(a.typed_cell_count for a in typed),
                columns=tuple(a.name for a in typed),
            )
        )

    column_profiles = tuple(a.finish() for a in accumulators)
    keys, search = _key_candidates(
        column_profiles, retained, max_composite_pairs=max_composite_pairs
    )
    duplicates = _duplicate_rows(row_digests)

    if progress:
        progress(
            Progress(
                phase="done",
                rows_read=rows_read,
                bytes_read=len(content),
                bytes_total=len(content),
            )
        )

    return FileProfile(
        source_key=source_key,
        source_fingerprint=source_fingerprint,
        structure=FileStructure(
            file_format=file_format,
            encoding="n/a",
            declared_encoding="n/a",
            delimiter=None,
            quote_char=None,
            line_ending="n/a",
            header_line=1,
            column_count=width,
            data_rows=rows_read,
            bytes_total=len(content),
            bytes_read=len(content),
        ),
        columns=column_profiles,
        findings=tuple(findings),
        key_candidates=keys,
        key_search=search,
        duplicates=duplicates,
    )


# ── findings ─────────────────────────────────────────────────────────────────
def _quoted_delimiter_finding(text: str, delimiter: str) -> tuple[Finding, ...]:
    """A value that CONTAINS the delimiter, after quote-aware parsing.

    Reported rather than silently handled, because the same file read by a
    payer's own tooling — or by a spreadsheet with the wrong import settings —
    splits those values, and that disagreement is worth naming before anyone
    compares row counts.
    """
    head = "\n".join(text.splitlines()[:_DELIMITER_SAMPLE_LINES])
    if '"' not in head:
        return ()
    quoted = sum(
        1
        for row in csv.reader(StringIO(head, newline=""), delimiter=delimiter)
        for value in row
        if delimiter in value
    )
    if not quoted:
        return ()
    return (
        Finding(
            quirk=Quirk.QUOTED_DELIMITER,
            detail=(
                f"{quoted} value(s) in the first {_DELIMITER_SAMPLE_LINES} lines contain the "
                'delimiter inside quotes — a name written "Smith, John", typically. The '
                "reader is quote-aware, so these stay one field; a tool that splits on the "
                "delimiter without honouring quotes will disagree about the column count."
            ),
            occurrences=quoted,
        ),
    )


def _header_findings(columns: Sequence[str]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    duplicates = sorted({c for c in columns if columns.count(c) > 1 and c})
    if duplicates:
        findings.append(
            Finding(
                quirk=Quirk.DUPLICATE_HEADER,
                detail=(
                    f"The header names {', '.join(duplicates)} more than once. The platform "
                    "reads columns by name, so two columns with one name cannot be told apart "
                    "and the file will be refused at ingestion."
                ),
                occurrences=len(duplicates),
                columns=tuple(duplicates),
                blocks_ingestion=True,
            )
        )
    empty = tuple(str(index + 1) for index, name in enumerate(columns) if not name)
    if empty:
        findings.append(
            Finding(
                quirk=Quirk.EMPTY_HEADER_NAME,
                detail=(
                    f"Column(s) at position {', '.join(empty)} have no name in the header. "
                    "They can still be mapped by position, but nothing can reference them by "
                    "name until the payer supplies one."
                ),
                occurrences=len(empty),
                blocks_ingestion=len(empty) > 1,
            )
        )
    return tuple(findings)


def _row_shape_findings(
    short_rows: Sequence[int], long_rows: Sequence[int], width: int, embedded_newlines: int
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    if short_rows or long_rows:
        detail_parts = []
        if short_rows:
            detail_parts.append(f"{len(short_rows):,} row(s) have FEWER than {width} fields")
        if long_rows:
            detail_parts.append(f"{len(long_rows):,} row(s) have MORE than {width} fields")
        findings.append(
            Finding(
                quirk=Quirk.RAGGED_ROW,
                detail=(
                    f"{' and '.join(detail_parts)}. They were profiled anyway — short rows "
                    "padded, extra fields ignored — so you can see the rest of the file. The "
                    "pipeline will refuse the file until they are fixed, because a row whose "
                    "fields have shifted loads the wrong value into the wrong column."
                ),
                occurrences=len(short_rows) + len(long_rows),
                first_lines=tuple(sorted([*short_rows, *long_rows])[:FIRST_LINES]),
                blocks_ingestion=True,
            )
        )
    if embedded_newlines:
        findings.append(
            Finding(
                quirk=Quirk.EMBEDDED_NEWLINE,
                detail=(
                    f"{embedded_newlines:,} value(s) contain a line break inside quotes — an "
                    "address field, usually. The reader handles it; a tool that counts lines "
                    "to count records will disagree with the platform's row count."
                ),
                occurrences=embedded_newlines,
            )
        )
    return tuple(findings)


def _value_findings(accumulators: Sequence[_ColumnAccumulator]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    null_like = [a for a in accumulators if a.null_like_count]
    if null_like:
        findings.append(
            Finding(
                quirk=Quirk.NULL_LIKE_TOKEN,
                detail=(
                    "Some values spell a null out as text — 'NULL', 'N/A', '-' and similar. "
                    "They are NOT empty, so a completeness rule counts them as populated and "
                    "the column loads the word rather than a null. Decide per column whether "
                    "they should be treated as missing."
                ),
                occurrences=sum(a.null_like_count for a in null_like),
                columns=tuple(a.name for a in null_like),
            )
        )
    padded = [a for a in accumulators if a.padded_count]
    if padded:
        findings.append(
            Finding(
                quirk=Quirk.WHITESPACE_PADDING,
                detail=(
                    "Some values carry leading or trailing spaces. They are trimmed for "
                    "profiling and by the reader, but an untrimmed key is why the same member "
                    "can appear twice."
                ),
                occurrences=sum(a.padded_count for a in padded),
                columns=tuple(a.name for a in padded),
            )
        )
    return tuple(findings)


def _duplicate_rows(row_digests: dict[str, list[int]]) -> DuplicateRows:
    groups = [lines for lines in row_digests.values() if len(lines) > 1]
    if not groups:
        return DuplicateRows()
    return DuplicateRows(
        duplicate_groups=len(groups),
        duplicate_rows=sum(len(lines) - 1 for lines in groups),
        first_lines=tuple(sorted(lines[1] for lines in groups)[:FIRST_LINES]),
    )


# ── candidate keys ───────────────────────────────────────────────────────────
def _key_candidates(
    columns: Sequence[ColumnProfile],
    rows: Sequence[Sequence[str]],
    *,
    max_composite_pairs: int,
) -> tuple[tuple[KeyCandidate, ...], KeySearch]:
    """Single columns always; pairs within a stated bound.

    Single-column candidates come free from the first pass. Pairs need a second
    look at the retained rows, so they are bounded — and the bound is REPORTED,
    because "no composite key found" after examining 50 of 703 pairs is not the
    same statement as "no composite key exists".
    """
    excluded = tuple(sorted(c.name for c in columns if not c.distinct_is_exact))
    singles: list[KeyCandidate] = []
    for index, column in enumerate(columns):
        if not column.distinct_is_exact:
            continue
        # EXACT, from the first pass: populated minus distinct is the number of
        # repeats, whatever the retention bound did. Only the EXAMPLES come
        # from retained rows, and an example is a courtesy — a count is not.
        _, examples = _duplicate_values(rows, (index,))
        singles.append(
            KeyCandidate(
                columns=(column.name,),
                distinct_count=column.distinct_count,
                populated_rows=column.populated_count,
                null_rows=column.null_count,
                duplicate_values=column.populated_count - column.distinct_count,
                examples=examples,
            )
        )

    unique_singles = {k.columns[0] for k in singles if k.is_unique}
    # A pair containing a column that is already unique on its own is not a
    # discovery, so those are excluded rather than counted as skipped.
    pairable = [
        (index, column)
        for index, column in enumerate(columns)
        if column.distinct_is_exact
        and column.name not in unique_singles
        and column.distinct_count > 1
    ]
    # Highest cardinality first: the pairs most likely to be unique are tried
    # first, so a bound that bites removes the least promising candidates.
    pairable.sort(key=lambda item: (-item[1].distinct_count, item[1].name))

    pairs = [(a, b) for position, a in enumerate(pairable) for b in pairable[position + 1 :]]
    examined = pairs[:max_composite_pairs]
    composites: list[KeyCandidate] = []
    for (left_index, left), (right_index, right) in examined:
        duplicates, examples = _duplicate_values(rows, (left_index, right_index))
        populated = sum(1 for row in rows if row[left_index].strip() and row[right_index].strip())
        distinct = len(
            {
                f"{row[left_index]}\x1f{row[right_index]}"
                for row in rows
                if row[left_index].strip() and row[right_index].strip()
            }
        )
        candidate = KeyCandidate(
            columns=tuple(sorted((left.name, right.name))),
            distinct_count=distinct,
            populated_rows=populated,
            null_rows=len(rows) - populated,
            duplicate_values=duplicates,
            examples=examples,
        )
        if candidate.is_unique:
            composites.append(candidate)

    notes: list[str] = []
    if excluded:
        notes.append(
            "Columns whose distinct count exceeded the profiler's exact-counting limit are "
            "excluded from key candidacy — unknown, not assumed non-unique."
        )
    if len(pairs) > len(examined):
        notes.append(
            f"{len(pairs) - len(examined):,} column pair(s) were not examined. Pairs are tried "
            "highest-cardinality first, so the ones most likely to be unique were tried; "
            "'no composite key found' here means none was found AMONG THOSE EXAMINED."
        )
    search = KeySearch(
        single_columns_examined=len(singles),
        composite_width=2,
        pairs_examined=len(examined),
        pairs_skipped=len(pairs) - len(examined),
        rows_retained=len(rows),
        excluded_columns=excluded,
        note=" ".join(notes),
    )
    # Usable keys first, then narrow before wide, then LEFT TO RIGHT as the
    # file presents them — a BA reads a candidate list against the file in
    # front of them, and alphabetical order is nobody's mental model.
    position_of = {column.name: column.position for column in columns}
    ordered = sorted(
        [*singles, *composites],
        key=lambda k: (
            not k.is_unique,
            len(k.columns),
            tuple(position_of.get(name, 0) for name in k.columns),
        ),
    )
    return tuple(ordered), search


def _duplicate_values(
    rows: Sequence[Sequence[str]], indexes: tuple[int, ...]
) -> tuple[int, tuple[tuple[str, tuple[int, ...]], ...]]:
    """How many key values repeat, and the first few that do — WITH LINES.

    The line numbers are what turn "MemberID is not unique" into something a BA
    can look at. They are value-bearing, so they redact with the values.
    """
    seen: dict[str, list[int]] = {}
    for offset, row in enumerate(rows):
        parts = [row[i].strip() for i in indexes]
        if not all(parts):
            continue
        seen.setdefault("\x1f".join(parts), []).append(offset + 2)
    repeated = [(value, lines) for value, lines in seen.items() if len(lines) > 1]
    repeated.sort(key=lambda item: (-len(item[1]), item[0]))
    return (
        sum(len(lines) - 1 for _, lines in repeated),
        tuple((value, tuple(lines[:FIRST_LINES])) for value, lines in repeated[:EXAMPLE_VALUES]),
    )


def suggest_contract_columns(profile: FileProfile) -> tuple[dict[str, Any], ...]:
    """The deterministic half of CF-V1-E5-02, computed here and for free.

    Every entry carries what the arithmetic supports and NOTHING ELSE: a type
    where exactly one fitted every value, `None` where more than one did, and
    the nullability the null count actually shows. The inference agent starts
    from this and is asked only about the entries whose `type` is None — which
    is the deterministic-first rule turned into a token budget.
    """
    return tuple(
        {
            "source_name": column.name,
            "position": column.position,
            "type": column.narrowest_type.value if column.narrowest_type else None,
            "nullable": column.null_count > 0,
            "precision": column.observed_precision,
            "scale": column.observed_scale,
            "date_formats": [f.label for f in column.date_formats],
            "needs_input": column.narrowest_type is None,
            "evidence": {
                "rows": column.row_count,
                "populated": column.populated_count,
                "distinct": column.distinct_count,
                "distinct_is_exact": column.distinct_is_exact,
                "candidates": [
                    f"{c.type.value} {c.matched}/{c.considered}" for c in column.type_candidates
                ],
            },
        }
        for column in profile.columns
    )
