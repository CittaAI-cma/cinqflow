"""The medallion spine, as a thing a screen can render — and the masking that
lets it show rows at all.

    "The medallion spine. Data cannot skip a layer, and cannot advance until
     that layer's gate passes."
    — core/model/vocabulary.Layer

Two jobs, both pure, and the purity is the point: no SQL, no connection, no
information_schema. This module decides WHAT a layer is and WHAT a viewer may
see of a value; `adapters/local/pg_layers.py` decides how to go and fetch it.
That split is what lets the Databricks renderer show the same six layers with
the same masking, and it is what `conformance/lint_core_purity.py` enforces.

WHY THIS MODULE EXISTS AT ALL, given `schema_spec` already declares the
schemas: a schema is not a layer. `control`, `queue`, `audit` and `knowledge`
are real schemas on the plane and none of them is a medallion layer, while
`identity` and `gold` are real layers with no schema on the plane at all. A
screen built from `all_schemas()` would show fifteen boxes and imply the spine
was finished; a screen built from `Layer` alone would show six boxes and imply
three of them held data. SPINE below is the join, stated once.

THE HONESTY RULE. Three of the six layers are not built. They are still on the
spine, each carrying the wave that builds it and the reason it is empty,
because a screen that hides them reads as "the spine is complete" and a screen
that shows them as ordinary empty tables reads as "something is broken". The
third answer — "not built yet, here is when" — is the only one that is true.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any, Protocol, runtime_checkable

from cinqflow.core.model.vocabulary import Gate, Layer
from cinqflow.core.schema_spec import Column, Table, TypeName, all_schemas

#: What a masked character looks like. One glyph, never a run of asterisks
#: whose length leaks the value's length — see `_mask_string`.
BULLET = "•"

#: The masked rendering every PHI value collapses to, regardless of type. It is
#: deliberately NOT value-shaped: an engineer reading a screen must be unable
#: to tell two masked members apart, and "J••• D•••" versus "J••••• D••"
#: tells them apart on name length alone. See the class docstring on
#: `MaskedValue` for why that mattered enough to give up legibility.
MASKED = BULLET * 3


@unique
class LayerStatus(StrEnum):
    """Why a layer is or is not holding data. Three answers, never a bare count.

    The distinction between PROVISIONED_EMPTY and NOT_BUILT is the one that
    earns its keep. `silver_ods` HAS a schema on the plane and zero tables in
    it, on purpose; `gold` has no schema at all. Both render "no data", and
    collapsing them would tell an operator the same thing about a deliberate
    contract and an absent one.
    """

    #: Schema exists, tables exist, the pipeline writes here today.
    BUILT = "built"
    #: Schema exists and is deliberately empty, waiting on a later wave's gate.
    PROVISIONED_EMPTY = "provisioned_empty"
    #: No schema on the plane. The layer is named by the architecture only.
    NOT_BUILT = "not_built"


@dataclass(frozen=True)
class LayerSpec:
    """One position on the spine: what it is for, what guards it, whether it
    exists yet, and which schema on the plane holds it."""

    layer: Layer
    label: str
    #: What this layer is for, in the words the architecture uses.
    purpose: str
    #: The gate that guards entry INTO this layer. `landing` has none — arrival
    #: is not a promotion.
    entry_gate: Gate | None
    status: LayerStatus
    #: The schema on the plane, or None when the layer has none yet.
    schema: str | None
    #: The wave that builds it. 0 for what is already here.
    wave: int
    #: Why it is empty, in one sentence, for the two statuses that need one.
    #: Empty for BUILT — a built layer's emptiness is a data question, not an
    #: architecture question, and answering it here would be a guess.
    absence_reason: str = ""

    @property
    def exists_on_plane(self) -> bool:
        return self.schema is not None

    @property
    def holds_data(self) -> bool:
        return self.status is LayerStatus.BUILT


#: The spine, in order. Adding a member is a plate change, not an edit here —
#: `Layer` is the closed set and `test_layers.py` asserts this covers it
#: exactly, so a seventh layer cannot be introduced by forgetting one.
SPINE: tuple[LayerSpec, ...] = (
    LayerSpec(
        layer=Layer.LANDING,
        label="Landing",
        purpose=(
            "The file as it arrived, byte-faithful and unparsed. Nothing is renamed, decoded, "
            "deduplicated or repaired — so the platform can always prove what was actually sent "
            "on a given date."
        ),
        entry_gate=None,
        status=LayerStatus.BUILT,
        schema="landing_ctl",
        wave=0,
    ),
    LayerSpec(
        layer=Layer.BRONZE,
        label="Bronze",
        purpose=(
            "An untouched copy of the source, parsed into rows but not corrected. Append-only "
            "at the DATABASE layer: an UPDATE or DELETE is refused by a trigger, not by a "
            "convention."
        ),
        entry_gate=Gate.G1,
        status=LayerStatus.BUILT,
        schema="bronze",
        wave=0,
    ),
    LayerSpec(
        layer=Layer.SILVER_RAW,
        label="Silver Raw",
        purpose=(
            "Typed, mapped and rule-evaluated. Every row that did not make it is in quarantine "
            "with the rule that excluded it named — stored, visible and reprocessable, never "
            "dropped."
        ),
        entry_gate=Gate.G2,
        status=LayerStatus.BUILT,
        schema="silver_raw",
        wave=0,
    ),
    LayerSpec(
        layer=Layer.IDENTITY,
        label="Identity",
        purpose=(
            "One member, one identity, across every source. Submitted must equal resolved plus "
            "unresolved plus failed, and an unresolved record never loads — it waits, visibly."
        ),
        entry_gate=Gate.G3,
        # PROVISIONED_EMPTY, not NOT_BUILT, as of the identity schema landing in
        # `schema_spec`. The distinction is the whole reason LayerStatus has
        # three members: the plane now HAS `identity` with its five tables —
        # the crosswalk, both Verato logs and the exception queue — and every
        # one of them is empty because G3 has not been built. Reporting "no
        # schema on the plane" while five tables sit there would be the screen
        # lying about the thing it exists to show.
        status=LayerStatus.PROVISIONED_EMPTY,
        schema="identity",
        wave=3,
        absence_reason=(
            "Provisioned EMPTY: the crosswalk, both Verato logs and the exception queue exist "
            "and hold nothing, because gate G3 and identity resolution are Wave 3 "
            "(CF-V3-E9-*, CF-V3-E10-*). Until G3 runs, no source id has been resolved to a "
            "person, so there is nothing to write."
        ),
    ),
    LayerSpec(
        layer=Layer.SILVER_ODS,
        label="Silver ODS",
        purpose=(
            "The canonical, member-centric model — the shape every downstream consumer reads "
            "instead of reading a source."
        ),
        entry_gate=Gate.G4,
        status=LayerStatus.PROVISIONED_EMPTY,
        schema="silver_ods",
        wave=3,
        absence_reason=(
            "Provisioned EMPTY on purpose: it sits behind G4 identity resolution, which is "
            "Wave 3. A record whose identity is unresolved never loads."
        ),
    ),
    LayerSpec(
        layer=Layer.GOLD,
        label="Gold",
        purpose=(
            "Consumer-shaped marts, published atomically after certification — relationship "
            "validation and consumer compatibility both green, or nothing moves."
        ),
        entry_gate=Gate.G5,
        status=LayerStatus.NOT_BUILT,
        schema=None,
        wave=4,
        absence_reason=(
            "Gate G5 and the published marts are Wave 4. The compiler refuses to plan past its "
            "terminal layer, so nothing can quietly write here first."
        ),
    ),
)

_BY_NAME = {spec.layer.value: spec for spec in SPINE}


def spine() -> tuple[LayerSpec, ...]:
    """The six layers, in promotion order."""
    return SPINE


def spec_of(layer: Layer | str) -> LayerSpec:
    """The spec for a layer, by enum or by its wire name.

    Raises `KeyError` for anything else. The API turns that into a 404 rather
    than defaulting to the first layer — a mistyped layer name that silently
    renders Landing is worse than a not-found.
    """
    key = layer.value if isinstance(layer, Layer) else str(layer)
    if key not in _BY_NAME:
        raise KeyError(
            f"{key!r} is not a medallion layer. The six are: "
            + ", ".join(s.layer.value for s in SPINE)
        )
    return _BY_NAME[key]


def built_layers() -> tuple[LayerSpec, ...]:
    """The layers a reader can actually browse rows in."""
    return tuple(s for s in SPINE if s.holds_data)


def tables_of(spec: LayerSpec) -> tuple[Table, ...]:
    """The tables the CONTRACT declares for this layer, from `schema_spec`.

    Deliberately the spec and not the engine. What the engine actually has is
    the catalog pin's answer, and the two are compared rather than conflated:
    a table the spec declares and the plane lacks is a provisioning gap, and a
    screen reading only the plane would render it as "no such table" — which
    reads as a design decision instead of a missing migration.
    """
    if spec.schema is None:
        return ()
    for schema in all_schemas():
        if schema.name == spec.schema:
            return schema.tables
    return ()


def table_of(spec: LayerSpec, table_name: str) -> Table:
    for table in tables_of(spec):
        if table.name == table_name:
            return table
    raise KeyError(f"{spec.schema} has no table {table_name!r} in the schema contract")


# ── masking ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MaskedValue:
    """A value as a screen may see it, and whether that is the whole truth.

    `masked` travels WITH the value rather than being recomputed in the browser,
    for the same reason every figure carries its citation: a UI that decides
    what to hide is a UI that can be wrong about it, and being wrong once is a
    disclosure that cannot be recalled.

    The masked rendering carries NO shape — not the length, not the first
    character, not the year. The first version of this kept an initial and a
    length hint because it read better on screen ("J••• D•••••"), and that
    version let a reader distinguish members from each other and re-identify a
    known one from a roster they already had. Legibility of a value nobody is
    entitled to see is not a feature.
    """

    #: The rendered value: the real one, or `MASKED`. Never the real one when
    #: `masked` is True — there is no "original" field, on purpose, so a
    #: serializer cannot leak what a renderer hid.
    value: str | None
    masked: bool
    #: Present only when masked: why. Shown as a tooltip, so the rule is
    #: readable at the point of the hiding rather than in a policy document.
    reason: str = ""


#: What a viewer is told about a hidden value. One sentence, because it is
#: rendered in a table cell's title attribute.
PHI_REASON = "flagged is_phi in the schema contract — masked for every viewer"


def mask_cell(column: Column, value: Any) -> MaskedValue:
    """Apply the contract's `is_phi` flag to one value.

    The FLAG decides, never the column name and never a regex over the value.
    `schema_spec.Column.is_phi` is a contract term whose change requires
    steward approval, and driving masking from it means this function makes no
    policy of its own — which is why there is no allowlist here, and why a
    newly-flagged column starts masking without a change to this module.

    NULL is not masked even in a flagged column, and this is a real decision
    rather than an oversight: "absent" is not protected information, and hiding
    it would make a completeness screen unable to show that a required
    identifier was missing — which is exactly the defect Bronze exists to keep
    visible. It is also not distinguishable-from-others information: every null
    looks like every other null.
    """
    if value is None:
        return MaskedValue(value=None, masked=False)
    if not column.is_phi:
        return MaskedValue(value=_render(value), masked=False)
    if column.type is TypeName.JSON:
        return MaskedValue(value=_mask_json(value), masked=True, reason=PHI_REASON)
    return MaskedValue(value=MASKED, masked=True, reason=PHI_REASON)


def mask_row(table: Table, row: dict[str, Any]) -> dict[str, MaskedValue]:
    """One row, every cell decided by its own column's flag.

    A column present in the row but absent from the contract is masked. That
    is the safe default and it is also a real case: a plane that has drifted
    ahead of the spec has columns the contract has never classified, and
    "unclassified" must not mean "public". The `_UNCLASSIFIED` column below
    carries the reason so the screen says WHY rather than showing bullets with
    no explanation.
    """
    masked: dict[str, MaskedValue] = {}
    for name, value in row.items():
        try:
            column = table.column(name)
        except KeyError:
            masked[name] = (
                MaskedValue(value=None, masked=False)
                if value is None
                else MaskedValue(value=MASKED, masked=True, reason=UNCLASSIFIED_REASON)
            )
            continue
        masked[name] = mask_cell(column, value)
    return masked


UNCLASSIFIED_REASON = (
    "this column is not in the schema contract — unclassified is masked, never public"
)


def phi_columns(table: Table) -> tuple[str, ...]:
    """The flagged columns, so a screen can say how many it is hiding."""
    return tuple(c.name for c in table.columns if c.is_phi)


def _mask_json(value: Any) -> str:
    """A JSON blob's KEYS survive; every value is replaced.

    Bronze's `raw_row` is one flagged column holding the entire source record,
    so masking it whole would render the most useful thing on the screen as
    three bullets. The keys are the source's COLUMN NAMES — which the mapping
    screens already publish, and which are the thing an engineer opening Bronze
    is actually looking for. The values are the member. So: keys shown, values
    gone, and the count stated.
    """
    parsed = value
    if isinstance(parsed, str):
        import json

        try:
            parsed = json.loads(parsed)
        except (ValueError, TypeError):
            return MASKED
    if not isinstance(parsed, dict):
        return MASKED
    keys = sorted(str(k) for k in parsed)
    if not keys:
        return "{}"
    return "{" + ", ".join(f"{key}: {MASKED}" for key in keys) + "}"


def _render(value: Any) -> str:
    """One rendering, here, rather than in each of two adapters and a browser.

    Dates and timestamps go out ISO-8601 because the UI must not reformat a
    value it cannot parse consistently, and `str()` on a `date` is already ISO.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ── what a reader must be able to answer ─────────────────────────────────────
