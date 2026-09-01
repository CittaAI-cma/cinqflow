"""CF-V1-E6-02 wired — the agent, the gateway and the store.

LANE 1. Scripted model, no credentials. This suite proves MACHINERY: that the
grounding reaches the prompt, that a proposal is the only thing written, that
the platform and not the model decides what counts as a target, and that a
declined column lands as UNMAPPED with a reason a person can act on. It proves
NOTHING about quality.

    "No evaluation threshold may be claimed from Lane 1 (mock) or Lane 2
     (replay)."
    — docs/architecture/plates/13-three-lane-ai-testing.md

The blind re-derivation gate is in `tests/evaluation/`, on the real endpoint,
against mappings the client's own analysts wrote.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core.agents.mapping_suggestion.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.mapping import FeedMapping, LineStatus, MappingLine
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.llm import CompletionFailedError, TaskClass
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.proposals import ProposalState
from cinqflow.core.registry.canonical import build
from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.gateway import LlmGateway

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "uhc-optum-ny"
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN)

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="members",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                Column("date_of_birth", TypeName.DATE, is_phi=True),
                Column("line_of_business", TypeName.STRING),
                Column("relationship_code", TypeName.STRING),
                Column("record_hash", TypeName.STRING),
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
            mapped_columns_original=("DOB", "MBR_DOB"),
            mapped_columns_corrected=("date_of_birth",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-060",
            term="Subscriber Relationship",
            definition="How the member relates to the subscriber.",
            mapped_domains=("Enrollment",),
            mapped_tables=("members",),
            mapped_columns_corrected=("relationship_code",),
        ),
    )
)

MODEL = build((DEPLOYED,), GLOSSARY)

CONTRACT = SchemaContract(
    feed_id=FEED,
    version=1,
    columns=(
        ContractColumn("mbr_dob", TypeName.STRING, source_name="MBR_DOB", is_phi=True),
        ContractColumn("subscr_rel_cd", TypeName.STRING, source_name="SUBSCR_REL_CD"),
    ),
)


def _answer(**overrides: object) -> str:
    entry: dict[str, object] = {
        "source_column": "SUBSCR_REL_CD",
        "target_entity": "members",
        "target_field": "relationship_code",
        "glossary_id": "BG-060",
        "transform": "direct",
        "confidence": 0.92,
        "rationale": "The Fidelis feed maps the same column this way.",
        "like_feed_id": "fidelis-downstate-roster",
    }
    entry.update(overrides)
    return json.dumps({"mappings": [entry]})


def _agent(store: MemMetadataDb, llm: ScriptedLlm) -> MappingSuggestionAgent:
    gateway = LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        estimate_usd=Decimal("0.01"),
        clock=lambda: NOW,
    )
    return MappingSuggestionAgent(llm=gateway, metadata=store)


def _published(obj):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture
def store() -> MemMetadataDb:
    metadata = MemMetadataDb()
    for template in TEMPLATES:
        metadata.save(_published(template.as_governed(author=BA, now=NOW)))
    for term in GLOSSARY.terms:
        metadata.save(term.as_governed(author=BA, now=NOW))
    return metadata


def _propose(store: MemMetadataDb, llm: ScriptedLlm, **kwargs: object):  # type: ignore[no-untyped-def]
    return _agent(store, llm).propose(
        CONTRACT,
        feed_id=FEED,
        glossary=GLOSSARY,
        model=MODEL,
        caller=BA,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )


# ── the guardrail trio, for this agent ───────────────────────────────────────


def test_the_agent_writes_a_proposal_and_nothing_else(store: MemMetadataDb) -> None:
    """ "No agent path writes production state." Asserted on the STORE: after a
    run there is a proposal and no mapping."""
    result = _propose(store, ScriptedLlm(lambda p, t: _answer()))

    assert result.proposal.state is ProposalState.PENDING_REVIEW
    assert len(store.list_proposals(feed_id=FEED)) == 1
    assert list(store.list(ObjectType.MAPPING)) == [], "no mapping was created"


def test_the_proposal_is_r2_and_created_by_an_ai_actor(store: MemMetadataDb) -> None:
    result = _propose(store, ScriptedLlm(lambda p, t: _answer()))
    assert result.proposal.risk_class is RiskClass.R2
    assert result.proposal.created_by.actor_type is ActorType.AI


def test_no_payer_value_reaches_the_model(store: MemMetadataDb) -> None:
    """The mapping grounding is names, definitions, canonical fields and
    precedents. No sample values — not because a scrubber would catch them,
    but because a name-to-concept decision does not need them."""
    llm = ScriptedLlm(lambda p, t: _answer())
    _propose(store, llm)
    sent = "\n".join(prompt for prompt, _ in llm.calls)
    for shape in ("19360201", "123-45-6789", "MBR000001"):
        assert shape not in sent


# ── the glossary settles, and the model is not called ───────────────────────


def test_a_feed_the_glossary_names_entirely_calls_no_model(store: MemMetadataDb) -> None:
    """Not a cheap call: none. The deterministic-first rule on an invoice."""
    llm = ScriptedLlm(lambda p, t: _answer())
    settled = SchemaContract(
        feed_id=FEED,
        version=1,
        columns=(ContractColumn("mbr_dob", TypeName.STRING, source_name="MBR_DOB"),),
    )
    result = _agent(store, llm).propose(
        settled, feed_id=FEED, glossary=GLOSSARY, model=MODEL, caller=BA, now=NOW
    )

    assert result.model_called is False
    assert llm.calls == []
    assert result.cost_usd == Decimal("0")
    assert result.lines[0].settled_by == "glossary"


def test_the_glossary_line_lands_on_the_estates_own_spelling(store: MemMetadataDb) -> None:
    result = _propose(store, ScriptedLlm(lambda p, t: _answer()))
    dob = next(line for line in result.lines if line.source_column == "MBR_DOB")
    assert (dob.line.target_entity, dob.line.target_field) == ("members", "date_of_birth")
    assert dob.confidence == 1.0


# ── the platform decides what counts as a target ────────────────────────────


def test_a_target_the_canonical_model_does_not_have_is_discarded(store: MemMetadataDb) -> None:
    """A mapping to a field nobody has designed or deployed has nowhere to
    land — and it would sail through review looking exactly like a real one."""
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(
                target_entity="patient", target_field="dob", glossary_id="", confidence=0.99
            )
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")

    assert line.is_unmapped
    assert any("canonical model does not have" in refusal for refusal in result.refusals)


def test_a_source_column_the_contract_does_not_have_is_discarded(store: MemMetadataDb) -> None:
    result = _propose(store, ScriptedLlm(lambda p, t: _answer(source_column="INVENTED_COLUMN")))
    assert any("not a column of this feed's contract" in r for r in result.refusals)
    assert all(line.source_column != "INVENTED_COLUMN" for line in result.lines)


def test_the_platform_spells_the_name_the_model_cited(store: MemMetadataDb) -> None:
    """THE MODEL PICKS THE CONCEPT; THE PLATFORM SPELLS THE NAME. On
    CF-V1-E5-02's gate this one rule was the difference between 80% and 100%."""
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(glossary_id="BG-060", target_field="subscriber_relationship")
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")

    assert line.line.target_field == "relationship_code"
    assert any("the estate's vocabulary spells it" in r for r in result.refusals)


