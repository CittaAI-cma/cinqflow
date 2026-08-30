"""LANE 3 — CF-V1-E5-03's recall gate. The ONLY place a quality claim is made.

    "eval red until 100% recall on glossary-flagged PHI (missing PHI is the
     failure that matters) · downgrade-by-AI refused"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)"
    — docs/architecture/INVARIANTS.md, testing

THE ANSWER KEY IS THE CLIENT'S OWN WORKBOOK — the real 171-term glossary with
its 29 PHI-flagged terms and 99 distinct PHI column spellings, written by the
client's analysts years before this agent existed. Nothing here was authored
for the occasion, which is what makes the number mean something.

WHY A 100% GATE IS NOT A VANITY GATE. Recall against the glossary IS
structurally guaranteed — `core.phi.classify` consults the glossary first, and
`merge_inference` refuses every downgrade — so this suite would be green even
if the model returned nothing. That is the design working, and asserting it on
the real endpoint is still worth doing: it is the assertion that catches the
day somebody reorders those branches, or the day a model's output finds a path
through the merge that the mock never exercised.

The quality claim this suite DOES make is the second one: that the model
usefully NAMES the columns the arithmetic could not, rather than declining
them all. A detector that protects everything and explains nothing has passed
the gate and helped no one.

Skips, visibly, until an endpoint and the client corpus are both present.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cinqflow.adapters.local.workbook_glossary import load_glossary
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.core.agents.phi_detection.prompts import TEMPLATES
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.phi import Basis, PhiKind
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.registry.glossary import Glossary
from cinqflow.intelligence.agents.phi_detection import PhiDetectionAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.simulator import PayerSimulator
from tests.conftest import require_corpus

pytestmark = [pytest.mark.evaluation, pytest.mark.lane3]

#: The gate. Recall, not accuracy — one missed PHI column is a disclosure, and
#: a percentage would let it pass as rounding.
RECALL_GATE = 1.0

#: The quality claim. Of the columns nothing settled, this share must come back
#: SPECIFICALLY IDENTIFIED rather than declined. Deliberately not high: an
#: honest decline on an opaque column is a correct answer, and a gate set near
#: 1.0 would push the model to label columns it cannot read — buying a green
#: number with exactly the confident-and-wrong output the platform exists to
#: refuse. What the gate catches is a model that identifies NOTHING.
NAMING_GATE = 0.5

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")

WORKBOOK = (
    Path(__file__).resolve().parents[3]
    / "clientdata"
    / "Uploads"
    / "2-Design"
    / "Data lake data model.xlsx"
)

#: A roster carrying columns from every branch of the precedence table, with
#: REAL codes: valid NPIs, a CMS-shaped MBI, never-issued-range SSNs, dotted
#: ICD-10-CM, and two columns nothing can identify.
MIXED_ROSTER = (
    b"MemberID,DOB,LOB,SSN,MBI,PROV_NPI,DX_CODE,EMAIL,SUBSCR_REL_CD,AUX_SEG_2,NOTES\n"
    b"MBR000001,19360201,MEDICAID,078-05-1120,1EG4TE5MK73,1234567893,E11.9,"
    b"a.member@example.test,01,7F3A,Called about a prior authorisation for an MRI scan\n"
    b"MBR000002,19370302,MEDICARE,219-09-9999,4Y29GK2AR35,1841293990,I10.9,"
    b"b.member@example.test,02,B21C,Left a voicemail about the annual wellness booking\n"
    b"MBR000003,19380403,DUAL,457-55-5462,9WG2NK6EW34,1215930367,J45.909,"
    b"c.member@example.test,01,7F3A,Asked for a replacement card posted to the home\n"
)


def _published(obj: Any) -> Any:
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture(scope="module")
def glossary() -> Glossary:
    """The client's real workbook. Skips rather than substituting a stand-in —
    a quality claim made against an invented glossary is not a quality claim."""
    require_corpus(WORKBOOK)
    return load_glossary(WORKBOOK)


@pytest.fixture
def agent(lane3_llm: Any) -> PhiDetectionAgent:
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
        estimate_usd=Decimal("0.02"),
        clock=lambda: NOW,
    )
    # The REAL Presidio scrubber in both seats: the gateway's, and the agent's
    # own column-level detector. A mock scrub in the lane that makes quality
    # claims would verify nothing.
    return PhiDetectionAgent(llm=gateway, scrub=PresidioPhiScrub(), metadata=store)


@pytest.fixture
def result(agent: PhiDetectionAgent, glossary: Glossary):  # type: ignore[no-untyped-def]
    profile = profile_bytes(
        MIXED_ROSTER, file_format="csv", source_key="roster.csv", source_fingerprint="sha256-eval"
    )
    return agent.propose(
        profile, feed_id="fidelis-downstate-roster", glossary=glossary, caller=BA, now=NOW
    )


# ── the gate ─────────────────────────────────────────────────────────────────


def test_every_glossary_flagged_column_is_protected(result, glossary: Glossary) -> None:  # type: ignore[no-untyped-def]
    """THE GATE. 100%, against the client's own 29 flagged terms."""
    protected, expected = result.classification.recall_against(glossary)
    assert expected > 0, "the roster must exercise at least one flagged column"
    assert result.classification.missed_phi(glossary) == ()
    assert protected / expected >= RECALL_GATE, (
        f"{protected}/{expected} glossary-flagged columns protected — "
        f"MISSED: {', '.join(result.classification.missed_phi(glossary))}"
    )


