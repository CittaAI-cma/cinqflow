"""CF-V3-E6-05 — the two structural transforms that run before
`core.mapping`: unpivoting wide columns into rows, and per-source ADT field
quirks as reviewable configuration.

    "unpivoting wide diagnosis/procedure columns into rows"
    "Encode per-source ADT quirks (field-name variants, date formats, known
     typos) as reviewable configuration, not buried code."
    — CF-V3-E6-05
"""

from __future__ import annotations

import pytest

from cinqflow.core.structural_transforms import (
    ADT_SOURCE_QUIRKS,
    AdtFieldQuirk,
    UnpivotSpec,
    apply_adt_quirks,
    unpivot,
)

pytestmark = pytest.mark.unit

# CCLF5's own wide diagnosis columns — `BCDA_Data_Dictionary.xlsx`,
# "CCLF-FHIR STU3 Mapping": CLM_DGNS_1_CD .. CLM_DGNS_12_CD, each discriminated
# by its own column position into `Eob.diagnosis[N].sequence`.
_DIAGNOSIS_SPEC = UnpivotSpec(
    source_columns=("CLM_DGNS_1_CD", "CLM_DGNS_2_CD", "CLM_DGNS_3_CD"),
    sequence_field="diagnosis_sequence",
    value_field="source_diagnosis_code",
)


# ── unpivot ───────────────────────────────────────────────────────────────


def test_each_populated_wide_column_becomes_one_row() -> None:
    row = {
        "CUR_CLM_UNIQ_ID": "CLM-1",
        "CLM_DGNS_1_CD": "E1165",
        "CLM_DGNS_2_CD": "I10",
        "CLM_DGNS_3_CD": "",
    }

    rows = unpivot(_DIAGNOSIS_SPEC, row)

    assert rows == (
        {
            "CUR_CLM_UNIQ_ID": "CLM-1",
            "source_diagnosis_code": "E1165",
            "diagnosis_sequence": "1",
        },
        {
            "CUR_CLM_UNIQ_ID": "CLM-1",
            "source_diagnosis_code": "I10",
            "diagnosis_sequence": "2",
        },
    )


def test_sequence_is_the_columns_own_position_not_a_recount() -> None:
    """The second diagnosis is missing; the third must still carry sequence
    3, matching CCLF5's own discriminator ('column number = sequence'), not
    2 as if the codes had been compacted."""
    row = {"CLM_DGNS_1_CD": "E1165", "CLM_DGNS_2_CD": "", "CLM_DGNS_3_CD": "I10"}

    rows = unpivot(_DIAGNOSIS_SPEC, row)

    sequences = {r["source_diagnosis_code"]: r["diagnosis_sequence"] for r in rows}
    assert sequences == {"E1165": "1", "I10": "3"}


def test_no_populated_columns_unpivots_to_no_rows() -> None:
    row = {"CUR_CLM_UNIQ_ID": "CLM-1", "CLM_DGNS_1_CD": "", "CLM_DGNS_2_CD": ""}
    assert unpivot(_DIAGNOSIS_SPEC, row) == ()


def test_every_other_column_is_carried_onto_every_output_row() -> None:
    row = {
        "CUR_CLM_UNIQ_ID": "CLM-1",
        "BatchId": "batch-42",
        "CLM_DGNS_1_CD": "E1165",
        "CLM_DGNS_2_CD": "I10",
    }

    rows = unpivot(_DIAGNOSIS_SPEC, row)

    assert all(r["CUR_CLM_UNIQ_ID"] == "CLM-1" and r["BatchId"] == "batch-42" for r in rows)


# ── apply_adt_quirks ──────────────────────────────────────────────────────


def test_a_source_with_no_quirks_passes_the_row_through_unchanged() -> None:
    row = {"FacilityName": "Mercy Hospital", "AttendingNPI": "1234567890"}
    quirks = (AdtFieldQuirk(source_system="Healthix", field="FacilityName", strip_quotes=True),)

    result = apply_adt_quirks(quirks, source_system="HiBridge", row=row)

    assert result == row


