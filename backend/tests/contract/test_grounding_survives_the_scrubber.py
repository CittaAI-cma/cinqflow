"""Every agent's grounding, through the REAL scrubber. No credentials, seconds.

    "PHI is scrubbed BEFORE any prompt is assembled" — stage 2 of six.

THIS SUITE EXISTS BECAUSE THE SCRUBBER WAS ONLY EVER EXERCISED IN LANE 3.

CF-V1-E6-02's grounding rendered canonical addresses as `entity.field`.
Presidio's URL recogniser reads `a.b` as a hostname, so
`claim_header.source_claim_id` reached the model as `claim_<URL>urce_claim_id`
and `MBR_DOB->members.date_of_birth` as `<LOCATION>` entire. The model copied
the mangled names back faithfully, the platform refused every one of its
targets as "not in the canonical model", and the agent proposed nothing for a
whole feed.

Two correct components, one unusable agent, and nothing in either of them was
wrong. It took a twenty-four-minute Lane-3 run to see it, because no cheaper
test put a real grounding through a real scrubber — the Lane-1 suites use
`PatternPhiScrub`, which has no URL recogniser and therefore cannot reproduce
it at all.

So this suite runs the LOCAL scrubber (no endpoint, no credentials, no cost)
over each agent's rendered grounding and asserts the identifiers a model must
copy back come out intact. It is the class of bug that is invisible to every
component test and fatal in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.core.agents.mapping_suggestion import ground as ground_mapping
from cinqflow.core.agents.schema_inference.grounding import ground as ground_schema
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.registry.canonical import build
from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName
from tests.conftest import require_corpus

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

#: What Presidio writes where it redacts. Its presence in a grounding means an
#: identifier the model is asked to copy back no longer exists.
REDACTED = "<"

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="claim_header",
            columns=(
                Column("source_claim_id", TypeName.STRING, nullable=False),
                Column("claim_received_date", TypeName.DATE),
                Column("admission_source", TypeName.STRING),
            ),
            primary_key=("source_claim_id",),
        ),
        Table(
            name="patient",
            columns=(
                Column("source_member_id", TypeName.STRING, nullable=False),
                Column("date_of_birth", TypeName.DATE, is_phi=True),
            ),
            primary_key=("source_member_id",),
        ),
    ),
)

GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-004",
            term="Member Date of Birth",
            definition="Date of birth of the member.",
            mapped_domains=("Enrollment",),
            mapped_tables=("patient",),
            mapped_columns_original=("DOB", "patient_dob"),
            mapped_columns_corrected=("date_of_birth",),
            is_phi=True,
        ),
    )
)

#: The client's real Fidelis claims column names, which is where this bug bit.
CONTRACT = SchemaContract(
    feed_id="fidelis-claims",
    version=1,
    columns=(
        ContractColumn("claim_id", TypeName.STRING, source_name="claim_id"),
        ContractColumn("posted_date", TypeName.STRING, source_name="posted_date"),
        ContractColumn("source_of_admission", TypeName.STRING, source_name="source_of_admission"),
    ),
)


@pytest.fixture(scope="module")
def scrub():  # type: ignore[no-untyped-def]
    """The LOCAL scrubber — the same one Lane 3 uses, and no endpoint."""
    try:
        from cinqflow.adapters.local.presidio_scrub import PresidioPhiScrub
    except ImportError:  # pragma: no cover - environment, not logic
        pytest.skip("presidio is not installed — install requirements/ai.txt")
    return PresidioPhiScrub()


def _scrubbed(scrub, text: str) -> str:  # type: ignore[no-untyped-def]
    result = scrub.scrub(text)
    return getattr(result, "text", result)


def test_the_mapping_groundings_targets_survive(scrub) -> None:  # type: ignore[no-untyped-def]
    """THE REGRESSION. Every canonical address a model is told to choose from
    must still be there after the scrubber has read it."""
    grounding = ground_mapping(
        CONTRACT,
        feed_id="fidelis-claims",
        glossary=GLOSSARY,
        model=build((DEPLOYED,), GLOSSARY),
        published_mappings=(
            FeedMapping(
                feed_id="cclf-claims",
                version=2,
                lines=(
                    MappingLine(
                        target_entity="claim_header",
                        target_field="source_claim_id",
                        source_columns=("claim_id",),
                    ),
                ),
            ),
        ),
    )
    after = _scrubbed(scrub, grounding.as_prompt_grounding())

    # THE GUARANTEE IS THE NUMBER, not the name. Every option the model may
    # choose must still be selectable after the scrubber has read the page —
    # `date_of_birth` beside the sentence "Date of birth of the member." comes
    # back `<PERSON>` under every rendering tried, and the scrubber is right
    # about it.
    for index in range(1, len(grounding.vocabulary.entries) + 1):
        assert f"  {index} = " in after, f"target #{index} is not selectable after the scrub"

    # And most names DO survive, which is what keeps the list legible. The
    # bracketing is a mitigation; this is where its value is measured.
    intact = sum(
        1 for entity, field, _ in grounding.vocabulary.entries if f"[{entity}] [{field}]" in after
    )
    assert intact >= len(grounding.vocabulary.entries) - 1, (
        f"only {intact} of {len(grounding.vocabulary.entries)} target names survived; "
        "the list is becoming unreadable even though it stays selectable"
    )


def test_the_mapping_groundings_source_columns_survive(scrub) -> None:  # type: ignore[no-untyped-def]
    """The other half: a source column the model cannot copy back exactly is a
    column the platform discards as invented."""
    grounding = ground_mapping(
        CONTRACT,
        feed_id="fidelis-claims",
        glossary=GLOSSARY,
        model=build((DEPLOYED,), GLOSSARY),
    )
    after = _scrubbed(scrub, grounding.as_prompt_grounding())
    for column in CONTRACT.source_columns:
        assert f"[{column}]" in after, f"{column} did not survive the scrubber"


#: The client's real Fidelis claims workbook. A fixture proves the rendering;
#: only the corpus proves the rendering is enough — `id_qualifier` and
#: `date_of_birth` are the two names that broke the previous one, and neither
#: would have occurred to anybody writing a fixture.
WORKBOOK = (
    Path(__file__).resolve().parents[3]
    / "clientdata"
    / "Uploads"
    / "Claims Mapping"
    / "Fidelis_Claims_Silver_Raw_Mapping (1).xlsx"
)


def test_every_real_canonical_name_survives_the_scrubber(scrub) -> None:  # type: ignore[no-untyped-def]
    """THE CONTROL, over the estate that actually exists.

    Seventy-three canonical fields and ninety payer column names from the
    client's own claims workbook, each rendered as the grounding renders it and
    put through the same scrubber Lane 3 uses. A name that trips a recogniser
    is caught here in a second — the Lane-3 run that found the first version of
    this bug took twenty-four minutes and cost real money to learn less.
    """
    from cinqflow.adapters.local.workbook_mappings import distinct_pairs, load_mappings
    from cinqflow.core.agents.mapping_suggestion.grounding import (
        address_for_a_prompt,
        identifier,
    )

    require_corpus(WORKBOOK)
    rows = distinct_pairs(load_mappings(WORKBOOK, "Fidelis to Silver Raw"))

    targets = {(row.target_entity.lower(), row.target_field): row.description for row in rows}
    rendered = "\n".join(
        f"  {address_for_a_prompt(entity, field)}" + (f" — {definition}" if definition else "")
        for (entity, field), definition in sorted(targets.items())
    )
    mangled = [line for line in _scrubbed(scrub, rendered).splitlines() if REDACTED in line]
    # A BUDGET, NOT ZERO. `date_of_birth` cannot survive and should not; what
    # matters is that the count stays near zero, because every redacted line is
    # one the model must choose by definition alone. A jump here means a
    # recogniser changed and the list is going blind.
    assert len(mangled) <= 2, (
        f"{len(mangled)} of {len(targets)} canonical names were redacted — the target list "
        "is going blind:\n" + "\n".join(mangled)
    )

    columns = "\n".join(
        f"- source column {identifier(name)}" for name in sorted({r.source_field for r in rows})
    )
    lost = [line for line in _scrubbed(scrub, columns).splitlines() if REDACTED in line]
    assert not lost, "payer column names the model could not copy back:\n" + "\n".join(lost)


def test_the_schema_inference_grounding_keeps_its_column_names(scrub) -> None:  # type: ignore[no-untyped-def]
    """CF-V1-E5-02's grounding DOES carry sample values, so redaction inside it
    is correct and expected. What must survive is the column names — the model
    is told to copy `source_name` exactly, and a mangled one is discarded."""
    roster = b"MemberID,DOB,SUBSCR_REL_CD\nMBR000001,19360201,01\nMBR000002,19370302,02\n"
    grounding = ground_schema(
        profile_bytes(roster, file_format="csv", source_fingerprint="sha256-a"),
        feed_id="fidelis-downstate-roster",
        glossary=GLOSSARY,
    )
    after = _scrubbed(scrub, grounding.as_prompt_grounding())
    for column in ("MemberID", "SUBSCR_REL_CD"):
        assert column in after, f"{column} did not survive the scrubber"
