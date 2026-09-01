"""CF-V1-E5-03's classification, on the REAL rung-0.5 plane.

The mock proves the semantics; this proves they survive the rows that store
them. Three things only Postgres can show:

  • the profile's new `pattern_matches` round-trip through `profiling.file_profile`
    as JSON and come back as the same integers, so a classification made from a
    STORED profile is the classification made from a computed one;
  • the classification and its masking policy survive `proposals.proposal`'s
    UPDATE, which deliberately concatenates only corrections onto `payload` —
    so a steward's downgrade cannot rewrite what the agent said it found;
  • the profiler version bump changes the profile's identity rather than
    silently overwriting the old row.

Every write rolls back (the `plane` fixture), so the suite leaves nothing
behind and needs no cleanup code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType, RiskClass
from cinqflow.core.phi import Basis, PhiKind, classify, masking_policy
from cinqflow.core.profiling import PROFILER_VERSION, profile_bytes
from cinqflow.core.proposals import Correction, Proposal, ProposalState, approve, submit
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.ports.metadata_db import FileProfileRecord

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
AGENT = Actor(subject="phi-detection", actor_type=ActorType.AI, display_name="PHI detection")
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ada")
FEED = "fidelis-downstate-roster"

ROSTER = (
    b"MemberID,DOB,LOB,SSN,PROV_NPI,SUBSCR_REL_CD\n"
    b"MBR000001,19360201,MEDICAID,078-05-1120,1234567893,01\n"
    b"MBR000002,19370302,MEDICARE,219-09-9999,1841293990,02\n"
    b"MBR000003,19380403,DUAL,457-55-5462,1215930367,01\n"
)

GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-004",
            term="Member Date of Birth",
            definition="Date of birth of the member.",
            mapped_columns_original=("DOB",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-050",
            term="Line of Business",
            definition="The product line a member is enrolled under.",
            mapped_columns_original=("LOB",),
            is_phi=False,
        ),
    )
)


def _profile():  # type: ignore[no-untyped-def]
    return profile_bytes(
        ROSTER, file_format="csv", source_key="roster.csv", source_fingerprint="sha256-real"
    )


def _stored(store: PostgresMetadataDb) -> FileProfileRecord:
    return store.record_profile(
        FileProfileRecord(
            feed_id=FEED, profile=_profile(), profiled_by=STEWARD.subject, profiled_ts=NOW
        )
    )


def test_the_computed_value_shapes_survive_the_row(plane: object) -> None:
    """The counts go out as JSON and come back as the same integers.

    Which is what makes the next test meaningful: a classification built from
    a stored profile has to be the same classification as one built from the
    bytes, or the review screen and the pipeline are reading different files.
    """
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    written = _stored(store)
    read_back = store.get_profile(written.profile_id, FEED)

    ssn = read_back.profile.column("SSN")
    npi = read_back.profile.column("PROV_NPI")
    assert ssn is not None and npi is not None
    assert {m.pattern_id: (m.matched, m.considered) for m in ssn.total_pattern_matches}["ssn"] == (
        3,
        3,
    )
    assert [m.pattern_id for m in npi.decisive_patterns] == ["npi"]
    assert read_back.profile == _profile(), "the whole profile round-trips, not just the shapes"


def test_a_classification_from_the_stored_profile_matches_the_computed_one(plane: object) -> None:
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    written = _stored(store)

    from_bytes = classify(_profile(), feed_id=FEED, glossary=GLOSSARY)
    from_row = classify(
        store.get_profile(written.profile_id, FEED).profile, feed_id=FEED, glossary=GLOSSARY
    )
    assert from_row == from_bytes

    by_name = {c.source_name: c for c in from_row.columns}
    assert by_name["SSN"].basis is Basis.COMPUTATION and by_name["SSN"].phi_kind is PhiKind.SSN
    assert by_name["DOB"].basis is Basis.GLOSSARY
    assert by_name["PROV_NPI"].is_phi is False
    assert from_row.missed_phi(GLOSSARY) == ()


def test_the_profiler_version_is_part_of_the_profiles_identity(plane: object) -> None:
    """1.1.0 added the value shapes, so the same bytes now have a different id.

    Asserted on the STORE rather than in a unit test: the property that matters
    is that the new evidence lands on a NEW row instead of quietly overwriting
    a profile computed by an older build. The older row is not wrong — it is
    evidence about less.
    """
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    current = _stored(store)
    assert current.profile.profiler_version == PROFILER_VERSION

    older = _profile().to_dict()
    older["profiler_version"] = "1.0.0"
    from cinqflow.core.profiling import FileProfile

    legacy = FileProfile.from_dict(older)
    store.record_profile(
        FileProfileRecord(
            feed_id=FEED, profile=legacy, profiled_by=STEWARD.subject, profiled_ts=NOW
        )
    )

    assert legacy.profile_id != current.profile_id
    assert len(store.list_profiles(feed_id=FEED)) == 2


def _classification_proposal(store: PostgresMetadataDb) -> Proposal:
    result = classify(_profile(), feed_id=FEED, glossary=GLOSSARY)
    policy = masking_policy(result)
    return store.record_proposal(
        submit(
            Proposal(
                proposal_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                agent="phi-detection",
                capability="propose_phi_classification",
                risk_class=RiskClass.R2,
                run_id="run-phi-1",
                feed_id=FEED,
                payload={
                    "key": "source_name",
                    "profile_id": result.profile_id,
                    "records": [
                        {
                            "source_name": c.source_name,
                            "position": c.position,
                            "is_phi": c.is_phi,
                            "basis": c.basis.value,
                            "phi_kind": c.phi_kind.value if c.phi_kind else None,
                            "code_set": c.code_set.value if c.code_set else None,
                            "needs_steward_review": c.needs_steward_review,
                            "rationale": c.rationale,
                        }
                        for c in result.columns
                    ],
                    "masked_columns": list(policy.masked_columns),
                    "refusals": [],
                },
                created_by=AGENT,
                created_ts=NOW,
                confidence=0.0,
                grounding_citations=result.columns[0].citations,
                prompt_hash="abc123",
            ),
            now=NOW,
        )
    )


def test_a_classification_round_trips_through_the_row(plane: object) -> None:
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    written = _classification_proposal(store)

    assert written.state is ProposalState.PENDING_REVIEW
    assert written.created_by.actor_type is ActorType.AI
    assert written.risk_class is RiskClass.R2

    stored = store.get_proposal(written.proposal_id)
    by_name = {r["source_name"]: r for r in stored.payload["records"]}
    assert by_name["SSN"]["basis"] == "computation"
    assert by_name["PROV_NPI"]["code_set"] == "npi"
    assert "SSN" in stored.payload["masked_columns"]
    assert "PROV_NPI" not in stored.payload["masked_columns"]


def test_a_stewards_downgrade_cannot_rewrite_what_the_agent_found(plane: object) -> None:
    """The correction is recorded; the finding is not edited.

    A decision able to rewrite the classification would erase the evidence it
    is evidence of — and "the agent flagged this and a steward cleared it" is
    exactly the pair an auditor needs to see. Same reason `record_transition`
    leaves `body` out of its UPDATE.
    """
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    written = _classification_proposal(store)
    original = dict(written.payload)

    decided = approve(
        store.get_proposal(written.proposal_id),
        approver=STEWARD,
        comment="relationship code, no member data — checked with the payer",
        corrections=(Correction("SUBSCR_REL_CD.is_phi", True, False),),
        now=NOW,
    )
    stored = store.record_proposal(decided)

    assert stored.payload == original, "the agent's own finding is intact"
    by_name = {r["source_name"]: r for r in stored.payload["records"]}
    assert by_name["SUBSCR_REL_CD"]["is_phi"] is True, "the finding still says it flagged it"
    assert [c.field_path for c in stored.corrections] == ["SUBSCR_REL_CD.is_phi"]
    assert stored.corrections[0].accepted is False
    assert stored.decided_by is not None and stored.decided_by.subject == STEWARD.subject


def test_the_review_queue_separates_the_two_agents(plane: object) -> None:
    """One table, one queue, two agents — and a steward filtering to PHI work
    gets only PHI work."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    _classification_proposal(store)
    store.record_proposal(
        submit(
            Proposal(
                proposal_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                agent="schema-inference",
                capability="propose_schema_contract",
                risk_class=RiskClass.R2,
                run_id="run-schema-1",
                feed_id=FEED,
                payload={"records": []},
                created_by=Actor(subject="schema-inference", actor_type=ActorType.AI),
                created_ts=NOW,
            ),
            now=NOW,
        )
    )

    assert len(store.list_proposals(feed_id=FEED)) == 2
    phi = store.list_proposals(agent="phi-detection")
    assert [p.proposal_id for p in phi] == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
    assert len(store.list_proposals(state=ProposalState.PENDING_REVIEW)) == 2