def test_low_confidence_becomes_unmapped_whatever_the_model_claimed(
    store: MemMetadataDb,
) -> None:
    """A threshold inside a prompt is one the model REPORTS; this one is
    enforced. Higher than schema inference's floor on purpose: a mis-typed
    column is caught by the next load, a mis-mapped one lands a copay in the
    deductible field and reconciles perfectly while doing it."""
    result = _propose(store, ScriptedLlm(lambda p, t: _answer(confidence=0.5)))
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")

    assert line.is_unmapped
    assert "below the platform's floor" in line.line.unmapped_reason
    assert "members.relationship_code" in line.line.unmapped_reason, (
        "the suggestion is preserved so a steward confirms rather than starts over"
    )


# ── UNMAPPED, never guessed — and it costs a sentence ───────────────────────


def test_a_declined_column_lands_as_unmapped_with_the_models_reason(
    store: MemMetadataDb,
) -> None:
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(
                unmapped=True,
                unmapped_reason="UnitedHealth's REL codes are a proprietary set with no "
                "published crosswalk.",
                confidence=0.3,
            )
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")

    assert line.line.status is LineStatus.UNMAPPED
    assert "proprietary set" in line.line.unmapped_reason


def test_an_unmapped_line_can_never_be_reasonless(store: MemMetadataDb) -> None:
    """ "Never guessed" is enforced by a TYPE. `MappingLine` refuses an unmapped
    field with a blank reason, so an agent that declines has to say why — even
    when the model itself said nothing."""
    result = _propose(
        store, ScriptedLlm(lambda p, t: _answer(unmapped=True, rationale="", unmapped_reason=""))
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert line.line.unmapped_reason.strip()


def test_a_column_the_model_ignored_entirely_becomes_unmapped(store: MemMetadataDb) -> None:
    """Silence is not consent. A column the model returned nothing for must not
    vanish from the proposal — it is precisely the one a steward needs to see."""
    result = _propose(store, ScriptedLlm(lambda p, t: json.dumps({"mappings": []})))
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")

    assert line.is_unmapped
    assert "No proposal was returned" in line.line.unmapped_reason


# ── the agent's own output is validated before anybody can approve it ───────


def test_a_phi_laundering_suggestion_is_refused_not_queued(store: MemMetadataDb) -> None:
    """THE ONE THAT MATTERS. `MBR_DOB` is flagged PHI on the contract;
    `line_of_business` is not flagged on the target. A suggestion carrying the
    first into the second moves the value out of the masking policy without
    breaking a rule anywhere — exactly the mistake a plausible-sounding mapping
    makes, and it must not reach a queue where somebody could approve it."""
    contract = SchemaContract(
        feed_id=FEED,
        version=1,
        columns=(ContractColumn("mbr_ssn", TypeName.STRING, source_name="MBR_SSN", is_phi=True),),
    )
    result = _agent(
        store,
        ScriptedLlm(
            lambda p, t: json.dumps(
                {
                    "mappings": [
                        {
                            "source_column": "MBR_SSN",
                            "target_entity": "members",
                            "target_field": "line_of_business",
                            "confidence": 0.95,
                            "rationale": "It looks like a code.",
                        }
                    ]
                }
            )
        ),
    ).propose(contract, feed_id=FEED, glossary=GLOSSARY, model=MODEL, caller=BA, now=NOW)

    line = result.lines[0]
    assert line.is_unmapped
    assert "nothing marks as PHI" in line.line.unmapped_reason
    assert any("Masking reads the TARGET" in refusal for refusal in result.refusals)
    assert [f.key for f in result.findings if f.blocks] == ["phi_laundering"]


def test_every_refusal_leaves_an_agent_action_row(store: MemMetadataDb) -> None:
    """A guardrail nobody can see fired is a comment."""
    _propose(store, ScriptedLlm(lambda p, t: _answer(source_column="INVENTED_COLUMN")))
    actions = store.read_agent_actions(agent="mapping-suggestion")
    assert actions, "a refusal must leave a row"
    assert any("not a column of this feed's contract" in a.detail for a in actions)


def test_the_proposals_confidence_is_the_weakest_line_not_the_mean(
    store: MemMetadataDb,
) -> None:
    """A mapping is approved as a whole, and averaging lets forty glossary
    lookups hide one column the agent barely placed."""
    result = _propose(store, ScriptedLlm(lambda p, t: _answer(confidence=0.8)))
    assert result.proposal.confidence == pytest.approx(0.8)


# ── precedent reaches the prompt ────────────────────────────────────────────


def test_another_feeds_approved_mapping_reaches_the_prompt_as_precedent(
    store: MemMetadataDb,
) -> None:
    """ "new payer's MBR_DOB maps like Fidelis date_of_birth did" — and the
    person who approved it travels too, because that is what makes it
    precedent rather than similarity."""
    llm = ScriptedLlm(lambda p, t: _answer())
    _propose(
        store,
        llm,
        published_mappings=(
            FeedMapping(
                feed_id="fidelis-downstate-roster",
                version=2,
                lines=(
                    MappingLine(
                        target_entity="members",
                        target_field="relationship_code",
                        source_columns=("SUBSCR_REL_CD",),
                    ),
                ),
            ),
        ),
        approvers={"fidelis-downstate-roster": "ola@cinqcare.test"},
    )
    sent = "\n".join(prompt for prompt, _ in llm.calls)

    assert "[SUBSCR_REL_CD] -> [members] [relationship_code]" in sent
    assert "fidelis-downstate-roster" in sent
    assert "approved" in sent, "a precedent must read as approved, not as somebody's draft"
    assert "ola@cinqcare.test" not in sent, (
        "the approver's identity belongs on the reviewer's screen, not in a model prompt"
    )


def test_the_target_vocabulary_reaches_the_prompt(store: MemMetadataDb) -> None:
    llm = ScriptedLlm(lambda p, t: _answer())
    _propose(store, llm)
    sent = "\n".join(prompt for prompt, _ in llm.calls)
    assert "[members] [relationship_code]" in sent
    assert "[members] [date_of_birth]" in sent


def test_the_proposal_previews_as_a_real_mapping(store: MemMetadataDb) -> None:
    """The payload is not a bespoke shape — it rebuilds into the same
    `FeedMapping` the manual editor saves, which is what makes accepting a
    proposal and typing it by hand the same act."""
    result = _propose(store, ScriptedLlm(lambda p, t: _answer()))
    mapping = result.mapping

    assert mapping.feed_id == FEED
    assert mapping.coverage == (2, 2)
    assert mapping.line("members", "date_of_birth") is not None


# ── through the routes: propose, correct, accept ────────────────────────────


def _app(store: MemMetadataDb, answer: str):  # type: ignore[no-untyped-def]
    from cinqflow.adapters.mock.authn import StaticAuthn
    from cinqflow.api.app import create_app

    return create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        mapping_suggestion_factory=lambda metadata: _agent(
            metadata, ScriptedLlm(lambda p, t: answer)
        ),
    )


