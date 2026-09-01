"""CF-V0-E8-01 — what the compiled plan does to each row.

    "Route records that fail basic checks to quarantine WITH A REASON, and keep
     processing the good ones."
    "Row counts balance exactly at every stage: rows in = rows out +
     quarantined" — the measurable bar

The decision to quarantine lives in core/ rather than in a compute adapter,
deliberately: if it lived in the renderer, the Databricks renderer would have
to re-implement a judgement, and the cross-engine golden comparison would be
comparing two implementations rather than two renderings of one plan.
"""

from __future__ import annotations

from datetime import date

import pytest

from cinqflow.core.compiler import compile_feed
from cinqflow.core.compiler.execute import apply
from cinqflow.core.registry.contract import (
    ContractColumn,
    DqRule,
    SchemaContract,
    Severity,
    not_null,
)
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.schema_spec import TypeName

pytestmark = pytest.mark.unit

FEED = FeedRecord(
    feed_id="fidelis-downstate-roster",
    domain="enrollments",
    source_system="fidelis",
    file_format="xlsx",
    landing_path="enrollments/fidelis_downstate/roster",
    file_pattern=r"_CINQDOWNSTATE_Member_Roster_\d{6}\.xlsx",
    schedule_cron="0 3 1 * *",
    sample_filename="_CINQDOWNSTATE_Member_Roster_202608.xlsx",
)

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn(
            "source_member_id", TypeName.STRING, nullable=False, source_name="MemberID", is_phi=True
        ),
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn("date_of_birth", TypeName.DATE, source_name="DOB", is_phi=True),
    ),
)

DQ_002 = not_null(
    "DQ-002",
    "first_name",
    name="Member First Name Not Null",
    severity=Severity.HIGH,
    description="Required for member outreach, care coordination and CMS submissions",
    glossary_id="BG-002",
)

DQ_LOW = DqRule(
    rule_id="DQ-091",
    name="Date of Birth Present",
    description="Useful for risk stratification, but not disqualifying",
    severity=Severity.LOW,
    columns=("date_of_birth",),
    predicate=lambda row: row.get("date_of_birth") is not None,
)


def _plan(rules: tuple[DqRule, ...] = (DQ_002,)):
    return compile_feed(feed=FEED, feed_version=1, contract=CONTRACT, rules=rules)


def _row(member: str, first: str = "ARUN", dob: str = "19900101") -> dict[str, str]:
    return {"MemberID": member, "First_Name": first, "DOB": dob}


def test_good_rows_load_and_bad_rows_quarantine_with_a_reason() -> None:
    """ "five seeded bad rows sit in quarantine WITH REASONS, and the batch
    shows Completed with counts at every stage" — CF-V0-E8-01, happy path"""
    rows = [_row("MBR000001"), _row("MBR000002", first=""), _row("MBR000003")]
    result = apply(_plan(), rows=rows, contract=CONTRACT, rules=(DQ_002,), batch_id="8842")

    assert len(result.loaded) == 2
    assert len(result.quarantined) == 1
    assert result.quarantined[0].rule_id == "DQ-002"
    assert result.quarantined[0].reason == "Member First Name Not Null"
    assert result.quarantined[0].columns == ("first_name",)


def test_processing_continues_past_a_bad_row() -> None:
    """ "keep processing the good ones". One null name must not cost a roster."""
    rows = [_row("MBR000001", first=""), _row("MBR000002"), _row("MBR000003", first="")]
    result = apply(_plan(), rows=rows, contract=CONTRACT, rules=(DQ_002,), batch_id="8842")
    assert [r["source_member_id"] for r in result.loaded] == ["MBR000002"]


def test_the_equation_balances_and_the_ledger_names_every_exclusion() -> None:
    """rows_in == rows_out + quarantined + attributed_drops, with each drop
    attributed to the specific rule that excluded it."""
    rows = [_row(f"MBR{i:06d}", first="" if i % 4 == 0 else "ARUN") for i in range(1, 21)]
    result = apply(_plan(), rows=rows, contract=CONTRACT, rules=(DQ_002,), batch_id="8842")

    assert result.balances is True
    assert result.reconciliation.records_in == 20
    assert result.reconciliation.records_out == 15
    assert result.reconciliation.attributed_drops == 5
    (entry,) = result.reconciliation.drops
    assert (entry.rule_id, entry.record_count) == ("DQ-002", 5)


