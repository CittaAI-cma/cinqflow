"""CF-V3-E5-05 — structure-tree counting and fixed-width boundary detection.

"Happy path — Given a BA uploads a BCDA EOB sample, when profiling
 runs, then she sees the resource tree (items 1-40 per claim,
 adjudications 3-8 per item), per-path fill rates, and a proposed
 flattening that the mapping step picks up."
"Exception — Given a fixed-width sample has two plausible boundary
 interpretations for adjacent columns, when profiling runs, then both
 interpretations are shown with their evidence and the CCLF layout
 reference, and the BA chooses — the profiler never guesses silently."
— CF-V3-E5-05
"""

from __future__ import annotations

import pytest

from cinqflow.core.complex_format_profiling import (
    CCLF1_LAYOUT,
    ambiguous_boundaries,
    detect_fixed_width_boundaries,
    layout_from_reference,
    profile_structure,
    propose_flattening,
)

pytestmark = pytest.mark.unit


# ── profile_structure ────────────────────────────────────────────────────────


def _eob_claim(item_count: int, adjudications_per_item: int) -> dict:
    return {
        "resourceType": "ExplanationOfBenefit",
        "id": "A100",
        "status": "active",
        "item": [
            {
                "sequence": i + 1,
                "adjudication": [
                    {"category": "eligible", "amount": {"value": 100}}
                    for _ in range(adjudications_per_item)
                ],
            }
            for i in range(item_count)
        ],
    }


def test_a_scalar_field_present_in_every_document_has_a_full_fill_rate() -> None:
    docs = (_eob_claim(2, 3), _eob_claim(1, 3), _eob_claim(4, 3))
    paths = {p.path: p for p in profile_structure(docs)}
    assert paths["status"].documents_with_path == 3
    assert paths["status"].documents_total == 3
    assert paths["status"].fill_rate == 1.0
    assert not paths["status"].is_array


def test_a_field_missing_from_some_documents_has_a_partial_fill_rate() -> None:
    docs = ({"id": "A1", "note": "x"}, {"id": "A2"}, {"id": "A3", "note": "y"})
    paths = {p.path: p for p in profile_structure(docs)}
    assert paths["note"].documents_with_path == 2
    assert paths["note"].fill_rate == pytest.approx(2 / 3)


def test_a_repeating_group_reports_element_count_range_across_documents() -> None:
    """The story's own happy path: 'items 1-40 per claim' — here, a small
    sample standing in for the same shape: item counts vary across claims,
    and `array_length_min`/`_max` report the true range."""
    docs = (_eob_claim(1, 3), _eob_claim(4, 3), _eob_claim(2, 3))
    paths = {p.path: p for p in profile_structure(docs)}
    item = paths["item"]
    assert item.is_array
    assert item.array_length_min == 1
    assert item.array_length_max == 4
    assert item.documents_with_path == 3


def test_a_nested_repeating_group_is_counted_per_occurrence_not_per_document() -> None:
    """'adjudications 3-8 per item' — cardinality of the INNER repeating
    group, over every item across every claim, not once per claim."""
    docs = (
        {
            "id": "A1",
            "item": [
                {"adjudication": [{"amount": 1}, {"amount": 2}, {"amount": 3}]},
                {
                    "adjudication": [
                        {"amount": 1},
                        {"amount": 2},
                        {"amount": 3},
                        {"amount": 4},
                        {"amount": 5},
                    ]
                },
            ],
        },
    )
    paths = {p.path: p for p in profile_structure(docs)}
    adjudication = paths["item.adjudication"]
    assert adjudication.is_array
    assert adjudication.array_length_min == 3
    assert adjudication.array_length_max == 5
    assert adjudication.array_occurrences == 2  # two items, each with its own adjudication list


def test_the_path_of_a_deeply_nested_scalar_is_dotted_with_no_array_index() -> None:
    docs = ({"item": [{"adjudication": [{"category": "eligible"}]}]},)
    paths = {p.path for p in profile_structure(docs)}
    assert "item.adjudication.category" in paths


def test_an_empty_sample_produces_no_paths() -> None:
    assert profile_structure(()) == ()


# ── propose_flattening ───────────────────────────────────────────────────────


def test_every_repeating_group_gets_one_proposal_never_applied() -> None:
    docs = (_eob_claim(2, 3), _eob_claim(4, 5))
    paths = profile_structure(docs)

    proposals = propose_flattening(paths)

    by_path = {p.source_path: p for p in proposals}
    assert "item" in by_path
    assert "item.adjudication" in by_path
    assert by_path["item"].proposed_entity == "item"
    assert by_path["item.adjudication"].proposed_entity == "item_adjudication"


