"""CF-V3-E5-05 — the two computations `core.profiling` did not yet have:
nested-structure tree counting, and fixed-width boundary detection.

    "the profiler and schema tools extended to nested NDJSON/FHIR (structure
     trees, repeating groups), HL7-derived JSON, and fixed-width files
     (boundary detection), so that onboarding a CMS FHIR feed or an ADT
     source gets the same guided, evidence-based start as a simple CSV did."
    "Visualize nested structure as an explorable tree with counts at every
     path."
    "Propose flattening rules the mapping studio can consume directly."
    "Detect fixed-width boundaries statistically and show the confidence
     evidence per column."
    — CF-V3-E5-05

`core/profiling/__init__.py` already has the socket this plugs into: its own
`profile_bytes` refuses NDJSON and fixed-width today with the exact words
"FHIR NDJSON, HL7-derived JSON and fixed-width arrive in later waves" — this
module is that later wave, kept SEPARATE from the 2000-line profiler file for
the same reason `core/claim_lineage.py` stayed out of `core/mapping`: these
are BATCH-shaped computations (a tree needs every document in the sample; a
boundary needs every line), not the per-row streaming shape the rest of that
file is built around. `core/profiling/__init__.py` imports this module and
calls it from two new dispatch cases; this module imports nothing back.

THE DELIBERATE-FIRST RULE, KEPT. Same discipline as the flat-file profiler:
every number here is arithmetic over the sample (a count, a fraction of
rows agreeing a position is blank), never a model call. Ambiguity is
reported, never resolved by guessing — the fixed-width exception below is
this module's own version of the flat profiler's "no field named `type`":
there is no field named `the` boundary, only candidates and their evidence.

THE CCLF LAYOUT IS HARVESTED, NOT INVENTED. `CCLF1_LAYOUT` is CCLF1's real,
complete field layout — every one of its 36 fields' exact byte positions —
copied verbatim from `BCDA_Data_Dictionary.xlsx`'s own "CCLF-FHIR STU3
Mapping" sheet (columns "CCLF Start Position" / "CCLF End Position"). It
exists here for exactly one reason: two of its real fields,
`CLM_BILL_FAC_TYPE_CD` (position 64) and `CLM_BILL_CLSFCTN_CD` (position
65), are adjacent one-character codes with no delimiter and no padding
between them — a real case where whitespace-based statistical detection
CANNOT tell two columns from one, which is precisely the exception this
story names: "two plausible boundary interpretations for adjacent columns."
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# ── nested structure trees (NDJSON / FHIR / HL7-derived JSON) ───────────────


@dataclass(frozen=True)
class StructurePath:
    """One JSON path, across a sample of documents: how many documents
    populate it, and — when it is a repeating group — how many elements it
    carries per occurrence.

    `path` is dotted (`item.adjudication`), with no array index: an array's
    elements share their collection's path, because "how many documents
    have at least one adjudication" and "how many adjudications does item
    #3 have" are different questions, and only the first is what "counts at
    every path" asks for. The second is `array_length_min`/`_max`.
    """

    path: str
    documents_with_path: int
    documents_total: int
    is_array: bool = False
    array_length_min: int | None = None
    array_length_max: int | None = None
    array_length_total: int = 0
    array_occurrences: int = 0

    @property
    def fill_rate(self) -> float:
        return self.documents_with_path / self.documents_total if self.documents_total else 0.0

    @property
    def array_length_avg(self) -> float | None:
        if self.array_occurrences == 0:
            return None
        return self.array_length_total / self.array_occurrences


def profile_structure(documents: Sequence[Mapping[str, Any]]) -> tuple[StructurePath, ...]:
    """Walk a sample of parsed JSON documents and count, at every distinct
    path, how many documents populate it and — for repeating groups — how
    many elements each occurrence carries.

    Pure arithmetic over already-parsed structures, matching `core.
    profiling`'s own rule that the facts come from computation: this
    function does not parse bytes (the caller decodes each NDJSON line) and
    it does not decide anything about the shape it finds, only counts it.
    """
    touched: dict[str, set[int]] = {}
    array_lengths: dict[str, list[int]] = {}

    def walk(value: Any, path: str, doc_index: int) -> None:
        if isinstance(value, Mapping):
            for key, sub in value.items():
                sub_path = f"{path}.{key}" if path else str(key)
                touched.setdefault(sub_path, set()).add(doc_index)
                walk(sub, sub_path, doc_index)
        elif isinstance(value, list):
            array_lengths.setdefault(path, []).append(len(value))
            for item in value:
                walk(item, path, doc_index)

    for index, document in enumerate(documents):
        walk(document, "", index)

    total = len(documents)
    results = []
    for path in sorted(touched):
        lengths = array_lengths.get(path)
        results.append(
            StructurePath(
                path=path,
                documents_with_path=len(touched[path]),
                documents_total=total,
                is_array=lengths is not None,
                array_length_min=min(lengths) if lengths else None,
                array_length_max=max(lengths) if lengths else None,
                array_length_total=sum(lengths) if lengths else 0,
                array_occurrences=len(lengths) if lengths else 0,
            )
        )
    return tuple(results)


@dataclass(frozen=True)
class FlattenProposal:
    """One repeating group, proposed as a flattening target — never
    applied. CF-V3-E5-05's own `Don'ts`: "Flatten silently — every
    structural transformation is a visible, approvable proposal." A BA (or
    Data Engineer, downstream in `core.structural_transforms`) reviews this
    and decides; nothing here writes a mapping line.
    """

    source_path: str
    proposed_entity: str
    element_count_min: int
    element_count_max: int
    description: str


def propose_flattening(paths: Sequence[StructurePath]) -> tuple[FlattenProposal, ...]:
    """Every repeating group in `paths` becomes one reviewable proposal,
    for "the mapping studio to consume directly" — described in the same
    plain-English style `core.mapping.Transform.describe()` uses, so a
    reviewer reads a sentence, not a path expression.
    """
    return tuple(
        FlattenProposal(
            source_path=path.path,
            proposed_entity=_entity_name(path.path),
            element_count_min=path.array_length_min or 0,
            element_count_max=path.array_length_max or 0,
            description=(
                f"{path.path}: {path.documents_with_path} of {path.documents_total} "
                f"document(s) carry this repeating group, {path.array_length_min}-"
                f"{path.array_length_max} element(s) each — propose flattening to one "
                f"{_entity_name(path.path)!r} row per element."
            ),
        )
        for path in sorted((p for p in paths if p.is_array), key=lambda p: p.path)
    )


def _entity_name(path: str) -> str:
    return "_".join(path.split("."))


# ── fixed-width boundary detection ───────────────────────────────────────────


@dataclass(frozen=True)
class FixedWidthColumn:
    """One column, 1-based and inclusive at both ends — matching how the
    harvested CCLF layout itself is written, so a position from one can be
    compared to the other with no off-by-one translation anywhere."""

    start: int
    end: int
    name: str | None = None
    confidence: float = 1.0

    @property
    def width(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class FixedWidthLayout:
    """One full candidate segmentation of a line. `source` is
    `"statistical"` for a detected layout, or a reference layout's own name
    (`"CCLF1"`) when it came from `layout_from_reference`."""

    columns: tuple[FixedWidthColumn, ...]
    source: str = "statistical"

    @property
    def line_width(self) -> int:
        return self.columns[-1].end if self.columns else 0


def detect_fixed_width_boundaries(
    lines: Sequence[str], *, min_gap_confidence: float = 0.95
) -> FixedWidthLayout:
    """The ONLY signal available with no reference layout: a character
    position is a column boundary when it is blank in at least
    `min_gap_confidence` of the sample's rows. Everything between two
    boundaries (or the line's own edges) is one column.

    This is honestly incomplete on purpose — see the module docstring's
    CCLF1 example. Two always-populated adjacent columns produce no gap
    between them and are indistinguishable from one wider column by this
    function alone; `ambiguous_boundaries` is where a NAMED reference
    layout is allowed to say so.
    """
    if not lines:
        return FixedWidthLayout(columns=())

    width = max(len(line) for line in lines)
    gap_confidence: dict[int, float] = {}
    for position in range(width):
        blank = sum(1 for line in lines if position >= len(line) or line[position] == " ")
        gap_confidence[position] = blank / len(lines)

    is_gap = {
        position: confidence >= min_gap_confidence
        for position, confidence in gap_confidence.items()
    }

    columns: list[FixedWidthColumn] = []
    start: int | None = None
    for position in range(width):
        if is_gap[position]:
            if start is not None:
                columns.append(_close_column(start, position - 1, gap_confidence))
                start = None
            continue
        if start is None:
            start = position
    if start is not None:
        columns.append(_close_column(start, width - 1, gap_confidence))

    return FixedWidthLayout(columns=tuple(columns))


def _close_column(start0: int, end0: int, gap_confidence: Mapping[int, float]) -> FixedWidthColumn:
    #: The column's confidence is how sure the sample is about the boundary
    #: that OPENED it — the line's own start needs no evidence to be a
    #: boundary, so a column beginning at position 0 is reported at full
    #: confidence rather than penalised for a boundary nothing needed to
    #: prove.
    leading_gap_confidence = gap_confidence.get(start0 - 1, 1.0) if start0 > 0 else 1.0
    return FixedWidthColumn(start=start0 + 1, end=end0 + 1, confidence=leading_gap_confidence)


def layout_from_reference(name: str, fields: Sequence[tuple[str, int, int]]) -> FixedWidthLayout:
    """A named, harvested layout (`CCLF1_LAYOUT`), in the same shape as a
    detected one so the two can be compared and shown side by side."""
    return FixedWidthLayout(
        columns=tuple(
            FixedWidthColumn(start=start, end=end, name=field_name, confidence=1.0)
            for field_name, start, end in fields
        ),
        source=name,
    )


@dataclass(frozen=True)
class BoundaryAmbiguity:
    """One statistically-detected column that a named reference layout
    would instead split into two or more narrower ones — the shape of
    "two plausible boundary interpretations for adjacent columns," with
    both readings carried so a BA can choose rather than have one guessed
    for them.
    """

    statistical: FixedWidthColumn
    reference_columns: tuple[FixedWidthColumn, ...]
    reference_name: str


def ambiguous_boundaries(
    statistical: FixedWidthLayout, reference: FixedWidthLayout
) -> tuple[BoundaryAmbiguity, ...]:
    """Every statistical column overlapping MORE THAN ONE reference
    column — the reference layout drew a boundary the sample's whitespace
    gave no evidence for, because the fields either side of it are always
    populated."""
    ambiguities = []
    for column in statistical.columns:
        overlapping = tuple(
            candidate
            for candidate in reference.columns
            if candidate.start <= column.end and candidate.end >= column.start
        )
        if len(overlapping) > 1:
            ambiguities.append(
                BoundaryAmbiguity(
                    statistical=column,
                    reference_columns=overlapping,
                    reference_name=reference.source,
                )
            )
    return tuple(ambiguities)


#: CCLF1's complete, real field layout — harvested verbatim from
#: `clientdata/Uploads/Claims Mapping/BCDA_Data_Dictionary (1).xlsx`, sheet
#: "CCLF-FHIR STU3 Mapping", columns "CCLF Start Position" / "CCLF End
#: Position". 37 fields, positions 1-292, contiguous with no gaps —
#: precisely why a whitespace-only scan of a real CCLF1 extract cannot
#: recover it: there is nowhere in the layout FOR a gap to be.
CCLF1_LAYOUT: tuple[tuple[str, int, int], ...] = (
    ("CUR_CLM_UNIQ_ID", 1, 13),
    ("PRVDR_OSCAR_NUM", 14, 19),
    ("BENE_MBI_ID", 20, 30),
    ("BENE_HIC_NUM", 31, 41),
    ("CLM_TYPE_CD", 42, 43),
    ("CLM_FROM_DT", 44, 53),
    ("CLM_THRU_DT", 54, 63),
    ("CLM_BILL_FAC_TYPE_CD", 64, 64),
    ("CLM_BILL_CLSFCTN_CD", 65, 65),
    ("PRNCPL_DGNS_CD", 66, 72),
    ("ADMTG_DGNS_CD", 73, 79),
    ("CLM_MDCR_NPMT_RSN_CD", 80, 81),
    ("CLM_PMT_AMT", 82, 98),
    ("CLM_NCH_PRMRY_PYR_CD", 99, 99),
    ("PRVDR_FAC_FIPS_ST_CD", 100, 101),
    ("BENE_PTNT_STUS_CD", 102, 103),
    ("DGNS_DRG_CD", 104, 107),
    ("CLM_OP_SRVC_TYPE_CD", 108, 108),
    ("FAC_PRVDR_NPI_NUM", 109, 118),
    ("OPRTG_PRVDR_NPI_NUM", 119, 128),
    ("ATNDG_PRVDR_NPI_NUM", 129, 138),
    ("OTHR_PRVDR_NPI_NUM", 139, 148),
    ("CLM_ADJSMT_TYPE_CD", 149, 150),
    ("CLM_EFCTV_DT", 151, 160),
    ("CLM_IDR_LD_DT", 161, 170),
    ("BENE_EQTBL_BIC_HICN_NUM", 171, 181),
    ("CLM_ADMSN_TYPE_CD", 182, 183),
    ("CLM_ADMSN_SRC_CD", 184, 185),
    ("CLM_BILL_FREQ_CD", 186, 186),
    ("CLM_QUERY_CD", 187, 187),
    ("DGNS_PRCDR_ICD_IND", 188, 188),
    ("CLM_MDCR_INSTNL_TOT_CHRG_AMT", 189, 203),
    ("CLM_MDCR_IP_PPS_CPTL_IME_AMT", 204, 218),
    ("CLM_OPRTNL_IME_AMT", 219, 240),
    ("CLM_MDCR_IP_PPS_DSPRPRTNT_AMT", 241, 255),
    ("CLM_HIPPS_UNCOMPD_CARE_AMT", 256, 270),
    ("CLM_OPRTNL_DSPRTNT_AMT", 271, 292),
)

#: Layouts a fixed-width sample can be automatically compared against, keyed
#: by the width they cover. `core.profiling` uses this to decide WHICH
#: reference (if any) a sample's own detected line width makes plausible —
#: never applying one that does not even cover the same span.
KNOWN_FIXED_WIDTH_LAYOUTS: dict[str, tuple[tuple[str, int, int], ...]] = {
    "CCLF1": CCLF1_LAYOUT,
}
