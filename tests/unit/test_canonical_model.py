"""CF-V1-E6-01 — the canonical browser, and the claim that it cannot drift.

    "generated from deployed model (drift impossible by construction) ·
     business-term search · 'definition missing' visible"

The drift claim is the one worth proving, and it is proved by a pair of tests
rather than asserted: every deployed column appears in the browser, and every
field the browser marks `deployed` resolves to a real column of the spec. There
is no third list, so there is nothing to keep in step.

`tests/pipeline/test_canonical_on_the_real_corpus.py` runs the same build
against the client's actual 171-term workbook, where the model has three
domains and twenty entities and one of them is deployed.
"""

from __future__ import annotations

import pytest

from cinqflow.core.registry.canonical import (
    DEFINITION_MISSING,
    build,
    canonical_schemas,
)
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName

pytestmark = pytest.mark.unit

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="members",
            comment="The member, canonically.",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                Column("date_of_birth", TypeName.DATE, is_phi=True),
                Column("line_of_business", TypeName.STRING),
                # No glossary term claims this one. It is what
                # "definition missing" is for.
                Column("record_hash", TypeName.STRING, nullable=False),
            ),
            primary_key=("member_row_id",),
        ),
    ),
)

GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-004",
            term="Member Date of Birth",
            definition="Date of birth of the member, used for age and eligibility.",
            mapped_domains=("Enrollment", "Claims"),
            mapped_tables=("Members", "Claim_IPHeader"),
            mapped_columns_original=("DOB", "Patient_dob"),
            mapped_columns_corrected=("Date_Of_Birth",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-050",
            term="Line of Business",
            definition="The product line a member is enrolled under.",
            mapped_domains=("Enrollment",),
            mapped_tables=("Members",),
            mapped_columns_corrected=("Line_Of_Business",),
        ),
        GlossaryTerm(
            glossary_id="BG-090",
            term="Claim Paid Amount",
            definition="What the plan paid on the claim.",
            mapped_domains=("Claims",),
            mapped_tables=("Claim_IPHeader",),
            mapped_columns_corrected=("Paid_Amount",),
        ),
    )
)


@pytest.fixture
def model():  # type: ignore[no-untyped-def]
    return build((DEPLOYED,), GLOSSARY)


# ── drift is impossible because there is no third list ───────────────────────


def test_every_deployed_column_appears_in_the_browser(model) -> None:  # type: ignore[no-untyped-def]
    """Half of the drift claim. A column in the database that the browser did
    not show would be a column nobody could map to."""
    entity = model.entity("Members")
    assert entity is not None
    for column in DEPLOYED.table("members").columns:
        assert entity.field(column.name) is not None, f"{column.name} is deployed and unlisted"


def test_every_field_marked_deployed_is_a_real_column(model) -> None:  # type: ignore[no-untyped-def]
    """The other half. A field claiming to be deployed that is not would send
    a BA to map against a column that does not exist."""
    real = {
        (table.name.lower(), column.name.lower())
        for table in DEPLOYED.tables
        for column in table.columns
    }
    for entity in model.entities:
        for column in entity.fields:
            if column.deployed:
                assert (entity.name.lower(), column.name.lower()) in real, (
                    f"{entity.name}.{column.name} claims to be deployed and is not"
                )


def test_the_canonical_target_is_not_every_schema() -> None:
    """A BA offered `batch_stage_status` as a mapping target has been shown
    the wrong thing — the platform's own plumbing is not the canonical model."""
    names = {schema.name for schema in canonical_schemas()}
    assert names == {"silver_raw", "silver_ods"}
    assert "control" not in names and "audit" not in names


# ── domains → entities → fields ──────────────────────────────────────────────


def test_an_entity_is_a_table_carrying_its_domains(model) -> None:  # type: ignore[no-untyped-def]
    """THE SHAPE FIX. Building entities from the glossary's (domain x table)
    cross product produced 44 entities where the client has 20 —
    `Claim_IPHeader` filed under three domains as if it were three tables."""
    assert sorted(e.name for e in model.entities) == ["Claim_IPHeader", "Members"]
    members = model.entity("Members")
    assert members is not None
    assert members.domains == ("Claims", "Enrollment")


def test_one_entity_may_belong_to_two_domains(model) -> None:  # type: ignore[no-untyped-def]
    """Not a bug — `Members` really is used by Enrollment and by Claims."""
    assert "Members" in [e.name for e in model.in_domain("Enrollment")]
    assert "Members" in [e.name for e in model.in_domain("Claims")]


def test_the_deployed_and_the_designed_are_distinguished(model) -> None:  # type: ignore[no-untyped-def]
    """A browser showing only deployed entities hides the roadmap; one showing
    everything without the distinction implies things exist that do not."""
    assert [e.name for e in model.deployed] == ["Members"]
    assert [e.name for e in model.gap] == ["Claim_IPHeader"]


