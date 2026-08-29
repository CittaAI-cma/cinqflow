"""CF-V1-E3-03 — finding the feed you meant, and the one it clones from.

The similarity half is the one worth testing hard. Filtering a table is table
stakes; what makes "clone a similar feed" a minute's work is the platform
offering the RIGHT feed first, from the registry's own structured fields
rather than from a name-similarity trick.

The estate's own shape is the fixture: Centene has a Medicaid and a Medicare
roster (near-clones), Fidelis has a downstate and an upstate roster
(near-clones), and a claims extract from a third payer shares almost nothing
with any of them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.search import (
    FeedFilter,
    pattern_shape,
    search,
    similar_to,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, tzinfo=UTC)
BA = Actor(subject="meera@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera Rao")


def _feed(
    feed_id: str,
    *,
    domain: str = "membership",
    source_system: str = "centene",
    source_id: str = "centene-ny",
    file_format: str = "csv",
    pattern: str = r"^CENTENE_Medicaid_Roster_\d{8}\.csv$",
    schedule: str = "0 6 * * 1",
    delivery: str = "sftp",
    owner: str = "sam@cinqcare.test",
    state: LifecycleState = LifecycleState.PUBLISHED,
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=feed_id,
        version=1,
        lifecycle_state=state,
        created_by=BA,
        created_ts=NOW,
        body={
            "domain": domain,
            "source_system": source_system,
            "file_format": file_format,
            "file_pattern": pattern,
            "schedule_cron": schedule,
            "operations": {
                "source_id": source_id,
                "delivery_method": delivery,
                "owners": [{"role": "technical", "subject": owner, "display_name": "Sam Okafor"}],
            },
        },
        approved_by=BA if state is LifecycleState.PUBLISHED else None,
        approved_ts=NOW if state is LifecycleState.PUBLISHED else None,
    )


CENTENE_MEDICAID = _feed("centene-medicaid-roster")
CENTENE_MEDICARE = _feed("centene-medicare-roster", pattern=r"^CENTENE_Medicare_Roster_\d{8}\.csv$")
FIDELIS_DOWNSTATE = _feed(
    "fidelis-downstate-roster",
    source_system="fidelis",
    source_id="fidelis-ny",
    file_format="xlsx",
    pattern=r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$",
    owner="ada@cinqcare.test",
)
ACME_CLAIMS = _feed(
    "acme-claims",
    domain="claims",
    source_system="acme",
    source_id="acme-health",
    file_format="json",
    pattern=r"^acme\.claims\.\d{4}-\d{2}-\d{2}\.json$",
    schedule="0 2 * * *",
    delivery="api_pull",
    owner="lee@cinqcare.test",
    state=LifecycleState.DRAFT,
)

REGISTRY = (CENTENE_MEDICAID, CENTENE_MEDICARE, FIDELIS_DOWNSTATE, ACME_CLAIMS)


# ── filtering ────────────────────────────────────────────────────────────────


def test_an_empty_filter_is_the_list_screen() -> None:
    """The list screen and the search screen are the same screen. A search
    that requires a term cannot be the default view."""
    assert FeedFilter().is_empty
    assert len(search(REGISTRY, FeedFilter())) == len(REGISTRY)


def test_free_text_reaches_the_id_the_domain_and_the_pattern() -> None:
    assert [f.object_id for f in search(REGISTRY, FeedFilter(text="medicare"))] == [
        "centene-medicare-roster"
    ]
    assert len(search(REGISTRY, FeedFilter(text="roster"))) == 3
    assert [f.object_id for f in search(REGISTRY, FeedFilter(text="claims"))] == ["acme-claims"]


def test_free_text_reaches_the_owners() -> None:
    """ "Which feeds is Sam on the hook for" is the question somebody asks on
    the Monday after Sam leaves, and a search that only reads the feed's own
    name cannot answer it."""
    found = [f.object_id for f in search(REGISTRY, FeedFilter(text="ada@cinqcare.test"))]
    assert found == ["fidelis-downstate-roster"]


def test_free_text_ignores_punctuation_and_case() -> None:
    """`CINQ DOWNSTATE`, `cinqdownstate` and `CINQ_DOWNSTATE` are the same
    thing typed by three people."""
    for typed in ("CINQ DOWNSTATE", "cinqdownstate", "CinqDownstate"):
        assert [f.object_id for f in search(REGISTRY, FeedFilter(text=typed))] == [
            "fidelis-downstate-roster"
        ]


def test_structured_filters_combine() -> None:
    found = search(REGISTRY, FeedFilter(domain="membership", source_id="centene-ny"))
    assert [f.object_id for f in found] == [
        "centene-medicaid-roster",
        "centene-medicare-roster",
    ]


def test_filtering_by_owner_is_exact_not_fuzzy() -> None:
    """An owner filter that matched substrings would put `sam@` and
    `samantha@` on each other's lists."""
    assert search(REGISTRY, FeedFilter(owner="sam@")) == ()
    assert len(search(REGISTRY, FeedFilter(owner="sam@cinqcare.test"))) == 2


