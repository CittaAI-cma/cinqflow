"""CF-V1-E11-02 — the approval packet, and the hole that blocks a signature.

    "Given a mapping change touching 4 jobs and 2 reports awaits approval, when
     the approver opens the packet, then they see the diff, both impact lists,
     and the dry-run evidence on one screen."
    "Given lineage cannot determine impact for one downstream item ... that
     item is listed as 'impact unknown — needs manual check', and production
     approval is BLOCKED."
    — CF-V1-E11-02

The documented don't: "Present an approval with an empty impact section as if
impact were zero." Nothing-downstream and could-not-tell are different answers,
and this file asserts the platform can say both.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core import lifecycle
from cinqflow.core.impact import (
    REFERENCES,
    ImpactUnknownError,
    build_packet,
    dependents_of,
    diff_bodies,
)
from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
STEWARD = Actor(
    subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Daniel"
)
STEWARD_ROLES = frozenset({Role.DATA_STEWARD})

FEED = "fidelis-downstate-roster"


def _obj(
    object_type: ObjectType,
    object_id: str,
    body: dict[str, object] | None = None,
    state: LifecycleState = LifecycleState.PUBLISHED,
    version: int = 1,
) -> GovernedObject:
    return GovernedObject(
        object_type=object_type,
        object_id=object_id,
        version=version,
        lifecycle_state=state,
        created_by=BA,
        created_ts=NOW,
        body=body or {},
        approved_by=STEWARD if state is LifecycleState.PUBLISHED else None,
        approved_ts=NOW if state is LifecycleState.PUBLISHED else None,
    )


def _estate() -> tuple[GovernedObject, ...]:
    """A small but real shape: one feed, its contract, a mapping and two rules
    that read that contract, a glossary term the mapping links, and a runbook."""
    return (
        _obj(ObjectType.FEED, FEED, {"domain": "membership"}),
        _obj(ObjectType.CONTRACT, "roster-contract", {"feed_id": FEED}),
        _obj(
            ObjectType.MAPPING,
            "roster-mapping",
            {"feed_id": FEED, "contract_id": "roster-contract", "glossary_ids": ["BG-004"]},
        ),
        _obj(ObjectType.DQ_RULE, "DQ-002", {"feed_id": FEED, "contract_id": "roster-contract"}),
        _obj(ObjectType.DQ_RULE, "DQ-014", {"feed_id": FEED, "contract_id": "roster-contract"}),
        _obj(ObjectType.GLOSSARY_TERM, "BG-004", {"name": "Member Date of Birth"}),
        _obj(ObjectType.RUNBOOK, "rb-roster", {"feed_id": FEED}),
    )


# ── impact is COMPUTED, never typed ──────────────────────────────────────────


def test_changing_a_contract_reaches_the_mapping_and_both_rules() -> None:
    """The author writes nothing about blast radius; the graph answers."""
    estate = _estate()
    contract = next(o for o in estate if o.object_type is ObjectType.CONTRACT)
    reached = {(t.object_type, t.object_id) for t in dependents_of(contract, estate)}
    assert reached == {
        (ObjectType.MAPPING, "roster-mapping"),
        (ObjectType.DQ_RULE, "DQ-002"),
        (ObjectType.DQ_RULE, "DQ-014"),
    }


def test_impact_is_transitive__a_feed_change_reaches_what_its_contract_feeds() -> None:
    """A feed change reaches the contract directly, and the mapping and rules
    THROUGH it. One hop of lineage would understate the blast radius."""
    estate = _estate()
    feed = next(o for o in estate if o.object_type is ObjectType.FEED)
    reached = {t.object_id for t in dependents_of(feed, estate)}
    assert reached == {"roster-contract", "roster-mapping", "DQ-002", "DQ-014", "rb-roster"}


def test_every_touched_item_says_how_lineage_reached_it() -> None:
    """An approver can check the reasoning rather than trust the count."""
    estate = _estate()
    contract = next(o for o in estate if o.object_type is ObjectType.CONTRACT)
    assert all(t.via for t in dependents_of(contract, estate))


def test_a_retired_object_is_not_listed_as_affected() -> None:
    """A retired mapping is not affected by a change to the feed it used to
    read — padding the packet with noise trains approvers to skim."""
    estate = (
        *_estate(),
        _obj(
            ObjectType.MAPPING,
            "old-mapping",
            {"feed_id": FEED},
            state=LifecycleState.RETIRED,
        ),
    )
    feed = next(o for o in estate if o.object_type is ObjectType.FEED)
    assert "old-mapping" not in {t.object_id for t in dependents_of(feed, estate)}


def test_the_packet_splits_impact_by_audience_not_by_table() -> None:
    """ "One packet carries both sides" — a glossary term is what a business
    person recognises; a contract is what an engineer does."""
    estate = _estate()
    feed = next(o for o in estate if o.object_type is ObjectType.FEED)
    packet = build_packet(feed, estate)
    assert {t.object_id for t in packet.business_impact} == {"rb-roster"}
    assert {t.object_id for t in packet.engineering_impact} == {
        "roster-contract",
        "roster-mapping",
        "DQ-002",
        "DQ-014",
    }


def test_every_object_type_declares_its_references() -> None:
    """A new ObjectType cannot ship with no lineage — it would compute an
    impact of zero and read as safe."""
    assert set(REFERENCES) == set(ObjectType)


# ── the unknown, and what it blocks ──────────────────────────────────────────


def test_a_consumer_lineage_cannot_resolve_is_listed_as_unknown() -> None:
    """ "impact unknown — needs manual check", shown explicitly."""
    estate = _estate()
    feed = next(o for o in estate if o.object_type is ObjectType.FEED)
    changed = _obj(
        ObjectType.FEED,
        FEED,
        {"domain": "membership", "business_consumers": ["BG-004", "Acute Event Command"]},
        state=LifecycleState.PENDING_REVIEW,
    )
    packet = build_packet(changed, (*estate, changed))
    assert [u.name for u in packet.unknowns] == ["Acute Event Command"]
    assert "needs manual check" in packet.unknowns[0].reason
    assert packet.blocks_production is True
    _ = feed


def test_approval_is_refused_while_the_packet_has_a_hole_in_it() -> None:
    """The gate. Not because the change is wrong — because nobody can say."""
    changed = _obj(
        ObjectType.MAPPING,
        "roster-mapping",
        {"feed_id": FEED, "business_consumers": ["30-day Readmission Measure"]},
        state=LifecycleState.PENDING_REVIEW,
    )
    packet = build_packet(changed, (*_estate(), changed))
    with pytest.raises(ImpactUnknownError, match="needs manual check"):
        lifecycle.approve(
            changed,
            actor=STEWARD,
            roles=STEWARD_ROLES,
            comment="looks right to me",
            packet=packet,
        )


def test_nothing_downstream_is_a_different_answer_from_could_not_tell() -> None:
    """The documented don't: an empty impact section presented as if impact
    were zero. `is_empty` says nothing-downstream; `unknowns` says we could
    not tell — and only the second blocks."""
    lonely = _obj(ObjectType.GLOSSARY_TERM, "BG-999", {"name": "Unused Term"})
    packet = build_packet(lonely, (lonely,))
    assert packet.is_empty is True
    assert packet.blocks_production is False


def test_an_approval_must_state_its_rationale() -> None:
    """ "Require a written rationale on every decision; the rationale becomes
    part of the audit record." """
    mapping = _obj(
        ObjectType.MAPPING, "roster-mapping", {"feed_id": FEED}, state=LifecycleState.PENDING_REVIEW
    )
    from cinqflow.core.model.governed import LifecycleViolationError

    with pytest.raises(LifecycleViolationError, match="rationale"):
        lifecycle.approve(mapping, actor=STEWARD, roles=STEWARD_ROLES, comment="   ")


