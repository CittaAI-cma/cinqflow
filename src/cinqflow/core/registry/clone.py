"""CF-V1-E3-03 — clone a similar feed, and see exactly how it differs.

    "Clone a similar feed + search/filter the registry"
    "Highest-leverage BA feature in the data: Centene Medicare is a near-clone
     of Medicaid, Fidelis downstate of upstate."
    — CF-V1-E3-03

    "config/mappings/rules copied, history/status never · differences panel ·
     unapproved inherited parts marked · clones always Draft"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

THE LINE THIS MODULE DRAWS: CONFIGURATION IS INHERITED, HISTORY IS NOT.

A clone gets the contract, the mappings and the rules, because those are the
morning's work somebody already did. It gets NONE of the approval — not the
state, not the approver, not the version number, not the audit trail, and not
the evidence. A clone that arrived Approved would let one review approve two
feeds, which is precisely the segregation the platform exists to keep.

THE CLASSIFICATION IS EXHAUSTIVE, AND A TEST ENFORCES THAT. Every field of
`GovernedObject` appears in exactly one of `REPLACED`, `INHERITED` or `RESET`
below, and `tests/unit/test_feed_clone.py` fails if a new field belongs to
none of them. The failure mode this prevents is specific: somebody adds
`published_ts` to the governed object, nobody thinks about cloning, and clones
silently start carrying a publication date they never had.

THE WALK IS THE REFERENCE GRAPH, not a list of types. `core.impact.REFERENCES`
already declares that a MAPPING's `feed_id` points at a FEED; this module
reads that same table, so a new object type that references a feed becomes
clonable by declaring its edge rather than by editing anything here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cinqflow.core.impact import REFERENCES
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType

#: Fields the clone SUPPLIES for itself. Copying either would produce a second
#: object at the same address.
REPLACED: frozenset[str] = frozenset({"object_id"})

#: Fields carried across unchanged. `object_type` because a cloned mapping is
#: still a mapping; `body` because the body IS the configuration, subject to
#: the body-level exclusions below.
INHERITED: frozenset[str] = frozenset({"object_type", "body"})

#: Fields the clone starts again from nothing. THE APPROVAL, in every form it
#: takes. A clone is a first draft that has never been reviewed, whatever its
#: original had earned.
RESET: frozenset[str] = frozenset(
    {"version", "lifecycle_state", "created_by", "created_ts", "approved_by", "approved_ts"}
)

#: Body keys that are about a PARTICULAR FILE and therefore about the original
#: feed, never the clone.
#:
#: `evidence` is the one that would actually hurt. CF-V1-E4-03 blocks a
#: submission whose evidence is stale by comparing profile fingerprints — and
#: a clone inheriting the original's fingerprint would sail through that gate
#: having profiled nothing at all. The clone must profile its own sample, and
#: dropping the key here is what makes it.
BODY_NEVER_CLONED: frozenset[str] = frozenset({"evidence", "profile_id", "source_fingerprint"})


class CloneError(ValueError):
    """A clone the platform will not make."""


@dataclass(frozen=True)
class Inherited:
    """One object the clone received, and whether anybody had approved it.

    `was_approved` is the whole of "unapproved inherited parts marked". A
    mapping copied from a PUBLISHED feed carries somebody's signature behind
    it; the same mapping copied from a draft carries nobody's, and the two
    look identical on the clone unless something says so.
    """

    object_type: ObjectType
    source_object_id: str
    source_version: int
    source_state: LifecycleState
    new_object_id: str

    @property
    def was_approved(self) -> bool:
        return self.source_state in {LifecycleState.APPROVED, LifecycleState.PUBLISHED}

    @property
    def warning(self) -> str:
        """What the review screen says about an unapproved inheritance."""
        return (
            f"{self.object_type.value} {self.new_object_id!r} was copied from "
            f"{self.source_object_id}@v{self.source_version}, which is "
            f"{self.source_state.value} — nobody has approved it. You are inheriting a "
            "draft, not a decision."
        )


@dataclass(frozen=True)
class Difference:
    """One field where the clone and its original disagree.

    The differences PANEL is the story's acceptance criterion, and it is
    computed rather than described: a BA cloning Centene Medicare from
    Medicaid needs to see the four things they changed, not re-read forty
    fields to find them.
    """

    object_type: ObjectType
    field_path: str
    original: Any
    clone: Any


@dataclass(frozen=True)
class CloneResult:
    """Everything the clone produced, and everything a reviewer should know."""

    feed_id: str
    cloned_from: str
    cloned_from_version: int
    objects: tuple[GovernedObject, ...] = ()
    inherited: tuple[Inherited, ...] = ()
    differences: tuple[Difference, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(item.warning for item in self.inherited if not item.was_approved)

    @property
    def feed(self) -> GovernedObject:
        for obj in self.objects:
            if obj.object_type is ObjectType.FEED:
                return obj
        raise CloneError("a clone with no feed in it")

    @property
    def is_all_draft(self) -> bool:
        """Asserted as a property rather than trusted: clones are always Draft."""
        return all(obj.lifecycle_state is LifecycleState.DRAFT for obj in self.objects)


def clone_feed(
    original: GovernedObject,
    related: Sequence[GovernedObject],
    *,
    new_feed_id: str,
    author: Actor,
    overrides: dict[str, Any] | None = None,
    include: frozenset[ObjectType] | None = None,
    now: datetime | None = None,
) -> CloneResult:
    """Copy a feed and its configuration into fresh drafts.

    `related` is the registry's LATEST version of everything; this function
    picks out what references the original feed by reading `core.impact`'s
    declared edges. Passing the whole registry rather than a pre-filtered list
    is deliberate — a caller that filtered first would decide what counts as
    related, and that decision belongs to the reference graph.
    """
    if original.object_type is not ObjectType.FEED:
        raise CloneError(f"{original.object_type} is not a feed — only feeds are cloned")
    if new_feed_id == original.object_id:
        raise CloneError(
            f"{new_feed_id!r} is the feed being cloned. A clone onto its own id is an edit, "
            "and an edit is a new version — use that instead."
        )
    if not new_feed_id.strip():
        raise CloneError("a clone needs an id of its own")

    stamp = now or datetime.now(UTC)
    changes = dict(overrides or {})
    wanted = include if include is not None else frozenset(ObjectType)

    feed_body = _clone_body(original.body, changes)
    feed = _fresh(original, new_object_id=new_feed_id, body=feed_body, author=author, now=stamp)

    objects = [feed]
    inherited = [
        Inherited(
            object_type=ObjectType.FEED,
            source_object_id=original.object_id,
            source_version=original.version,
            source_state=original.lifecycle_state,
            new_object_id=new_feed_id,
        )
    ]
    differences = list(
        _differences(ObjectType.FEED, original.body, feed_body, skip=BODY_NEVER_CLONED)
    )

    for child in _children_of(original.object_id, related):
        if child.object_type not in wanted:
            continue
        # The child's own id follows the feed's wherever it WAS the feed's —
        # a contract is keyed by feed_id, so the cloned contract is keyed by
        # the clone's. A child with an id of its own keeps its shape and gains
        # the new feed's prefix, so two feeds' rules cannot collide.
        child_id = (
            new_feed_id
            if child.object_id == original.object_id
            else f"{new_feed_id}:{child.object_id}"
        )
        body = _clone_body(child.body, {}, feed_id=new_feed_id, old_feed_id=original.object_id)
        objects.append(_fresh(child, new_object_id=child_id, body=body, author=author, now=stamp))
        inherited.append(
            Inherited(
                object_type=child.object_type,
                source_object_id=child.object_id,
                source_version=child.version,
                source_state=child.lifecycle_state,
                new_object_id=child_id,
            )
        )

    return CloneResult(
        feed_id=new_feed_id,
        cloned_from=original.object_id,
        cloned_from_version=original.version,
        objects=tuple(objects),
        inherited=tuple(inherited),
        differences=tuple(differences),
    )


def _children_of(feed_id: str, registry: Sequence[GovernedObject]) -> tuple[GovernedObject, ...]:
    """Everything that names this feed, found through the declared edges.

    Reads `core.impact.REFERENCES` rather than a list of types written here,
    so a new object type that references a feed is cloned by declaring its
    edge — and a type that stops referencing a feed stops being cloned,
    without anybody remembering to come back to this file.
    """
    found: list[GovernedObject] = []
    for candidate in registry:
        if candidate.object_type is ObjectType.FEED:
            continue
        for spec in REFERENCES.get(candidate.object_type, ()):
            if spec.target_type is not ObjectType.FEED:
                continue
            value = candidate.body.get(spec.body_key)
            names = value if spec.many and isinstance(value, list) else [value]
            if feed_id in [str(name) for name in names if name is not None]:
                found.append(candidate)
                break
    return tuple(sorted(found, key=lambda o: (o.object_type.value, o.object_id)))


def _clone_body(
    body: dict[str, Any],
    overrides: dict[str, Any],
    *,
    feed_id: str | None = None,
    old_feed_id: str | None = None,
) -> dict[str, Any]:
    """The configuration, minus what belongs to the original file.

    Overrides are applied ONE LEVEL DEEP for `operations`, so a BA cloning
    Centene Medicare from Medicaid can change the line of business and the
    endpoint without restating the owners, the SLA and the alert chain.
    """
    cloned = {key: value for key, value in body.items() if key not in BODY_NEVER_CLONED}
    if feed_id is not None and old_feed_id is not None:
        for key, value in list(cloned.items()):
            if value == old_feed_id:
                cloned[key] = feed_id
    for key, value in overrides.items():
        if key == "operations" and isinstance(value, dict):
            cloned["operations"] = {**(cloned.get("operations") or {}), **value}
        else:
            cloned[key] = value
    if feed_id is not None:
        cloned["feed_id"] = feed_id
    return cloned


def _fresh(
    original: GovernedObject,
    *,
    new_object_id: str,
    body: dict[str, Any],
    author: Actor,
    now: datetime,
) -> GovernedObject:
    """A first draft, at v1, authored by whoever pressed clone.

    Written as an explicit construction rather than a `replace(...)` on the
    original, deliberately: `replace` carries every field it is not told
    about, so a new field on `GovernedObject` would be inherited silently.
    Here, a new field is a type error until somebody decides about it — which
    is the same reason `REPLACED`/`INHERITED`/`RESET` exist above.
    """
    return GovernedObject(
        object_type=original.object_type,
        object_id=new_object_id,
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=now,
        body=body,
        approved_by=None,
        approved_ts=None,
    )


def _differences(
    object_type: ObjectType,
    original: dict[str, Any],
    cloned: dict[str, Any],
    *,
    skip: frozenset[str] = frozenset(),
    prefix: str = "",
) -> tuple[Difference, ...]:
    """Field-by-field, one level into nested dictionaries.

    One level is enough for the panel and stops the diff exploding into forty
    lines about a contract's column list — which is a change a reviewer reads
    on the contract's own screen, not here.
    """
    found: list[Difference] = []
    for key in sorted(set(original) | set(cloned)):
        if key in skip:
            continue
        left, right = original.get(key), cloned.get(key)
        if left == right:
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            found.extend(
                _differences(object_type, left, right, skip=skip, prefix=f"{prefix}{key}.")
            )
            continue
        found.append(
            Difference(
                object_type=object_type, field_path=f"{prefix}{key}", original=left, clone=right
            )
        )
    return tuple(found)


def differences_between(original: GovernedObject, other: GovernedObject) -> tuple[Difference, ...]:
    """The panel, for two objects that already exist.

    Used by the clone screen after the fact and by CF-V1-E3-04's version
    compare — the same computation, so "how does this differ" has one answer
    in the platform rather than one per screen.
    """
    return _differences(original.object_type, original.body, other.body)


#: Re-exported so a caller can restate the classification in a test without
#: reaching into a private name.
FIELD_CLASSIFICATION: dict[str, frozenset[str]] = {
    "replaced": REPLACED,
    "inherited": INHERITED,
    "reset": RESET,
}


def carries_no_approval(obj: GovernedObject) -> bool:
    """Whether an object is a first draft nobody has signed.

    The property every cloned object must have, expressed once so the clone
    function, the API route and the tests all mean the same thing by "clones
    are always Draft".
    """
    return (
        obj.version == 1
        and obj.lifecycle_state is LifecycleState.DRAFT
        and obj.approved_by is None
        and obj.approved_ts is None
    )
