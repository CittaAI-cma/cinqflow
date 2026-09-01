"""CF-V1-E14-01 — the glossary the platform speaks with.

    "Given the glossary is seeded, when a BA hovers 'Member Internal
     Identifier' anywhere in the platform, then they see the approved
     definition, its PHI status, and every table and rule that uses it."
    — CF-V1-E14-01, happy path

    "Let anyone change a PHI flag without steward approval — masking everywhere
     depends on these flags." (a documented don't)

The fixtures here are REAL ROWS, copied field-for-field from the client's
`Data lake data model.xlsx`. A glossary test written against invented terms
would prove the code parses something; these prove it parses what actually
arrives — including the synonym set BG-004 really carries.
"""

from __future__ import annotations

import pytest

from cinqflow.core.registry.glossary import (
    Glossary,
    GlossaryTerm,
    PhiDowngradeError,
    amend,
)

pytestmark = pytest.mark.unit

#: BG-004, verbatim from the workbook — five spellings of one concept, which
#: is exactly the knowledge a semantic mapper needs and a string comparison
#: does not have.
BG_004 = GlossaryTerm(
    glossary_id="BG-004",
    term="Member Date of Birth",
    definition="Date of birth of the member, used for age calculations, eligibility, "
    "and quality measure stratification.",
    domain_category="Member Identity",
    sub_category="Demographics",
    classification="PII",
    mapped_domains=("Enrollment", "Claims", "Census"),
    mapped_tables=("Members", "Claim_IPHeader", "Claim_Pharmacy", "DailyCensus"),
    mapped_columns_original=(
        "Date_of_Birth",
        "Patient_dob",
        "patient_dob",
        "Patient_Date_of_birth",
        "MemberDateOfBirth",
    ),
    mapped_columns_corrected=("Member_Date_Of_Birth", "Patient_Date_Of_Birth"),
    sensitivity="High",
    is_phi=True,
    notes="Used for HEDIS age stratification",
    source_row=5,
)

BG_002 = GlossaryTerm(
    glossary_id="BG-002",
    term="Member First Name",
    definition="Legal given name of the member, required for outreach and CMS "
    "encounter submissions.",
    domain_category="Member Identity",
    mapped_columns_original=("First_Name", "first_name", "MemberFirstName"),
    is_phi=True,
)

BG_101 = GlossaryTerm(
    glossary_id="BG-101",
    term="Attribution Model",
    definition="The methodology by which a member is assigned to a primary care provider.",
    domain_category="Risk Stratification",
    mapped_columns_original=("attribution_model",),
    is_phi=False,
)


@pytest.fixture
def glossary() -> Glossary:
    return Glossary(terms=(BG_002, BG_004, BG_101))


# ── the synonym set, which is why this service exists ────────────────────────


def test_a_term_carries_every_spelling_the_estate_uses(glossary: Glossary) -> None:
    """One concept, five payer spellings. The client's analysts recorded this;
    the platform must not lose it."""
    assert set(BG_004.synonyms) >= {
        "Date_of_Birth",
        "Patient_dob",
        "MemberDateOfBirth",
        "Member_Date_Of_Birth",
    }
    _ = glossary


def test_matching_a_column_ignores_case_and_separators() -> None:
    """`Patient_dob`, `patient_dob` and `PatientDOB` are the same concept
    arriving from three payers — treating them as three is how a mapping
    studio asks a human the same question three times."""
    for spelling in ("Patient_dob", "patient_dob", "PATIENT_DOB", "PatientDob"):
        assert BG_004.matches_column(spelling), spelling


def test_an_unrelated_column_does_not_match() -> None:
    """The deterministic matcher must be precise, because CF-V1-E6-02 only
    calls the model where this returns nothing."""
    assert BG_004.matches_column("member_status") is False


def test_the_lookup_answers_by_column(glossary: Glossary) -> None:
    assert [t.glossary_id for t in glossary.for_column("MemberDateOfBirth")] == ["BG-004"]


def test_search_finds_a_term_by_business_language(glossary: Glossary) -> None:
    """ "date of birth" finds `date_of_birth` — CF-V1-E6-01's requirement, and
    the difference between a glossary business people use and one only
    engineers can."""
    assert [t.glossary_id for t in glossary.search("date of birth")] == ["BG-004"]


