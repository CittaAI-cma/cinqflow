"""CF-V1-E7-01 — the rule as a spec, and the three renderings that must agree.

    "plain English → SQL/PySpark + business-language explanation + confidence;
     both texts stored"
    "one spec, two renderings, identical semantics"
    — plate 08

THE MODEL PRODUCES NEITHER SQL NOR PYSPARK, and these tests are what makes that
safe rather than merely different: the check vocabulary is closed, every
identifier passes one validator, every value is bound, and the local predicate
agrees with the rendered SQL about which rows fail.

The kinds are harvested from the client's own 110-rule sheet — 22 Mandatory
Field, 13 Code Set, 7 Format, 7 Intra-Record, 7 Referential, 6 Range — so the
fixtures here are shaped like rules they already wrote.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.rules import (
    Check,
    CheckKind,
    Comparison,
    Dimension,
    RuleError,
    RuleSpec,
    Severity,
    UnsafeIdentifierError,
    passes,
    render_pyspark,
    render_sql,
    rule_as_governed,
    rules_from_governed,
    safe_identifier,
    sql_parameters,
)

pytestmark = pytest.mark.unit

BA = Actor(subject="ba@cinqcare.com", actor_type=ActorType.HUMAN)
TODAY = date(2026, 8, 30)


# ── injection is structurally impossible ────────────────────────────────────


def test_a_check_carries_no_expression_field_anywhere() -> None:
    """THE SECURITY PROPERTY, asserted rather than described.

    A model asked to emit SQL is a model whose output is code, and code has to
    be reviewed as code. There is no path from a `Check` to a query string
    except through `render_sql`, and a `Check` cannot express anything but its
    own parameters.
    """
    fields = set(Check.__dataclass_fields__) | set(RuleSpec.__dataclass_fields__)
    for banned in ("sql", "expression", "predicate", "query", "code", "script"):
        assert banned not in fields, f"a rule may not carry {banned!r}"


@pytest.mark.parametrize(
    "hostile",
    [
        "member_id; DROP TABLE members",
        'member_id" OR "1"="1',
        "members.member_id",
        "member id",
        "1_starts_with_a_digit",
        "",
        "member_id--",
    ],
)
def test_a_name_that_is_not_an_identifier_never_reaches_a_query(hostile: str) -> None:
    """Checked rather than escaped. There is no quoting rule to get wrong if
    nothing but an identifier is ever accepted."""
    with pytest.raises(UnsafeIdentifierError):
        safe_identifier(hostile)
    with pytest.raises(UnsafeIdentifierError):
        Check(kind=CheckKind.NOT_NULL, column=hostile)


def test_values_are_bound_never_inlined() -> None:
    """A code set arrives from a payer's published list. Even a value the
    platform controls stays out of the query text."""
    check = Check(kind=CheckKind.IN_SET, column="lob", allowed=("MEDICAID", "O'BRIEN"))
    sql = render_sql(check, table="members")

    assert "O'BRIEN" not in sql
    assert sql.count("?") == 2
    assert sql_parameters(check) == ("MEDICAID", "O'BRIEN")


def test_every_check_kind_has_a_declared_shape() -> None:
    """The completeness test. A kind added without a shape entry raises
    KeyError on first construction — a crash, not a refusal."""
    from cinqflow.core.rules import _SHAPE

    assert set(_SHAPE) == set(CheckKind)


def test_every_check_kind_renders_in_all_three_notations() -> None:
    """Plate 08's law reaches every member of the vocabulary, not the two
    somebody remembered to wire."""
    examples = {
        CheckKind.NOT_NULL: Check(kind=CheckKind.NOT_NULL, column="first_name"),
        CheckKind.IN_SET: Check(kind=CheckKind.IN_SET, column="lob", allowed=("MEDICAID",)),
        CheckKind.MATCHES_PATTERN: Check(
            kind=CheckKind.MATCHES_PATTERN, column="mbi", pattern=r"^[0-9A-Z]{11}$"
        ),
        CheckKind.BETWEEN: Check(kind=CheckKind.BETWEEN, column="age", minimum="0", maximum="120"),
        CheckKind.COMPARE_COLUMNS: Check(
            kind=CheckKind.COMPARE_COLUMNS,
            column="discharge_date",
            other_column="admission_date",
            comparison=Comparison.GTE,
        ),
        CheckKind.UNIQUE: Check(kind=CheckKind.UNIQUE, column="member_id"),
        CheckKind.EXISTS_IN: Check(
            kind=CheckKind.EXISTS_IN,
            column="member_id",
            reference_table="members",
            reference_column="source_member_id",
        ),
        CheckKind.FRESHNESS: Check(kind=CheckKind.FRESHNESS, column="filedate", within_days=45),
    }
    assert set(examples) == set(CheckKind), "every kind needs an example here"

    for kind, check in examples.items():
        assert render_sql(check, table="members").startswith("SELECT * FROM members WHERE "), kind
        assert render_pyspark(check), kind
        assert check.explain().strip().endswith("."), f"{kind} must explain itself as a sentence"


# ── a check that cannot run is not storable ─────────────────────────────────


def test_a_code_set_check_with_no_codes_is_refused() -> None:
    with pytest.raises(RuleError, match="needs allowed"):
        Check(kind=CheckKind.IN_SET, column="lob")


def test_a_range_with_neither_bound_is_refused() -> None:
    """It passes everything while looking like a rule that says something."""
    with pytest.raises(RuleError, match="passes everything"):
        Check(kind=CheckKind.BETWEEN, column="age")


def test_a_not_null_check_carrying_a_pattern_is_refused() -> None:
    with pytest.raises(RuleError, match="no use for pattern"):
        Check(kind=CheckKind.NOT_NULL, column="first_name", pattern="^x$")


def test_an_unusable_pattern_is_refused_at_construction() -> None:
    with pytest.raises(RuleError, match="not a usable pattern"):
        Check(kind=CheckKind.MATCHES_PATTERN, column="mbi", pattern="[unclosed")


def test_a_freshness_window_is_at_least_a_day() -> None:
    with pytest.raises(RuleError, match="at least one day"):
        Check(kind=CheckKind.FRESHNESS, column="filedate", within_days=0)


def test_a_rule_with_no_stated_intent_is_refused() -> None:
    """Both texts are stored, and the BA's own words are the half a reviewer
    cannot reconstruct."""
    with pytest.raises(RuleError, match="no stated intent"):
        RuleSpec(
            rule_id="DQ-900",
            name="Something",
            stated="   ",
            check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
        )


# ── the local predicate, which is the third rendering ───────────────────────


def test_not_null_fails_blank_and_missing_alike() -> None:
    """DQ-002's own SQL reads `IS NULL OR LTRIM(RTRIM(First_Name)) = ''` — the
    client already treats a blank as absent, and so does this."""
    check = Check(kind=CheckKind.NOT_NULL, column="first_name")
    assert passes(check, {"first_name": "Ada"})
    assert not passes(check, {"first_name": "   "})
    assert not passes(check, {"first_name": None})
    assert not passes(check, {})


def test_a_null_passes_every_check_but_not_null() -> None:
    """THE LEDGER DEPENDS ON THIS. "Is this value in the allowed set?" has no
    answer for a value that is not there, and conflating absence with
    invalidity makes one missing field fail two rules — so the drop ledger
    double-counts it and the balance equation stops adding up."""
    for check in (
        Check(kind=CheckKind.IN_SET, column="lob", allowed=("MEDICAID",)),
        Check(kind=CheckKind.MATCHES_PATTERN, column="lob", pattern="^X$"),
        Check(kind=CheckKind.BETWEEN, column="lob", minimum="1", maximum="2"),
    ):
        assert passes(check, {"lob": None}), check.kind


def test_a_code_set_may_ignore_case() -> None:
    check = Check(kind=CheckKind.IN_SET, column="lob", allowed=("Medicaid",), case_sensitive=False)
    assert passes(check, {"lob": "MEDICAID"})
    assert not passes(
        Check(kind=CheckKind.IN_SET, column="lob", allowed=("Medicaid",)), {"lob": "MEDICAID"}
    )


def test_a_range_compares_numbers_as_numbers() -> None:
    """`BETWEEN 1 AND 10` must not put 9 outside the range, which a textual
    comparison does quietly."""
    check = Check(kind=CheckKind.BETWEEN, column="los", minimum="1", maximum="10")
    assert passes(check, {"los": "9"})
    assert not passes(check, {"los": "11"})


def test_a_range_compares_dates_as_dates() -> None:
    """Incident #8: service months of `1000-01` and `1753-01` in the legacy
    estate. A compact `19900101` must not be compared as text against a
    hyphenated bound."""
    check = Check(kind=CheckKind.BETWEEN, column="dob", minimum="1900-01-01", maximum="2100-01-01")
    assert passes(check, {"dob": "19900101"})
    assert not passes(check, {"dob": "1753-01-01"})


def test_comparing_two_dates_uses_date_order() -> None:
    check = Check(
        kind=CheckKind.COMPARE_COLUMNS,
        column="discharge_date",
        other_column="admission_date",
        comparison=Comparison.GTE,
    )
    assert passes(check, {"discharge_date": "2026-03-02", "admission_date": "2026-03-01"})
    assert not passes(check, {"discharge_date": "2026-02-28", "admission_date": "2026-03-01"})


def test_comparing_against_a_missing_other_column_passes() -> None:
    """`discharge_date >= admission_date` has no answer when the admission date
    is absent, and answering it anyway silently fails a row for the wrong
    reason. Absence is `NOT_NULL`'s question."""
    check = Check(
        kind=CheckKind.COMPARE_COLUMNS,
        column="discharge_date",
        other_column="admission_date",
        comparison=Comparison.GTE,
    )
    assert passes(check, {"discharge_date": "2026-03-02", "admission_date": ""})


