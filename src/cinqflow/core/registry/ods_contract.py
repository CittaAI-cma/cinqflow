"""CF-V3-E10-02 — the downstream data contract.

    "I want the model browser extended with version history and a downstream
     contract view — what downstream consumers may rely on, what changed
     between versions, what is deprecated ... so that downstream teams build
     against a stated contract rather than reverse-engineering tables."
    "Given a proposed change would remove a column two reports consume, when
     the proposal is drafted, then lineage lists the consumers on the
     proposal automatically, and their owners are notified as part of the
     review — not after the break."
    — CF-V3-E10-02

NO NEW LINEAGE GRAPH. `core.impact.REFERENCES` deliberately leaves
`ObjectType.ODS_MODEL` with no edges ("the thing a future MAPPING or CONTRACT
will reference, never the reverse") — but that graph resolves whole-object
references by exact `object_id`, and an ODS entity/column is not a governed
object of its own; it is a name inside the ONE `silver_ods` object's body.
Forcing that graph to carry a field-level edge would bend a mechanism built
for "mapping references feed X" onto a question it was not shaped to answer.

Two facts already exist and say everything E10-02 needs without a new one:
a mapping LINE already names the exact ODS column it populates
(`target_entity`/`target_field`, CF-V1-E6-03), and a mapping's own
`business_consumers` key already names the downstream reports that depend on
it (CF-V1-E11-02's `CONSUMERS_KEY`). "Which reports break if I drop
`Members.LegacySsn`" is those two facts joined — new composition, no new
data model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from cinqflow.core.model.governed import GovernedObject, LifecycleState, ObjectType
from cinqflow.core.registry.ods_model import ChangeKind, FieldChange, OdsModel, OdsModelDiff
from cinqflow.core.schema_spec import Column

#: Non-terminal states a version can sit in while it is still a PROPOSAL —
#: visible to a reviewer, never presented as current. Mirrors the states
#: `core.lifecycle.TRANSITIONS` treats as "not yet Published, not abandoned".
_PENDING_STATES = frozenset(
    {LifecycleState.DRAFT, LifecycleState.PENDING_REVIEW, LifecycleState.APPROVED}
)

#: The whole-entity marker `ods_model.diff()` uses in `FieldChange.column`
#: for `NEW_ENTITY`/`DEPRECATED_ENTITY` — no single column to look consumers
#: up against.
_WHOLE_ENTITY = "*"


@dataclass(frozen=True)
class Consumer:
    """One mapping that populates an ODS column, and the business-named
    reports it declares depend on that mapping. An empty
    `business_consumers` is a real, reportable answer — the mapping itself
    still consumes the column even if it names no downstream report."""

    mapping_id: str
    business_consumers: tuple[str, ...] = ()


def consumers_of(
    entity: str, column: str, mappings: Sequence[GovernedObject]
) -> tuple[Consumer, ...]:
    """Every non-retired mapping that targets `entity.column`, and what it
    says relies on that value. Reads the same body vocabulary
    `core.mapping.mapping_body()` writes, without decoding into
    `FeedMapping` — the same distance `core.impact._targets` keeps from the
    types it reads."""
    found: list[Consumer] = []
    for obj in mappings:
        if obj.object_type is not ObjectType.MAPPING:
            continue
        if obj.lifecycle_state is LifecycleState.RETIRED:
            continue
        lines = obj.body.get("lines") or []
        targets = any(
            line.get("target_entity") == entity and line.get("target_field") == column
            for line in lines
        )
        if targets:
            consumers = tuple(str(c) for c in (obj.body.get("business_consumers") or []))
            found.append(Consumer(mapping_id=obj.object_id, business_consumers=consumers))
    return tuple(found)


def all_consumers_of(
    entity: str, columns: Sequence[str], mappings: Sequence[GovernedObject]
) -> tuple[Consumer, ...]:
    """Every consumer of ANY column this entity carries, deduplicated by
    mapping — CF-V3-E10-03's "notify registered consumers on publication,
    per the registry's dependency declarations" asks a whole-entity
    question ("who consumes `Members` at all"), not `consumers_of`'s own
    single-column deprecation question. Composes it rather than replacing
    it: a mapping that targets three of an entity's columns is still ONE
    consumer to notify, not three."""
    merged: dict[str, Consumer] = {}
    for column in columns:
        for consumer in consumers_of(entity, column, mappings):
            if consumer.mapping_id not in merged:
                merged[consumer.mapping_id] = consumer
                continue
            existing = merged[consumer.mapping_id]
            combined = tuple(
                dict.fromkeys((*existing.business_consumers, *consumer.business_consumers))
            )
            merged[consumer.mapping_id] = Consumer(
                mapping_id=consumer.mapping_id, business_consumers=combined
            )
    return tuple(merged.values())


@dataclass(frozen=True)
class DeprecationNotice:
    """One removal a PROPOSED version would make, with its real, computed
    blast radius — never an author's guess at who is affected."""

    change: FieldChange
    consumers: tuple[Consumer, ...] = ()

    @property
    def is_breaking(self) -> bool:
        return bool(self.consumers)