def test_the_worked_example_from_the_story_reproduces_exactly() -> None:
    """22,000 in = 21,820 out + 175 (DQ-002) + 5 (structure). Balanced."""
    rows = [_row(f"MBR{i:06d}") for i in range(1, 22_001)]
    for i in range(175):
        rows[i]["First_Name"] = ""
    for i in range(175, 180):
        rows[i]["DOB"] = "not-a-date"

    result = apply(_plan(), rows=rows, contract=CONTRACT, rules=(DQ_002,), batch_id="8842")
    assert result.reconciliation.records_in == 22_000
    assert result.reconciliation.records_out == 21_820
    assert result.balances is True
    ledger = {d.rule_id: d.record_count for d in result.reconciliation.drops}
    assert ledger["DQ-002"] == 175
    assert ledger["CAST-date_of_birth"] == 5


def test_a_cast_failure_is_attributed_to_the_contract_not_to_a_dq_rule() -> None:
    """It is the CONTRACT that declared the type, so the contract owns the
    failure. Blaming a DQ rule would send someone to read the wrong object."""
    rows = [_row("MBR000001", dob="17530101")]  # incident #8: legacy type debt
    result = apply(_plan(), rows=rows, contract=CONTRACT, rules=(DQ_002,), batch_id="8842")
    (dropped,) = result.quarantined
    assert dropped.rule_id == "CAST-date_of_birth"
    assert "plausible range" in dropped.reason


def test_a_row_is_attributed_to_exactly_one_rule_even_when_it_breaks_several() -> None:
    """A row can only be dropped ONCE, and the ledger must add up.

    Attributing one row to three rules would triple-count it and break the
    balance equation — the exact failure the ledger exists to prevent.
    """
    both = DqRule(
        rule_id="DQ-999",
        name="Member ID Present",
        description="a second rule the same row also breaks",
        severity=Severity.CRITICAL,
        columns=("source_member_id",),
        predicate=lambda row: bool(row.get("source_member_id")),
    )
    rows = [{"MemberID": "MBR000001", "First_Name": "", "DOB": "19900101"}]
    result = apply(
        _plan(rules=(DQ_002, both)),
        rows=rows,
        contract=CONTRACT,
        rules=(DQ_002, both),
        batch_id="8842",
    )
    assert len(result.quarantined) == 1
    assert sum(d.record_count for d in result.reconciliation.drops) == 1
    assert result.balances is True


def test_a_low_severity_rule_warns_and_the_row_still_loads() -> None:
    """ "which rules run, at which severity — 2 reject, 7 warn."

    Quarantining on every rule would empty a roster over a missing middle name.
    """
    rows = [{"MemberID": "MBR000001", "First_Name": "ARUN", "DOB": ""}]
    result = apply(
        _plan(rules=(DQ_LOW,)), rows=rows, contract=CONTRACT, rules=(DQ_LOW,), batch_id="8842"
    )
    assert len(result.loaded) == 1
    assert len(result.quarantined) == 0
    assert result.warnings[0].rule_id == "DQ-091"
    assert result.balances is True


def test_the_map_step_renames_source_columns_to_canonical_ones() -> None:
    """In Wave 0 the mapping IS the rename declared on the contract column.
    Wave 1's mapping studio adds transforms; the step stays where it is."""
    result = apply(
        _plan(), rows=[_row("MBR000001")], contract=CONTRACT, rules=(DQ_002,), batch_id="8842"
    )
    (loaded,) = result.loaded
    assert set(loaded) == {"source_member_id", "first_name", "date_of_birth"}
    assert loaded["date_of_birth"] == date(1990, 1, 1)


def test_an_empty_delivery_balances_at_zero_and_is_visible() -> None:
    """A cycle with no members is NEWS, not an error — it must reach
    reconciliation as 0 in = 0 out so somebody sees the empty delivery."""
    result = apply(_plan(), rows=[], contract=CONTRACT, rules=(DQ_002,), batch_id="8842")
    assert result.balances is True
    assert result.reconciliation.records_in == 0
    assert result.reconciliation.explain().startswith("0 in = 0 out")


def test_the_plan_carries_no_branch_on_feed_id() -> None:
    """ "Contain any feed-specific code — everything feed-specific must come
    from metadata."

    Asserted over the execution module's AST: no comparison against a feed id
    anywhere, and nowhere in the IR to put one.
    """
    import ast
    import inspect

    from cinqflow.core.compiler import execute

    tree = ast.parse(inspect.getsource(execute))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            source = ast.unparse(node)
            assert "feed_id" not in source, f"a feed-specific branch: {source}"
