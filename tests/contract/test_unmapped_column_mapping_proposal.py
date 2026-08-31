"""W1-33 (F3) — the mapping-suggestion agent, triggered by an UNMAPPED_COLUMN
finding.

    "auto-run the mapping-suggestion agent, scoped to exactly the columns
     W1-32's drift detection just found unmapped" — W1-33

`core.drift.classify` (W1-32) already computes the exact question —
additive, contract-unknown, AND no line of the published mapping reads it —
and folds it into `DriftKind.UNMAPPED_COLUMN`. Until this slab nothing acted
on the answer; it sat in `detail` for a human to happen to read. This suite
proves the act: `workers.drift.propose_mapping_for_unmapped_columns` asks the
SAME agent CF-V1-E6-02 built, about exactly the columns one run's drift
classification just found ungoverned, through the same `core.proposals.
submit` machinery every other Wave-1 proposal travels — and proves it never
duplicates a column `propose_mapping_redirect` already claimed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.agents.mapping_suggestion.graph import (
    CAPABILITY as MAPPING_SUGGESTION_CAPABILITY,
)
from cinqflow.core.drift import classify
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.proposals import ProposalState
from cinqflow.core.registry.canonical import build
from cinqflow.core.registry.contract import (
    ContractColumn,
    DriftKind,
    SchemaContract,
    compare_to_contract,
)
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.workers.drift import (
    MAPPING_REDIRECT_CAPABILITY,
    propose_mapping_for_unmapped_columns,
    propose_mapping_redirect,
)

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="members",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                Column("date_of_birth", TypeName.DATE, is_phi=True),
                Column("relationship_code", TypeName.STRING),
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
            definition="Date of birth of the member.",
            mapped_domains=("Enrollment",),
            mapped_tables=("members",),
            mapped_columns_original=("DOB",),
            mapped_columns_corrected=("date_of_birth",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-060",
            term="Subscriber Relationship",
            definition="How the member relates to the subscriber.",
            mapped_domains=("Enrollment",),
            mapped_tables=("members",),
            mapped_columns_original=("SUBSCR_REL_CD",),
            mapped_columns_corrected=("relationship_code",),
        ),
    )
)

MODEL = build((DEPLOYED,), GLOSSARY)

#: The REAL contract — deliberately unaware of `SUBSCR_REL_CD`, which is the
#: whole point: a column this is unaware of, and the published mapping below
#: does not read either, is what `classify` calls UNMAPPED_COLUMN.
CONTRACT = SchemaContract(
    feed_id=FEED,
    version=7,
    columns=(
        ContractColumn("source_member_id", TypeName.STRING, nullable=False, source_name="MemberID"),
        ContractColumn("date_of_birth", TypeName.DATE, source_name="DOB", is_phi=True),
    ),
    key_columns=("source_member_id",),
)

#: The feed's REAL, PUBLISHED mapping — the one `classify` reads to decide a
#: column is unmapped, and the one `propose_mapping_for_unmapped_columns`
#: passes on as precedent.
PUBLISHED_MAPPING = FeedMapping(
    feed_id=FEED,
    version=5,
    lines=(
        MappingLine(
            target_entity="members", target_field="source_member_id", source_columns=("MemberID",)
        ),
        MappingLine(target_entity="members", target_field="date_of_birth", source_columns=("DOB",)),
    ),
)


def _agent(store: MemMetadataDb) -> MappingSuggestionAgent:
    def _must_not_be_called(prompt: str, task_class: object) -> str:
        raise AssertionError(
            "the model must not be called — every unmapped column in this suite is settled "
            "by the glossary, which is the property this suite is checking"
        )

    gateway = LlmGateway(
        llm=ScriptedLlm(_must_not_be_called),
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        estimate_usd=Decimal("0.01"),
        clock=lambda: NOW,
    )
    return MappingSuggestionAgent(llm=gateway, metadata=store)


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


def _classified(arrived: tuple[str, ...]):  # type: ignore[no-untyped-def]
    findings = compare_to_contract(arrived, CONTRACT)
    return classify(findings, contract=CONTRACT, glossary=GLOSSARY, mapping=PUBLISHED_MAPPING)


def _unmapped_columns(assessment) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    return tuple(f.column for f in assessment.findings if f.kind is DriftKind.UNMAPPED_COLUMN)


# ── an UNMAPPED_COLUMN finding triggers exactly one, correctly-scoped proposal ──


def test_an_unmapped_column_triggers_one_proposal_scoped_to_just_that_column(
    store: MemMetadataDb,
) -> None:
    assessment = _classified(("MemberID", "DOB", "SUBSCR_REL_CD"))
    unmapped = _unmapped_columns(assessment)
    assert unmapped == ("SUBSCR_REL_CD",), (
        "the fixture must produce the finding, or this proves nothing"
    )

    proposal = propose_mapping_for_unmapped_columns(
        _agent(store),
        feed_id=FEED,
        unmapped_columns=unmapped,
        contract_version=CONTRACT.version,
        glossary=GLOSSARY,
        model=MODEL,
        published_mapping=PUBLISHED_MAPPING,
        run_id="B-1",
        now=NOW,
    )

    assert proposal is not None
    assert proposal.feed_id == FEED
    assert proposal.state is ProposalState.PENDING_REVIEW
    assert proposal.capability == MAPPING_SUGGESTION_CAPABILITY
    # SCOPED — the assertion this slab is for. The proposal's records cover
    # ONLY the unmapped column, never the contract's other, already-governed
    # columns (`source_member_id`, `date_of_birth`).
    assert {r["source_column"] for r in proposal.payload["records"]} == {"SUBSCR_REL_CD"}
    (record,) = proposal.payload["records"]
    assert record["settled_by"] == "glossary"
    assert record["target_entity"] == "members"
    assert record["target_field"] == "relationship_code"
    assert record["unmapped"] is False

    assert len(store.list_proposals(feed_id=FEED)) == 1, "nothing else was written"


def test_no_unmapped_columns_proposes_nothing(store: MemMetadataDb) -> None:
    assert (
        propose_mapping_for_unmapped_columns(
            _agent(store),
            feed_id=FEED,
            unmapped_columns=(),
            contract_version=CONTRACT.version,
            glossary=GLOSSARY,
            model=MODEL,
            published_mapping=PUBLISHED_MAPPING,
            run_id="B-1",
            now=NOW,
        )
        is None
    )
    assert store.list_proposals(feed_id=FEED) == ()


# ── idempotent per unmapped-column set ──────────────────────────────────────


def test_a_daily_redelivered_unmapped_column_earns_one_proposal_not_one_per_day(
    store: MemMetadataDb,
) -> None:
    agent = _agent(store)
    assessment = _classified(("MemberID", "DOB", "SUBSCR_REL_CD"))
    unmapped = _unmapped_columns(assessment)

    first = propose_mapping_for_unmapped_columns(
        agent,
        feed_id=FEED,
        unmapped_columns=unmapped,
        contract_version=CONTRACT.version,
        glossary=GLOSSARY,
        model=MODEL,
        published_mapping=PUBLISHED_MAPPING,
        run_id="B-1",
        now=NOW,
    )
    second = propose_mapping_for_unmapped_columns(
        agent,
        feed_id=FEED,
        unmapped_columns=unmapped,
        contract_version=CONTRACT.version,
        glossary=GLOSSARY,
        model=MODEL,
        published_mapping=PUBLISHED_MAPPING,
        run_id="B-2",
        now=NOW,
    )

    assert first is not None
    assert second is None, "re-running for the same finding must not double-propose"
    assert len(store.list_proposals(feed_id=FEED)) == 1


# ── never doubles up with the rename redirect ───────────────────────────────


def test_a_renamed_column_never_also_gets_an_unmapped_column_suggestion(
    store: MemMetadataDb,
) -> None:
    """`DOB` is renamed to `date_of_birth` (settled by BG-004) IN THE SAME
    ARRIVAL that also brings a genuinely new, ungoverned `SUBSCR_REL_CD`.
    `classify` excludes a settled rename's new spelling from `additions`
    before `UNMAPPED_COLUMN` is ever considered, so `date_of_birth` can never
    reach this trigger — it reaches `propose_mapping_redirect` instead. Both
    triggers fire off the SAME assessment, and this proves they partition it:
    two proposals, two feed_ids the same, two disjoint sets of columns."""
    assessment = _classified(("MemberID", "date_of_birth", "SUBSCR_REL_CD"))
    assert {r.was for r in assessment.renames} == {"DOB"}
    unmapped = _unmapped_columns(assessment)
    assert unmapped == ("SUBSCR_REL_CD",), "the renamed column must never surface here"

    redirect = propose_mapping_redirect(
        store,
        feed_id=FEED,
        mapping=PUBLISHED_MAPPING,
        renames=assessment.renames,
        run_id="B-1",
        now=NOW,
    )
    suggestion = propose_mapping_for_unmapped_columns(
        _agent(store),
        feed_id=FEED,
        unmapped_columns=unmapped,
        contract_version=CONTRACT.version,
        glossary=GLOSSARY,
        model=MODEL,
        published_mapping=PUBLISHED_MAPPING,
        run_id="B-1",
        now=NOW,
    )

    assert redirect is not None
    assert suggestion is not None
    assert redirect.capability == MAPPING_REDIRECT_CAPABILITY
    assert suggestion.capability == MAPPING_SUGGESTION_CAPABILITY

    redirected = {
        r["source_column"]
        for r in redirect.payload["records"]
        if r.get("settled_by") == "rename_redirect"
    }
    suggested = {r["source_column"] for r in suggestion.payload["records"]}
    assert redirected == {"date_of_birth"}
    assert suggested == {"SUBSCR_REL_CD"}
    assert redirected.isdisjoint(suggested), "the two triggers must never claim the same column"

    # Exactly two proposals exist for this feed — one per trigger, no third
    # and no duplicate of either.
    assert len(store.list_proposals(feed_id=FEED)) == 2
