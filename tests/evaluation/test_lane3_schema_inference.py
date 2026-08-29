"""LANE 3 — CF-V1-E5-02's acceptance gate. The ONLY place a quality claim is made.

    "eval red until >= 90% fields accepted without correction on feeds with
     existing human schemas"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

    "no evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)"
    — docs/architecture/INVARIANTS.md, testing

THE GOLDEN SET IS NOT WRITTEN FOR THE OCCASION. The answer key is
`core/registry/golden_fidelis.CONTRACT` — the human-authored schema the Wave-0
golden pipeline already proves byte-exact, written before this agent existed.
Grading against a key invented alongside the thing it grades measures nothing.

THE GRADE IS BLIND. The agent is given the profile and the glossary; it is NOT
given the contract. Its proposal is then compared field by field against what
the human wrote, and any difference is a correction — exactly as if a BA had
made it on the review screen.

Skips, visibly, until an endpoint is configured. A deliberate incompleteness
rather than a quietly-passing tick: CF-V1-E5-02's Definition of Done is not met
until this has run green.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.core.agents.schema_inference.prompts import TEMPLATES
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.proposals import approve, diff_fields, measure
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.registry.golden_fidelis import CONTRACT
from cinqflow.intelligence.agents.schema_inference import SchemaInferenceAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.simulator import PayerSimulator

pytestmark = [pytest.mark.evaluation, pytest.mark.lane3]

#: CF-V1-E5-02's gate. Stated once, here and in `api/app.SCHEMA_ACCEPTANCE_GATE`.
GATE = 0.90

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")

#: The glossary terms that cover the golden contract's columns, with the
#: spellings the client's own workbook records. These are the grounding a real
#: deployment has after `cinqflow seed-glossary`.
GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-001",
            term="Member Internal Identifier",
            definition="The source system's identifier for a member.",
            mapped_columns_original=("MemberID", "Member_Id"),
            mapped_columns_corrected=("Source_Member_Id",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-002",
            term="Member First Name",
            definition="Legal given name of the member.",
            mapped_columns_original=("First_Name", "first_name"),
            mapped_columns_corrected=("First_Name",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-003",
            term="Member Last Name",
            definition="Legal family name of the member.",
            mapped_columns_original=("Last_Name", "last_name"),
            mapped_columns_corrected=("Last_Name",),
            is_phi=True,
        ),
        # BG-004's REAL spellings, from the workbook. Note that bare `DOB` is
        # NOT among them — the client's analysts recorded five longer forms.
        # So the Fidelis roster's `DOB` column genuinely does not match by
        # synonym, and naming it is exactly the inference this agent exists to
        # make. Adding `DOB` here to help the agent pass would be grading it
        # against a key edited to fit.
        GlossaryTerm(
            glossary_id="BG-004",
            term="Member Date of Birth",
            definition="Date of birth of the member, used for age calculations, eligibility "
            "and quality measure stratification.",
            mapped_columns_original=(
                "Date_of_Birth",
                "Patient_dob",
                "patient_dob",
                "Patient_Date_of_birth",
                "MemberDateOfBirth",
            ),
            # The canonical name THIS FEED's human-written contract uses. The
            # workbook records `Member_Date_Of_Birth` first; the Wave-0 golden
            # contract calls it `date_of_birth`. That disagreement is real and
            # is recorded in the glossary rather than picked silently — and a
            # deployment's seeded glossary is the same person's vocabulary as
            # their contracts, so the eval uses the order they would.
            mapped_columns_corrected=("Date_Of_Birth", "Member_Date_Of_Birth"),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-050",
            term="Line of Business",
            definition="The product line a member is enrolled under.",
            mapped_columns_original=("LOB",),
            mapped_columns_corrected=("Line_Of_Business",),
            is_phi=False,
        ),
    )
)


def _published(obj: Any) -> Any:
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture
def agent(lane3_llm: Any) -> SchemaInferenceAgent:
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
        # The REAL scrub, not the pattern stand-in: "PHI scrub verified
        # pre-prompt" is one of this story's gates, and a mock scrub in the
        # lane that makes quality claims would verify nothing.
        phi_scrub=PresidioPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=budget_from(profile),
        routing=routing_from(profile, DotenvSecrets()),
        estimate_usd=Decimal("0.02"),
        clock=lambda: NOW,
    )
    return SchemaInferenceAgent(llm=gateway, metadata=store)


def _human_answer_key() -> dict[str, Any]:
    """The contract a person actually wrote, in the proposal's record shape."""
    return {
        "key": "source_name",
        "records": [
            {
                "source_name": column.reads_from,
                "name": column.name,
                "type": column.type.value,
                "nullable": column.nullable,
                "is_phi": column.is_phi,
            }
            for column in CONTRACT.columns
        ],
    }