def test_freshness_measures_against_a_supplied_date() -> None:
    check = Check(kind=CheckKind.FRESHNESS, column="filedate", within_days=45)
    assert passes(check, {"filedate": "2026-08-01"}, as_of=TODAY)
    assert not passes(check, {"filedate": "2026-01-01"}, as_of=TODAY)


def test_a_set_level_check_refuses_to_pretend_it_is_a_predicate() -> None:
    """`UNIQUE` needs the batch and `EXISTS_IN` needs another dataset.
    Answering from one row is how a preview reports a pass it never ran."""
    for check in (
        Check(kind=CheckKind.UNIQUE, column="member_id"),
        Check(
            kind=CheckKind.EXISTS_IN,
            column="member_id",
            reference_table="members",
            reference_column="source_member_id",
        ),
    ):
        assert not check.kind.is_row_level
        with pytest.raises(RuleError, match="not decided by one row"):
            passes(check, {"member_id": "MBR1"})


# ── both texts are stored, and the platform's one is generated ──────────────


def test_the_platform_explains_the_check_in_business_language() -> None:
    rule = RuleSpec(
        rule_id="DQ-002",
        name="Member First Name Not Null",
        stated="Member first name must be populated for all active members",
        check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
        dimension=Dimension.COMPLETENESS,
        proposed_severity=Severity.HIGH,
        glossary_id="BG-002",
    )
    assert rule.stated == "Member first name must be populated for all active members"
    assert rule.explanation == "first_name must be present and not blank."