@dataclass(frozen=True)
class ColumnCensus:
    """One column, as the CONTRACT declares it and as the ENGINE actually has it.

    Both, side by side, and never merged into one "type" field. A screen that
    shows only the engine's answer cannot show a drift; a screen that shows
    only the spec's cannot show that the plane never got the migration. The
    conformance kit compares these two for a verdict — this carries them so a
    person can see the same difference the kit sees.
    """

    name: str
    #: The portable type from `schema_spec`, e.g. `timestamp_utc`.
    declared_type: str
    #: What the engine reports, e.g. `timestamptz`. Empty when the column is
    #: not on the plane at all.
    engine_type: str
    nullable: bool
    is_phi: bool
    #: False when the contract declares it and the plane lacks it — a
    #: provisioning gap, rendered as such rather than as a missing value.
    present_on_plane: bool = True


@dataclass(frozen=True)
class TableCensus:
    """One table in one layer: its shape, and how much is in it."""

    schema: str
    name: str
    comment: str
    append_only: bool
    #: None when the table is not on the plane — distinct from 0, which means
    #: the table is there and empty. Conflating them is how a missing
    #: migration reads as "no data yet".
    row_count: int | None
    columns: tuple[ColumnCensus, ...]
    primary_key: tuple[str, ...] = ()

    @property
    def present_on_plane(self) -> bool:
        return self.row_count is not None

    @property
    def phi_column_count(self) -> int:
        return sum(1 for c in self.columns if c.is_phi)


