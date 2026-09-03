"""The read/write contract every data-plane engine must satisfy.

Five verbs: provision a layer table from the contract, append source-aligned rows,
write mapped rows, rebuild one batch, and read back what landed.
"""

from __future__ import annotations

from typing import Protocol

from cinqflow.dataplane.contract import BronzeRow, Table


class DataPlanePort(Protocol):
    def install_layer(self, layer: str, *, physical: str | None = None) -> None:
        """Create the layer's namespace and any enforcement it needs. Idempotent.

        `layer` is the logical position; `physical` is the namespace it renders into.
        """
        ...

    def ensure_table(self, table: Table) -> None:
        """Render and apply the table declaration. Idempotent and additive."""
        ...

    def append_bronze(self, table: Table, rows: list[BronzeRow]) -> int:
        """Append source-aligned rows. Returns the number written."""
        ...

    def write_rows(
        self,
        table: Table,
        rows: list[dict[str, object]],
        *,
        source_system: str,
        batch_id: str,
    ) -> int:
        """Write mapped rows to a rebuildable layer. Returns the number written."""
        ...

    def delete_batch(self, table: Table, batch_id: str) -> int:
        """Remove one batch's rows so it can be rebuilt. Refused on append-only tables."""
        ...

    def count_rows(self, table: Table, batch_id: str) -> int: ...

    def read_rows(
        self, table: Table, batch_id: str, *, limit: int, offset: int = 0, stride: int = 1
    ) -> list[dict]:
        """Rows of one batch in file order; `stride` > 1 samples across it."""
        ...