def test_the_explanation_is_generated_so_it_cannot_drift() -> None:
    """The BA's words are kept verbatim; the platform's sentence comes from the
    check. When the two disagree the rule is wrong, and keeping only one makes
    that undiscoverable."""
    rule = RuleSpec(
        rule_id="DQ-XXX",
        name="Wrong",
        stated="The member's date of birth must be in the past.",
        check=Check(kind=CheckKind.NOT_NULL, column="date_of_birth"),
    )
    assert "in the past" in rule.stated
    assert "in the past" not in rule.explanation
    assert rule.explanation == "date_of_birth must be present and not blank."


def test_a_rule_set_round_trips_through_its_governed_body() -> None:
    rules = (
        RuleSpec(
            rule_id="DQ-002",
            name="Member First Name Not Null",
            stated="Member first name must be populated for all active members",
            check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
            dimension=Dimension.COMPLETENESS,
            proposed_severity=Severity.HIGH,
            glossary_id="BG-002",
            confidence=0.94,
        ),
        RuleSpec(
            rule_id="DQ-030",
            name="Line of Business Code Set",
            stated="LOB must be one of the published product lines",
            check=Check(
                kind=CheckKind.IN_SET,
                column="line_of_business",
                allowed=("MEDICAID", "MEDICARE", "DUAL"),
            ),
            dimension=Dimension.VALIDITY,
        ),
    )
    obj = rule_as_governed("fidelis-downstate-roster", rules, author=BA, contract_version=3)

    assert obj.object_type is ObjectType.DQ_RULE
    assert rules_from_governed(obj) == rules


