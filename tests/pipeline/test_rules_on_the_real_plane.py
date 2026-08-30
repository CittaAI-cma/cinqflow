"""CF-V1-E7-01's renderings, compared against REAL Postgres.

    "one spec, two renderings, identical semantics" — plate 08

The unit suite compares the local predicate against sqlite, which is neither
production engine on purpose. But sqlite has no date type and spells the regex
operator differently, so the two kinds where a disagreement could ACTUALLY bite
— dates and patterns — are compared here, on the engine the platform runs on.

That distinction is the point. Agreement that only holds where types are all
strings is not agreement; it is two string comparisons matching.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.rules import (
    Check,
    CheckKind,
    Comparison,
    Dimension,
    RuleSpec,
    Severity,
    passes,
    render_sql,
    rule_as_governed,
    rules_from_governed,
    sql_parameters,
)

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
TODAY = date(2026, 8, 30)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
FEED = "fidelis-downstate-roster"

#: Real shapes from the client's estate, including incident #8's legacy type
#: debt: service months of 1000-01 and 1753-01 that PARSE and are impossible.
ROWS: tuple[tuple[int, str | None, str | None, str | None], ...] = (
    (0, "1990-01-01", "MEDICAID", "2026-08-01"),
    (1, "1753-01-01", "MEDICAID", "2026-08-01"),
    (2, "1990-01-01", "COMMERCIAL", "2026-08-01"),
    (3, None, "DUAL", "2026-08-01"),
    (4, "1990-01-01", "DUAL", "2026-01-01"),
    (5, "2101-06-01", "DUAL", "2026-08-01"),
)

CHECKS = (
    Check(kind=CheckKind.BETWEEN, column="dob", minimum="1900-01-01", maximum="2100-01-01"),
    Check(kind=CheckKind.MATCHES_PATTERN, column="lob", pattern="^(MEDICAID|MEDICARE|DUAL)$"),
    Check(kind=CheckKind.IN_SET, column="lob", allowed=("MEDICAID", "MEDICARE", "DUAL")),
    Check(
        kind=CheckKind.COMPARE_COLUMNS,
        column="filedate",
        other_column="dob",
        comparison=Comparison.GTE,
    ),
)


def _rows_as_dicts() -> list[dict[str, str | None]]:
    return [{"dob": dob, "lob": lob, "filedate": filedate} for _, dob, lob, filedate in ROWS]


@pytest.fixture
def sample(plane: object):  # type: ignore[no-untyped-def]
    """A real typed table, inside the rolled-back transaction."""
    plane.execute(  # type: ignore[attr-defined]
        "CREATE TEMPORARY TABLE rule_sample "
        "(n INTEGER, dob DATE, lob TEXT, filedate DATE) ON COMMIT DROP"
    )
    for row in ROWS:
        plane.execute(  # type: ignore[attr-defined]
            "INSERT INTO rule_sample VALUES (%s, %s, %s, %s)", row
        )
    return plane


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: f"{c.kind.value}:{c.column}")
def test_the_predicate_and_the_sql_agree_on_real_types(sample: object, check: Check) -> None:
    """The disagreement this catches is not hypothetical: a compact `19900101`
    compared as TEXT against `1900-01-01` sorts wrong, and `1753-01-01` is a
    date Postgres accepts and the estate should not."""
    failing_locally = {
        index for index, row in enumerate(_rows_as_dicts()) if not passes(check, row, as_of=TODAY)
    }

    sql = render_sql(check, table="rule_sample").replace("SELECT *", "SELECT n")
    parameters = sql_parameters(check)
    # `?` is the platform's placeholder; psycopg's is `%s`. Substituted here
    # rather than in `render_sql`, because the rendered SQL is also SHOWN to a
    # person and `%s` reads as a formatting bug on a screen.
    failing_in_sql = {
        row[0]
        for row in sample.fetch_all(sql.replace("?", "%s"), parameters)  # type: ignore[attr-defined]
    }

    assert failing_locally == failing_in_sql, (
        f"the predicate and real Postgres disagree about {check.kind.value} on "
        f"{check.column}: locally {sorted(failing_locally)}, in SQL {sorted(failing_in_sql)}"
    )


def test_a_date_range_catches_the_legacy_type_debt(sample: object) -> None:
    """Incident #8, as a rule. `1753-01-01` parses and is not a service date."""
    check = Check(kind=CheckKind.BETWEEN, column="dob", minimum="1900-01-01", maximum="2100-01-01")
    sql = render_sql(check, table="rule_sample").replace("SELECT *", "SELECT n").replace("?", "%s")
    found = sample.fetch_all(sql, sql_parameters(check))  # type: ignore[attr-defined]
    assert {row[0] for row in found} == {1, 5}