def test_search_also_finds_a_term_by_its_column_name(glossary: Glossary) -> None:
    assert [t.glossary_id for t in glossary.search("attribution_model")] == ["BG-101"]


# ── the PHI flags: the gate's answer key ─────────────────────────────────────


def test_phi_columns_collect_every_spelling_of_every_flagged_term(
    glossary: Glossary,
) -> None:
    """CF-V1-E5-03's gate is 100% RECALL on glossary-flagged PHI. Missing one
    spelling of one term is a hole in that gate, so the answer key is built
    from the synonym sets rather than the corrected names alone."""
    assert glossary.is_phi_column("Patient_dob") is True
    assert glossary.is_phi_column("MemberFirstName") is True
    assert glossary.is_phi_column("attribution_model") is False
    # Every spelling of every flagged term, normalised — `Patient_dob` and
    # `patient_dob` are one answer, not two, so the key is smaller than the
    # raw synonym count and that is correct.
    assert glossary.phi_columns() == {
        "dateofbirth",
        "patientdob",
        "patientdateofbirth",
        "memberdateofbirth",
        "firstname",
        "memberfirstname",
    }


def test_a_phi_flag_cannot_be_cleared_without_steward_approval() -> None:
    """The documented don't, as a raise. Three things read this flag — the
    masking policy, the detection gate, and the vector store's PHI-absence
    guarantee — so a silent downgrade is three failures at once."""
    downgraded = GlossaryTerm(
        glossary_id=BG_004.glossary_id,
        term=BG_004.term,
        definition=BG_004.definition,
        mapped_columns_original=BG_004.mapped_columns_original,
        is_phi=False,
    )
    with pytest.raises(PhiDowngradeError, match="steward approval"):
        amend(BG_004, downgraded, approved_by_steward=False)


def test_a_steward_may_clear_a_phi_flag() -> None:
    downgraded = GlossaryTerm(
        glossary_id=BG_004.glossary_id,
        term=BG_004.term,
        definition=BG_004.definition,
        mapped_columns_original=BG_004.mapped_columns_original,
        is_phi=False,
    )
    assert amend(BG_004, downgraded, approved_by_steward=True).is_phi is False


def test_raising_a_phi_flag_never_needs_approval() -> None:
    """Requiring approval to protect MORE data would be a control that
    punishes caution."""
    raised = GlossaryTerm(
        glossary_id=BG_101.glossary_id,
        term=BG_101.term,
        definition=BG_101.definition,
        is_phi=True,
    )
    assert amend(BG_101, raised, approved_by_steward=False).is_phi is True


# ── governed like everything else ────────────────────────────────────────────


def test_a_seeded_term_arrives_as_a_draft() -> None:
    """Seeding straight to Published would hand the platform 171 definitions
    nobody signed."""
    from datetime import UTC, datetime

    from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
    from cinqflow.core.model.vocabulary import ActorType

    author = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)
    obj = BG_004.as_governed(author=author, now=datetime(2026, 8, 30, tzinfo=UTC))
    assert obj.object_type is ObjectType.GLOSSARY_TERM
    assert obj.lifecycle_state is LifecycleState.DRAFT
    assert obj.is_executable is False


def test_a_term_survives_the_round_trip_through_the_registry() -> None:
    """The body is the wire format between the registry and every agent that
    grounds in it — a lossy round trip would quietly drop synonyms."""
    from datetime import UTC, datetime

    from cinqflow.core.model.governed import Actor
    from cinqflow.core.model.vocabulary import ActorType

    author = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)
    obj = BG_004.as_governed(author=author, now=datetime(2026, 8, 30, tzinfo=UTC))
    assert GlossaryTerm.from_governed(obj) == BG_004


def test_the_slug_is_an_address_and_stays_stable() -> None:
    """`term:member-date-of-birth` is a citation the UI opens. An address that
    changed when a definition was edited would break every claim that cited
    it."""
    assert BG_004.slug == "member-date-of-birth"


def test_a_term_without_a_definition_is_refused() -> None:
    """A label is not a definition, and the platform grounds mappings and
    rules in these."""
    with pytest.raises(ValueError, match="definition"):
        GlossaryTerm(glossary_id="BG-999", term="Something", definition="  ")


def test_duplicate_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Glossary(terms=(BG_004, BG_004))
