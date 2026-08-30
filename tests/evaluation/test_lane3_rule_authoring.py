"""LANE 3 — CF-V1-E7-01's gate. The ONLY place a quality claim is made.

    "the 110 legacy rules are the labeled golden set, so accuracy is
     measurable from day one"
    — CF-V1-E7-01

THE ANSWER KEY IS THE CLIENT'S OWN RULE SHEET. Every one of its 110 rows
already pairs a plain-English description with executable SQL, a severity and a
glossary link — written by their analysts before this platform existed. The
exam is therefore a re-derivation: given the sentence they wrote, does the agent
choose the check their SQL implements?

WHAT IS GRADED IS THE CHECK KIND, NOT THE SQL. Comparing rendered SQL against
theirs would grade dialect: their queries are T-SQL, select the columns a person
would want to see, and vary in style across 110 rows. What matters is whether
`Member PK Not Null` becomes a NOT_NULL on the right column — the semantics
their SQL and this platform's rendering both express.

THE COLUMN IS GRADED TOO, and separately. A rule of the right KIND on the wrong
column is worse than no rule: it quarantines rows for a reason nobody meant, and
it looks like it is working.

Skips, visibly, until an endpoint is configured and the client corpus is on the
machine.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.core.agents.rule_authoring.prompts import TEMPLATES
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.registry.glossary import Glossary
from cinqflow.core.rules import CheckKind
from cinqflow.core.schema_spec import TypeName
from cinqflow.intelligence.agents.rule_authoring import RuleAuthoringAgent
from cinqflow.intelligence.gateway import LlmGateway

pytestmark = [pytest.mark.evaluation, pytest.mark.lane3]

#: A REGRESSION FLOOR, measured then set — see the same note in
#: `test_lane3_mapping_suggestion.py`. Its job is to catch the day this stops
#: working, not a target somebody tuned to.
#:
#: The first honest run scored 6 of 12: one disagreement and five sentences
#: sent to technical review. Read those in that order — the agent was never
#: confidently wrong about a column, and the single kind it "missed" is a row
#: where the client's own sheet disagrees with itself. See
#: `test_the_answer_key_has_a_row_that_disagrees_with_itself`.
GATE = 0.45

#: THE NUMBER THAT MATTERS. A rule of the right kind on the WRONG column
#: quarantines rows for a reason nobody meant, and looks like it is working.
#: An agent that declines is fine; one that is confidently wrong is not.
MAX_WRONG_COLUMN = 0.10

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
FEED = "cinq-enrollment"

WORKBOOK = (
    Path(__file__).resolve().parents[3]
    / "clientdata"
    / "Uploads"
    / "2-Design"
    / "Data lake data model.xlsx"
)

#: The client's sub-dimension vocabulary, mapped onto the check kinds it
#: describes. THEIR classification, not one invented for the grade — a rule
#: they filed under "Mandatory Field" is a NOT_NULL by their own taxonomy.
#:
#: The sub-dimensions absent from this table are the ones whose rules need
#: something the check vocabulary does not express (cross-domain joins,
#: population statistics, workflow state). Those are UNGRADED rather than
#: counted as misses: the honest answer for them is `unsupported`, and
#: CF-V1-E7-04's queue is where they belong.
GRADED_SUB_DIMENSIONS: dict[str, CheckKind] = {
    "Mandatory Field": CheckKind.NOT_NULL,
    "Primary Key": CheckKind.UNIQUE,
    "Business Key": CheckKind.UNIQUE,
    "Code Set": CheckKind.IN_SET,
    "Format": CheckKind.MATCHES_PATTERN,
    "Range": CheckKind.BETWEEN,
    "Intra-Record": CheckKind.COMPARE_COLUMNS,
    "Date Logic": CheckKind.COMPARE_COLUMNS,
    "Referential": CheckKind.EXISTS_IN,
    "Currency": CheckKind.FRESHNESS,
    "Staleness": CheckKind.FRESHNESS,
}

#: How many rules go into one run. Batched for the reason CF-V1-E6-02 was:
#: one call for everything returned nothing at all, twice.
BATCH = 12


def _published(obj: Any) -> Any:
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture(scope="module")
def golden() -> tuple[dict[str, Any], ...]:
    from cinqflow.adapters.local.workbook_glossary import load_dq_rule_rows

    if not WORKBOOK.exists():
        pytest.skip(f"the client corpus is not on this machine ({WORKBOOK.name} absent)")
    rows = load_dq_rule_rows(WORKBOOK)
    graded = tuple(
        row
        for row in rows
        if str(row.get("DQ Sub-Dimension") or "").strip() in GRADED_SUB_DIMENSIONS
        and str(row.get("Corrected Column(s)") or "").strip()
    )
    return graded[:BATCH]


@pytest.fixture(scope="module")
def contract(golden: tuple[dict[str, Any], ...]) -> SchemaContract:
    """A contract holding every column the graded rules are about.

    Built from the sheet's own `Corrected Column(s)`, which is the canonical
    spelling — a rule runs AFTER the mapping, on the contracted shape, so a
    rule about `OurID` would be a rule about a column that no longer exists by
    the time anything checks it.
    """
    seen: dict[str, None] = {}
    for row in golden:
        for name in str(row.get("Corrected Column(s)") or "").split(","):
            if cleaned := name.strip():
                seen.setdefault(cleaned, None)
    # Enough companions for the intra-record rules to have something to compare
    # against; the agent chooses, and a wrong choice is a wrong column.
    for extra in ("Effective_Date", "Termination_Date", "Admission_Date", "Discharge_Date"):
        seen.setdefault(extra, None)
    return SchemaContract(
        feed_id=FEED,
        version=1,
        columns=tuple(ContractColumn(name, TypeName.STRING) for name in seen),
    )


@pytest.fixture
def agent(lane3_llm: Any) -> RuleAuthoringAgent:
    from cinqflow.adapters.local.presidio_scrub import PresidioPhiScrub
    from cinqflow.adapters.local.secrets import DotenvSecrets
    from cinqflow.installer.profile import load
    from cinqflow.intelligence.wiring import budget_from, routing_from

    profile = load("profiles/local.yaml")
    store = MemMetadataDb()
    for template in TEMPLATES:
        store.save(_published(template.as_governed(author=BA, now=NOW)))

    gateway = LlmGateway(
        llm=lane3_llm,
        phi_scrub=PresidioPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=budget_from(profile),
        routing=routing_from(profile, DotenvSecrets()),
        estimate_usd=Decimal("0.05"),
        clock=lambda: NOW,
    )
    return RuleAuthoringAgent(llm=gateway, metadata=store)


#: ONE run, shared. Memoised rather than a module-scoped fixture because
#: `lane3_llm` is function-scoped by design — see the same note in
#: `test_lane3_mapping_suggestion.py`.
_RUN: list[Any] = []


@pytest.fixture
def proposed(agent, contract, golden):  # type: ignore[no-untyped-def]
    if not _RUN:
        _RUN.append(
            agent.propose(
                tuple(str(row["Rule Description"]).strip() for row in golden),
                feed_id=FEED,
                contract=contract,
                # EMPTY. The client's glossary covers business terms, and
                # seeding it with the answers would not be a glossary.
                glossary=Glossary(terms=()),
                caller=BA,
                now=NOW,
            )
        )
    return _RUN[0]


def _expected(golden: tuple[dict[str, Any], ...]) -> dict[str, tuple[CheckKind, set[str]]]:
    """Sentence -> (the kind their taxonomy implies, the columns they name)."""
    key: dict[str, tuple[CheckKind, set[str]]] = {}
    for row in golden:
        sentence = str(row["Rule Description"]).strip()
        kind = GRADED_SUB_DIMENSIONS[str(row["DQ Sub-Dimension"]).strip()]
        columns = {
            name.strip().lower()
            for name in str(row.get("Corrected Column(s)") or "").split(",")
            if name.strip()
        }
        key[sentence.lower()] = (kind, columns)
    return key


# ── the gate ─────────────────────────────────────────────────────────────────


def test_the_agent_re_derives_the_analysts_checks(proposed, golden) -> None:  # type: ignore[no-untyped-def]
    """THE GATE. Given the sentence an analyst wrote, does the agent choose the
    check their SQL implements?"""
    assert not proposed.manual_path, (
        "the gateway escalated to the manual path — every sentence came back as "
        "technical review. A broken run, and grading it would report a careful agent."
    )

    key = _expected(golden)
    right = wrong_kind = declined = 0
    misses: list[str] = []

    for authored in proposed.rules:
        expected = key.get(authored.stated.strip().lower())
        if expected is None:
            continue
        kind, _ = expected
        if authored.rule.check.kind is kind:
            right += 1
            continue
        wrong_kind += 1
        misses.append(
            f"  {authored.stated[:70]}: chose {authored.rule.check.kind.value}, "
            f"the analyst's SQL implements {kind.value}"
        )
    for review in proposed.needs_review:
        if review.stated.strip().lower() in key:
            declined += 1
            # The DECLINE REASONS travel with the failure, not just the count.
            # "Five went to technical review" and "five went because the
            # confidence floor is too high for this task" are different
            # findings, and only the second is actionable — the first sends
            # somebody back for another run to learn it.
            misses.append(f"  DECLINED · {review.stated[:60]}: {review.reason[:120]}")

    total = right + wrong_kind + declined
    assert total == len(key), "every graded sentence must appear in the proposal"
    rate = right / total if total else 0.0
    assert rate >= GATE, (
        f"{right}/{total} checks re-derived ({rate:.1%}, gate {GATE:.0%}) — "
        f"{wrong_kind} wrong kind, {declined} sent to technical review. "
        f"Cost ${proposed.cost_usd}.\n" + "\n".join(misses)
    )


def test_a_rule_is_never_written_against_the_wrong_column(proposed, golden) -> None:  # type: ignore[no-untyped-def]
    """THE NUMBER THAT MATTERS. A check of the right kind on the wrong column
    quarantines rows for a reason nobody meant — and it looks like it is
    working, which is why it survives."""
    key = _expected(golden)
    wrong: list[str] = []
    checked = 0

    for authored in proposed.rules:
        expected = key.get(authored.stated.strip().lower())
        if expected is None:
            continue
        checked += 1
        _, columns = expected
        if authored.rule.check.column.lower() not in columns:
            wrong.append(
                f"  {authored.stated[:70]}: wrote a rule on "
                f"{authored.rule.check.column}, the analyst named {', '.join(sorted(columns))}"
            )

    ratio = len(wrong) / checked if checked else 0.0
    assert ratio <= MAX_WRONG_COLUMN, (
        f"{len(wrong)}/{checked} rules landed on a column the analyst did not name "
        f"({ratio:.1%}, ceiling {MAX_WRONG_COLUMN:.0%}):\n" + "\n".join(wrong)
    )


def test_no_rule_carries_sql_the_model_wrote(proposed) -> None:  # type: ignore[no-untyped-def]
    """Every notation is rendered by the platform from the check. Asserted on a
    REAL model's output, because "the schema forbids it" and "the model never
    finds a way" are different claims."""
    for authored in proposed.rules:
        rendered = authored.rule.sql(table="silver_raw")
        assert rendered.startswith("SELECT * FROM silver_raw WHERE ")
        assert ";" not in rendered, "a rendered check is one statement, always"
        assert authored.rule.check.column in rendered