def test_a_rule_set_round_trips_through_its_row(plane: object) -> None:
    """The most nested body after a mapping: rules, each carrying a check,
    each check carrying a code list or a comparison."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
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
            rule_id="DQ-031",
            name="Line of Business Code Set",
            stated="LOB must be one of the published product lines",
            check=Check(
                kind=CheckKind.IN_SET,
                column="line_of_business",
                allowed=("MEDICAID", "MEDICARE", "DUAL"),
                case_sensitive=False,
            ),
            dimension=Dimension.VALIDITY,
        ),
        RuleSpec(
            rule_id="DQ-045",
            name="Discharge On Or After Admission",
            stated="A discharge date cannot precede the admission date",
            check=Check(
                kind=CheckKind.COMPARE_COLUMNS,
                column="discharge_date",
                other_column="admission_date",
                comparison=Comparison.GTE,
            ),
            dimension=Dimension.CONSISTENCY,
        ),
    )
    stored = store.save(rule_as_governed(FEED, rules, author=BA, created_ts=NOW))

    assert stored.object_type is ObjectType.DQ_RULE
    assert rules_from_governed(stored) == rules
    assert stored.body["glossary_ids"] == ["BG-002"]


# ── CF-V1-E7-02 · the evidence, on the row that actually stores it ──────────


def test_the_preview_evidence_survives_the_proposal_row(plane: object) -> None:
    """The masked rows and the counts are JSONB inside a JSONB — and this is the
    copy that outlives the request, so it is the copy worth proving.

    `record_proposal` never rewrites `payload`, which is why the evidence is
    folded in before the proposal is created rather than attached afterwards.
    This asserts the consequence: what was written is what comes back.
    """
    from cinqflow.core.citations import CitationId, CitationKind
    from cinqflow.core.model.vocabulary import RiskClass
    from cinqflow.core.proposals import Proposal, submit
    from cinqflow.core.registry.contract import ContractColumn, SchemaContract
    from cinqflow.core.rules.preview import MASKED, evidence_pack, preview_all
    from cinqflow.core.schema_spec import TypeName

    contract = SchemaContract(
        feed_id=FEED,
        version=3,
        columns=(
            ContractColumn("first_name", TypeName.STRING, is_phi=True),
            ContractColumn("line_of_business", TypeName.STRING),
        ),
    )
    rules = (
        RuleSpec(
            rule_id="DQ-002",
            name="Member First Name Not Null",
            stated="Member first name must be populated for all active members",
            check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
            dimension=Dimension.COMPLETENESS,
            proposed_severity=Severity.HIGH,
        ),
    )
    rows = (
        {"first_name": "Ada", "line_of_business": "MEDICAID"},
        {"first_name": "", "line_of_business": "MEDICARE"},
    )
    evidence = evidence_pack(preview_all(rules, rows, contract=contract), sample_rows=len(rows))

    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    stored = store.record_proposal(
        submit(
            Proposal(
                proposal_id="22222222-2222-4222-8222-222222222222",
                agent="rule-authoring",
                capability="propose_dq_rule",
                risk_class=RiskClass.R2,
                run_id="run-e702",
                feed_id=FEED,
                payload={"key": "stated", "records": [], "preview": evidence},
                created_by=Actor(
                    subject="rule-authoring", actor_type=ActorType.AI, display_name="Rules"
                ),
                created_ts=NOW,
                grounding_citations=(CitationId(kind=CitationKind.RULE, subject="DQ-002"),),
            ),
            now=NOW,
        )
    )

    back = store.get_proposal(stored.proposal_id).payload["preview"]
    assert back == evidence
    assert back["previews"][0]["failing_rows"][0]["values"]["first_name"] == MASKED
    assert "Ada" not in repr(back), "an unmasked value survived into the stored row"