def deprecations_in(
    proposed_diff: OdsModelDiff, mappings: Sequence[GovernedObject]
) -> tuple[DeprecationNotice, ...]:
    """Every removal in a diff, each with the consumers computed against the
    CURRENT mapping registry — so a reviewer sees who breaks before
    approving, not who broke after publishing."""
    notices: list[DeprecationNotice] = []
    for change in proposed_diff.removed:
        if change.column == _WHOLE_ENTITY or change.kind is ChangeKind.DEPRECATED_ENTITY:
            notices.append(DeprecationNotice(change=change))
            continue
        notices.append(
            DeprecationNotice(
                change=change, consumers=consumers_of(change.entity, change.column, mappings)
            )
        )
    return tuple(notices)


def latest_published(history: Sequence[GovernedObject]) -> GovernedObject | None:
    """The version a downstream consumer may actually rely on — never the
    highest version number alone, which may still be a proposal."""
    published = [obj for obj in history if obj.lifecycle_state is LifecycleState.PUBLISHED]
    return max(published, key=lambda obj: obj.version, default=None)


def pending_version(history: Sequence[GovernedObject]) -> GovernedObject | None:
    """The proposal in flight, if one exists — Draft, In Review or Approved,
    never Published or Retired. "Present a proposed model change as current
    before its release" is the don't this stays separate from
    `latest_published` to make impossible."""
    pending = [obj for obj in history if obj.lifecycle_state in _PENDING_STATES]
    return max(pending, key=lambda obj: obj.version, default=None)


@dataclass(frozen=True)
class ContractPage:
    """The stable, linkable page for one entity. CF-V3-E10-02's own words:
    "every entity a stable contract page downstream teams can link to."

    Keyed by entity NAME alone, never by version — that is what makes the
    link stable while the model underneath it moves.
    """

    entity: str
    model_version: int
    columns: tuple[Column, ...]
    consumers: dict[str, tuple[Consumer, ...]] = field(default_factory=dict)
    pending_deprecations: tuple[DeprecationNotice, ...] = ()


def contract_page(
    model: OdsModel,
    entity: str,
    mappings: Sequence[GovernedObject],
    *,
    pending_deprecations: Sequence[DeprecationNotice] = (),
) -> ContractPage:
    """`model` should be the LATEST PUBLISHED model — the page shows what a
    downstream team may build against today, never a proposal."""
    found = model.entity(entity)
    consumers = {
        column.name: consumers_of(entity, column.name, mappings) for column in found.columns
    }
    return ContractPage(
        entity=entity,
        model_version=model.version,
        columns=found.columns,
        consumers=consumers,
        pending_deprecations=tuple(n for n in pending_deprecations if n.change.entity == entity),
    )
