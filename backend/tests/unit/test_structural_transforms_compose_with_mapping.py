"""CF-V3-E6-05 — proof that unpivoting and ADT quirks are genuinely PLUG AND
PLAY with the already-shipped `core.mapping` engine, not a parallel pipeline
that happens to sit next to it.

Neither `core.structural_transforms` nor `core.claim_lineage` imports
`core.mapping`, and `core.mapping` imports neither of them — the composition
lives entirely in the CALLER, exactly like `core.registry.contract.
cast_value` composing with a mapping line's `CAST` transform. These tests
are that composition, exercised end to end: a wide CCLF5-shaped row and a
quirky ADT row, both landing in the SAME `FeedMapping.apply_to` used
everywhere else on the platform, with zero special-casing.
"""

from __future__ import annotations

import pytest

from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.structural_transforms import (
    ADT_SOURCE_QUIRKS,
    UnpivotSpec,
    apply_adt_quirks,
    unpivot,
)

pytestmark = pytest.mark.unit

_DIAGNOSIS_MAPPING = FeedMapping(
    feed_id="cclf5-diagnosis",
    lines=(
        MappingLine(
            target_entity="claim_diagnosis",
            target_field="source_claim_id",
            source_columns=("CUR_CLM_UNIQ_ID",),
        ),
        MappingLine(
            target_entity="claim_diagnosis",
            target_field="source_diagnosis_code",
            source_columns=("source_diagnosis_code",),
        ),
        MappingLine(
            target_entity="claim_diagnosis",
            target_field="diagnosis_sequence",
            source_columns=("diagnosis_sequence",),
        ),
    ),
)

_ADT_MAPPING = FeedMapping(
    feed_id="particle-adt",
    lines=(
        MappingLine(
            target_entity="encounter",
            target_field="facility_name",
            source_columns=("FacilityName",),
        ),
        MappingLine(
            target_entity="encounter",
            target_field="attending_npi",
            source_columns=("AttendingNPI",),
        ),
    ),
)


def test_a_wide_cclf5_row_unpivots_and_then_maps_with_the_stock_engine() -> None:
    spec = UnpivotSpec(
        source_columns=("CLM_DGNS_1_CD", "CLM_DGNS_2_CD"),
        sequence_field="diagnosis_sequence",
        value_field="source_diagnosis_code",
    )
    wide_row = {
        "CUR_CLM_UNIQ_ID": "CLM-9001",
        "CLM_DGNS_1_CD": "E1165",
        "CLM_DGNS_2_CD": "I10",
    }

    narrow_rows = unpivot(spec, wide_row)
    mapped = [_DIAGNOSIS_MAPPING.apply_to(row)[0] for row in narrow_rows]

    assert mapped == [
        {
            "source_claim_id": "CLM-9001",
            "source_diagnosis_code": "E1165",
            "diagnosis_sequence": "1",
        },
        {
            "source_claim_id": "CLM-9001",
            "source_diagnosis_code": "I10",
            "diagnosis_sequence": "2",
        },
    ]


def test_a_particle_adt_row_is_quirk_corrected_and_then_maps_with_the_stock_engine() -> None:
    raw_particle_row = {
        "facility_name": "Particle Health Facility",
        "attending_physician_npi": "1972000032",
    }

    canonical_row = apply_adt_quirks(
        ADT_SOURCE_QUIRKS, source_system="Particle", row=raw_particle_row
    )
    mapped, rejected = _ADT_MAPPING.apply_to(canonical_row)

    assert rejected is None
    assert mapped == {"facility_name": "Particle Health Facility", "attending_npi": "1972000032"}
