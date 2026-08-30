"""CF-V1-E6-01 against the client's REAL 171-term workbook.

    "You cannot map to a model you cannot see."

The unit suite proves the build's semantics on a fixture. This proves the
browser is worth opening on the estate that actually exists: three domains,
twenty entities, five hundred fields, and one entity deployed.

That last number is the point of the story rather than an embarrassment. The
client has designed a model; Wave 0 provisioned the first table of it. A
browser showing only the deployed table would hide the roadmap, and one
showing everything without the distinction would let a BA map to a table
nobody has created.

Skips, visibly, when the client corpus is not on the machine — a suite that
hard-failed there would train people to ignore it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.adapters.local.workbook_glossary import load_glossary
from cinqflow.core.registry.canonical import build, canonical_schemas
from cinqflow.core.registry.glossary import Glossary
from tests.conftest import require_corpus

pytestmark = pytest.mark.pipeline

WORKBOOK = (
    Path(__file__).resolve().parents[3]
    / "clientdata"
    / "Uploads"
    / "2-Design"
    / "Data lake data model.xlsx"
)


@pytest.fixture(scope="module")
def glossary() -> Glossary:
    require_corpus(WORKBOOK)
    return load_glossary(WORKBOOK)


@pytest.fixture(scope="module")
def model(glossary: Glossary):  # type: ignore[no-untyped-def]
    return build(canonical_schemas(), glossary)


def test_the_clients_three_domains_are_the_browsers_three_domains(model) -> None:  # type: ignore[no-untyped-def]
    """Census, Claims and Enrollment — read from the workbook, not chosen
    here. The client's own analysts filed all 171 terms into these three."""
    assert model.domains == ("Census", "Claims", "Enrollment")


def test_the_model_has_the_entities_the_workbook_declares(model) -> None:  # type: ignore[no-untyped-def]
    """Twenty, and each one a table the glossary names. Note what this is NOT:
    the sixty-odd rows a (domain x table) cross product would have produced."""
    names = {e.name for e in model.entities}
    assert len(model.entities) == 20
    for expected in ("Members", "DailyCensus", "Claim_IPHeader", "Members_Addresses"):
        assert expected in names


def test_one_entity_is_deployed_and_nineteen_are_designed(model) -> None:  # type: ignore[no-untyped-def]
    """The gap, computed. This is the roadmap somebody can plan against, and
    it is generated rather than maintained — so it cannot go stale."""
    assert [e.name for e in model.deployed] == ["Members"]
    assert len(model.gap) == 19


def test_the_deployed_entity_carries_its_real_columns(model) -> None:  # type: ignore[no-untyped-def]
    """`silver_raw.members` and the glossary's `Members` are ONE entity, so the
    deployed columns and the designed fields are on one page."""
    members = model.entity("Members")
    assert members is not None
    assert members.deployed and members.schema == "silver_raw"
    for column in ("member_row_id", "source_member_id", "batch_id"):
        assert members.field(column) is not None


def test_the_undefined_columns_are_a_real_worklist(model) -> None:  # type: ignore[no-untyped-def]
    """Twelve columns of the deployed table have no business definition — the
    audit columns, and a handful the glossary genuinely does not name. Shown
    as "definition missing", because hiding them would make the coverage
    number flattering and useless."""
    members = model.entity("Members")
    assert members is not None
    undefined = {f.name for f in members.undefined_fields}
    assert "record_hash" in undefined and "batch_id" in undefined
    assert 0 < len(undefined) < len(members.fields)


def test_coverage_is_high_but_not_total(model) -> None:  # type: ignore[no-untyped-def]
    """The client's glossary is genuinely good — which is why the handful of
    gaps are worth surfacing rather than lost in a sea of them."""
    defined, total = model.coverage
    assert total > 400, "the estate is bigger than the one table that exists"
    assert defined / total > 0.95
    assert defined < total, "if this ever equals total, the gap view has stopped working"


def test_a_business_term_and_a_payers_spelling_find_the_same_field(model) -> None:  # type: ignore[no-untyped-def]
    """BG-004 records that this concept arrives as `DOB`, `Patient_dob` and
    `MemberDateOfBirth`. The canonical name is `Member_Date_Of_Birth`, and
    both questions must reach it."""
    by_term = {f"{f.entity}.{f.name}" for f in model.search("date of birth")}
    by_spelling = {f"{f.entity}.{f.name}" for f in model.search("DOB")}
    assert "Members.Member_Date_Of_Birth" in by_term
    assert by_term == by_spelling


def test_every_deployed_column_of_the_real_spec_is_listed(model) -> None:  # type: ignore[no-untyped-def]
    """The drift claim, against the real spec: nothing provisioned is missing
    from the browser."""
    for schema in canonical_schemas():
        for table in schema.tables:
            entity = model.entity(table.name)
            assert entity is not None, f"{table.name} is deployed and not in the browser"
            for column in table.columns:
                assert entity.field(column.name) is not None, (
                    f"{table.name}.{column.name} is deployed and unlisted"
                )


def test_the_workbooks_own_oddities_are_surfaced_rather_than_corrected(model) -> None:  # type: ignore[no-untyped-def]
    """The glossary has a row whose "mapped table" is the phrase `All Claim
    tables` rather than a table name. The browser shows it as declared.

    Silently dropping it would be the tool editing the client's document on
    their behalf — and the whole point of generating this screen is that it
    reports what the sources say, including where they are untidy.
    """
    assert model.entity("All Claim tables") is not None
