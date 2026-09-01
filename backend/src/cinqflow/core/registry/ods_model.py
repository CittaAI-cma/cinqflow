"""CF-V3-E10-01/E10-02 — the canonical ODS model as versioned, governed truth.

    "the client's canonical model workbooks — Member, Enrollment Segments,
     Claims and Claim Lines, Diagnosis/Procedure bridges, Provider, Encounter
     — deployed as versioned, managed table definitions with surrogate keys,
     source-key retention and history handling, so that the model stops
     living in contested spreadsheets ('draft' vs 'final') and becomes one
     deployed, versioned truth every mapping targets."
    — CF-V3-E10-01

    "Show 'what changed and why' between any two model versions in terms a
     consumer understands ... every entity a stable contract page."
    — CF-V3-E10-02

    "model_rules: surrogate keys generated, source identifiers always
     retained; history per entity: current_only | effective_dated; every
     batch records the model version it loaded into"
    — docs/architecture/plates/10-silver-ods-canonical-model.md

THE DESIGN DECISION, and why it earns the indirection: `schema_spec.py`
declares `SILVER_ODS_SCHEMA` as a Python literal, provisioned empty since Wave
0 with a docstring pointing here. The tempting fix is to fill that literal in
directly — but E10-01 does not ask for a bigger literal, it asks for a
VERSIONED, APPROVABLE model whose draft-vs-final disagreements get a recorded
decision, and E10-02 asks for a diff between two versions "in terms a consumer
understands". A frozen dataclass in one file has exactly one version — the one
in git — and nowhere to put a steward's rationale.

So the canonical ODS model travels the SAME lifecycle every other piece of
platform configuration already does (`core.model.governed`), exactly as
`core.registry.contract.SchemaContract` does for a feed's schema. `render()`
is the one PURE function that turns a published `OdsModel` into the same
Schema/Table/Column vocabulary schema_spec already declares everything else
in — so the conformance kit's existing "engine vs spec" comparison checks
silver_ods with no new code, and a Databricks compute adapter renders the same
DDL from the same Schema with no plate change.

NOT TO BE CONFUSED WITH `core.registry.canonical.CanonicalModel` (CF-V1-E6-01)
— that is a READ-ONLY BROWSER PROJECTION generated from whatever is deployed
plus the glossary, built for people to search. This module is the thing that
browser reads FROM once a version of it is published: the SOURCE, not the
view. Two different questions ("what does the estate look like today" vs
"what is the approved, versioned truth") on purpose.

TWO REAL DISCREPANCIES, RESOLVED ONCE, APPLIED EVERYWHERE. Harvesting
`Enrollment_Lake_Models.xlsx` (`core.registry.ods_model_member_domain`)
surfaced two places the workbook disagrees with a convention the platform has
already deployed for the identical concept:

  1. A `datetime` column naming a date-only business fact (`DateOfBirth`,
     `EffectiveStartDate`, ...) vs. `silver_raw.members.date_of_birth`,
     already deployed as DATE for the same concept, with no time component.
  2. `BatchId` typed `int` in the workbook vs. `batch_id` typed STRING
     everywhere else in the platform — every control table, bronze, and
     silver_raw.

Both are recorded as `ModelDiscrepancy` with a `Decision`, once, in the seed
module — not fifteen separate tickets for every column the same two questions
touch. A decision, once made, is a STANDING CONVENTION for the next column
that asks the same question; re-opening it every time would be re-litigating,
not reviewing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique

from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName


class OdsModelError(RuntimeError):
    """The canonical ODS model refused something."""


class MissingEffectiveDatingError(OdsModelError):
    """An entity declares EFFECTIVE_DATED history but names no column that
    could carry it.

    "History per entity is declared: current_only or effective_dated. Both are
    property-tested" (plate 10) — this is that property, enforced at
    construction rather than discovered the day someone asks why an
    effective-dated satellite has no way to be effective-dated.
    """


class UndecidedDiscrepancyError(OdsModelError):
    """A workbook draft-vs-final discrepancy has no recorded decision.

    Raised at DEPLOY time, never at authoring time — a model may be BUILT
    with open discrepancies (that is what review is for); it may not be
    DEPLOYED with one. CF-V3-E10-01's exception, verbatim: "deployment waits
    for the steward's call, which is recorded with rationale."
    """


@unique
class HistoryMode(StrEnum):
    """SCD-1 or SCD-2, per entity — never inferred from the columns present,
    because "the columns imply it" is exactly how an entity ends up
    effective-dated in the data and current-only in nobody's declaration."""

    CURRENT_ONLY = "current_only"
    EFFECTIVE_DATED = "effective_dated"