def test_the_rationale_lands_on_the_audit_entry() -> None:
    mapping = _obj(
        ObjectType.MAPPING, "roster-mapping", {"feed_id": FEED}, state=LifecycleState.PENDING_REVIEW
    )
    _, entry = lifecycle.approve(
        mapping,
        actor=STEWARD,
        roles=STEWARD_ROLES,
        comment="dry run shows 9,992 of 10,000 loaded; the 8 are DQ-002 as expected",
    )
    assert "9,992" in entry.detail


# ── the diff an approver actually reads ──────────────────────────────────────


def test_the_diff_is_business_readable() -> None:
    """An approver reading a schedule change needs no JSON tooling."""
    lines = diff_bodies(
        {"schedule_cron": "0 6 * * 1", "domain": "membership"},
        {"schedule_cron": "0 6 1 * *", "domain": "membership", "owner": "Meera"},
    )
    assert "schedule_cron: '0 6 * * 1' -> '0 6 1 * *'" in lines
    assert "owner: added 'Meera'" in lines
    assert not any("domain" in line for line in lines)


def test_the_packet_diffs_against_the_previous_version() -> None:
    v1 = _obj(ObjectType.MAPPING, "roster-mapping", {"feed_id": FEED, "lines": 42}, version=1)
    v2 = _obj(
        ObjectType.MAPPING,
        "roster-mapping",
        {"feed_id": FEED, "lines": 45},
        state=LifecycleState.PENDING_REVIEW,
        version=2,
    )
    packet = build_packet(v2, (v1, v2))
    assert "lines: 42 -> 45" in packet.diff