@dataclass(frozen=True)
class LayerCensus:
    """A layer as a screen renders it: what it is, and what is in it today."""

    spec: LayerSpec
    tables: tuple[TableCensus, ...]

    @property
    def row_count(self) -> int | None:
        """Rows across the layer's tables, or None when nothing is on the plane."""
        counted = [t.row_count for t in self.tables if t.row_count is not None]
        return sum(counted) if counted else None


@dataclass(frozen=True)
class RowPage:
    """A page of rows, already masked. There is no unmasked variant of this type.

    Deliberate: a reader that returns raw rows plus a "mask this later" flag
    puts the disclosure one forgotten call away. The adapter masks as it reads,
    so the only rows that exist in memory above the adapter are safe ones.
    """

    schema: str
    table: str
    #: Column order as rendered — contract order, so the primary key and the
    #: identifiers come first and the audit columns last, the same on every
    #: engine. Reading the engine's column order would let a plane's migration
    #: history decide what a screen looks like.
    columns: tuple[str, ...]
    rows: tuple[dict[str, MaskedValue], ...]
    #: How many rows matched before the page limit — so a screen can say
    #: "20 of 294" instead of implying 20 is all there is.
    total_rows: int
    truncated: bool
    masked_columns: tuple[str, ...]
    #: The batch this page was filtered to, when it was.
    batch_id: str | None = None


