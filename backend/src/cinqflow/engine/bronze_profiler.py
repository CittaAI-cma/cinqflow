"""Deterministic profiling of a landed Bronze batch.

Reuses the file profiler: Bronze rows are read back through the data-plane port,
reassembled into the same ParsedFile shape, and profiled by the same arithmetic.
So a Bronze profile is directly comparable with the upload profile that preceded
it - if they disagree, something happened during landing.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.dataplane.contract import Table
from cinqflow.dataplane.port import DataPlanePort
from cinqflow.engine import profiler
from cinqflow.engine.parsers import ParsedFile
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import ProfileFacts

#: Bronze can hold millions of rows; profiling reads a bounded window rather than
#: the whole batch. The window is recorded on the artifact so nobody mistakes a
#: sample statistic for a census.
DEFAULT_SAMPLE_ROWS = 5000


@dataclass
class BronzeProfileResult:
    facts: ProfileFacts
    profile_id: str
    rows_in_batch: int
    rows_profiled: int

    @property
    def is_sample(self) -> bool:
        return self.rows_profiled < self.rows_in_batch


def profile_batch(
    plane: DataPlanePort,
    table: Table,
    batch_id: str,
    *,
    settings: Settings | None = None,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    column_order: list[str] | None = None,
) -> BronzeProfileResult:
    """`column_order` restores the source column order.

    Bronze stores each source row as JSONB, and JSONB does not preserve key order -
    Postgres normalises it. Column order is real information about the file, so the
    caller passes the order recorded by the upload profile; any column not in that
    list (schema drift) is appended in the order encountered.
    """
    s = settings or get_settings()
    total = plane.count_rows(table, batch_id)
    rows = plane.read_rows(table, batch_id, limit=sample_rows)

    raw_rows = [dict(row["raw_row"]) for row in rows]
    present: list[str] = []
    for raw in raw_rows:
        for key in raw:
            if key not in present:
                present.append(key)

    if column_order:
        known = [c for c in column_order if c in present]
        drifted = [c for c in present if c not in set(column_order)]
        columns = known + sorted(drifted)
    else:
        columns = present

    parsed = ParsedFile(
        columns=columns,
        rows=[{c: str(raw.get(c, "")) for c in columns} for raw in raw_rows],
        sheets=[(table.qualified, len(raw_rows))],
    )
    facts = profiler.profile(parsed, s)
    return BronzeProfileResult(
        facts=facts,
        profile_id=profiler.profile_id(facts),
        rows_in_batch=total,
        rows_profiled=len(raw_rows),
    )
