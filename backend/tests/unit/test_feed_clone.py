"""CF-V1-E3-03 — the clone, and the line it draws.

    "config/mappings/rules copied, history/status never · differences panel ·
     unapproved inherited parts marked · clones always Draft"

The suite is organised around that line. What crosses it is the morning's work
somebody already did; what does not cross it is anybody's signature.

The most important test in this file is the LAST one, and it asserts nothing
about behaviour: it walks `GovernedObject`'s fields and fails if any of them
belongs to none of `REPLACED`, `INHERITED` or `RESET`. The failure it prevents
is somebody adding `published_ts` to the governed object, nobody thinking about
cloning, and clones silently carrying a publication date they never had.
"""

from __future__ import annotations

import dataclasses

import pytest

from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.clone import (
    BODY_NEVER_CLONED,
    INHERITED,
    REPLACED,
    RESET,
    CloneError,
    carries_no_approval,
    clone_feed,
    differences_between,
)

pytestmark = pytest.mark.unit

from datetime import UTC, datetime  # noqa: E402 — kept beside NOW for readability

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="meera@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera Rao")
STEWARD = Actor(subject="ada@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ada Kaur")

ORIGINAL_ID = "centene-medicaid-roster"
CLONE_ID = "centene-medicare-roster"

OPERATIONS = {
    "source_id": "centene-ny",
    "direction": "inbound",
    "delivery_method": "sftp",
    "endpoint_ref": "centene-sftp",
    "owners": [
        {"role": "business", "subject": "meera@cinqcare.test", "display_name": "Meera Rao"},
        {"role": "technical", "subject": "sam@cinqcare.test", "display_name": "Sam Okafor"},
    ],
    "service_level": {
        "expected_by_local_time": "06:00",
        "timezone": "America/New_York",
        "calendar": "business_days",
        "grace_minutes": 30,
        "escalate_after_minutes": 120,
    },
    "volume": {"typical_records": 40000, "tolerance_percent": 20},
    "alert_chain": [
        {"after_minutes": 30, "channel": "email", "notify": ["sam@cinqcare.test"]},
        {"after_minutes": 120, "channel": "pager", "notify": ["meera@cinqcare.test"]},
    ],
    "documents": [],
    "notes": "",
}


def _feed(
    object_id: str = ORIGINAL_ID,
    *,
    state: LifecycleState = LifecycleState.PUBLISHED,
    version: int = 4,
    body: dict | None = None,  # type: ignore[type-arg]
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=object_id,
        version=version,
        lifecycle_state=state,
        created_by=BA,
        created_ts=NOW,
        body=body
        or {
            "domain": "membership",
            "source_system": "centene",
            "file_format": "csv",
            "landing_path": "landing/centene/medicaid",
            "file_pattern": r"^CENTENE_Medicaid_Roster_\d{8}\.csv$",
            "schedule_cron": "0 6 * * 1",
            "sample_filename": "CENTENE_Medicaid_Roster_20260801.csv",
            "operations": OPERATIONS,
            # Evidence about a PARTICULAR FILE. Must not travel.
            "evidence": {"profile_id": "sha256-abc", "profiled_ts": "2026-08-01"},
        },
        approved_by=STEWARD if state is LifecycleState.PUBLISHED else None,
        approved_ts=NOW if state is LifecycleState.PUBLISHED else None,
    )


def _child(
    object_type: ObjectType,
    object_id: str,
    *,
    state: LifecycleState = LifecycleState.PUBLISHED,
    feed_id: str = ORIGINAL_ID,
) -> GovernedObject:
    return GovernedObject(
        object_type=object_type,
        object_id=object_id,
        version=2,
        lifecycle_state=state,
        created_by=BA,
        created_ts=NOW,
        body={"feed_id": feed_id, "columns": [{"name": "member_id"}]},
        approved_by=STEWARD if state is LifecycleState.PUBLISHED else None,
        approved_ts=NOW if state is LifecycleState.PUBLISHED else None,
    )


# ── configuration is inherited ───────────────────────────────────────────────


def test_the_contract_the_mapping_and_the_rules_come_across() -> None:
    """The archetype the story names: Centene Medicare from Centene Medicaid,
    in under a minute, because the morning's work is already done."""
    related = [
        _child(ObjectType.CONTRACT, ORIGINAL_ID),
        _child(ObjectType.MAPPING, "centene-medicaid-member-map"),
        _child(ObjectType.DQ_RULE, ORIGINAL_ID),
    ]
    result = clone_feed(_feed(), related, new_feed_id=CLONE_ID, author=BA, now=NOW)

    types = {obj.object_type for obj in result.objects}
    assert types == {
        ObjectType.FEED,
        ObjectType.CONTRACT,
        ObjectType.MAPPING,
        ObjectType.DQ_RULE,
    }
    assert result.feed.body["operations"]["owners"] == OPERATIONS["owners"]