def test_the_agent_accepts_at_least_ninety_percent_of_fields_without_correction(
    agent: SchemaInferenceAgent,
) -> None:
    """The gate. Blind re-derivation of a contract a human wrote first.

    Reported with the deterministic and inferred shares apart, so a pass built
    mostly on arithmetic cannot be read as a claim about the model.
    """
    delivery = PayerSimulator(rows=200).deliver(business_date=NOW.date())
    profile = profile_bytes(
        delivery.content,
        file_format="csv",
        source_key=delivery.key,
        source_fingerprint="sha256-golden",
    )

    result = agent.propose(profile, feed_id=CONTRACT.feed_id, glossary=GLOSSARY, caller=BA, now=NOW)

    # Grade over the columns the ANSWER KEY covers. The Wave-0 golden contract
    # deliberately contracts five of the roster's eight columns, and a column
    # the key says nothing about is UNMEASURABLE rather than free — grading it
    # either way would be marking against a key that does not have the answer.
    key = _human_answer_key()
    covered = {r["source_name"] for r in key["records"]}
    graded = {
        **result.proposal.payload,
        "records": [r for r in result.proposal.payload["records"] if r["source_name"] in covered],
    }
    assert len(graded["records"]) == len(covered), "the agent proposed every contracted column"

    # NULLABILITY IS NOT GRADED. The agent does not decide it — the key columns
    # the approver declares do (see `_contract_body`), so scoring it here would
    # mark the agent for somebody else's decision.
    corrections = diff_fields(graded, key, key="source_name", fields=("name", "type", "is_phi"))
    decided = approve(
        replace(result.proposal, payload=graded), approver=BA, corrections=corrections, now=NOW
    )
    acceptance = measure(decided, deterministic_keys=result.deterministic_keys)

    assert acceptance.passes(GATE), (
        acceptance.report(GATE)
        + "\n"
        + "\n".join(
            f"  {c.field_path}: proposed {c.proposed!r}, human wrote {c.accepted!r}"
            for c in corrections
        )
    )


def test_no_glossary_flagged_phi_field_is_ever_proposed_unflagged(
    agent: SchemaInferenceAgent,
) -> None:
    """A 100%-recall obligation on the fields the client has already flagged.

    Distinct from the acceptance gate and not averaged into it: one missed PHI
    flag is a disclosure, and a percentage would let it pass as rounding.
    """
    delivery = PayerSimulator(rows=100).deliver(business_date=NOW.date())
    profile = profile_bytes(delivery.content, file_format="csv", source_fingerprint="sha256-phi")
    result = agent.propose(profile, feed_id=CONTRACT.feed_id, glossary=GLOSSARY, caller=BA, now=NOW)

    flagged = {c.source_name for c in result.columns if c.is_phi}
    expected = {
        column.reads_from
        for column in CONTRACT.columns
        if column.is_phi and GLOSSARY.is_phi_column(column.reads_from)
    }
    assert expected <= flagged, f"unflagged: {sorted(expected - flagged)}"


def test_a_column_the_model_cannot_ground_is_declined_rather_than_typed(
    agent: SchemaInferenceAgent,
) -> None:
    """ "Ungroundable column -> 'needs your input', never silently typed."

    A column of opaque codes with no glossary term is the case. The model is
    allowed to be wrong about what it is; it is not allowed to be confident.
    """
    opaque = (
        b"MemberID,ZZ_QQ_9,LOB\n"
        b"MBR000001,7F3A,MEDICAID\n"
        b"MBR000002,B21C,MEDICARE\n"
        b"MBR000003,7F3A,DUAL\n"
    )
    profile = profile_bytes(opaque, file_format="csv", source_fingerprint="sha256-opaque")
    result = agent.propose(profile, feed_id="opaque-feed", glossary=GLOSSARY, caller=BA, now=NOW)
    column = next(c for c in result.columns if c.source_name == "ZZ_QQ_9")
    assert column.needs_input or column.confidence < 0.9, (
        f"an opaque code column was typed with high confidence: {column.rationale}"
    )


def test_the_call_is_metered_and_leaves_an_audit_row(agent: SchemaInferenceAgent) -> None:
    """ "100% of model calls carry prompt hash, model version, cost and caller
    identity in the audit log." """
    delivery = PayerSimulator(rows=50).deliver(business_date=NOW.date())
    profile = profile_bytes(delivery.content, file_format="csv", source_fingerprint="sha256-meter")
    result = agent.propose(profile, feed_id=CONTRACT.feed_id, glossary=GLOSSARY, caller=BA, now=NOW)

    actions = agent.metadata.read_agent_actions(run_id=result.proposal.run_id)  # type: ignore[attr-defined]
    assert actions, "a model call left no audit row"
    assert all(a.actor.subject == BA.subject for a in actions), "the caller is named, not the agent"
    assert result.proposal.prompt_hash
    assert result.cost_usd >= Decimal("0")