def test_the_governed_body_carries_the_keys_lineage_expects() -> None:
    from cinqflow.core.impact import REFERENCES

    obj = rule_as_governed(
        "fidelis-downstate-roster",
        (
            RuleSpec(
                rule_id="DQ-002",
                name="n",
                stated="s",
                check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
                glossary_id="BG-002",
            ),
        ),
        author=BA,
        contract_version=3,
    )
    for spec in REFERENCES[ObjectType.DQ_RULE]:
        assert spec.body_key in obj.body, f"lineage reads {spec.body_key}, and it is not written"
    assert obj.body["glossary_ids"] == ["BG-002"]


# ── the renderings agree, which is plate 08's whole claim ───────────────────

ROWS = [
    {"first_name": "Ada", "lob": "MEDICAID", "los": "4", "dob": "19900101"},
    {"first_name": "", "lob": "MEDICAID", "los": "4", "dob": "19900101"},
    {"first_name": "Grace", "lob": "COMMERCIAL", "los": "4", "dob": "19900101"},
    {"first_name": "Alan", "lob": "DUAL", "los": "400", "dob": "19900101"},
    {"first_name": "Katherine", "lob": "DUAL", "los": "4", "dob": "1753-01-01"},
]

CHECKS = [
    Check(kind=CheckKind.NOT_NULL, column="first_name"),
    Check(kind=CheckKind.IN_SET, column="lob", allowed=("MEDICAID", "MEDICARE", "DUAL")),
    Check(kind=CheckKind.BETWEEN, column="los", minimum="0", maximum="365"),
    Check(kind=CheckKind.BETWEEN, column="dob", minimum="1900-01-01", maximum="2100-01-01"),
]


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: f"{c.kind.value}:{c.column}")
def test_the_predicate_and_the_sql_agree_about_which_rows_fail(check: Check) -> None:
    """PLATE 08, MADE OPERATIONAL. The local plane and the cluster must agree
    byte-for-byte, and the only way to guarantee it is for the semantics to
    live in one place neither of them owns.

    Executed here with sqlite, which is neither of the two production engines
    on purpose: agreement that only holds on the engine the code was written
    against is not agreement.
    """
    import sqlite3

    failing_locally = {
        index for index, row in enumerate(ROWS) if not passes(check, row, as_of=TODAY)
    }

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE members (rowid_ INTEGER, first_name TEXT, lob TEXT, los TEXT, dob TEXT)"
    )
    for index, row in enumerate(ROWS):
        connection.execute(
            "INSERT INTO members VALUES (?, ?, ?, ?, ?)",
            (index, row["first_name"], row["lob"], row["los"], row["dob"]),
        )

    sql = render_sql(check, table="members").replace("SELECT *", "SELECT rowid_")
    # sqlite spells the regex operator differently and has no date type; the
    # two checks that need those are compared through their own dialects.
    if check.kind is CheckKind.BETWEEN and check.column == "los":
        sql = sql.replace("los <", "CAST(los AS INTEGER) <").replace(
            "los >", "CAST(los AS INTEGER) >"
        )
    if check.kind is CheckKind.BETWEEN and check.column == "dob":
        pytest.skip(
            "sqlite has no date type, so agreement here would prove only that two string "
            "comparisons match. The date and pattern kinds are compared against real "
            "Postgres in tests/pipeline/test_rules_on_the_real_plane.py, where the types "
            "exist and the disagreement could actually happen."
        )

    failing_in_sql = {row[0] for row in connection.execute(sql, sql_parameters(check)).fetchall()}
    connection.close()

    assert failing_locally == failing_in_sql, (
        f"the predicate and the SQL disagree about {check.kind.value} on {check.column}: "
        f"locally {sorted(failing_locally)}, in SQL {sorted(failing_in_sql)}"
    )


def test_the_rendered_sql_selects_failures_like_the_clients_own_queries() -> None:
    """Every one of the client's 110 rules reads "SELECT ... WHERE <the thing
    that is wrong>" with "0 rows expected". Matching their shape means their
    queries and these are comparable by eye."""
    sql = render_sql(Check(kind=CheckKind.NOT_NULL, column="first_name"), table="members")
    assert sql == "SELECT * FROM members WHERE first_name IS NULL OR TRIM(first_name) = ''"


def test_the_pyspark_rendering_is_source_and_is_never_evaluated() -> None:
    """Shown to an engineer and stored in the evidence pack. The cluster
    adapter builds the real Column expression from the `Check` itself, so
    nothing in this platform ever `eval`s the string."""
    source = render_pyspark(Check(kind=CheckKind.NOT_NULL, column="first_name"))
    assert "F.col('first_name')" in source
    assert datetime.now(UTC).year  # the module imports cleanly under a clock
