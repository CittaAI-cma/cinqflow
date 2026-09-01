"""CF-V2-E5-04 — drift classified by what it means.

    "Given a payer renames a column to date_of_birth, when the file arrives,
     then drift is classified compatible with the glossary evidence shown,
     processing continues, and a proposed contract v2 awaits steward
     approval." — the story's happy path

Structure sees two events; the glossary sees one rename — and ambiguity is
never guessed through, for the same reason the profiler refused to type
`19360201`.
"""

from __future__ import annotations

import pytest

from cinqflow.core.drift import attach_blast_radius, blast_radius, classify
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.registry.contract import (
    ContractColumn,
    DriftKind,
    SchemaContract,
    Severity,
    compare_to_contract,
    not_null,
)
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import TypeName

pytestmark = pytest.mark.unit

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn("source_member_id", TypeName.STRING, nullable=False, source_name="MemberID"),
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn(
            "date_of_birth", TypeName.DATE, nullable=False, source_name="DOB", is_phi=True
        ),
    ),
    key_columns=("source_member_id",),
)

DQ_014 = not_null(
    "DQ-014",
    "date_of_birth",
    name="Member DOB Not Null",
    severity=Severity.HIGH,
    description="Required for identity resolution and CMS submissions",
    glossary_id="BG-004",
)


def _term(glossary_id: str, term: str, synonyms: tuple[str, ...]) -> GlossaryTerm:
    return GlossaryTerm(
        glossary_id=glossary_id,
        term=term,
        definition=f"{term}, as the estate spells it.",
        mapped_columns_original=synonyms,
        is_phi=True,
    )


GLOSSARY = Glossary(
    terms=(
        _term("BG-004", "Member Date of Birth", ("DOB", "date_of_birth", "Patient_dob")),
        _term("BG-002", "Member First Name", ("First_Name", "first_name")),
    )
)


def _classified(arrived: tuple[str, ...]):
    findings = compare_to_contract(arrived, CONTRACT)
    return classify(findings, contract=CONTRACT, glossary=GLOSSARY)


# ── the happy path, verbatim ──────────────────────────────────────────────────
def test_dob_to_date_of_birth_is_one_rename_with_the_term_as_evidence() -> None:
    assessment = _classified(("MemberID", "First_Name", "date_of_birth"))

    (rename,) = assessment.renames
    assert (rename.was, rename.now) == ("DOB", "date_of_birth")
    assert rename.glossary_id == "BG-004"
    assert "one concept, two spellings" in rename.explain()

    kinds = {f.kind for f in assessment.findings}
    assert kinds == {DriftKind.RENAMED}
    assert assessment.blocking == ()  # processing continues
    assert assessment.reads_as == {"DOB": "date_of_birth"}
    assert assessment.proposes_contract_update is True


def test_a_truly_new_column_is_additive_and_nothing_more() -> None:
    assessment = _classified(("MemberID", "First_Name", "DOB", "plan_tier"))
    assert assessment.renames == ()
    assert assessment.additions == ("plan_tier",)
    assert assessment.blocking == ()
    assert assessment.proposes_contract_update is False


def test_a_required_column_that_truly_vanished_still_blocks() -> None:
    """The glossary settles renames; it must never talk a genuine break out
    of blocking. `DOB` gone with nothing arriving in its place is the story's
    exception path — quarantine before Bronze."""
    assessment = _classified(("MemberID", "First_Name"))
    (finding,) = assessment.blocking
    assert finding.kind is DriftKind.REMOVED
    assert finding.column == "DOB"


def test_ambiguity_is_never_guessed_through() -> None:
    """Two arriving columns both claimed by the DOB term: the pairing is not
    unique, so it is NOT a rename — both findings stand, for a person."""
    assessment = _classified(("MemberID", "First_Name", "date_of_birth", "Patient_dob"))
    assert assessment.renames == ()
    kinds = sorted(f.kind.value for f in assessment.findings)
    assert kinds == ["added", "added", "removed"]
    assert assessment.blocking != ()  # DOB is required and unsettled


def test_classification_only_ever_downgrades_never_hides() -> None:
    """Every structural finding survives classification — as itself, or as
    the RENAMED it folded into. A finding count that shrinks by more than the
    folds is a finding somebody lost."""
    findings = compare_to_contract(("MemberID", "First_Name", "date_of_birth"), CONTRACT)
    assessment = classify(findings, contract=CONTRACT, glossary=GLOSSARY)
    folded_pairs = len(assessment.renames)
    assert len(assessment.findings) == len(findings) - folded_pairs


# ── blast radius ──────────────────────────────────────────────────────────────
def test_the_blast_radius_names_the_field_and_the_rules_from_lineage() -> None:
    radius = blast_radius("DOB", contract=CONTRACT, rules=(DQ_014,))
    assert radius.canonical_field == "date_of_birth"
    assert radius.rule_ids == ("DQ-014",)
    assert "DQ-014" in radius.explain()


def test_a_column_nothing_reads_has_an_empty_radius_not_an_error() -> None:
    radius = blast_radius("plan_tier", contract=CONTRACT, rules=(DQ_014,))
    assert radius.canonical_field == ""
    assert radius.rule_ids == ()


# ── W1-32: additive, contract-unknown AND mapping-unknown ───────────────────
#
#     "a column that's additive-and-contract-unknown AND has no mapping line
#      covering it becomes a new finding" — W1-32
#
# `additions` was already free from `classify()`; what was missing was the
# second question — does the feed's own PUBLISHED mapping cover it?