@dataclass(frozen=True)
class QuarantineReason:
    """Why rows did not cross a gate, and how many. Grouped by the RULE.

    The rule id is load-bearing rather than decorative: "17 rows dropped" is a
    number nobody can act on, and "DQ-002 excluded 11 rows for a missing date
    of birth" names the thing to go and fix.
    """

    rule_id: str
    reason: str
    stage: str
    row_count: int


@dataclass(frozen=True)
class ReconLine:
    """One batch's balance at one stage: in, out, quarantined, attributed.

    `balanced` is the engine's own recorded verdict, not recomputed here. A
    screen that re-derives the balance can disagree with the ledger, and the
    ledger is the thing an auditor reads.
    """

    batch_id: str
    feed_id: str
    stage: str
    records_in: int
    records_out: int
    quarantined: int
    attributed_drops: int
    balanced: bool
    recorded_ts: str

    @property
    def unattributed(self) -> int:
        """Rows that left and were explained by nothing. Must be zero.

        "record-level reconciliation, EVERY drop attributed" — G3. This is the
        figure that makes a green tick falsifiable, so it is computed and shown
        rather than trusted.
        """
        return self.records_in - self.records_out - self.quarantined - self.attributed_drops


@runtime_checkable
class LayerReader(Protocol):
    """Reading the medallion layers. NOT a pin, and that is a decision.

    The twenty-one pins are a closed set on plate 04, and this is not a
    twenty-second socket — it is a COMPOSITION over two pins that already
    exist: `catalog` answers what the engine has, `sql_query` answers how much
    and which rows. A Databricks implementation of this protocol fits the same
    two pins and needs no plate change, which is the test of whether something
    deserves to be a pin.

    It lives in core rather than in `ports/` for the same reason: `ports/`
    declares the pin-out, and adding a non-pin protocol there would make the
    count of sockets ambiguous to the next reader.
    """

    def census(self, spec: LayerSpec) -> LayerCensus:
        """Shape and counts for one layer.

        Must not raise for an unbuilt layer. Returning "nothing here, and here
        is why" is the honest answer, and raising would make the three unbuilt
        layers unrenderable — which is precisely how they would end up hidden.
        """
        ...

    def rows(
        self,
        spec: LayerSpec,
        table: Table,
        *,
        batch_id: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> RowPage:
        """A masked page of rows. Masking happens here, never above here."""
        ...

    def quarantine_reasons(self, *, batch_id: str | None = None) -> tuple[QuarantineReason, ...]:
        """Why rows did not cross, grouped by rule."""
        ...

    def reconciliation(self, *, batch_id: str | None = None) -> tuple[ReconLine, ...]:
        """The balance lines, newest first."""
        ...