#: Column names that satisfy "this entity can actually be effective-dated".
#: Both spellings are accepted because the ODS model preserves the client's
#: own PascalCase naming (this table replaces their legacy SQL Server system
#: directly), while a future entity harvested closer to platform convention
#: may spell it snake_case — the property is about the CAPABILITY existing,
#: not about which vocabulary named it.
_EFFECTIVE_DATING_COLUMNS = frozenset({"EffectiveStartDate", "effective_date"})


@dataclass(frozen=True)
class OdsEntity:
    """One table of the canonical model.

    Columns reuse schema_spec's OWN closed type vocabulary — not a parallel
    `OdsColumn` — so a rendered entity is byte-identical in shape to a
    hand-written schema_spec.Table, and the conformance kit that checks every
    other data-layer table needs no ODS-specific case.
    """

    name: str
    columns: tuple[Column, ...]
    surrogate_key: str
    history_mode: HistoryMode
    #: Source identifiers retained BESIDE the surrogate key — model rule #1.
    #: A satellite with none of these can update in place but can never be
    #: traced back to the file it came from.
    source_key_columns: tuple[str, ...] = ()
    #: The parent entity this satellite belongs to, or None for the spine.
    satellite_of: str | None = None
    comment: str = ""

    def __post_init__(self) -> None:
        names = {c.name for c in self.columns}
        if self.surrogate_key not in names:
            raise OdsModelError(
                f"{self.name}: surrogate key {self.surrogate_key!r} is not a declared column"
            )
        for key in self.source_key_columns:
            if key not in names:
                raise OdsModelError(f"{self.name}: source key {key!r} is not a declared column")
        if self.history_mode is HistoryMode.EFFECTIVE_DATED and not (
            names & _EFFECTIVE_DATING_COLUMNS
        ):
            raise MissingEffectiveDatingError(
                f"{self.name} is declared effective_dated but carries none of "
                f"{sorted(_EFFECTIVE_DATING_COLUMNS)} — an entity cannot be effective-dated by "
                "name alone."
            )

    def column(self, name: str) -> Column:
        for candidate in self.columns:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.name} has no column {name!r}")

    @property
    def signature(self) -> str:
        columns = ",".join(c.signature for c in self.columns)
        keys = ",".join(self.source_key_columns)
        return (
            f"{self.name}[{columns}]sk({self.surrogate_key})src({keys})"
            f"hist({self.history_mode.value})sat({self.satellite_of or ''})"
        )


@dataclass(frozen=True)
class OdsModel:
    """Every entity of the canonical ODS model, at one version.

    Versioned like `SchemaContract`, fingerprinted like `Schema` — this is the
    governed value `schema_spec.SILVER_ODS_SCHEMA` has been provisioned empty
    since Wave 0 waiting for.
    """

    version: int
    entities: tuple[OdsEntity, ...]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("versions start at 1")

    def entity(self, name: str) -> OdsEntity:
        for candidate in self.entities:
            if candidate.name == name:
                return candidate
        raise KeyError(f"v{self.version} has no entity {name!r}")

    @property
    def fingerprint(self) -> str:
        """Stable across processes and independent of entity ORDER, so a
        conformance report can name WHICH version an engine was checked
        against. A green result against an unnamed version is unfalsifiable."""
        material = f"v{self.version}|" + "|".join(sorted(e.signature for e in self.entities))
        return hashlib.sha256(material.encode()).hexdigest()[:32]