def _seed_contract(store: MemMetadataDb) -> None:
    from cinqflow.core.registry.contract import contract_as_governed

    store.save(contract_as_governed(CONTRACT, author=BA))


def _headers(subject: str = "dev-ba@cinqcare.test") -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def test_the_route_refuses_a_feed_with_no_approved_contract(store: MemMetadataDb) -> None:
    """A mapping is proposed against the CONTRACT — the shape a human approved.
    Mapping from anything else would map columns nobody agreed exist."""
    from fastapi.testclient import TestClient

    with TestClient(_app(store, _answer())) as client:
        refused = client.post(f"/api/feeds/{FEED}/suggest-mapping", headers=_headers())

    assert refused.status_code == 404
    assert "no schema contract yet" in refused.text


def test_the_route_writes_a_proposal_and_no_mapping(store: MemMetadataDb) -> None:
    from fastapi.testclient import TestClient

    _seed_contract(store)
    with TestClient(_app(store, _answer())) as client:
        proposed = client.post(f"/api/feeds/{FEED}/suggest-mapping", headers=_headers())

    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["state"] == "pending_review"
    assert list(store.list(ObjectType.MAPPING)) == []


def test_accepting_a_mapping_proposal_creates_a_draft_mapping(store: MemMetadataDb) -> None:
    """Not a contract — a MAPPING. And a DRAFT, authored by the approver, so
    they cannot then approve it: the agent's output enters the world at exactly
    the door a hand-typed mapping does."""
    from fastapi.testclient import TestClient

    _seed_contract(store)
    with TestClient(_app(store, _answer())) as client:
        proposal_id = client.post(f"/api/feeds/{FEED}/suggest-mapping", headers=_headers()).json()[
            "proposal_id"
        ]
        accepted = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={"comment": "both lines match the glossary"},
            headers=_headers(),
        )

    assert accepted.status_code == 200, accepted.text
    stored = store.get(ObjectType.MAPPING, FEED)
    assert stored.lifecycle_state is LifecycleState.DRAFT
    assert stored.created_by.subject == "dev-ba@cinqcare.test"
    assert stored.created_by.actor_type is ActorType.HUMAN
    assert len(stored.body["lines"]) == 2


