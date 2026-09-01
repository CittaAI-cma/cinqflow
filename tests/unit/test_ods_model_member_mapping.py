"""CF-V3-E8-05 — the canonical `silver_raw.members` -> `Members` mapping.

Proven against the REAL harvested `Members` entity (`MEMBER_DOMAIN_V1`) and
the REAL deployed `silver_raw.members` schema — every column on both sides
is named here explicitly, never assumed to line up.
"""

from __future__ import annotations

import pytest

from cinqflow.core.registry.ods_model_member_domain import MEMBER_DOMAIN_V1
from cinqflow.core.registry.ods_model_member_mapping import (
    CANONICAL_MEMBERS_FEED_ID,
    MEMBER_MAPPING_V1,
)
from cinqflow.core.schema_spec import SILVER_RAW_SCHEMA

pytestmark = pytest.mark.unit


def _silver_raw_members_columns() -> set[str]:
    (table,) = (t for t in SILVER_RAW_SCHEMA.tables if t.name == "members")
    return {c.name for c in table.columns}


def test_every_real_members_column_has_exactly_one_line() -> None:
    """No column is silently skipped, and none is declared twice — a
    reviewer must be able to trust this list is the whole entity."""
    real_columns = {c.name for c in MEMBER_DOMAIN_V1.entity("Members").columns}
    mapped_targets = [line.target_field for line in MEMBER_MAPPING_V1.lines]
    assert set(mapped_targets) == real_columns
    assert len(mapped_targets) == len(set(mapped_targets))


def test_every_mapped_source_column_is_a_real_silver_raw_column_or_crosswalk_field() -> None:
    """A mapping that reads a column silver_raw does not have would fail
    silently at load time, on a column nobody remembers declaring."""
    real_silver_raw = _silver_raw_members_columns()
    crosswalk_fields = {"_internal_member_id", "_verato_person_id"}
    for line in MEMBER_MAPPING_V1.lines:
        for source in line.source_columns:
            assert source in real_silver_raw | crosswalk_fields, (line.target_field, source)


def test_the_feed_id_is_the_canonical_layer_convention_not_a_real_feed() -> None:
    assert MEMBER_MAPPING_V1.feed_id == CANONICAL_MEMBERS_FEED_ID == "canonical:members"


def test_batch_id_is_a_direct_copy_matching_e10_01s_applied_discrepancy() -> None:
    """Both sides are STRING per the already-decided BatchId discrepancy —
    a mismatch here would mean the two stories quietly disagree."""
    (line,) = (line for line in MEMBER_MAPPING_V1.lines if line.target_field == "BatchId")
    assert line.source_columns == ("batch_id",)


def test_our_id_and_link_id_read_from_the_crosswalk_not_silver_raw_content() -> None:
    by_field = {line.target_field: line for line in MEMBER_MAPPING_V1.lines}
    assert by_field["OurId"].source_columns == ("_internal_member_id",)
    assert by_field["LinkId"].source_columns == ("_verato_person_id",)


def test_every_unmapped_line_carries_a_real_reason() -> None:
    for line in MEMBER_MAPPING_V1.lines:
        if not line.is_mapped and not line.platform_supplied:
            assert line.unmapped_reason.strip()


def test_coverage_fields_stay_unmapped_here_they_belong_to_enrollment_segments() -> None:
    """line_of_business/effective_date/end_date describe COVERAGE, not the
    member — wiring them into Members would be a wrong-column defect of
    exactly the kind CF-V3-E10-01's own test suite guards against."""
    real_silver_raw_only_columns = {"line_of_business", "effective_date", "end_date"}
    used_sources = {s for line in MEMBER_MAPPING_V1.lines for s in line.source_columns}
    assert not (real_silver_raw_only_columns & used_sources)
