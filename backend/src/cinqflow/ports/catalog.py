"""The `catalog` pin — describe schemas, lineage and grants.

    verb: schemas/lineage/grants   mock: dict   dev: information_schema
    target: unity_catalog
    — docs/architecture/plates/04-pin-out-map.md

`information_schema` at rung 0.5 is not a stand-in for Unity Catalog's ideas —
it answers the same three questions the platform actually asks. Which is why
this pin's verbs are questions rather than a vendor's object model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    # From the schema contract, not inferred. Masking is driven by the flag,
    # and a flag change requires steward approval.
    is_phi: bool = False


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    columns: tuple[ColumnInfo, ...] = field(default_factory=tuple)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@runtime_checkable
class CatalogPort(Protocol):
    def describe_table(self, schema: str, name: str) -> TableInfo: ...
    def list_tables(self, schema: str) -> Sequence[TableInfo]: ...
    def introspect_schema(self, schema: str) -> Sequence[TableInfo]:
        """What the engine ACTUALLY has.

        The conformance kit compares this against the portable DDL spec rather
        than comparing two engines to each other — so a drift is attributed to
        one engine immediately, instead of producing a diff nobody can
        adjudicate.
        """
        ...
