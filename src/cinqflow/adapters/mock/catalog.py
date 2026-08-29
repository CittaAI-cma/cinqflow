"""dict — schemas from a dictionary."""

from __future__ import annotations

from collections.abc import Sequence

from cinqflow.ports import port
from cinqflow.ports.catalog import TableInfo


@port("catalog", "mock")
class DictCatalog:
    def __init__(self, tables: Sequence[TableInfo] = ()) -> None:
        self._tables = {t.qualified_name: t for t in tables}

    def describe_table(self, schema: str, name: str) -> TableInfo:
        try:
            return self._tables[f"{schema}.{name}"]
        except KeyError:
            raise KeyError(f"no such table: {schema}.{name}") from None

    def list_tables(self, schema: str) -> Sequence[TableInfo]:
        return tuple(t for t in self._tables.values() if t.schema == schema)

    def introspect_schema(self, schema: str) -> Sequence[TableInfo]:
        return self.list_tables(schema)