def test_a_reviewer_redirecting_a_column_is_recorded_as_a_correction(
    store: MemMetadataDb,
) -> None:
    """Corrections are the eval set. A reviewer moving a column to a different
    target is the single most informative thing this agent can produce."""
    from fastapi.testclient import TestClient

    _seed_contract(store)
    with TestClient(_app(store, _answer())) as client:
        proposal_id = client.post(f"/api/feeds/{FEED}/suggest-mapping", headers=_headers()).json()[
            "proposal_id"
        ]
        client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={
                "comment": "the REL code is not the relationship code here",
                "mappings": [
                    {
                        "source_column": "SUBSCR_REL_CD",
                        "unmapped": True,
                        "unmapped_reason": "UnitedHealth's REL set is proprietary; ask the "
                        "payer for the crosswalk before mapping it.",
                    }
                ],
            },
            headers=_headers(),
        )

    decided = store.get_proposal(proposal_id)
    assert [c.field_path for c in decided.corrections] == ["SUBSCR_REL_CD.unmapped"]
    assert decided.corrections[0].proposed is False
    assert decided.corrections[0].accepted is True
    assert decided.payload["records"][1]["unmapped"] is False, "the agent's own output is intact"


def test_unmapping_a_column_without_a_reason_is_refused_on_the_approve_path(
    store: MemMetadataDb,
) -> None:
    """The same type that refuses it in the manual editor refuses it here. One
    rule, one place — a reviewer cannot leave a gap nobody can act on by
    coming through the proposal door instead of the editor door."""
    from fastapi.testclient import TestClient

    _seed_contract(store)
    with TestClient(_app(store, _answer())) as client:
        proposal_id = client.post(f"/api/feeds/{FEED}/suggest-mapping", headers=_headers()).json()[
            "proposal_id"
        ]
        refused = client.post(
            f"/api/proposals/{proposal_id}/approve",
            json={
                "comment": "not sure about this one",
                "mappings": [{"source_column": "SUBSCR_REL_CD", "unmapped": True}],
            },
            headers=_headers(),
        )

    assert refused.status_code == 403
    assert "no source and no reason" in refused.text
    assert list(store.list(ObjectType.MAPPING)) == [], "nothing was written"