def test_a_flattening_proposal_names_its_own_element_count_range() -> None:
    docs = (_eob_claim(1, 3), _eob_claim(4, 3))
    proposals = {p.source_path: p for p in propose_flattening(profile_structure(docs))}
    item = proposals["item"]
    assert item.element_count_min == 1
    assert item.element_count_max == 4
    assert "1-4 element" in item.description


def test_scalar_paths_produce_no_flattening_proposal() -> None:
    docs = (_eob_claim(2, 3),)
    proposals = propose_flattening(profile_structure(docs))
    assert not any(p.source_path == "status" for p in proposals)


# ── detect_fixed_width_boundaries ────────────────────────────────────────────


def test_columns_separated_by_a_consistent_blank_are_detected() -> None:
    lines = ["AAA 1X END", "BBB 2Y END", "CCC 3Z END"]
    layout = detect_fixed_width_boundaries(lines)
    ranges = [(c.start, c.end) for c in layout.columns]
    assert ranges == [(1, 3), (5, 6), (8, 10)]


def test_an_empty_sample_detects_no_columns() -> None:
    assert detect_fixed_width_boundaries(()).columns == ()


def test_column_confidence_reflects_the_bounding_gaps_agreement() -> None:
    lines = ["AB 1", "AB 2", "AB 3"]
    layout = detect_fixed_width_boundaries(lines)
    # the FIRST column opens at the line's own start, needing no evidence
    first, second = layout.columns
    assert first.confidence == 1.0
    # the SECOND column is bounded by a gap every sample row agrees on
    assert second.confidence == 1.0


def test_a_gap_only_some_rows_agree_on_is_not_treated_as_a_boundary() -> None:
    """One row's stray character where the others carry a space must not,
    on its own, be believed over the other two."""
    lines = ["AA B1", "AA B2", "AAXB3"]  # position 2 (0-based) is a space in 2/3 rows
    layout = detect_fixed_width_boundaries(lines, min_gap_confidence=0.95)
    assert len(layout.columns) == 1  # merged: 2/3 = 0.67, below the 0.95 threshold


# ── layout_from_reference / ambiguous_boundaries — the CCLF1 exception ──────


def test_the_harvested_cclf1_layout_covers_all_292_positions_with_no_gaps() -> None:
    layout = layout_from_reference("CCLF1", CCLF1_LAYOUT)
    assert len(layout.columns) == 37
    assert layout.columns[0].start == 1
    assert layout.columns[-1].end == 292
    for earlier, later in zip(layout.columns, layout.columns[1:], strict=False):
        assert later.start == earlier.end + 1  # contiguous — no delimiter anywhere


def test_two_adjacent_always_populated_cclf1_fields_are_a_real_ambiguity() -> None:
    """CLM_BILL_FAC_TYPE_CD (64) and CLM_BILL_CLSFCTN_CD (65) are both
    always-populated one-character codes with nothing between them — a
    whitespace scan of real CCLF1 data cannot recover this real boundary,
    which is exactly the exception CF-V3-E5-05 names."""
    reference = layout_from_reference("CCLF1", CCLF1_LAYOUT)
    # A minimal sample isolating just that pair, framed by genuine gaps on
    # both sides so the surrounding boundaries are unambiguous.
    statistical = detect_fixed_width_boundaries(["1A", "2B", "1C"])
    # Re-anchor the synthetic sample's positions onto the real 64-65 span so
    # it can be compared against the real reference layout directly.
    shifted = layout_from_reference(
        "statistical", tuple(("", c.start + 63, c.end + 63) for c in statistical.columns)
    )

    ambiguities = ambiguous_boundaries(shifted, reference)

    assert len(ambiguities) == 1
    (ambiguity,) = ambiguities
    assert ambiguity.statistical.start == 64
    assert ambiguity.statistical.end == 65
    names = {c.name for c in ambiguity.reference_columns}
    assert names == {"CLM_BILL_FAC_TYPE_CD", "CLM_BILL_CLSFCTN_CD"}
    assert ambiguity.reference_name == "CCLF1"


def test_a_boundary_every_layout_agrees_on_is_never_reported_as_ambiguous() -> None:
    reference = layout_from_reference("CCLF1", CCLF1_LAYOUT)
    # CUR_CLM_UNIQ_ID (1-13) alone, matching the reference exactly.
    statistical = layout_from_reference("statistical", (("", 1, 13),))
    assert ambiguous_boundaries(statistical, reference) == ()
