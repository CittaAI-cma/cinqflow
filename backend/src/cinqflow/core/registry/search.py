"""CF-V1-E3-03 — finding the feed you meant, and the one it is a near-clone of.

    "Clone a similar feed + search/filter the registry"
    "Centene Medicare is a near-clone of Medicaid, Fidelis downstate of upstate."
    — CF-V1-E3-03

TWO HALVES, AND THE SECOND IS THE POINT. Filtering a registry is table stakes.
What makes "clone a similar feed" a minute's work rather than a morning's is
the platform KNOWING which feed to offer — and knowing it from the registry's
own contents rather than from a name-similarity trick.

`similar_to` scores by things that make a clone actually useful: the same
payer, the same domain, the same file format, the same delivery method, a
file-name pattern of the same shape. Every point is attributed, so the
ranking is a claim a BA can check rather than an ordering they have to trust.
That attribution is why this is deterministic arithmetic and not a model call:
"why is this first" has an answer, in one line, for free.

NO FUZZY STRING MATCHING ON NAMES. `fidelis-downstate-roster` and
`fidelis-upstate-roster` share a prefix, and so do `centene-medicaid-claims`
and `centene-medicaid-roster` — which are not near-clones of each other at
all. Names are the least reliable signal in this estate; the registry's own
structured fields are the reliable one, and they are right there.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from cinqflow.core.model.governed import GovernedObject, LifecycleState, ObjectType


@dataclass(frozen=True)
class FeedFilter:
    """What a BA typed into the registry screen. Every field optional.

    An empty filter matches everything, deliberately: the list screen and the
    search screen are the same screen, and a search that requires a term
    cannot be the default view.
    """

    text: str = ""
    domain: str = ""
    source_system: str = ""
    source_id: str = ""
    delivery_method: str = ""
    lifecycle_state: str = ""
    owner: str = ""
    #: Only feeds that cannot yet be activated. The working view for whoever
    #: is chasing SLAs out of payers this week.
    not_ready: bool = False

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.text,
                self.domain,
                self.source_system,
                self.source_id,
                self.delivery_method,
                self.lifecycle_state,
                self.owner,
                self.not_ready,
            )
        )


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _operations(obj: GovernedObject) -> dict[str, object]:
    value = obj.body.get("operations")
    return value if isinstance(value, dict) else {}


def _owner_subjects(obj: GovernedObject) -> tuple[str, ...]:
    owners = _operations(obj).get("owners")
    if not isinstance(owners, list):
        return ()
    return tuple(str(owner.get("subject", "")) for owner in owners if isinstance(owner, dict))


def _haystack(obj: GovernedObject) -> str:
    """Everything a free-text search should reach.

    Includes the OWNERS' subjects, because "which feeds is Sam on the hook
    for" is the question somebody asks on the Monday after Sam leaves, and a
    search that only reads the feed's own name cannot answer it.
    """
    body = obj.body
    operations = _operations(obj)
    parts = [
        obj.object_id,
        str(body.get("domain", "")),
        str(body.get("source_system", "")),
        str(body.get("file_format", "")),
        str(body.get("file_pattern", "")),
        str(operations.get("source_id", "")),
        str(operations.get("notes", "")),
        *_owner_subjects(obj),
    ]
    return _normalise(" ".join(parts))


def matches(obj: GovernedObject, criteria: FeedFilter, *, is_ready: bool | None = None) -> bool:
    """Whether one feed satisfies the filter. Pure, so it is testable in bulk."""
    body = obj.body
    operations = _operations(obj)

    if criteria.text and _normalise(criteria.text) not in _haystack(obj):
        return False
    if criteria.domain and str(body.get("domain", "")) != criteria.domain:
        return False
    if criteria.source_system and str(body.get("source_system", "")) != criteria.source_system:
        return False
    if criteria.source_id and str(operations.get("source_id", "")) != criteria.source_id:
        return False
    if (
        criteria.delivery_method
        and str(operations.get("delivery_method", "")) != criteria.delivery_method
    ):
        return False
    if criteria.lifecycle_state and obj.lifecycle_state.value != criteria.lifecycle_state:
        return False
    if criteria.owner and criteria.owner not in _owner_subjects(obj):
        return False
    return not (criteria.not_ready and is_ready is not False)


def search(
    feeds: Sequence[GovernedObject],
    criteria: FeedFilter,
    *,
    readiness: dict[str, bool] | None = None,
) -> tuple[GovernedObject, ...]:
    """Filter, in a stable order.

    `readiness` is passed IN rather than computed here so this module stays
    free of `operations` semantics beyond reading its fields — and so a caller
    that already computed the checklist for the page does not compute it twice.
    """
    ready = readiness or {}
    return tuple(
        obj
        for obj in sorted(feeds, key=lambda o: o.object_id)
        if matches(obj, criteria, is_ready=ready.get(obj.object_id))
    )


# ── "clone a similar feed" ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Similarity:
    """One candidate to clone from, its score, and WHY it scored.

    `reasons` is the field that makes this usable. A ranked list with no
    explanation is a ranked list somebody scrolls past; "same payer, same
    domain, same file shape" is a sentence that makes the top result obviously
    right or obviously wrong in one glance.
    """

    feed: GovernedObject
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def feed_id(self) -> str:
        return self.feed.object_id


#: Points per signal, most-reliable first. Weights are DATA so that tuning
#: them is a visible diff rather than an edit buried in a scoring function —
#: and so a test can assert the ordering they produce on the real registry.
WEIGHTS: dict[str, int] = {
    # The same payer sending a second thing is the archetype the story names:
    # Centene Medicare from Centene Medicaid.
    "source": 5,
    "domain": 4,
    "file_format": 3,
    "delivery_method": 2,
    # A file-name pattern of the same SHAPE — same literal skeleton, possibly
    # different words — means the payer's naming convention carries across,
    # which is most of what makes a clone save time.
    "pattern_shape": 3,
    "schedule": 1,
    # A feed nobody has published is a poor thing to copy: its contract and
    # rules have not been through a review, and the clone would inherit
    # somebody's unfinished afternoon.
    "published": 2,
}

#: A pattern's SHAPE: every run of letters and digits collapsed, so
#: `^CENTENE_Medicaid_Roster_\d{8}\.csv$` and `^CENTENE_Medicare_Roster_\d{8}\.csv$`
#: come out identical while a wholly different convention does not.
_WORD = re.compile(r"[A-Za-z]+|\d+")


def pattern_shape(pattern: str) -> str:
    return _WORD.sub("*", pattern)


def similar_to(
    target: GovernedObject, candidates: Sequence[GovernedObject], *, limit: int = 5
) -> tuple[Similarity, ...]:
    """Rank the feeds worth cloning from, highest first.

    The target itself is excluded, and so is anything scoring zero: offering a
    BA a list of feeds with nothing in common would teach them the feature
    does not work.
    """
    scored: list[Similarity] = []
    target_ops = _operations(target)

    for candidate in candidates:
        if candidate.object_id == target.object_id or candidate.object_type is not ObjectType.FEED:
            continue
        score = 0
        reasons: list[str] = []
        ops = _operations(candidate)

        source = str(target_ops.get("source_id", ""))
        if source and source == str(ops.get("source_id", "")):
            score += WEIGHTS["source"]
            reasons.append(f"same source ({source})")
        if target.body.get("domain") and target.body.get("domain") == candidate.body.get("domain"):
            score += WEIGHTS["domain"]
            reasons.append(f"same domain ({target.body['domain']})")
        if target.body.get("file_format") == candidate.body.get("file_format"):
            score += WEIGHTS["file_format"]
            reasons.append(f"same format ({target.body.get('file_format')})")
        if target_ops.get("delivery_method") and target_ops.get("delivery_method") == ops.get(
            "delivery_method"
        ):
            score += WEIGHTS["delivery_method"]
            reasons.append(f"same delivery ({target_ops['delivery_method']})")
        if pattern_shape(str(target.body.get("file_pattern", "x"))) == pattern_shape(
            str(candidate.body.get("file_pattern", "y"))
        ):
            score += WEIGHTS["pattern_shape"]
            reasons.append("same file-name convention")
        if target.body.get("schedule_cron") == candidate.body.get("schedule_cron"):
            score += WEIGHTS["schedule"]
            reasons.append("same schedule")
        if candidate.lifecycle_state is LifecycleState.PUBLISHED:
            score += WEIGHTS["published"]
            reasons.append("published, so its contract and rules were reviewed")

        if score:
            scored.append(Similarity(feed=candidate, score=score, reasons=tuple(reasons)))

    # Ties broken by id, so two runs of the same registry agree.
    return tuple(sorted(scored, key=lambda s: (-s.score, s.feed_id))[:limit])