def test_a_child_keyed_by_the_feed_follows_the_new_feeds_id() -> None:
    """A contract is keyed by `feed_id`, so the clone's contract is keyed by
    the clone's. A child with an id of its own gains the new feed's prefix, so
    two feeds' mappings cannot collide."""
    related = [
        _child(ObjectType.CONTRACT, ORIGINAL_ID),
        _child(ObjectType.MAPPING, "centene-medicaid-member-map"),
    ]
    result = clone_feed(_feed(), related, new_feed_id=CLONE_ID, author=BA, now=NOW)
    ids = {obj.object_type: obj.object_id for obj in result.objects}

    assert ids[ObjectType.CONTRACT] == CLONE_ID
    assert ids[ObjectType.MAPPING] == f"{CLONE_ID}:centene-medicaid-member-map"


def test_a_child_body_is_repointed_at_the_clone() -> None:
    """A cloned contract that still named the original feed would be a
    contract for somebody else's data."""
    result = clone_feed(
        _feed(), [_child(ObjectType.CONTRACT, ORIGINAL_ID)], new_feed_id=CLONE_ID, author=BA
    )
    contract = next(o for o in result.objects if o.object_type is ObjectType.CONTRACT)
    assert contract.body["feed_id"] == CLONE_ID


def test_children_are_found_through_the_declared_reference_graph() -> None:
    """Not through a list of types written in the clone module.

    A RUNBOOK declares `feed_id -> FEED` in `core.impact.REFERENCES`, and is
    therefore cloned without anybody having edited `clone.py` — which is what
    "the walk is the reference graph" buys.
    """
    result = clone_feed(
        _feed(),
        [_child(ObjectType.RUNBOOK, "rb-centene-medicaid")],
        new_feed_id=CLONE_ID,
        author=BA,
    )
    assert any(o.object_type is ObjectType.RUNBOOK for o in result.objects)


def test_an_unrelated_feeds_contract_is_not_cloned() -> None:
    result = clone_feed(
        _feed(),
        [_child(ObjectType.CONTRACT, "fidelis-roster", feed_id="fidelis-roster")],
        new_feed_id=CLONE_ID,
        author=BA,
    )
    assert [o.object_type for o in result.objects] == [ObjectType.FEED]


def test_the_caller_can_choose_what_comes_across() -> None:
    """A BA cloning the shape but not the rules — because the new payer's data
    quality is a different conversation."""
    related = [
        _child(ObjectType.CONTRACT, ORIGINAL_ID),
        _child(ObjectType.DQ_RULE, ORIGINAL_ID),
    ]
    result = clone_feed(
        _feed(),
        related,
        new_feed_id=CLONE_ID,
        author=BA,
        include=frozenset({ObjectType.FEED, ObjectType.CONTRACT}),
    )
    assert {o.object_type for o in result.objects} == {ObjectType.FEED, ObjectType.CONTRACT}


# ── history is not ───────────────────────────────────────────────────────────


def test_every_cloned_object_is_a_first_draft_nobody_signed() -> None:
    """ "Clones always Draft."

    A clone that arrived Approved would let one review approve two feeds,
    which is precisely the segregation the platform exists to keep.
    """
    related = [
        _child(ObjectType.CONTRACT, ORIGINAL_ID),
        _child(ObjectType.DQ_RULE, ORIGINAL_ID),
    ]
    result = clone_feed(_feed(), related, new_feed_id=CLONE_ID, author=BA, now=NOW)

    assert result.is_all_draft
    for obj in result.objects:
        assert carries_no_approval(obj), obj.object_id
        assert obj.created_by == BA, "authored by whoever pressed clone, not the original author"
        assert obj.created_ts == NOW


def test_the_originals_version_number_does_not_travel() -> None:
    """The original is at v4 and has been through four reviews. The clone has
    been through none, and its version says so."""
    result = clone_feed(_feed(version=4), [], new_feed_id=CLONE_ID, author=BA)
    assert result.feed.version == 1
    assert result.cloned_from_version == 4


def test_evidence_about_the_original_file_is_dropped() -> None:
    """THE ONE THAT WOULD ACTUALLY HURT.

    CF-V1-E4-03 blocks a submission whose evidence is stale by comparing
    profile fingerprints. A clone inheriting the original's fingerprint would
    sail through that gate having profiled nothing at all — so the clone must
    profile its own sample, and dropping the key is what makes it.
    """
    result = clone_feed(_feed(), [], new_feed_id=CLONE_ID, author=BA)
    assert "evidence" not in result.feed.body
    for key in BODY_NEVER_CLONED:
        assert key not in result.feed.body


# ── unapproved inherited parts are marked ────────────────────────────────────


