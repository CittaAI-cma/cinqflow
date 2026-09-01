"""W1-32 (F2's remaining half) — the mapping-line redirect W2-27 never wrote.

    "the CONTRACT-read alias already updates via `reads_as` (W2-27,
     unchanged, working). But the PUBLISHED MAPPING's own MappingLine's
     source_columns still says the OLD name — nothing updates it."
    "a redirect is NOT a loss in the blast-radius gate — meaning a redirect
     should NOT be treated as harshly as an unmapped/lost column when a
     reviewer sees it, but it should still be REVIEWED, not silently
     applied." — the design note this slab implements
    — W1-32

These tests prove the whole loop, the same shape `test_drift_proposal.py`
already proved for a CONTRACT redirect: the worker writes a draft proposal,
a person accepts it over the real API, and only THEN does a new mapping
version exist — the PUBLISHED line the pipeline actually reads is untouched
the entire time, because "agents propose; humans dispose" has to be true of
a rename's mapping consequence exactly as much as of the rename itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.drift import Rename
from cinqflow.core.mapping import (
    FeedMapping,
    MappingLine,
    from_governed,
    mapping_as_governed,
)
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.proposals import ProposalState
from cinqflow.workers.drift import (
    MAPPING_REDIRECT_CAPABILITY,
    propose_mapping_redirect,
)

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
BA = "dev-ba@cinqcare.test"
AUTHOR = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
REVIEWER = Actor(
    subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ola"
)

RENAME = Rename(
    was="DOB",
    now="date_of_birth",
    glossary_id="BG-004",
    term="Member Date of Birth",
    term_slug="member-date-of-birth",
)

PUBLISHED_MAPPING = FeedMapping(
    feed_id=FEED,
    version=1,
    lines=(
        MappingLine(
            target_entity="members", target_field="source_member_id", source_columns=("MemberID",)
        ),
        MappingLine(target_entity="members", target_field="date_of_birth", source_columns=("DOB",)),
        MappingLine(
            target_entity="members",
            target_field="line_of_business",
            unmapped_reason="No payer-supplied taxonomy at onboarding.",
        ),
    ),
)


def _publish(store: MemMetadataDb, mapping: FeedMapping) -> FeedMapping:
    """Through the real lifecycle — `intelligence.demo._published`'s own
    shortcut, since what this suite proves is the redirect, not the
    lifecycle machinery `test_mapping_on_the_real_plane.py` already owns."""
    draft = mapping_as_governed(mapping, author=AUTHOR, created_ts=NOW)
    reviewed, _ = draft.transition_to(LifecycleState.PENDING_REVIEW, actor=AUTHOR, now=NOW)
    approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=REVIEWER, now=NOW)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=REVIEWER, now=NOW)
    store.save(published)
    stored = store.get(ObjectType.MAPPING, FEED)
    assert stored.is_executable, "the fixture must publish, or this test proves nothing"
    return from_governed(stored)


@pytest.fixture
def store() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def client(store: MemMetadataDb) -> Iterator[TestClient]:
    app = create_app(authn=StaticAuthn(), metadata_db=store)
    with TestClient(app) as test_client:
        yield test_client


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


# ── the worker writes a distinct, deterministic proposal ────────────────────


def test_a_settled_rename_over_a_referencing_line_proposes_a_redirect(
    store: MemMetadataDb,
) -> None:
    mapping = _publish(store, PUBLISHED_MAPPING)
    proposal = propose_mapping_redirect(
        store, feed_id=FEED, mapping=mapping, renames=(RENAME,), run_id="B-9001", now=NOW
    )

    assert proposal is not None
    assert proposal.state is ProposalState.PENDING_REVIEW
    assert proposal.capability == MAPPING_REDIRECT_CAPABILITY
    assert proposal.confidence == 1.0
    assert [str(c) for c in proposal.grounding_citations] == ["term:member-date-of-birth"]

    redirected = next(
        r for r in proposal.payload["records"] if r["settled_by"] == "rename_redirect"
    )
    assert redirected["source_column"] == "date_of_birth"
    assert redirected["target_field"] == "date_of_birth"
    assert "one concept, two spellings" in redirected["rationale"]
    # Every OTHER line travels too — accepting a mapping proposal replaces
    # the whole line set, so an untouched line missing from `records` would
    # be a silent drop dressed up as a redirect.
    fields = {r["target_field"] for r in proposal.payload["records"]}
    assert fields == {"source_member_id", "date_of_birth", "line_of_business"}
    unmapped = next(
        r for r in proposal.payload["records"] if r["target_field"] == "line_of_business"
    )
    assert unmapped["unmapped"] is True
    assert unmapped["unmapped_reason"] == "No payer-supplied taxonomy at onboarding."


def test_a_rename_with_no_referencing_line_has_nothing_to_redirect(store: MemMetadataDb) -> None:
    """`renames` may settle a column no mapping line reads at all — a rename
    for a field nothing was ever mapped through. There is nothing to propose,
    and proposing anyway would be a review item that changes nothing."""
    unrelated = FeedMapping(
        feed_id=FEED,
        version=1,
        lines=(
            MappingLine(
                target_entity="members", target_field="first_name", source_columns=("First_Name",)
            ),
        ),
    )
    mapping = _publish(store, unrelated)
    proposal = propose_mapping_redirect(
        store, feed_id=FEED, mapping=mapping, renames=(RENAME,), run_id="B-9001", now=NOW
    )
    assert proposal is None


def test_a_daily_renamed_delivery_earns_one_redirect_not_one_per_day(store: MemMetadataDb) -> None:
    mapping = _publish(store, PUBLISHED_MAPPING)
    first = propose_mapping_redirect(
        store, feed_id=FEED, mapping=mapping, renames=(RENAME,), run_id="B-1", now=NOW
    )
    second = propose_mapping_redirect(
        store, feed_id=FEED, mapping=mapping, renames=(RENAME,), run_id="B-2", now=NOW
    )
    assert first is not None
    assert second is None


def test_the_redirect_never_touches_the_published_mapping(store: MemMetadataDb) -> None:
    """THE ASSERTION THIS SLAB IS FOR. Right after the worker writes the
    proposal — before any human has looked at it — the governed object's
    body is exactly what it was published as."""
    mapping = _publish(store, PUBLISHED_MAPPING)
    before = store.get(ObjectType.MAPPING, FEED).body

    propose_mapping_redirect(
        store, feed_id=FEED, mapping=mapping, renames=(RENAME,), run_id="B-9001", now=NOW
    )

    after = store.get(ObjectType.MAPPING, FEED).body
    assert after == before
    assert {tuple(line["source_columns"]) for line in after["lines"]} == {
        ("MemberID",),
        ("DOB",),
        (),
    }


# ── a person accepts it over the real API ────────────────────────────────────


def test_acceptance_produces_a_draft_mapping_v2_while_v1_stays_published(
    client: TestClient, store: MemMetadataDb
) -> None:
    """The whole loop: rename settles -> redirect proposed -> a reviewer
    accepts -> DRAFT mapping v2 reads `date_of_birth`, and the PUBLISHED v1
    the pipeline runs on is untouched — "only after acceptance" means only a
    NEW version appears, never that the old one was edited in place."""
    mapping = _publish(store, PUBLISHED_MAPPING)
    v1_body_before = store.get(ObjectType.MAPPING, FEED, version=1).body

    proposal = propose_mapping_redirect(
        store, feed_id=FEED, mapping=mapping, renames=(RENAME,), run_id="B-9001", now=NOW
    )
    assert proposal is not None

    response = client.post(
        f"/api/proposals/{proposal.proposal_id}/approve",
        json={"comment": "the payer renamed DOB — reading the estate's own evidence"},
        headers=_as(BA),
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "applied"

    v1_after = store.get(ObjectType.MAPPING, FEED, version=1)
    assert v1_after.body == v1_body_before
    assert v1_after.lifecycle_state is LifecycleState.PUBLISHED

    draft = store.get(ObjectType.MAPPING, FEED)  # latest — the new draft
    assert draft.version == 2
    assert draft.lifecycle_state is LifecycleState.DRAFT
    rebuilt = from_governed(draft)
    assert rebuilt.line("members", "date_of_birth").source_columns == ("date_of_birth",)
    assert rebuilt.line("members", "source_member_id").source_columns == ("MemberID",)
