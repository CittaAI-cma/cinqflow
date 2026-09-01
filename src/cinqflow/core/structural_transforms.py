"""CF-V3-E6-05 — the two structural transforms that are not claim lineage:
unpivoting wide columns into rows, and per-source ADT field quirks.

    "the transform library extended for the hard structures:
     flattening FHIR ExplanationOfBenefit JSON, parsing HL7-derived ADT JSON
     with per-source quirks... unpivoting wide diagnosis/procedure columns
     into rows, and claim lineage derivation..."
    "Encode per-source ADT quirks (field-name variants, date formats, known
     typos) as reviewable configuration, not buried code."
    — CF-V3-E6-05

BOTH RUN BEFORE `core.mapping`, NOT INSIDE IT. `core.mapping.apply_to` maps
one row to one row, keyed by target field. Unpivoting changes the ROW COUNT
(one wide row becomes N narrow rows) and quirk-correction changes which KEY a
value lives under before any mapping line ever reads it — neither is
expressible as a `Transform.kind` without breaking that module's own
one-row-in-one-row-out contract (see `core/claim_lineage.py`'s module
docstring for the same argument about lineage). Composition is the answer
both stories already lean on: run this module's functions first, then hand
each resulting flat row to `FeedMapping.apply_to` exactly as before. Neither
module imports the other.

UNPIVOT IS HARVESTED, NOT INVENTED. CCLF5 (`BCDA_Data_Dictionary.xlsx`,
"CCLF-FHIR STU3 Mapping") carries up to 12 diagnosis codes as separate wide
columns — `CLM_DGNS_1_CD` .. `CLM_DGNS_12_CD` — each mapping to
`Eob.diagnosis[N]` where `N` is the column's own position: "Discriminator =
Eob.diagnosis[N].sequence = {corresponding number in column B}." `unpivot`'s
`sequence` output is that same 1-based column position, so a downstream
mapping into `claim_diagnosis.diagnosis_sequence` needs no extra derivation.

ADT QUIRKS ARE HARVESTED, NOT INVENTED. `ADT SilverRaw HL7 Mapping.xlsx`'s
own "Key Differences by Source" sheet documents eight real ADT sources
(BronxRhio, Healthix, HiBridge, HeC, HealtheLINK, PCC_IL, PCC_Natl, Particle)
and, in its "HL7 All Sources -> Silver Raw" sheet, the field-level notes
naming their differences verbatim — including BronxRhio's own real typo:
"NOTE BronxRhio: field may be named 'Discharge_Diposition' (missing s) in
JSON — ETL must handle this source-specific key name." `ADT_SOURCE_QUIRKS`
below is that sheet, not an invented example.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# ── unpivot: wide columns -> rows ────────────────────────────────────────────


@dataclass(frozen=True)
class UnpivotSpec:
    """Which wide columns become rows, and what to call the two new fields.

    `source_columns` is ORDERED — position is the 1-based sequence a
    downstream mapping line reads as `diagnosis_sequence` / `procedure_
    sequence`, matching CCLF5's own discriminator rule exactly. Reordering
    this tuple would silently renumber every diagnosis on the feed, so it is
    a tuple (stable, reviewable order) rather than a set.
    """

    source_columns: tuple[str, ...]
    sequence_field: str = "sequence"
    value_field: str = "value"


def unpivot(spec: UnpivotSpec, row: Mapping[str, str]) -> tuple[dict[str, str], ...]:
    """One wide row -> one narrow row per POPULATED wide column.

    Every column `row` carries that is not one of `spec.source_columns`
    (the claim's own key, its batch id, every other field) is copied onto
    EVERY output row unchanged — an unpivoted diagnosis row must still carry
    `CUR_CLM_UNIQ_ID` to be mappable into `claim_diagnosis.source_claim_id`.
    An empty wide column produces no row at all: CCLF5 pads every claim out
    to twelve diagnosis columns whether or not the claim carries that many,
    and a blank `CLM_DGNS_9_CD` is the absence of a ninth diagnosis, not a
    diagnosis whose code is the empty string.
    """
    carried = {key: value for key, value in row.items() if key not in spec.source_columns}
    results: list[dict[str, str]] = []
    for position, column in enumerate(spec.source_columns, start=1):
        value = (row.get(column) or "").strip()
        if not value:
            continue
        results.append({**carried, spec.value_field: value, spec.sequence_field: str(position)})
    return tuple(results)


# ── per-source ADT quirks, as reviewable configuration ───────────────────────


@dataclass(frozen=True)
class AdtFieldQuirk:
    """One source's documented deviation for one field. A row of
    configuration, never a branch of code — the same discipline
    `core.mapping.Transform` holds for the mapping taxonomy: a new source's
    quirk is a new tuple entry, not a new `if source_system == ...:`
    somewhere in an ETL script nobody reviews as data.

    `rename_from`: the literal key this source's OWN payload carries the
    value under, when it differs from the canonical Silver Raw column name
    (`field`) every other source already matches — BronxRhio's real
    `Discharge_Diposition` typo, or Particle's JSON using `facility_name`
    where every other source lands on `FacilityName`.

    `strip_quotes`: this source wraps the value in literal quote
    characters that are not part of the data (documented repeatedly in the
    harvested sheet as "PV1.3.4 strip quotes").
    """

    source_system: str
    field: str
    rename_from: str | None = None
    strip_quotes: bool = False


def apply_adt_quirks(
    quirks: Sequence[AdtFieldQuirk], *, source_system: str, row: Mapping[str, str]
) -> dict[str, str]:
    """Rewrite one source's row onto the canonical field names every
    mapping line downstream expects — every key this source's quirks do not
    touch passes through unchanged.

    Applying a rename before a strip means BronxRhio's `Discharge_Diposition`
    key lands on `DischargeDispositionCode` even on sources where that same
    canonical field ALSO needs quote-stripping; the two settings are
    independent per quirk row, not a chain across rows.
    """
    result = dict(row)
    for quirk in quirks:
        if quirk.source_system != source_system:
            continue
        value = (
            row.get(quirk.rename_from) if quirk.rename_from is not None else row.get(quirk.field)
        )
        if value is None:
            continue
        if quirk.strip_quotes:
            value = value.strip().strip('"').strip("'")
        result[quirk.field] = value
    return result


#: Harvested verbatim from `ADT SilverRaw HL7 Mapping.xlsx` — the "Key
#: Differences by Source" sheet and the field-level notes in "HL7 All
#: Sources -> Silver Raw". Not exhaustive of every documented difference
#: (HL7 segment-path selection, e.g. `PV1.3.4` vs `MSH.4.1`, is Mirth's job
#: before this module ever sees a row); this covers the JSON-key-level
#: renames and quote-stripping the story's own words name: "field-name
#: variants... and known typos."
ADT_SOURCE_QUIRKS: tuple[AdtFieldQuirk, ...] = (
    # The real, named typo: "BronxRhio Typo Note: Discharge_Diposition
    # (missing s) — ETL must handle this specific key name."
    AdtFieldQuirk(
        source_system="BronxRhio",
        field="DischargeDispositionCode",
        rename_from="Discharge_Diposition",
    ),
    # FacilityName: "Strip double quotes for all HL7 sources" except HeC,
    # which the sheet marks with no strip-quotes note, and Particle, whose
    # JSON is not quoted HL7 text to begin with.
    AdtFieldQuirk(source_system="BronxRhio", field="FacilityName", strip_quotes=True),
    AdtFieldQuirk(source_system="Healthix", field="FacilityName", strip_quotes=True),
    AdtFieldQuirk(source_system="HiBridge", field="FacilityName", strip_quotes=True),
    AdtFieldQuirk(source_system="HealtheLINK", field="FacilityName", strip_quotes=True),
    AdtFieldQuirk(source_system="PCC_IL", field="FacilityName", strip_quotes=True),
    AdtFieldQuirk(source_system="PCC_Natl", field="FacilityName", strip_quotes=True),
    # Particle is the one JSON (not HL7 v2.5) source, so its own field names
    # differ from the canonical Silver Raw columns every HL7 source already
    # matches after Mirth's own segment extraction.
    AdtFieldQuirk(source_system="Particle", field="FacilityName", rename_from="facility_name"),
    AdtFieldQuirk(
        source_system="Particle", field="AttendingNPI", rename_from="attending_physician_npi"
    ),
    AdtFieldQuirk(
        source_system="Particle", field="DischargeDate", rename_from="visit_end_date_time"
    ),
    AdtFieldQuirk(
        source_system="Particle",
        field="DischargeDispositionCode",
        rename_from="discharge_disposition",
    ),
    AdtFieldQuirk(
        source_system="Particle", field="AdmissionReason", rename_from="discharge_summary"
    ),
)