def test_the_not_ready_filter_is_the_chasing_view() -> None:
    """Whoever is chasing SLAs out of payers this week needs a list of exactly
    the feeds that cannot be activated."""
    readiness = {f.object_id: True for f in REGISTRY} | {"acme-claims": False}
    found = search(REGISTRY, FeedFilter(not_ready=True), readiness=readiness)
    assert [f.object_id for f in found] == ["acme-claims"]


def test_results_are_ordered_stably() -> None:
    assert [f.object_id for f in search(REGISTRY, FeedFilter())] == sorted(
        f.object_id for f in REGISTRY
    )


# ── the near-clone ───────────────────────────────────────────────────────────


def test_the_estates_own_near_clone_ranks_first() -> None:
    """ "Centene Medicare is a near-clone of Medicaid."

    Same payer, same domain, same format, same delivery, same naming
    convention, same schedule — and none of that is a guess about names.
    """
    ranked = similar_to(CENTENE_MEDICAID, REGISTRY)
    assert ranked[0].feed_id == "centene-medicare-roster"
    assert ranked[0].score > ranked[1].score


def test_the_ranking_explains_itself() -> None:
    """A ranked list with no explanation is a ranked list somebody scrolls
    past. The score is deterministic arithmetic a BA is entitled to check."""
    top = similar_to(CENTENE_MEDICAID, REGISTRY)[0]
    assert "same source (centene-ny)" in top.reasons
    assert "same domain (membership)" in top.reasons
    assert "same file-name convention" in top.reasons


def test_a_wholly_different_feed_does_not_appear() -> None:
    """Offering a BA a list of feeds with nothing in common would teach them
    the feature does not work."""
    ranked = {match.feed_id for match in similar_to(CENTENE_MEDICAID, REGISTRY)}
    assert "acme-claims" not in ranked


def test_a_different_payer_with_the_same_shape_still_ranks() -> None:
    """Fidelis's roster is a different payer and a different format, but it is
    a membership roster — which is worth something and is scored as such,
    below the same-payer match."""
    ranked = similar_to(CENTENE_MEDICAID, REGISTRY)
    fidelis = next(m for m in ranked if m.feed_id == "fidelis-downstate-roster")
    assert "same domain (membership)" in fidelis.reasons
    assert fidelis.score < ranked[0].score


def test_a_feed_is_never_similar_to_itself() -> None:
    assert all(
        m.feed_id != CENTENE_MEDICAID.object_id for m in similar_to(CENTENE_MEDICAID, REGISTRY)
    )


def test_a_published_original_is_preferred_over_a_draft() -> None:
    """A feed nobody has published is a poor thing to copy: its contract and
    rules have not been through a review, and the clone would inherit
    somebody's unfinished afternoon."""
    draft_twin = _feed(
        "centene-medicare-roster-draft",
        pattern=r"^CENTENE_Medicare_Roster_\d{8}\.csv$",
        state=LifecycleState.DRAFT,
    )
    ranked = similar_to(CENTENE_MEDICAID, (*REGISTRY, draft_twin))
    assert ranked[0].feed_id == "centene-medicare-roster"
    assert "published" in " ".join(ranked[0].reasons)


def test_ties_break_stably() -> None:
    """Two runs of the same registry must agree, or a screen reorders itself
    between refreshes."""
    twice = [tuple(m.feed_id for m in similar_to(CENTENE_MEDICAID, REGISTRY)) for _ in range(2)]
    assert twice[0] == twice[1]


# ── the shape function ───────────────────────────────────────────────────────


def test_a_pattern_shape_ignores_the_words_and_keeps_the_skeleton() -> None:
    """`Medicaid` and `Medicare` are different words in the same convention.
    A payer's naming convention carrying across is most of what makes a clone
    save time."""
    assert pattern_shape(r"^CENTENE_Medicaid_Roster_\d{8}\.csv$") == pattern_shape(
        r"^CENTENE_Medicare_Roster_\d{8}\.csv$"
    )


def test_a_different_convention_has_a_different_shape() -> None:
    assert pattern_shape(r"^CENTENE_Medicaid_Roster_\d{8}\.csv$") != pattern_shape(
        r"^acme\.claims\.\d{4}-\d{2}-\d{2}\.json$"
    )
