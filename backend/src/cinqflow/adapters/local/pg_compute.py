"""pg-compute — render the plan as set-based SQL inside one transaction.

    pg-compute: {status: REAL_TODAY,
                 emits: "set-based SQL in a transaction (COPY, INSERT..SELECT, CTE)"}
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

This adapter WRITES. It does not decide. Every judgement about a row — cast it,
map it, quarantine it, attribute it — was already made in core/compiler/execute
so that the Databricks renderer inherits the same judgements rather than
re-implementing them, and so the cross-engine golden comparison compares
RENDERINGS rather than two people's ideas about what DQ-002 means.

The transaction boundary is the second reason this is a rung-0.5 advantage
rather than a stopgap: a batch is published atomically — "a batch is visible
downstream in full or not at all" — and on Postgres that is one BEGIN.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.compiler.execute import ExecutionResult
from cinqflow.core.compiler.plan import LogicalPlan
from cinqflow.core.model.vocabulary import Layer


@dataclass(frozen=True)
class WriteCounts:
    bronze: int = 0
    silver_raw: int = 0
    quarantine: int = 0


class PostgresCompute:
    """Writes Bronze, Silver Raw and quarantine for one batch."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def land_bronze(
        self, *, plan: LogicalPlan, batch_id: str, rows: list[dict[str, str]], source_system: str
    ) -> int:
        """Bronze is an UNTOUCHED COPY of the source.

        The whole parsed row goes in as JSON, unmapped and uncast, because
        Bronze's job is fidelity: when a mapping turns out to be wrong six
        months later, the original is still there to re-derive from. That is
        also why it is append-only at the database layer.
        """
        now = datetime.now(UTC)
        for row_number, raw in enumerate(rows, start=1):
            self._db.execute(
                "INSERT INTO bronze.members_raw (bronze_id, feed_id, row_number, raw_row, "
                "source_system, ingestion_ts, batch_id, record_hash, created_ts) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()),
                    plan.feed_id,
                    row_number,
                    json.dumps(raw, sort_keys=True),
                    source_system,
                    now,
                    batch_id,
                    _record_hash(raw),
                    now,
                ),
            )
        return len(rows)

    def load_silver_raw(
        self, *, plan: LogicalPlan, batch_id: str, result: ExecutionResult, source_system: str
    ) -> WriteCounts:
        """Load the survivors, and store every excluded row with its reason.

        Quarantine STORAGE holds the row — that is what makes "reprocess only
        the failed records" possible. No certified query tool can reach it, and
        no summary carries it.
        """
        now = datetime.now(UTC)
        for row in result.loaded:
            self._db.execute(
                "INSERT INTO silver_raw.members (member_row_id, feed_id, source_member_id, "
                "first_name, last_name, date_of_birth, gender, line_of_business, "
                "effective_date, end_date, is_active, source_system, ingestion_ts, batch_id, "
                "record_hash, created_ts) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()),
                    plan.feed_id,
                    row.get("source_member_id"),
                    row.get("first_name"),
                    row.get("last_name"),
                    row.get("date_of_birth"),
                    row.get("gender"),
                    row.get("line_of_business"),
                    row.get("effective_date"),
                    row.get("end_date"),
                    bool(row.get("is_active", True)),
                    source_system,
                    now,
                    batch_id,
                    _record_hash(row),
                    now,
                ),
            )

        for dropped in result.quarantined:
            self._db.execute(
                "INSERT INTO quarantine.quarantined_rows (quarantine_id, batch_id, stage_name, "
                "rule_id, reason, record_key, raw_row, quarantined_ts) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()),
                    batch_id,
                    Layer.SILVER_RAW.value,
                    dropped.rule_id,
                    dropped.reason,
                    str(dropped.row_number),
                    json.dumps(dropped.row, sort_keys=True),
                    now,
                ),
            )

        return WriteCounts(silver_raw=len(result.loaded), quarantine=len(result.quarantined))

    def record_recon_history(self, *, result: ExecutionResult, feed_id: str) -> None:
        """Reconciliation history, so drop-rate TRENDS are visible per feed.

        "Keep reconciliation history so trends are visible per feed" — a single
        batch's drop rate says nothing; the same rule creeping from 0.1% to 3%
        over six cycles is the signal.
        """
        recon = result.reconciliation
        self._db.execute(
            "INSERT INTO recon.recon_history (history_id, batch_id, feed_id, stage_name, "
            "records_in, records_out, quarantined, attributed_drops, balanced, recorded_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
            (
                str(uuid.uuid4()),
                recon.batch_id,
                feed_id,
                recon.stage.value,
                recon.records_in,
                recon.records_out,
                recon.quarantined,
                recon.attributed_drops,
                recon.balances,
            ),
        )

    def clear_derived_layers(self, batch_id: str) -> None:
        """Remove this batch's DERIVED rows before a restart re-loads them.

        Silver Raw and quarantine are derived: they can be rebuilt from Bronze
        and the plan, so clearing them is not data loss. BRONZE IS NOT TOUCHED
        — it is append-only at the database layer, and it is the thing being
        rebuilt from.

        This is what "restart resumes from the last completed stage: no
        duplicates, no skips" requires in practice. Without it a restart hits
        the (batch_id, feed_id, source_member_id) uniqueness constraint and the
        recovery path fails on its second attempt — which is precisely how the
        incumbent estate ended up doing manual control-row surgery ("delete the
        control rows so bronze will accept the replay").
        """
        self._db.execute("DELETE FROM silver_raw.members WHERE batch_id = %s", (batch_id,))
        self._db.execute("DELETE FROM quarantine.quarantined_rows WHERE batch_id = %s", (batch_id,))

    def count_bronze(self, batch_id: str) -> int:
        row = self._db.fetch_one(
            "SELECT count(*) FROM bronze.members_raw WHERE batch_id = %s", (batch_id,)
        )
        return int(row[0]) if row else 0

    def count_silver_raw(self, batch_id: str) -> int:
        row = self._db.fetch_one(
            "SELECT count(*) FROM silver_raw.members WHERE batch_id = %s", (batch_id,)
        )
        return int(row[0]) if row else 0


def _record_hash(row: dict[str, Any]) -> str:
    """Lineage: every row can be traced back to the file it came from.

    Deterministic and content-based, so the SAME row re-derived after a
    reprocess hashes the same — which is what lets a corrected batch be
    compared against the original rather than merely replacing it.
    """
    import hashlib

    material = json.dumps({k: str(v) for k, v in sorted(row.items())}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:32]
