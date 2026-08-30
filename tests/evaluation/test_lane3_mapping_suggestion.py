"""LANE 3 — CF-V1-E6-02's gate. The ONLY place a quality claim is made.

    "AI source→target mapping with confidence + exemplars from the golden
     workbooks; UNMAPPED flagged, never guessed"
    "benchmarked by blind re-derivation of live sources"
    — CF-V1-E6-02

    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)"
    — docs/architecture/INVARIANTS.md, testing

THE ANSWER KEY IS THE CLIENT'S OWN WORKBOOK. `Fidelis_Claims_Silver_Raw_Mapping`
carries 188 MAPPED rows — 102 distinct decisions — each one a pairing an
analyst made and recorded, months before this agent existed. Nothing here was
written for the occasion.

THE GRADE IS BLIND, AND MAKING IT BLIND IS AN ACTIVE CHOICE. The agent's
strongest grounding is prior approved mappings, so an eval that left the feed's
own mapping in the pool would be measuring the platform reading its answer key.
`published_mappings` is passed EMPTY, and a test asserts that leaving it in
would have produced a perfect score — which is what makes the exclusion a
control rather than an oversight.

TWO NUMBERS, AND THE GATE IS ON THE HARDER ONE. `distinct_pairs` is 188 and
`distinct_decisions` is 102: 75 of the pairs are `diagnosis_1..11` and
`poa_1..11`, eleven copies of two decisions. An agent that gets `diagnosis_1`
right gets all eleven free, so the gate is set on decisions.

THE UNIT IS A SOURCE COLUMN, AND SOME HAVE TWO RIGHT ANSWERS. Those 102
decisions cover 90 columns: `claim_id` legitimately populates both
`claim_header.source_claim_id` and `claim_line.source_claim_id`. This agent
proposes one target per column on purpose, so either answer is correct — and
the fan-out is CF-V1-E6-03's editor's job, which the grade must not pretend
otherwise about.

Skips, visibly, until an endpoint is configured and the client corpus is on the
machine. A deliberate incompleteness rather than a quietly-passing tick.
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
from cinqflow.core.agents.mapping_suggestion.prompts import TEMPLATES
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.canonical import CanonicalEntity, CanonicalField, CanonicalModel
from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.registry.glossary import Glossary
from cinqflow.core.schema_spec import TypeName
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.gateway import LlmGateway

pytestmark = [pytest.mark.evaluation, pytest.mark.lane3]

#: A REGRESSION FLOOR, NOT A QUALITY TARGET, and the distinction is the point.
#:
#: MEASURED, THEN SET — which is the thing one is not supposed to do, so it is
#: worth being exact about what this number is and is not. The first honest
#: measurement of this configuration was 50 of 90 (55.6%) with ZERO columns
#: mapped wrongly. This gate is set below that, and its whole job is to catch a
#: regression: two separate defects today took this same measurement to 0%, and
#: both looked like a careful agent from the outside.
#:
#: It is NOT a claim that the platform maps half a feed. This eval runs the
#: hardest configuration that exists — an EMPTY glossary, no precedents, no
#: sample values, against a nine-entity target model the agent has never seen.
#: A real deployment has the client's 171 terms and a growing pool of approved
#: mappings, and both settle columns DETERMINISTICALLY before a model is asked.
#: The number here is what is left when every one of those advantages is taken
#: away.
#:
#: The story's actual quality bar is the ceiling below, not this floor.
GATE = 0.50

#: THE NUMBER THAT MATTERS. What the platform must never do, at any accuracy:
#: map a column confidently and wrongly rather than decline it.
#:
#: Measured separately from the gate because an agent can reach any accuracy by
#: mapping everything and being right that often — and that agent is WORSE than
#: one that maps less and declines the rest. A declined column costs a BA five
#: minutes; a confidently wrong one loads real values into the wrong field and
#: reconciles perfectly while doing it.
#:
#: The first honest run scored 0 of 90 here. That is the result worth keeping.
MAX_CONFIDENT_AND_WRONG = 0.15

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
FEED = "fidelis-claims"

WORKBOOK = (
    Path(__file__).resolve().parents[3]
    / "clientdata"
    / "Uploads"
    / "Claims Mapping"
    / "Fidelis_Claims_Silver_Raw_Mapping (1).xlsx"
)
SHEET = "Fidelis to Silver Raw"


def _published(obj: Any) -> Any:
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture(scope="module")
def golden():  # type: ignore[no-untyped-def]
    from cinqflow.adapters.local.workbook_mappings import distinct_decisions, load_mappings

    if not WORKBOOK.exists():
        pytest.skip(f"the client corpus is not on this machine ({WORKBOOK.name} absent)")
    return distinct_decisions(load_mappings(WORKBOOK, SHEET))


@pytest.fixture(scope="module")
def model(golden):  # type: ignore[no-untyped-def]
    """The canonical target, built from the workbook's own Silver-Raw side.

    From the same artefact the answer key came from, deliberately. The client's
    claims model is designed and not yet in `core.schema_spec` — using the
    deployed spec would offer the agent a target list that does not contain the
    right answers, which grades the estate's roadmap rather than the agent.
    """
    fields: dict[str, dict[str, CanonicalField]] = {}
    for row in golden:
        entity = row.target_entity.lower()
        fields.setdefault(entity, {})[row.target_field.lower()] = CanonicalField(
            name=row.target_field,
            entity=entity,
            definition=row.description,
            deployed=True,
            type=TypeName.STRING,
        )
    return CanonicalModel(
        entities=tuple(
            CanonicalEntity(
                name=name,
                domains=("Claims",),
                fields=tuple(sorted(columns.values(), key=lambda f: f.name)),
                schema="silver_raw",
                deployed=True,
            )
            for name, columns in sorted(fields.items())
        )
    )


@pytest.fixture(scope="module")
def contract(golden):  # type: ignore[no-untyped-def]
    """The feed's contract — one column per source field the workbook names.

    Types are all STRING: this eval measures WHERE a column goes, and typing is
    CF-V1-E5-02's exam. Giving the agent real types would grade two stories at
    once and neither of them clearly.
    """
    seen: dict[str, None] = {}
    for row in golden:
        seen.setdefault(row.source_field, None)
    return SchemaContract(
        feed_id=FEED,
        version=1,
        columns=tuple(
            ContractColumn(name.lower(), TypeName.STRING, source_name=name) for name in seen
        ),
    )


@pytest.fixture
def agent(lane3_llm: Any) -> MappingSuggestionAgent:
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
    return MappingSuggestionAgent(llm=gateway, metadata=store)


#: ONE run, shared by every assertion in this module.
#:
#: Memoised rather than a module-scoped fixture because `lane3_llm` — the one
#: door to a real endpoint — is function-scoped by design, and widening it here
#: would widen it for every eval.
#:
#: Not a convenience. Each test re-proposing meant five runs of four batches
#: against a real endpoint: twenty calls and twenty-four minutes to measure one
#: thing five times. Worse, the tests could DISAGREE — two runs of the same
#: model are not the same run, so "the gate passed but the decline-rate check
#: failed" would be unreadable.
_RUN: list[Any] = []


@pytest.fixture
def proposed(agent, contract, model):  # type: ignore[no-untyped-def]
    if not _RUN:
        _RUN.append(_propose(agent, contract, model))
    return _RUN[0]


def _propose(agent: MappingSuggestionAgent, contract, model, **kwargs):  # type: ignore[no-untyped-def]
    return agent.propose(
        contract,
        feed_id=FEED,
        # EMPTY. The client's 171-term glossary covers enrollment, not this
        # claims extract, and seeding it with the answers would not be a
        # glossary — it would be the answer key wearing one.
        glossary=Glossary(terms=()),
        model=model,
        caller=BA,
        now=NOW,
        **kwargs,
    )


def _answer_key(golden) -> dict[str, set[str]]:  # type: ignore[no-untyped-def]
    """Source column -> every target the analysts gave it.

    A SET, because a column can have more than one right answer. `claim_id`
    goes to `claim_header.source_claim_id` AND `claim_line.source_claim_id`;
    12 of the workbook's 102 decisions are a second target for a column
    already mapped. The agent proposes ONE target per column by design (see
    the graph's scope note), so proposing either is correct and proposing
    something else is not.
    """
    key: dict[str, set[str]] = {}
    for row in golden:
        key.setdefault(row.source_field.lower(), set()).add(row.address.lower())
    return key


def _score(result, golden) -> tuple[int, int, int, list[str]]:  # type: ignore[no-untyped-def]
    """(right, confidently wrong, declined, the misses) over source columns."""
    key = _answer_key(golden)
    right = wrong = declined = 0
    misses: list[str] = []
    for line in result.lines:
        expected = key.get(line.source_column.lower())
        if expected is None:
            continue
        if line.is_unmapped:
            declined += 1
            continue
        if line.line.address.lower() in expected:
            right += 1
            continue
        wrong += 1
        misses.append(
            f"  {line.source_column}: proposed {line.line.address} "
            f"(confidence {line.confidence:.2f}), the analyst wrote "
            f"{' or '.join(sorted(expected))}"
        )
    return right, wrong, declined, misses


# ── the gate ─────────────────────────────────────────────────────────────────


def test_the_agent_re_derives_the_analysts_mapping(proposed, golden) -> None:  # type: ignore[no-untyped-def]
    """THE GATE. Blind re-derivation of a mapping people wrote first.

    No glossary, no precedents, no sample values — only the payer's column
    names and the canonical model's fields and definitions. That is the
    hardest honest version of this task, and it is the version a new payer
    actually presents.
    """
    result = proposed

    # A BROKEN RUN IS NOT A CAREFUL ONE, and the first version of this gate
    # could not tell them apart: the model returned an empty completion twice,
    # the gateway escalated to the manual path, and the eval reported "0 of 90,
    # all declined and explained, cost $0" — a sentence that reads like an
    # honest agent being careful. Checked FIRST so the failure names itself.
    assert not result.manual_path, (
        "the gateway escalated to the manual path — the model could not answer in the "
        "required shape, so every column came back unmapped. That is a broken run, and "
        "grading it would report a careful agent."
    )

    right, wrong, declined, misses = _score(result, golden)
    total = right + wrong + declined

    assert total == len(_answer_key(golden)), (
        "every graded source column must appear in the proposal"
    )
    rate = right / total if total else 0.0
    report = (
        f"{right}/{total} decisions re-derived ({rate:.1%}, gate {GATE:.0%}) — "
        f"{wrong} confidently wrong, {declined} declined and explained. "
        f"Cost ${result.cost_usd}."
    )
    assert rate >= GATE, report + "\n" + "\n".join(misses)


def test_the_agent_declines_rather_than_guesses_wrong(proposed, golden) -> None:  # type: ignore[no-untyped-def]
    """THE MORE IMPORTANT NUMBER, and it is not the gate.

    An agent can reach 70% by mapping every column and being right 70% of the
    time. That agent is WORSE than one that maps 60% and declines the rest: a
    declined column costs a BA five minutes, and a confidently wrong one loads
    real values into the wrong field and reconciles perfectly while doing it.
    """
    result = proposed
    assert not result.manual_path, "a degraded run declines everything and proves nothing"
    right, wrong, declined, misses = _score(result, golden)
    total = right + wrong + declined
    ratio = wrong / total if total else 1.0

    assert ratio <= MAX_CONFIDENT_AND_WRONG, (
        f"{wrong}/{total} columns were mapped confidently and wrongly "
        f"({ratio:.1%}, ceiling {MAX_CONFIDENT_AND_WRONG:.0%}); {declined} were declined.\n"
        + "\n".join(misses)
    )


def test_every_declined_column_says_why(proposed) -> None:  # type: ignore[no-untyped-def]
    """ "UNMAPPED flagged, never guessed" — and a flag with no reason is a gap
    nobody can act on. Enforced by `MappingLine` itself, measured here against
    a real model in case a future refactor loosens the type."""
    result = proposed
    assert not result.manual_path
    for line in result.unmapped:
        assert line.line.unmapped_reason.strip(), f"{line.source_column} declined with no reason"


def test_no_proposed_target_is_outside_the_canonical_model(proposed, model) -> None:  # type: ignore[no-untyped-def]
    """The platform, not the model, decides what counts as a target. A field
    the canonical model does not have would sail through review looking exactly
    like a real one."""
    result = proposed
    for line in result.lines:
        if not line.line.is_mapped:
            continue
        entity = model.entity(line.line.target_entity)
        assert entity is not None, f"{line.line.address} names an entity that does not exist"
        assert entity.field(line.line.target_field) is not None, (
            f"{line.line.address} names a field that does not exist"
        )


# ── the blindness of the grade is itself a control ──────────────────────────


def test_the_feeds_own_mapping_would_have_answered_everything(contract, model, golden) -> None:  # type: ignore[no-untyped-def]
    """WHY `published_mappings` IS PASSED EMPTY, proved rather than asserted.

    Grounding uses the feed's own published mapping to SETTLE a column with no
    model call. Left in the pool for this eval, it would settle every column
    correctly and the gate would report 100% — a measurement of the platform
    reading its own answer key.

    No model is needed to demonstrate that, which is why this test carries no
    `agent` fixture: the grounding alone shows it.
    """
    from cinqflow.core.agents.mapping_suggestion import ground

    answer_key = FeedMapping(
        feed_id=FEED,
        lines=tuple(
            MappingLine(
                target_entity=row.target_entity,
                target_field=row.target_field,
                source_columns=(row.source_field,),
            )
            for row in golden
        ),
    )
    grounding = ground(
        contract,
        feed_id=FEED,
        glossary=Glossary(terms=()),
        model=model,
        published_mappings=(answer_key,),
    )

    assert grounding.needs_no_model, (
        "with its own mapping in the pool the agent asks nothing — which is correct "
        "behaviour and a worthless benchmark"
    )
    assert all(c.settled_by == "published_mapping" for c in grounding.settled)
