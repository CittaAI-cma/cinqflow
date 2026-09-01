"""The client's ACTUAL glossary and rule sheets, loaded and counted.

    "All of these already exist in `clientdata/`. That is the point — the exam was
     free."
    — memory/05-ground-truth/03-golden-sets.md

These assertions are the programme's documented ground truth: 171 terms, 29 of
them PHI-flagged, 110 DQ rules. If the workbook changes, this suite says so
rather than letting a seeded glossary quietly shrink — which would weaken the
100%-recall PHI gate by exactly the number of terms nobody noticed were gone.

SKIPS rather than fails when the corpus is not on disk: the repository is
useful without `clientdata/`, and a suite that hard-failed there would train people
to ignore it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.adapters.local.workbook_glossary import (
    EXPECTED_RULES,
    EXPECTED_TERMS,
    WorkbookError,
    load_dq_rule_rows,
    load_glossary,
)
from cinqflow.core.registry.glossary import Glossary
from tests.conftest import require_corpus

pytestmark = pytest.mark.pipeline

# parents[4], NOT parents[3]. This file is
# backend/tests/pipeline/<this>.py, so the walk up is pipeline -> tests ->
# backend -> the repo root -> the workspace that holds `clientdata/`
# beside it. It was [3] when tests/ sat at the repo root, and the reorg
# into backend/ silently made [3] the repo root itself — where there is no
# corpus. The failure would not have been a red test: `require_corpus`
# SKIPS on a missing workbook, so these gates would have quietly stopped
# grading against the client's own numbers while still reporting green.
WORKBOOK = (
    Path(__file__).resolve().parents[4]
    / "clientdata"
    / "Uploads"
    / "2-Design"
    / "Data lake data model.xlsx"
)


@pytest.fixture(scope="module")
def workbook() -> Path:
    require_corpus(WORKBOOK)
    return WORKBOOK


@pytest.fixture(scope="module")
def glossary(workbook: Path) -> Glossary:
    return load_glossary(workbook)


def test_the_workbook_holds_the_counted_171_terms(glossary: Glossary) -> None:
    assert len(glossary.terms) == EXPECTED_TERMS


def test_exactly_29_terms_are_phi_flagged(glossary: Glossary) -> None:
    """ "29 of 171 terms are PHI-flagged. Those 29 drive both the masking
     policy and the PHI-detection gate."

    A drift here changes what the platform will mask, so it is asserted rather
    than derived at run time.
    """
    assert len(glossary.phi_terms) == 29


def test_the_phi_answer_key_covers_every_spelling(glossary: Glossary) -> None:
    """The gate is 100% recall on flagged PHI, and payers send `Patient_dob`
    where the model says `Member_Date_Of_Birth`."""
    assert glossary.is_phi_column("Patient_dob") is True
    assert glossary.is_phi_column("MemberDateOfBirth") is True
    assert len(glossary.phi_columns()) > len(glossary.phi_terms)


def test_bg_004_carries_the_synonym_set_the_mapper_needs(glossary: Glossary) -> None:
    """The worked example from the ground-truth notes, asserted against the
    real row rather than a copy of it."""
    term = glossary.get("BG-004")
    assert term is not None
    assert term.term == "Member Date of Birth"
    assert term.is_phi is True
    assert {"Date_of_Birth", "Patient_dob", "MemberDateOfBirth"} <= set(term.synonyms)


def test_every_term_has_a_definition_and_a_citable_slug(glossary: Glossary) -> None:
    """A term with no definition cannot ground a claim, and one with no slug
    cannot be cited — both are defects in a knowledge base."""
    assert all(t.definition.strip() for t in glossary.terms)
    assert all(t.slug for t in glossary.terms)
    assert len({t.slug for t in glossary.terms}) == len(glossary.terms)


def test_the_workbook_holds_the_counted_110_dq_rules(workbook: Path) -> None:
    assert len(load_dq_rule_rows(workbook)) == EXPECTED_RULES


def test_every_rule_pairs_plain_english_with_executable_sql(workbook: Path) -> None:
    """ "each rule already pairs a natural-language description with executable
     SQL and a glossary link — which is exactly the shape the NL->rule agent
     must produce. The exam is a re-derivation benchmark."

    CF-V1-E7-01's gate depends on this being true of the whole set, not of the
    first row somebody looked at.
    """
    rows = load_dq_rule_rows(workbook)
    assert all(str(r.get("Rule Description") or "").strip() for r in rows)
    assert all(str(r.get("SQL Validation Query") or "").strip() for r in rows)


def test_a_count_that_disagrees_with_the_programme_is_reported_not_absorbed(
    workbook: Path,
) -> None:
    """ "record a discrepancy between documents; never silently pick a side." """
    with pytest.raises(WorkbookError, match="expected 5 glossary terms"):
        load_glossary(workbook, expect=5)