def test_inheriting_from_a_draft_is_flagged() -> None:
    """A mapping copied from a published feed carries somebody's signature
    behind it; the same mapping copied from a draft carries nobody's, and the
    two look identical on the clone unless something says so."""
    related = [
        _child(ObjectType.CONTRACT, ORIGINAL_ID, state=LifecycleState.PUBLISHED),
        _child(ObjectType.DQ_RULE, ORIGINAL_ID, state=LifecycleState.DRAFT),
    ]
    result = clone_feed(_feed(), related, new_feed_id=CLONE_ID, author=BA)

    approved = {i.object_type: i.was_approved for i in result.inherited}
    assert approved[ObjectType.CONTRACT] is True
    assert approved[ObjectType.DQ_RULE] is False

    assert len(result.warnings) == 1
    assert "nobody has approved it" in result.warnings[0]
    assert "dq_rule" in result.warnings[0]


def test_cloning_a_draft_feed_warns_about_the_feed_itself() -> None:
    result = clone_feed(_feed(state=LifecycleState.DRAFT), [], new_feed_id=CLONE_ID, author=BA)
    assert result.warnings and "feed" in result.warnings[0]


# ── the differences panel ────────────────────────────────────────────────────


def test_the_panel_shows_exactly_what_the_ba_changed() -> None:
    """Four things changed, and the BA sees four lines — not forty fields to
    re-read looking for them."""
    result = clone_feed(
        _feed(),
        [],
        new_feed_id=CLONE_ID,
        author=BA,
        overrides={
            "landing_path": "landing/centene/medicare",
            "file_pattern": r"^CENTENE_Medicare_Roster_\d{8}\.csv$",
            "sample_filename": "CENTENE_Medicare_Roster_20260801.csv",
        },
    )
    changed = {d.field_path for d in result.differences}
    assert changed == {"landing_path", "file_pattern", "sample_filename"}

    landing = next(d for d in result.differences if d.field_path == "landing_path")
    assert landing.original == "landing/centene/medicaid"
    assert landing.clone == "landing/centene/medicare"


def test_an_operations_override_merges_rather_than_replaces() -> None:
    """A BA changing the endpoint must not have to restate the owners, the
    SLA and the alert chain — which is how a clone loses its escalation
    ladder and nobody notices until the feed is late."""
    result = clone_feed(
        _feed(),
        [],
        new_feed_id=CLONE_ID,
        author=BA,
        overrides={"operations": {"endpoint_ref": "centene-medicare-sftp"}},
    )
    envelope = result.feed.body["operations"]
    assert envelope["endpoint_ref"] == "centene-medicare-sftp"
    assert envelope["owners"] == OPERATIONS["owners"]
    assert envelope["alert_chain"] == OPERATIONS["alert_chain"]

    assert {d.field_path for d in result.differences} == {"operations.endpoint_ref"}


def test_two_existing_objects_can_be_compared_with_the_same_function() -> None:
    """The same computation serves the clone panel and CF-V1-E3-04's version
    compare, so "how does this differ" has ONE answer in the platform."""
    left = _feed(body={"domain": "membership", "schedule_cron": "0 6 * * 1"})
    right = _feed(body={"domain": "membership", "schedule_cron": "0 7 * * 1"})
    assert [d.field_path for d in differences_between(left, right)] == ["schedule_cron"]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_cloning_a_feed_onto_its_own_id_is_refused() -> None:
    with pytest.raises(CloneError, match="is an edit"):
        clone_feed(_feed(), [], new_feed_id=ORIGINAL_ID, author=BA)


def test_only_feeds_are_cloned() -> None:
    with pytest.raises(CloneError, match="is not a feed"):
        clone_feed(_child(ObjectType.CONTRACT, "x"), [], new_feed_id=CLONE_ID, author=BA)


def test_a_clone_needs_an_id() -> None:
    with pytest.raises(CloneError, match="needs an id"):
        clone_feed(_feed(), [], new_feed_id="   ", author=BA)


# ── the structural guarantee ─────────────────────────────────────────────────


def test_every_governed_field_is_classified() -> None:
    """THE TEST THAT EARNS ITS KEEP.

    Nothing here checks behaviour. It fails when somebody adds a field to
    `GovernedObject` without deciding whether a clone should carry it — which
    is exactly how a clone starts carrying a publication date it never had,
    silently, three releases later.
    """
    fields = {f.name for f in dataclasses.fields(GovernedObject)}
    classified = REPLACED | INHERITED | RESET

    assert fields == classified, (
        "unclassified governed field(s): "
        + ", ".join(sorted(fields - classified))
        + " — decide whether a clone REPLACES, INHERITS or RESETS each one, "
        "and say so in core/registry/clone.py"
    )
    assert not (REPLACED & INHERITED) and not (INHERITED & RESET) and not (REPLACED & RESET), (
        "a field classified twice has two answers to one question"
    )