# ── batching, and the difference between declining and failing ──────────────


def test_a_wide_feed_is_asked_in_batches(store: MemMetadataDb) -> None:
    """FAILURE ISOLATION, not tuning. One call for the client's ninety-column
    claims extract failed twice on the real endpoint — an empty completion,
    then a timeout — and both times the whole run produced nothing."""
    from cinqflow.core.agents.mapping_suggestion.graph import BATCH_SIZE

    wide = SchemaContract(
        feed_id=FEED,
        version=1,
        columns=tuple(
            ContractColumn(f"c{i}", TypeName.STRING, source_name=f"COL_{i}")
            for i in range(BATCH_SIZE * 2 + 3)
        ),
    )
    llm = ScriptedLlm(lambda p, t: json.dumps({"mappings": []}))
    _agent(store, llm).propose(
        wide, feed_id=FEED, glossary=GLOSSARY, model=MODEL, caller=BA, now=NOW
    )

    assert len(llm.calls) == 3, "53 columns at 25 per batch is three calls, not one"
    for prompt, _ in llm.calls:
        assert "[members] [relationship_code]" in prompt, (
            "every batch keeps the whole target vocabulary — a batch shown a third of the "
            "model would decline perfectly mappable columns"
        )


def test_a_batch_the_model_cannot_answer_does_not_cost_the_run(store: MemMetadataDb) -> None:
    """A partial answer is worth more than a clean nothing. The batch that
    failed becomes UNMAPPED; the others still produce suggestions."""
    from cinqflow.core.agents.mapping_suggestion.graph import BATCH_SIZE

    wide = SchemaContract(
        feed_id=FEED,
        version=1,
        columns=(
            *[
                ContractColumn(f"c{i}", TypeName.STRING, source_name=f"COL_{i}")
                for i in range(BATCH_SIZE)
            ],
            ContractColumn("subscr_rel_cd", TypeName.STRING, source_name="SUBSCR_REL_CD"),
        ),
    )

    calls: list[int] = []

    def scripted(prompt: str, task: object) -> str:
        calls.append(1)
        # The first batch is unanswerable; the second answers properly.
        return "not json at all" if len(calls) <= 2 else _answer()

    result = _agent(store, ScriptedLlm(scripted)).propose(
        wide, feed_id=FEED, glossary=GLOSSARY, model=MODEL, caller=BA, now=NOW
    )

    assert result.manual_path is True
    placed = [line for line in result.lines if not line.is_unmapped]
    assert [line.source_column for line in placed] == ["SUBSCR_REL_CD"], (
        "the batch that answered still produced its suggestion"
    )


