"""CF-V1-E7-02 — the preview, and the four things it must not get wrong.

    "Trust is built in the preview, not the prose: the BA sees exactly what the
     rule catches before anyone approves it."

The prose is the part that is always plausible. "Member first name must be
populated" is agreeable on any screen; what a person needs to know is that it
fails 3 of 200 rows and which three.
"""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.rules import Check, CheckKind, RuleSpec
from cinqflow.core.rules.preview import MASKED, evidence_pack, preview, preview_all
from cinqflow.core.schema_spec import TypeName

pytestmark = pytest.mark.unit

TODAY = date(2026, 8, 30)

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn("source_member_id", TypeName.STRING, nullable=False, is_phi=True),
        ContractColumn("first_name", TypeName.STRING, is_phi=True),
        ContractColumn("line_of_business", TypeName.STRING),
    ),
)

ROWS = (
    {"source_member_id": "MBR000001", "first_name": "Ada", "line_of_business": "MEDICAID"},
    {"source_member_id": "MBR000002", "first_name": "", "line_of_business": "MEDICARE"},
    {"source_member_id": "MBR000003", "first_name": "Grace", "line_of_business": "COMMERCIAL"},
    {"source_member_id": "MBR000001", "first_name": "Alan", "line_of_business": "DUAL"},
    {"source_member_id": "MBR000005", "first_name": "Katherine", "line_of_business": None},
)


def _rule(check: Check, rule_id: str = "DQ-002", stated: str = "a rule") -> RuleSpec:
    return RuleSpec(rule_id=rule_id, name="n", stated=stated, check=check)


NOT_NULL = _rule(
    Check(kind=CheckKind.NOT_NULL, column="first_name"),
    stated="Member first name must be populated for all active members",
)
IN_SET = _rule(
    Check(
        kind=CheckKind.IN_SET,
        column="line_of_business",
        allowed=("MEDICAID", "MEDICARE", "DUAL"),
    ),
    rule_id="DQ-031",
)


# ── the counts, and the fact that they add up ───────────────────────────────


def test_the_counts_are_what_a_ba_reads_first() -> None:
    result = preview(NOT_NULL, ROWS, contract=CONTRACT)
    assert (result.tested, result.passed, result.failed) == (5, 4, 1)
    assert "5 row(s): 4 passed, 1 failed (20.0%)" in result.summary()


def test_tested_plus_skipped_is_every_row() -> None:
    """A preview whose numbers do not add up is a preview nobody can reason
    from."""
    result = preview(IN_SET, ROWS, contract=CONTRACT)
    assert result.tested + result.skipped == len(ROWS)


def test_a_row_with_no_value_is_skipped_not_passed() -> None:
    """`core.rules.passes` returns True for an absent value so a missing field
    fails exactly one rule and the drop ledger balances. Reporting those as
    PASSES here would tell a BA their rule was satisfied by rows it never
    looked at."""
    result = preview(IN_SET, ROWS, contract=CONTRACT)
    assert result.skipped == 1
    assert result.tested == 4
    assert result.failed == 1  # COMMERCIAL


def test_a_rule_that_tested_nothing_says_so_rather_than_reporting_a_pass() -> None:
    empty = ({"first_name": "Ada", "line_of_business": ""},)
    result = preview(IN_SET, empty, contract=CONTRACT)
    assert result.tested == 0
    assert "tested no rows" in result.summary()
    assert result.failure_rate == 0.0


# ── the failing rows, and the masking that happens before they exist ────────


def test_failing_rows_are_identified_by_their_line_in_the_file() -> None:
    """1-based, counting data rows — what a person sees when they open it. An
    off-by-one sends somebody to the wrong line of a payer's delivery."""
    result = preview(NOT_NULL, ROWS, contract=CONTRACT)
    assert [row.row_number for row in result.failing_rows] == [2]


def test_a_phi_column_is_masked_in_the_failing_rows() -> None:
    """From the CONTRACT's flags — the same ones CF-V1-E5-03 sets and
    CF-V4-E2-03 masks by, so there is one answer to "what is protected here"."""
    result = preview(NOT_NULL, ROWS, contract=CONTRACT)
    assert result.masked_columns == ("first_name",)
    assert result.failing_rows[0].values["first_name"] == MASKED