def render(model: OdsModel) -> Schema:
    """The deployed shape, and nothing render() did not name.

    Pure — no I/O, no enrichment — so it belongs beside the model it reads,
    not in the installer that eventually calls it. A Databricks compute
    adapter renders its own DDL from the SAME Schema this returns; nothing
    here is engine-specific, which is the whole point of declaring the type
    vocabulary once.
    """
    return Schema(
        name="silver_ods",
        description=f"The canonical, member-centric model — v{model.version}.",
        tables=tuple(
            Table(
                name=entity.name,
                columns=entity.columns,
                primary_key=(entity.surrogate_key,),
                comment=entity.comment,
            )
            for entity in model.entities
        ),
    )


# ── the discrepancy gate — deploy waits for the steward's call ──────────────


@dataclass(frozen=True)
class Decision:
    """A steward's call on one workbook discrepancy, recorded with rationale.

    A `Decision` with no rationale is not a decision, it is a guess with a
    name attached — refused at construction rather than discovered the day
    someone asks why a column is shaped the way it is.
    """

    chosen: str
    decided_by: Actor
    rationale: str
    decided_ts: datetime

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise OdsModelError(
                f"{self.decided_by.subject}'s decision for {self.chosen!r} carries no rationale"
            )


@dataclass(frozen=True)
class ModelDiscrepancy:
    """One place two sources of truth about the model disagreed.

    `sources` names BOTH origins and BOTH values — a steward reviewing this
    needs to see where each value came from, not just that they differ.
    Undecided by default: a discrepancy is opened by discovery, not by
    decision.
    """

    entity: str
    column: str
    sources: tuple[tuple[str, str], ...]
    decision: Decision | None = None

    @property
    def is_decided(self) -> bool:
        return self.decision is not None


def refuse_undecided(discrepancies: Sequence[ModelDiscrepancy]) -> None:
    """Block deployment while any discrepancy has no recorded decision.

    Never silently deploys the newer, the older or the workbook's own
    spelling — the whole point of the gate is that a human reads the list and
    the platform can prove one did.
    """
    undecided = [d for d in discrepancies if not d.is_decided]
    if undecided:
        names = ", ".join(f"{d.entity}.{d.column}" for d in undecided)
        raise UndecidedDiscrepancyError(
            f"{len(undecided)} discrepancy(ies) undecided: {names} — deployment waits for the "
            "steward's call."
        )


# ── the governed round trip — an ODS model is a GovernedObject like any other ─


def as_governed(
    model: OdsModel, *, author: Actor, created_ts: datetime | None = None
) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.ODS_MODEL,
        object_id="silver_ods",
        version=model.version,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=author,
        created_ts=created_ts or datetime.now(UTC),
        body={
            "entities": [
                {
                    "name": entity.name,
                    "surrogate_key": entity.surrogate_key,
                    "history_mode": entity.history_mode.value,
                    "source_key_columns": list(entity.source_key_columns),
                    "satellite_of": entity.satellite_of,
                    "comment": entity.comment,
                    "columns": [
                        {
                            "name": column.name,
                            "type": column.type.value,
                            "nullable": column.nullable,
                            "precision": column.precision,
                            "scale": column.scale,
                            "is_phi": column.is_phi,
                            "comment": column.comment,
                        }
                        for column in entity.columns
                    ],
                }
                for entity in model.entities
            ]
        },
    )