def test_a_failed_run_never_reads_as_a_careful_one(store: MemMetadataDb) -> None:
    """THE DEFECT THE LANE-3 GATE FOUND. With no flag, the eval reported
    "0 of 90, all declined and explained, cost $0" for a run that had spent
    real money on two calls and got nothing back — a sentence that reads like
    an honest agent being careful."""
    _seed_contract(store)
    result = _propose(store, ScriptedLlm(lambda p, t: "{ this is not json"))

    assert result.manual_path is True
    assert any("could not be reached" in refusal for refusal in result.refusals)
    declined = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert "FAILED RUN" in declined.line.unmapped_reason
    assert "for you to decide" not in declined.line.unmapped_reason


def test_a_healthy_run_reports_no_manual_path(store: MemMetadataDb) -> None:
    result = _propose(store, ScriptedLlm(lambda p, t: _answer()))
    assert result.manual_path is False


class _RaisingLlm:
    """A fake `llm` port whose `.complete()` always raises the given
    exception — the shape the gateway is handed once Part 1 of W2-37's fix
    has translated a real vendor failure at the adapter boundary."""

    def __init__(self, to_raise: Exception) -> None:
        self._to_raise = to_raise
        self.calls: list[tuple[str, TaskClass]] = []

    def complete(
        self,
        *,
        prompt,
        task_class,
        response_schema=None,
        max_tokens=2048,
        temperature=0.0,
    ):
        self.calls.append((prompt, task_class))
        raise self._to_raise

    def embed(self, texts):
        raise NotImplementedError

    def declared_endpoints(self):
        return frozenset({"mock://scripted"})


def test_a_transport_failure_also_reads_as_a_failed_run_not_a_crash(
    store: MemMetadataDb,
) -> None:
    """W2-37 — the identical defect `test_a_failed_run_never_reads_as_a_careful_one`
    guards for a schema failure, for the OTHER way `_suggest`'s own call can
    fail: the call is made and fails in flight. This module is untouched by
    the fix — `_suggest` already catches `ManualPathRequiredError` per batch;
    the gateway now hands it that exception instead of letting a raw
    transport failure propagate past it."""
    _seed_contract(store)
    result = _propose(store, _RaisingLlm(CompletionFailedError("simulated network timeout")))

    assert result.manual_path is True
    assert any("could not be reached" in refusal for refusal in result.refusals)
    declined = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert "FAILED RUN" in declined.line.unmapped_reason