def test_a_column_nothing_flags_is_shown() -> None:
    """A preview that masked everything would be a preview nobody could read,
    and `line_of_business` is not protected."""
    result = preview(IN_SET, ROWS, contract=CONTRACT)
    assert result.masked_columns == ()
    assert result.failing_rows[0].values["line_of_business"] == "COMMERCIAL"


def test_with_no_contract_every_column_is_masked() -> None:
    """The safe direction, and the same asymmetry CF-V1-E5-03 chose: a field
    masked wrongly costs a reviewer a question, one unmasked wrongly is a
    disclosure."""
    result = preview(IN_SET, ROWS)
    assert result.masked_columns == ("line_of_business",)
    assert result.failing_rows[0].values["line_of_business"] == MASKED


def test_no_unmasked_value_survives_into_the_evidence() -> None:
    """THE PROPERTY, asserted over the serialised form rather than the object:
    the evidence pack is what a route returns and what is stored beside a
    proposal, and it must not contain a protected value anywhere."""
    pack = evidence_pack(
        preview_all((NOT_NULL, IN_SET), ROWS, contract=CONTRACT), sample_rows=len(ROWS)
    )
    rendered = repr(pack)
    for protected in ("Ada", "Grace", "Katherine", "Alan"):
        assert protected not in rendered, f"{protected} reached the evidence pack"


def test_the_failing_row_list_is_capped() -> None:
    """Every row kept is PHI held in a proposal payload for as long as the
    proposal lives, and a BA who needs more than a handful to believe a rule
    needs the rule explained rather than a longer list."""
    many = tuple({"first_name": "", "line_of_business": "X"} for _ in range(50))
    result = preview(NOT_NULL, many, contract=CONTRACT, limit=3)
    assert result.failed == 50
    assert result.sampled_rows_shown == 3


# ── a check that cannot be previewed says so ────────────────────────────────


def test_a_referential_check_is_not_previewable_and_says_why() -> None:
    """Reporting 0 failures for a check that never ran is the most misleading
    green a preview can show."""
    rule = _rule(
        Check(
            kind=CheckKind.EXISTS_IN,
            column="source_member_id",
            reference_table="members",
            reference_column="member_id",
        ),
        rule_id="DQ-070",
    )
    result = preview(rule, ROWS, contract=CONTRACT)

    assert not result.ran
    assert result.failed == 0
    assert "never ran" in result.summary() or "never ran" in result.not_previewable
    assert "could not be previewed" in result.summary()


def test_a_uniqueness_check_is_previewable_because_the_sample_is_enough() -> None:
    """Everything it needs is here. A duplicate member in one delivery is an
    ordinary payer fault and exactly the thing a BA wants to see before
    approving a Critical key rule."""
    rule = _rule(Check(kind=CheckKind.UNIQUE, column="source_member_id"), rule_id="DQ-001")
    result = preview(rule, ROWS, contract=CONTRACT)

    assert result.ran
    assert result.failed == 2, "MBR000001 appears on rows 1 and 4"
    assert [row.row_number for row in result.failing_rows] == [1, 4]
    assert result.failing_rows[0].values["source_member_id"] == MASKED


# ── the evidence pack carries its denominator ───────────────────────────────


def test_the_evidence_pack_records_the_sample_size() -> None:
    """ "3 rows failed" means one thing in 200 rows and another in 200,000. A
    stored figure whose denominator is missing is one somebody will quote
    wrongly."""
    pack = evidence_pack(
        preview_all((NOT_NULL, IN_SET), ROWS, contract=CONTRACT), sample_rows=len(ROWS)
    )
    assert pack["sample_rows"] == 5
    assert pack["rules_previewed"] == 2
    assert pack["total_failures"] == 2


def test_the_evidence_pack_counts_what_could_not_be_previewed() -> None:
    rule = _rule(
        Check(
            kind=CheckKind.EXISTS_IN,
            column="source_member_id",
            reference_table="members",
            reference_column="member_id",
        ),
        rule_id="DQ-070",
    )
    pack = evidence_pack(preview_all((NOT_NULL, rule), ROWS, contract=CONTRACT), sample_rows=5)
    assert pack["rules_not_previewable"] == 1