def from_governed(obj: GovernedObject) -> OdsModel:
    if obj.object_type is not ObjectType.ODS_MODEL:
        raise OdsModelError(f"{obj.object_type} is not an ODS model")
    return OdsModel(
        version=obj.version,
        entities=tuple(
            OdsEntity(
                name=entity["name"],
                surrogate_key=entity["surrogate_key"],
                history_mode=HistoryMode(entity["history_mode"]),
                source_key_columns=tuple(entity.get("source_key_columns", ())),
                satellite_of=entity.get("satellite_of"),
                comment=entity.get("comment", ""),
                columns=tuple(
                    Column(
                        name=column["name"],
                        type=TypeName(column["type"]),
                        nullable=bool(column.get("nullable", True)),
                        precision=column.get("precision"),
                        scale=column.get("scale"),
                        is_phi=bool(column.get("is_phi", False)),
                        comment=column.get("comment", ""),
                    )
                    for column in entity["columns"]
                ),
            )
            for entity in obj.body.get("entities", [])
        ),
    )


# ── diff — what a downstream consumer needs to know, never a JSON diff ──────


@unique
class ChangeKind(StrEnum):
    """What CF-V3-E10-02's contract page renders. Never a raw body diff —
    "columns: [...] -> [...]" answers nothing a consumer asked."""

    ADDED = "added"
    REMOVED = "removed"
    RETYPED = "retyped"
    NEW_ENTITY = "new_entity"
    DEPRECATED_ENTITY = "deprecated_entity"


@dataclass(frozen=True)
class FieldChange:
    entity: str
    column: str
    kind: ChangeKind
    was: str = ""
    now: str = ""


@dataclass(frozen=True)
class OdsModelDiff:
    from_version: int
    to_version: int
    changes: tuple[FieldChange, ...]

    @property
    def added(self) -> tuple[FieldChange, ...]:
        return tuple(c for c in self.changes if c.kind in (ChangeKind.ADDED, ChangeKind.NEW_ENTITY))

    @property
    def removed(self) -> tuple[FieldChange, ...]:
        return tuple(
            c for c in self.changes if c.kind in (ChangeKind.REMOVED, ChangeKind.DEPRECATED_ENTITY)
        )


def diff(a: OdsModel, b: OdsModel) -> OdsModelDiff:
    """What changed between two versions, in terms a downstream consumer
    understands — entity and column names, never the stored JSON body."""
    changes: list[FieldChange] = []
    left = {entity.name: entity for entity in a.entities}
    right = {entity.name: entity for entity in b.entities}

    for name in sorted(set(left) | set(right)):
        old_entity, new_entity = left.get(name), right.get(name)
        if old_entity is None:
            changes.append(FieldChange(entity=name, column="*", kind=ChangeKind.NEW_ENTITY))
            continue
        if new_entity is None:
            changes.append(FieldChange(entity=name, column="*", kind=ChangeKind.DEPRECATED_ENTITY))
            continue

        old_columns = {c.name: c for c in old_entity.columns}
        new_columns = {c.name: c for c in new_entity.columns}
        for column_name in sorted(set(old_columns) | set(new_columns)):
            old_column, new_column = old_columns.get(column_name), new_columns.get(column_name)
            if old_column is None and new_column is not None:
                changes.append(
                    FieldChange(
                        entity=name,
                        column=column_name,
                        kind=ChangeKind.ADDED,
                        now=new_column.signature,
                    )
                )
            elif new_column is None and old_column is not None:
                changes.append(
                    FieldChange(
                        entity=name,
                        column=column_name,
                        kind=ChangeKind.REMOVED,
                        was=old_column.signature,
                    )
                )
            elif (
                old_column is not None
                and new_column is not None
                and old_column.signature != new_column.signature
            ):
                changes.append(
                    FieldChange(
                        entity=name,
                        column=column_name,
                        kind=ChangeKind.RETYPED,
                        was=old_column.signature,
                        now=new_column.signature,
                    )
                )

    return OdsModelDiff(from_version=a.version, to_version=b.version, changes=tuple(changes))