def test_a_budget_exhaustion_also_reads_as_a_failed_run_not_a_crash(
    store: MemMetadataDb,
) -> None:
    """Same defect, the DESIGNED-FOR trigger: a per-run budget exhausted on
    the very first call must read as a failed run exactly like a transport
    failure or a schema failure, not propagate as a bare
    `BudgetExhaustedError` past every `except ManualPathRequiredError`."""
    _seed_contract(store)
    llm = _RaisingLlm(AssertionError("must not be called once the budget refuses"))
    gateway = LlmGateway(
        llm=llm,
        phi_scrub=PatternPhiScrub(),
        metadata_db=store,
        observability=NoopObservability(),
        # Deliberately below the gateway's own default `estimate_usd`
        # (0.01), so the very FIRST call is refused before it is made.
        budget=Budget(per_run_usd=Decimal("0.001"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        clock=lambda: NOW,
    )
    agent = MappingSuggestionAgent(llm=gateway, metadata=store)

    result = agent.propose(
        CONTRACT, feed_id=FEED, glossary=GLOSSARY, model=MODEL, caller=BA, now=NOW
    )

    assert result.manual_path is True
    assert llm.calls == []
    declined = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert "FAILED RUN" in declined.line.unmapped_reason


# ── the number is the answer ─────────────────────────────────────────────────
#
# `target_ref` is the PRIMARY field of `SUGGEST_SCHEMA` and the reason this
# agent survives its own scrubber, and until now no test in any lane below
# Lane 3 sent one. Every scripted answer here carried a resolvable
# `glossary_id`, which settles at branch 1 of `_resolve_target` and returns
# before the number is ever read — so the branch the real model actually takes
# was exercised only against the real endpoint, in a suite that is deselected
# by default.
#
# The numbering in this fixture, for anyone reading the refs below:
#
#   1 = [members] [date_of_birth]     4 = [members] [record_hash]
#   2 = [members] [line_of_business]  5 = [members] [relationship_code]
#   3 = [members] [member_row_id]


def test_the_target_list_reaches_the_prompt_numbered(store: MemMetadataDb) -> None:
    """A model told to answer with a number must be shown the numbers."""
    llm = ScriptedLlm(lambda p, t: _answer())
    _propose(store, llm)
    sent = "\n".join(prompt for prompt, _ in llm.calls)
    assert "5 = " in sent and "relationship_code" in sent


def test_the_target_is_resolved_from_the_number_not_the_name(store: MemMetadataDb) -> None:
    """The number wins over the names beside it. Same rule as the glossary
    branch — the model picks, the platform spells — one source down."""
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(
                glossary_id="", target_ref=2, target_entity="whatever", target_field="whatever"
            )
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert (line.line.target_entity, line.line.target_field) == ("members", "line_of_business")


def test_a_redacted_target_name_still_resolves_through_its_number(store: MemMetadataDb) -> None:
    """THE REGRESSION TEST FOR THE TWENTY-FOUR MINUTES CF-V1-E6-02 PAID.

    Presidio reads `date_of_birth` beside its own definition as `<PERSON>` and
    `claim_header.source_claim_id` as a hostname. With names as the answer the
    model copies back the redaction, the platform matches nothing, and every
    column on the feed is refused as "not in the canonical model" — two correct
    components and an agent that proposes nothing.

    Here the model answers the way a scrubbed one does: the right number, and a
    name that came back mangled. The platform must still land the target.
    """
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(
                glossary_id="", target_ref=1, target_entity="<PERSON>", target_field="<PERSON>"
            )
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert (line.line.target_entity, line.line.target_field) == ("members", "date_of_birth")


def test_a_target_number_outside_the_list_is_refused_rather_than_guessed(
    store: MemMetadataDb,
) -> None:
    """A number the list does not have is not a near miss to round toward."""
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(glossary_id="", target_ref=99, target_entity="", target_field="")
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert line.line.status is LineStatus.UNMAPPED
    assert any("target #99" in refusal for refusal in result.refusals)


def test_a_number_beats_the_names_when_the_two_disagree(store: MemMetadataDb) -> None:
    """Both resolvable, and pointing at different fields. The number is the
    one the prompt asked for, so the number is the one that counts."""
    result = _propose(
        store,
        ScriptedLlm(
            lambda p, t: _answer(
                glossary_id="", target_ref=5, target_entity="members", target_field="record_hash"
            )
        ),
    )
    line = next(line for line in result.lines if line.source_column == "SUBSCR_REL_CD")
    assert line.line.target_field == "relationship_code"