MAPPING = FeedMapping(
    feed_id="fidelis-downstate-roster",
    version=4,
    lines=(
        MappingLine(
            target_entity="members", target_field="source_member_id", source_columns=("MemberID",)
        ),
        MappingLine(
            target_entity="members", target_field="first_name", source_columns=("First_Name",)
        ),
        MappingLine(target_entity="members", target_field="date_of_birth", source_columns=("DOB",)),
    ),
)


def test_an_addition_with_no_mapping_line_becomes_unmapped_column() -> None:
    """`plan_tier` is additive AND contract-unknown already
    (`test_a_truly_new_column_is_additive_and_nothing_more`); handing
    `classify` the published mapping asks the SECOND question and gets a
    SECOND, more specific finding — the plain ADDED finding is not hidden,
    only joined."""
    findings = compare_to_contract(("MemberID", "First_Name", "DOB", "plan_tier"), CONTRACT)
    assessment = classify(findings, contract=CONTRACT, glossary=GLOSSARY, mapping=MAPPING)

    assert assessment.additions == ("plan_tier",)  # unchanged by W1-32
    kinds = sorted(f.kind.value for f in assessment.findings)
    assert kinds == ["added", "unmapped_column"]
    (unmapped,) = (f for f in assessment.findings if f.kind is DriftKind.UNMAPPED_COLUMN)
    assert unmapped.column == "plan_tier"
    assert unmapped.blocks_batch is False, "coverage drift never blocks — same rule as RENAMED"
    assert "plan_tier" in unmapped.detail
    assert "mapping" in unmapped.detail.lower()


def test_an_addition_the_mapping_already_reads_is_not_flagged() -> None:
    """A line reading `plan_tier` already exists — a BA mapped ahead of the
    contract, or `unlisted` corpus dust — so there is nothing to flag: the
    mapping, not just the contract, already governs it."""
    covered = FeedMapping(
        feed_id=MAPPING.feed_id,
        version=MAPPING.version,
        lines=(
            *MAPPING.lines,
            MappingLine(
                target_entity="members",
                target_field="line_of_business",
                source_columns=("plan_tier",),
            ),
        ),
    )
    findings = compare_to_contract(("MemberID", "First_Name", "DOB", "plan_tier"), CONTRACT)
    assessment = classify(findings, contract=CONTRACT, glossary=GLOSSARY, mapping=covered)

    kinds = {f.kind for f in assessment.findings}
    assert DriftKind.UNMAPPED_COLUMN not in kinds
    assert kinds == {DriftKind.ADDED}


def test_without_a_mapping_the_unmapped_column_check_never_runs() -> None:
    """`mapping=None` (the default, and still the common case per W1-30) must
    leave `classify` byte-identical to every caller above this comment — none
    of them passes one."""
    findings = compare_to_contract(("MemberID", "First_Name", "DOB", "plan_tier"), CONTRACT)
    assessment = classify(findings, contract=CONTRACT, glossary=GLOSSARY)

    assert {f.kind for f in assessment.findings} == {DriftKind.ADDED}


# ── W1-32: wiring `blast_radius` into the real path ─────────────────────────


def test_attach_blast_radius_folds_a_real_radius_into_removed_and_renamed() -> None:
    """REMOVED and RENAMED both have real lineage — REMOVED because the
    column IS a contract column, RENAMED because the OLD spelling
    (`Rename.was`) is. Both get a NON-EMPTY radius here, naming DQ-014."""
    removed_findings = compare_to_contract(("MemberID", "First_Name"), CONTRACT)
    removed = classify(removed_findings, contract=CONTRACT, glossary=GLOSSARY)
    removed = attach_blast_radius(removed, contract=CONTRACT, rules=(DQ_014,))
    (finding,) = removed.blocking
    assert "DQ-014" in finding.detail

    renamed_findings = compare_to_contract(("MemberID", "First_Name", "date_of_birth"), CONTRACT)
    renamed = classify(renamed_findings, contract=CONTRACT, glossary=GLOSSARY)
    renamed = attach_blast_radius(renamed, contract=CONTRACT, rules=(DQ_014,))
    (finding,) = (f for f in renamed.findings if f.kind is DriftKind.RENAMED)
    assert "DQ-014" in finding.detail, "RENAMED must read the OLD spelling's lineage, not the new"


def test_attach_blast_radius_on_an_unmapped_column_is_present_but_honestly_empty() -> None:
    """THE TRADEOFF, NAMED RATHER THAN HIDDEN. `plan_tier` has no contract
    lineage BY DEFINITION — that is what "contract-unknown" means — so
    `blast_radius` correctly hands back an empty radius, exactly as it does
    for its own `test_a_column_nothing_reads_has_an_empty_radius_not_an_error`.
    Wiring it in does not manufacture rules that do not exist; it makes the
    absence of any into a fact `detail` states, in the operator's own words,
    instead of the finding staying silent about impact altogether."""
    findings = compare_to_contract(("MemberID", "First_Name", "DOB", "plan_tier"), CONTRACT)
    assessment = classify(findings, contract=CONTRACT, glossary=GLOSSARY, mapping=MAPPING)
    assessment = attach_blast_radius(assessment, contract=CONTRACT, rules=(DQ_014,))

    (unmapped,) = (f for f in assessment.findings if f.kind is DriftKind.UNMAPPED_COLUMN)
    assert "affected rules: no rules" in unmapped.detail
    assert unmapped.detail != ""
