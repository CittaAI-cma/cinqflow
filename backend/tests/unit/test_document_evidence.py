"""CF-V1-E16-06 — the guide and the file disagree, and both get cited.

"Given the uploaded guide contradicts the profiled sample (the guide says
 42 columns, the file shows 45), when suggestions build, then the conflict
 is surfaced with both sources cited, and sample evidence wins by default."
— CF-V1-E16-06, exception path
"""

from __future__ import annotations

import pytest

from cinqflow.core.agents.document_evidence import column_count_conflicts
from cinqflow.core.citations import CitationId, CitationKind

pytestmark = pytest.mark.unit

GUIDE = CitationId(kind=CitationKind.DOCUMENT, subject="fidelis-companion-guide", fragment="p14")
SAMPLE = CitationId(kind=CitationKind.PROFILE, subject="sha256-3f1993a21dac")


def _conflicts(text: str, *, columns: int = 45):
    return column_count_conflicts(
        chunks=((GUIDE, text),), sample_columns=columns, sample_citation=SAMPLE
    )


def test_the_storys_own_example_produces_one_conflict_citing_both_sources() -> None:
    (conflict,) = _conflicts("The roster layout contains 42 columns, listed in the table below.")
    assert conflict.document_says == 42
    assert conflict.sample_shows == 45
    assert conflict.document_citation == GUIDE
    assert conflict.sample_citation == SAMPLE


def test_sample_evidence_wins_by_default_and_says_which_it_proceeded_on() -> None:
    (conflict,) = _conflicts("The file has 42 fields.")
    assert "Proceeding on the sample: 45" in conflict.resolution


def test_the_guide_is_recorded_never_discarded() -> None:
    """A truncated delivery and a wrong guide look identical from here, so a
    reviewer needs the number the guide actually stated."""
    (conflict,) = _conflicts("Layout: 42 data elements per member record.")
    assert conflict.document_says == 42
    assert "42 data elements" in conflict.quote


def test_agreement_is_not_a_conflict() -> None:
    assert _conflicts("The layout contains 45 columns.") == ()


def test_a_guide_that_states_no_count_produces_nothing() -> None:
    """Never a conflict asserted from the ABSENCE of a claim — that would make
    every feed without a specification look like a feed with a bad one."""
    assert _conflicts("This guide describes the monthly roster transmission.") == ()


def test_no_document_at_all_produces_nothing() -> None:
    assert column_count_conflicts(chunks=(), sample_columns=45, sample_citation=SAMPLE) == ()


def test_one_claim_repeated_on_a_page_is_one_conflict_not_three() -> None:
    (conflict,) = _conflicts("42 columns. As stated, 42 columns. Again: 42 columns.")
    assert conflict.document_says == 42


def test_two_different_claims_are_two_conflicts() -> None:
    conflicts = _conflicts("Section A lists 42 columns; Section B lists 44 fields.")
    assert sorted(c.document_says for c in conflicts) == [42, 44]


def test_a_five_digit_number_beside_the_word_is_not_a_column_count() -> None:
    """A record count that happens to sit beside the word is a coincidence,
    and matching it would manufacture a conflict out of one."""
    assert _conflicts("The extract carried 22000 fields of data in total.") == ()


def test_the_quote_travels_with_the_number() -> None:
    """A reviewer shown "the guide says 42" with no sentence around it cannot
    tell a layout table's total from a paragraph about a different file."""
    (conflict,) = _conflicts(
        "Appendix C, the professional claims extract, is a separate file of 42 columns."
    )
    assert "professional claims extract" in conflict.quote


def test_the_record_carries_everything_a_review_screen_needs() -> None:
    (conflict,) = _conflicts("42 columns.")
    record = conflict.as_record()
    assert record["document_says"] == 42
    assert record["sample_shows"] == 45
    assert record["document_citation"] == str(GUIDE)
    assert record["sample_citation"] == str(SAMPLE)
    assert "Proceeding on the sample" in str(record["resolution"])