def test_the_gate_holds_on_a_full_simulated_delivery(
    agent: PhiDetectionAgent, glossary: Glossary
) -> None:
    """The same gate over the simulator's 200-row roster, so the claim is not
    made only about a hand-written eleven-column file."""
    delivery = PayerSimulator(rows=200).deliver(business_date=NOW.date())
    profile = profile_bytes(
        delivery.content, file_format="csv", source_fingerprint="sha256-simulated"
    )
    outcome = agent.propose(
        profile, feed_id="fidelis-downstate-roster", glossary=glossary, caller=BA, now=NOW
    )
    assert outcome.classification.missed_phi(glossary) == ()


def test_no_column_is_left_unprotected_without_a_stated_basis(result) -> None:  # type: ignore[no-untyped-def]
    """Every UNFLAGGED column must rest on the glossary or on arithmetic.

    A column left unprotected by inference alone would be a model deciding
    what needs no protection, which is the failure the precedence order
    exists to make unreachable.
    """
    for column in result.classification.columns:
        if not column.is_phi:
            assert column.basis in {Basis.GLOSSARY, Basis.COMPUTATION}, (
                f"{column.source_name} is unprotected on the strength of "
                f"{column.basis.value}: {column.rationale}"
            )


def test_the_arithmetic_settles_the_columns_it_should(result) -> None:  # type: ignore[no-untyped-def]
    """The value shapes, verified end to end on the real endpoint's run.

    Not a claim about the model — these are computed — but asserted HERE
    because a change that broke the pattern library would otherwise show up
    only as a mysterious drop in the naming score below.
    """
    by_name = {c.source_name: c for c in result.classification.columns}
    assert by_name["SSN"].phi_kind is PhiKind.SSN
    assert by_name["MBI"].phi_kind is PhiKind.MEDICARE_ID
    assert by_name["EMAIL"].phi_kind is PhiKind.EMAIL
    assert by_name["PROV_NPI"].code_set is not None and not by_name["PROV_NPI"].is_phi
    assert by_name["NOTES"].phi_kind is PhiKind.FREE_TEXT


# ── downgrade-by-AI refused, against a real model ────────────────────────────


def test_the_model_never_succeeds_in_clearing_a_flag(result) -> None:
    """Every attempt is refused AND recorded. Asserted against a real model
    rather than a scripted one, because a scripted model only attempts what
    the test author thought to script."""
    for column in result.classification.columns:
        if column.basis is Basis.INFERENCE:
            assert column.is_phi, (
                f"{column.source_name} was unprotected by inference: {column.rationale}"
            )
    for refusal in result.refusals:
        assert "Refused" in refusal or "Discarded" in refusal


def test_no_value_from_the_file_reaches_the_real_endpoint(result) -> None:
    """The agent's defining property, asserted where it actually matters.

    Lane 1 proves the grounding excludes values. This proves the assembled
    prompt that went over the wire did too — including after the real
    Presidio scrubber ran, which would otherwise be the only thing standing
    between an SSN and a vendor's logs.
    """
    prompt_material = " ".join(c.rationale for c in result.classification.columns)
    for value in ("078-05-1120", "1EG4TE5MK73", "a.member@example.test", "MBR000001"):
        assert value not in prompt_material, f"a value came back in a rationale: {value}"