def test_every_declined_sentence_says_what_is_missing(proposed) -> None:  # type: ignore[no-untyped-def]
    """CF-V1-E7-04's queue is only worth having if its entries are actionable.
    "Never silent failure" means a BA who typed a sentence and got nothing back
    has been told nothing."""
    for review in proposed.needs_review:
        assert review.reason.strip(), f"{review.stated!r} was declined with no reason"
        assert len(review.reason) > 20, (
            f"{review.stated!r} was declined with {review.reason!r}, which tells an engineer "
            "nothing they can act on"
        )


# ── the golden set disagrees with itself, once, and that is worth knowing ────


def test_the_answer_key_has_a_row_that_disagrees_with_itself(golden) -> None:  # type: ignore[no-untyped-def]
    """FOUND BY THE GATE, and kept rather than quietly corrected.

    A rule reading "every claim must have a unique claim identifier" is filed
    under "Mandatory Field" and the SQL beside it implements NOT NULL — but the
    sentence says UNIQUE, and the agent chose UNIQUE. Reading the sentence, the
    agent is right and the label is not.

    Recorded here rather than patched into `GRADED_SUB_DIMENSIONS`, because the
    answer key is the CLIENT'S artefact and editing it so a score looks better
    is the one thing a golden set must never allow. It costs one point; it buys
    a real finding for the steward who owns that sheet.
    """
    disagreeing = [
        row
        for row in golden
        if "unique" in str(row.get("Rule Description") or "").lower()
        and str(row.get("DQ Sub-Dimension") or "").strip() == "Mandatory Field"
    ]
    if not disagreeing:
        pytest.skip("the batch under test does not include the row this documents")
    row = disagreeing[0]
    assert "NULL" in str(row.get("SQL Validation Query") or "").upper(), (
        "the SQL is what makes this a real disagreement rather than a loose sentence"
    )