def test_a_quirk_renames_a_source_specific_key_onto_the_canonical_field() -> None:
    quirks = (
        AdtFieldQuirk(
            source_system="BronxRhio",
            field="DischargeDispositionCode",
            rename_from="Discharge_Diposition",
        ),
    )
    row = {"Discharge_Diposition": "01", "FacilityName": "St. Barnabas"}

    result = apply_adt_quirks(quirks, source_system="BronxRhio", row=row)

    assert result["DischargeDispositionCode"] == "01"
    assert result["FacilityName"] == "St. Barnabas"


def test_a_strip_quotes_quirk_removes_literal_quote_characters() -> None:
    quirks = (AdtFieldQuirk(source_system="Healthix", field="FacilityName", strip_quotes=True),)
    row = {"FacilityName": '"Mercy Hospital"'}

    result = apply_adt_quirks(quirks, source_system="Healthix", row=row)

    assert result["FacilityName"] == "Mercy Hospital"


def test_a_quirk_for_a_different_source_does_not_apply() -> None:
    quirks = (
        AdtFieldQuirk(
            source_system="BronxRhio",
            field="DischargeDispositionCode",
            rename_from="Discharge_Diposition",
        ),
    )
    row = {"Discharge_Diposition": "01"}

    result = apply_adt_quirks(quirks, source_system="Healthix", row=row)

    assert "DischargeDispositionCode" not in result


def test_a_missing_renamed_source_key_leaves_the_canonical_field_untouched() -> None:
    quirks = (
        AdtFieldQuirk(
            source_system="BronxRhio",
            field="DischargeDispositionCode",
            rename_from="Discharge_Diposition",
        ),
    )
    result = apply_adt_quirks(quirks, source_system="BronxRhio", row={})
    assert "DischargeDispositionCode" not in result


def test_multiple_quirks_for_one_source_all_apply() -> None:
    row = {
        "facility_name": "Particle Health Facility",
        "attending_physician_npi": "1972000032",
        "visit_end_date_time": "2026-08-30T14:00:00Z",
    }

    result = apply_adt_quirks(ADT_SOURCE_QUIRKS, source_system="Particle", row=row)

    assert result["FacilityName"] == "Particle Health Facility"
    assert result["AttendingNPI"] == "1972000032"
    assert result["DischargeDate"] == "2026-08-30T14:00:00Z"


# ── ADT_SOURCE_QUIRKS — the real, harvested table ────────────────────────────


def test_the_real_bronxrhio_typo_is_handled_by_the_harvested_table() -> None:
    """The actual documented defect: BronxRhio's own JSON carries the
    discharge-disposition key misspelled as `Discharge_Diposition`."""
    row = {"Discharge_Diposition": "02"}

    result = apply_adt_quirks(ADT_SOURCE_QUIRKS, source_system="BronxRhio", row=row)

    assert result["DischargeDispositionCode"] == "02"


# ── determinism — "the same input arrives twice... safely skipped" ─────────


def test_unpivoting_the_same_row_twice_produces_identical_rows() -> None:
    """Both structural transforms are pure functions with no state of their
    own — the platform's dedup guarantee lives at the landing/control-table
    layer, and what THIS layer owes it is simply never producing a
    different answer for the same input, twice."""
    row = {"CUR_CLM_UNIQ_ID": "CLM-1", "CLM_DGNS_1_CD": "E1165", "CLM_DGNS_2_CD": "I10"}
    assert unpivot(_DIAGNOSIS_SPEC, row) == unpivot(_DIAGNOSIS_SPEC, row)


def test_applying_the_same_quirks_twice_produces_an_identical_row() -> None:
    row = {"Discharge_Diposition": "02"}
    once = apply_adt_quirks(ADT_SOURCE_QUIRKS, source_system="BronxRhio", row=row)
    twice = apply_adt_quirks(ADT_SOURCE_QUIRKS, source_system="BronxRhio", row=row)
    assert once == twice


def test_every_harvested_quirk_source_is_one_of_the_eight_real_adt_sources() -> None:
    known_sources = {
        "BronxRhio",
        "Healthix",
        "HiBridge",
        "HeC",
        "HealtheLINK",
        "PCC_IL",
        "PCC_Natl",
        "Particle",
    }
    assert {quirk.source_system for quirk in ADT_SOURCE_QUIRKS} <= known_sources