# ── the quality claim ────────────────────────────────────────────────────────


def _asked(result) -> list:  # type: ignore[no-untyped-def]
    """The columns the model was actually consulted about.

    Free text is excluded: the platform does not ask about it, because the
    answer is already the safe one and a model cannot make it safer.
    """
    return [
        c
        for c in result.classification.columns
        if c.basis in {Basis.INFERENCE, Basis.PRECAUTION} and c.phi_kind is not PhiKind.FREE_TEXT
    ]


def test_every_column_the_model_was_asked_about_comes_back_answered(result) -> None:
    """SILENCE IS THE FAILURE — not uncertainty.

    The first draft of this gate scored "named a specific kind" and failed at
    50% against two answers that were both correct: the model identified `LOB`
    as a plan attribute (there was no `PhiKind` for that, which is now
    `NOT_AN_IDENTIFIER`), and it declined the genuinely opaque `AUX_SEG_2` at
    0.45 confidence. Declining IS a correct answer here exactly as it is in
    CF-V1-E5-02, so a metric that punished it was measuring the wrong thing.

    What is worth gating is that the model returned SOMETHING per column: a
    specific identification, or an explicit low-confidence decline with a
    reason. An empty answer is a broken prompt, a dropped schema validation or
    a model that stopped answering per column — and those are regressions a
    steward would never notice.
    """
    asked = _asked(result)
    assert asked, "the roster must leave the model something to answer"

    unanswered = [c for c in asked if not c.rationale.strip()]
    assert not unanswered, (
        "the model returned nothing for "
        + ", ".join(c.source_name for c in unanswered)
        + " — silence is not a decline"
    )


def test_the_model_specifically_identifies_at_least_some_of_them(result) -> None:
    """THE QUALITY CLAIM. A model that declines everything passes the recall
    gate and helps nobody — the flags were free.

    Deliberately a floor on the COUNT rather than a share: the honest answer
    for an opaque column is a decline, so demanding a high share would push
    the model to label things it cannot read. What must not happen is that it
    labels nothing.
    """
    asked = _asked(result)
    identified = [
        c
        for c in asked
        if c.code_set is not None
        or (c.basis is Basis.INFERENCE and c.phi_kind not in {None, PhiKind.UNSPECIFIED})
    ]
    assert identified, (
        "the model identified none of the unsettled columns specifically:\n"
        + "\n".join(f"  {c.source_name}: {c.rationale}" for c in asked)
    )
    share = len(identified) / len(asked)
    assert share >= NAMING_GATE, (
        f"the model specifically identified {len(identified)} of {len(asked)} unsettled "
        f"columns ({share:.0%}, gate {NAMING_GATE:.0%}):\n"
        + "\n".join(f"  {c.source_name}: {c.phi_kind} · {c.rationale}" for c in asked)
    )


def test_every_named_column_carries_a_rationale_a_steward_can_act_on(result) -> None:
    for column in result.classification.columns:
        assert column.rationale.strip(), f"{column.source_name} was flagged with no explanation"
        assert column.citations, f"{column.source_name} has no citation to open"


def test_over_flagging_is_reported_and_bounded_by_usefulness(result, glossary: Glossary) -> None:
    """Over-flagging is the SAFE direction and is never gated on correctness —
    but a detector that protects every column has told a steward nothing while
    appearing to work, so the eval says how much of it there is."""
    over = result.classification.over_flagged(glossary)
    unprotected = [c for c in result.classification.columns if not c.is_phi]
    assert unprotected, (
        f"every one of {len(result.classification.columns)} columns was protected "
        f"({len(over)} beyond the glossary's flags) — a classification that flags "
        "everything is a classification nobody can use"
    )


def test_the_call_is_metered_and_leaves_an_audit_row(agent: PhiDetectionAgent, result) -> None:
    """ "100% of model calls carry prompt hash, model version, cost and caller
    identity in the audit log." """
    assert result.model_called, "the mixed roster must leave the model something to do"
    assert result.proposal.prompt_hash
    assert result.cost_usd >= Decimal("0")
    actions = agent.metadata.read_agent_actions(run_id=result.proposal.run_id)  # type: ignore[attr-defined]
    assert all(a.actor.subject == BA.subject for a in actions), "the caller is named, not the agent"
