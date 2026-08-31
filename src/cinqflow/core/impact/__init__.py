"""CF-V1-E11-02 — the approval packet, and impact computed from LINEAGE.

    "Compute impact automatically from lineage — approvers never depend on the
     author remembering what their change touches."
    "Given lineage cannot determine impact for one downstream item, when the
     packet is built, then that item is listed as 'impact unknown — needs
     manual check', and production approval is BLOCKED until someone resolves
     it."
    — CF-V1-E11-02

Rubber-stamping is the classic failure of approval systems, and it has two
causes: a packet that shows nothing, and a packet that shows a blank where
something should be. This module refuses both.

THE REFERENCE GRAPH IS DATA. `REFERENCES` declares which body key of which
object type points at which other type. Impact is then "everything that
references this, transitively" — a graph walk, never a list somebody typed
into a change request. An author who forgets to mention the four jobs their
mapping feeds does not thereby hide them.

THE UNKNOWN IS FIRST-CLASS. A declared consumer that resolves to nothing the
platform knows is not silently dropped and not counted as zero: it is an
`Unknown`, and `ImpactPacket.blocks_production` is True while any exists.
"An approval with an empty impact section presented as if impact were zero" is
the documented don't this exists to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cinqflow.core.model.governed import GovernedObject, LifecycleState, ObjectType


@dataclass(frozen=True)
class ReferenceSpec:
    """One edge type: "a MAPPING's `feed_id` body key points at a FEED".

    Declared rather than coded so that adding an object type means adding a
    row here — and the completeness test then makes the omission visible,
    instead of the new type quietly having no lineage.
    """

    body_key: str
    target_type: ObjectType
    many: bool = False


#: The reference graph, as data. Read: "objects of this type reference…".
REFERENCES: dict[ObjectType, tuple[ReferenceSpec, ...]] = {
    ObjectType.SOURCE: (),
    ObjectType.FEED: (ReferenceSpec("source_id", ObjectType.SOURCE),),
    ObjectType.CONTRACT: (ReferenceSpec("feed_id", ObjectType.FEED),),
    ObjectType.MAPPING: (
        ReferenceSpec("feed_id", ObjectType.FEED),
        ReferenceSpec("contract_id", ObjectType.CONTRACT),
        ReferenceSpec("glossary_ids", ObjectType.GLOSSARY_TERM, many=True),
    ),
    ObjectType.DQ_RULE: (
        ReferenceSpec("feed_id", ObjectType.FEED),
        ReferenceSpec("contract_id", ObjectType.CONTRACT),
        ReferenceSpec("glossary_ids", ObjectType.GLOSSARY_TERM, many=True),
    ),
    ObjectType.GLOSSARY_TERM: (),
    ObjectType.RUNBOOK: (ReferenceSpec("feed_id", ObjectType.FEED),),
    ObjectType.RELEASE: (),
    ObjectType.PROMPT: (),
    ObjectType.EXECUTION_PLANE_CONTRACT: (),
    #: `feed_id` is OPTIONAL on a document's body (E16-04's client specs are
    #: not always feed-scoped; E16-06's wizard uploads always are) — the same
    #: shape RUNBOOK's own optional `feed_id` already has, and the reference
    #: resolver already treats a missing key as no edge, not an Unknown.
    ObjectType.KNOWLEDGE_DOCUMENT: (ReferenceSpec("feed_id", ObjectType.FEED),),
}

#: Body key naming the downstream business consumers a change would reach —
#: reports, metrics, executive outputs. Each entry is resolved against the
#: registry; one that resolves to nothing becomes an Unknown rather than a
#: silent zero.
CONSUMERS_KEY = "business_consumers"


@dataclass(frozen=True)
class Touched:
    """One object a change would reach, and why it is in the list."""

    object_type: ObjectType
    object_id: str
    version: int
    lifecycle_state: LifecycleState
    via: str
    """The reference path that reached it — so an approver can check the
    reasoning rather than trust the count."""


@dataclass(frozen=True)
class Unknown:
    """A declared consumer lineage could not resolve.

    "impact unknown — needs manual check" is shown EXPLICITLY and blocks
    production approval. A blank where a downstream item should be is the
    failure mode this type exists to prevent.
    """

    name: str
    reason: str = "impact unknown — needs manual check"


@dataclass(frozen=True)
class ImpactPacket:
    """What an approver sees: the change, both sides of its impact, and the
    evidence — on one screen, computed, never typed."""

    object_type: ObjectType
    object_id: str
    version: int
    lifecycle_state: LifecycleState
    author_subject: str
    diff: tuple[str, ...] = ()
    engineering_impact: tuple[Touched, ...] = ()
    business_impact: tuple[Touched, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def blocks_production(self) -> bool:
        """True while any impact is unknown. The approve path asks this, so a
        packet with a hole in it cannot be signed."""
        return bool(self.unknowns)

    @property
    def is_empty(self) -> bool:
        """Nothing downstream — a real and reportable answer, distinct from
        "we could not tell", which is what `unknowns` carries."""
        return not (self.engineering_impact or self.business_impact or self.unknowns)


class ImpactUnknownError(RuntimeError):
    """Approval attempted while the packet still has a hole in it."""


def _latest_by_key(
    objects: tuple[GovernedObject, ...],
) -> dict[tuple[ObjectType, str], GovernedObject]:
    latest: dict[tuple[ObjectType, str], GovernedObject] = {}
    for obj in objects:
        key = (obj.object_type, obj.object_id)
        if key not in latest or obj.version > latest[key].version:
            latest[key] = obj
    return latest


def _targets(obj: GovernedObject) -> set[tuple[ObjectType, str]]:
    """Everything this object points AT, per the declared reference graph."""
    found: set[tuple[ObjectType, str]] = set()
    for spec in REFERENCES.get(obj.object_type, ()):
        value = obj.body.get(spec.body_key)
        if value is None:
            continue
        names = value if spec.many and isinstance(value, list | tuple) else [value]
        found.update((spec.target_type, str(name)) for name in names if name)
    return found


def dependents_of(
    target: GovernedObject, objects: tuple[GovernedObject, ...]
) -> tuple[Touched, ...]:
    """Everything that references the target, transitively.

    Retired objects are excluded — a retired mapping is not affected by a
    change to the feed it used to read, and listing it would pad the packet
    with noise that trains approvers to skim.
    """
    latest = _latest_by_key(objects)
    reached: dict[tuple[ObjectType, str], Touched] = {}
    frontier = {(target.object_type, target.object_id)}
    while frontier:
        next_frontier: set[tuple[ObjectType, str]] = set()
        for key, candidate in latest.items():
            if key in reached or key == (target.object_type, target.object_id):
                continue
            if candidate.lifecycle_state is LifecycleState.RETIRED:
                continue
            hit = _targets(candidate) & frontier
            if not hit:
                continue
            source_type, source_id = next(iter(sorted(hit)))
            reached[key] = Touched(
                object_type=candidate.object_type,
                object_id=candidate.object_id,
                version=candidate.version,
                lifecycle_state=candidate.lifecycle_state,
                via=f"{source_type.value}:{source_id}",
            )
            next_frontier.add(key)
        frontier = next_frontier
    return tuple(sorted(reached.values(), key=lambda t: (t.object_type.value, t.object_id)))


def build_packet(
    target: GovernedObject,
    objects: tuple[GovernedObject, ...],
    *,
    evidence: dict[str, object] | None = None,
) -> ImpactPacket:
    """The packet, computed. Nothing here is supplied by the author except the
    body they wrote and the evidence their test run produced."""
    touched = dependents_of(target, objects)
    # The split is by AUDIENCE, not by table: a glossary term is what a
    # business person recognises ("Member Date of Birth"), a mapping or
    # contract is what an engineer does. Same graph, two readings — which is
    # ValueBridge's "one packet carries both sides".
    business_types = {ObjectType.GLOSSARY_TERM, ObjectType.RUNBOOK}
    business = tuple(t for t in touched if t.object_type in business_types)
    engineering = tuple(t for t in touched if t.object_type not in business_types)

    known = {obj.object_id for obj in objects} | {str(obj.body.get("name", "")) for obj in objects}
    declared = target.body.get(CONSUMERS_KEY) or []
    unknowns = tuple(Unknown(name=str(name)) for name in declared if str(name) not in known)

    previous = [
        o
        for o in objects
        if (o.object_type, o.object_id) == (target.object_type, target.object_id)
        and o.version == target.version - 1
    ]
    return ImpactPacket(
        object_type=target.object_type,
        object_id=target.object_id,
        version=target.version,
        lifecycle_state=target.lifecycle_state,
        author_subject=target.created_by.subject,
        diff=diff_bodies(previous[0].body if previous else {}, target.body),
        engineering_impact=engineering,
        business_impact=business,
        unknowns=unknowns,
        evidence=dict(evidence or {}),
    )


def diff_bodies(before: dict[str, object], after: dict[str, object]) -> tuple[str, ...]:
    """The change, in plain language. Business-readable by design: an approver
    reading "schedule_cron: 0 6 * * 1 -> 0 6 1 * *" needs no JSON tooling."""
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key), after.get(key)
        if was == now:
            continue
        if key not in before:
            lines.append(f"{key}: added {now!r}")
        elif key not in after:
            lines.append(f"{key}: removed (was {was!r})")
        else:
            lines.append(f"{key}: {was!r} -> {now!r}")
    return tuple(lines)


def refuse_if_unknown(packet: ImpactPacket) -> None:
    """The gate E11-02 requires. Called before an approval is allowed to land.

    An approver may not sign a packet with a hole in it — not because the
    change is wrong, but because nobody can say whether it is.
    """
    if packet.blocks_production:
        names = ", ".join(u.name for u in packet.unknowns)
        raise ImpactUnknownError(
            f"impact unknown — needs manual check: {names}. Production approval is blocked "
            "until someone resolves it. An empty impact section presented as if impact were "
            "zero is how rubber-stamping hides."
        )