def test_a_deployed_table_is_listed_under_the_glossarys_spelling(model) -> None:  # type: ignore[no-untyped-def]
    """`silver_ods.members` and the glossary's `Members` are one entity.
    Listing both would put the estate's most important table on the screen
    twice with half its fields each."""
    assert len([e for e in model.entities if e.name.lower() == "members"]) == 1


def test_a_deployed_field_carries_its_type_and_a_designed_one_does_not(model) -> None:  # type: ignore[no-untyped-def]
    members = model.entity("Members")
    claims = model.entity("Claim_IPHeader")
    assert members is not None and claims is not None

    dob = members.field("Date_Of_Birth")
    assert dob is not None and dob.deployed and dob.type is TypeName.DATE

    paid = claims.field("Paid_Amount")
    assert paid is not None and not paid.deployed and paid.type is None


# ── "definition missing" is shown, not suppressed ────────────────────────────


def test_a_column_no_term_claims_says_definition_missing(model) -> None:  # type: ignore[no-untyped-def]
    """Not a blank cell, and certainly not the column's name repeated back as
    if it were an explanation."""
    members = model.entity("Members")
    assert members is not None
    orphan = members.field("record_hash")
    assert orphan is not None
    assert not orphan.is_defined
    assert orphan.shown_definition == DEFINITION_MISSING


def test_undefined_fields_are_a_stewards_worklist(model) -> None:  # type: ignore[no-untyped-def]
    members = model.entity("Members")
    assert members is not None
    assert [f.name for f in members.undefined_fields] == ["member_row_id", "record_hash"]


def test_coverage_is_two_integers_not_a_percentage(model) -> None:  # type: ignore[no-untyped-def]
    """So a reader can recompute the rate rather than trust a rounded one."""
    defined, total = model.coverage
    assert total > defined > 0
    assert (defined, total) == (
        sum(len(e.defined_fields) for e in model.entities),
        sum(len(e.fields) for e in model.entities),
    )


def test_undefined_columns_are_counted_not_hidden(model) -> None:  # type: ignore[no-untyped-def]
    """Hiding them would make the coverage number flattering and useless."""
    members = model.entity("Members")
    assert members is not None
    assert members.coverage == (2, 4)


# ── business-term search ─────────────────────────────────────────────────────


def test_a_business_term_finds_the_canonical_field(model) -> None:  # type: ignore[no-untyped-def]
    found = {f"{f.entity}.{f.name}" for f in model.search("date of birth")}
    assert "Members.Date_Of_Birth" in found


def test_a_payers_spelling_finds_the_canonical_field(model) -> None:  # type: ignore[no-untyped-def]
    """`DOB` is what arrives in a file header; `Date_Of_Birth` is what the
    estate calls it. The synonym set is the bridge, and a browser that ignored
    it would answer only the BA's half of the question."""
    found = {f.name for f in model.search("DOB")}
    assert "Date_Of_Birth" in found


def test_synonym_matching_is_whole_word_not_substring(model) -> None:
    """A substring match would make `id` find every column in the estate, and
    a search that returns everything has answered nothing."""
    assert not any(f.name == "Line_Of_Business" for f in model.search("DOB"))


def test_search_reaches_the_definition_text(model) -> None:  # type: ignore[no-untyped-def]
    """A BA who remembers the concept but not the field name."""
    assert {f.name for f in model.search("eligibility")} == {"Date_Of_Birth"}


def test_an_empty_search_returns_nothing_rather_than_everything(model) -> None:  # type: ignore[no-untyped-def]
    assert model.search("") == ()
    assert model.search("   ") == ()


def test_a_phi_flag_reaches_the_canonical_field(model) -> None:  # type: ignore[no-untyped-def]
    """The same flag CF-V1-E5-03 protects and CF-V4-E2-03 masks — read from
    the glossary, so the browser and the masking policy cannot disagree."""
    members = model.entity("Members")
    assert members is not None
    assert [f.name for f in members.phi_fields] == ["Date_Of_Birth"]


# ── nothing is silently corrected ────────────────────────────────────────────


def test_a_deployed_table_no_domain_claims_is_surfaced_not_dropped() -> None:
    """A provisioned table nobody's business language names is a finding worth
    a steward's attention, not a row to hide."""
    orphan = Schema(
        name="silver_ods",
        description="test",
        tables=(
            Table(
                name="care_gaps",
                columns=(Column("gap_id", TypeName.UUID, nullable=False),),
                primary_key=("gap_id",),
            ),
        ),
    )
    model = build((orphan,), GLOSSARY)
    assert model.unclaimed_tables == ("care_gaps",)
    entity = model.entity("care_gaps")
    assert entity is not None and entity.domains == ("unmapped",)
