"""PipelineRunner: the only mover of rows.

Sequences one promotion, opens and closes its run, and enforces the balance
equation. Deterministic throughout - no model is reachable from this module.

`land_bronze` moves an approved upload into Bronze; `promote_silver` runs an
approved mapping over a Bronze batch into Silver Raw. Both open a run before they
touch data, both balance before they finish, and neither can reach a model.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import psycopg

from cinqflow.dataplane import contract
from cinqflow.dataplane.contract import MEMBER_KEY, Table
from cinqflow.dataplane.filestore import FileStore, Folder
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine.mapping_exec import (
    ExecutionResult,
    execute_spec,
    expected_entity_rows,
    group_by_entity,
    is_empty,
    row_reasons,
)
from cinqflow.engine.parsers import ParseError, parse
from cinqflow.knowledge.canonical import CanonicalModel
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import MappingVersion, RunCounts, Upload
from cinqflow.workflow.states import RunState, UploadStatus
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)

#: Bronze rows are written in chunks so a large file never becomes one giant
#: statement. The whole batch remains one transaction.
INSERT_CHUNK = 2000

#: Promotion reads and maps the batch a page at a time. Per-field detail is kept
#: for the page being written and then dropped, so quarantine reasons stay precise
#: without a large batch materialising 600,000 field records at once.
PROMOTE_PAGE = 1000


class LandingFailure(Exception):
    """Raised after the run and upload have both been marked failed."""


class PromotionFailure(Exception):
    """Raised after the promotion run has been marked failed."""


class WriteShortfall(Exception):
    """The plane accepted fewer rows than it was handed.

    Checked because the balance equation is only worth having if it is measured
    against what the database actually holds. Counting what the executor intended
    to write would make the check confirm itself.
    """


@dataclass
class LandingOutcome:
    batch_id: str
    bronze_table: str
    counts: RunCounts
    landing_key: str


@dataclass
class PageResult:
    """What one page of a promotion did. Every row read is in exactly one bucket."""

    written: dict[str, int]
    promoted: int
    quarantined: int
    empty: int


@dataclass
class PromotionOutcome:
    batch_id: str
    feed: str
    mapping_version: int
    counts: RunCounts
    #: entity table -> rows written
    silver_tables: dict[str, int]
    quarantine_table: str
    quarantined: int
    rebuilt: bool


class PipelineRunner:
    def __init__(
        self,
        conn: psycopg.Connection,
        settings: Settings | None = None,
        *,
        plane: PostgresDataPlane | None = None,
    ) -> None:
        self.conn = conn
        self.s = settings or get_settings()
        self.store = WorkflowStore(conn, self.s)
        self.filestore = FileStore(self.s)
        self.plane = plane or PostgresDataPlane(conn)

    # --------------------------------------------------------------- land_bronze
    def land_bronze(self, upload: Upload) -> LandingOutcome:
        """Approved upload -> Bronze, source-aligned, with lineage.

        Order matters: the batch exists before any data moves, so a crash always
        leaves a run to explain what happened.
        """
        batch_id = contract.new_batch_id()
        table = contract.bronze_table(upload.feed)

        self.store.open_run(
            batch_id=batch_id, upload_id=upload.upload_id, feed=upload.feed, kind="land_bronze"
        )
        self.store.put_lineage(
            batch_id=batch_id,
            upload_id=upload.upload_id,
            fingerprint=upload.fingerprint,
            landing_key=upload.landing_key,
        )
        self.store.set_run_state(batch_id, RunState.IN_PROGRESS)
        if upload.status != UploadStatus.LANDING:
            self.store.set_status(upload.upload_id, UploadStatus.LANDING)
        # Durable before any data moves: rolling back the row work must not erase
        # the run that explains it.
        self.conn.commit()

        try:
            content = self.filestore.read_bytes(upload.landing_key)
            parsed = parse(content, upload.file_type)
        except (ParseError, FileNotFoundError, OSError) as exc:
            self._fail(batch_id, upload, f"{type(exc).__name__}: {exc}")
            raise LandingFailure(str(exc)) from exc

        self.plane.ensure_table(table)

        rows = [
            contract.BronzeRow(
                bronze_id=str(uuid.uuid4()),
                feed_id=upload.feed,
                row_number=index,
                raw_row=row,
                source_system=upload.source_system,
                batch_id=batch_id,
                record_hash=contract.record_hash(row),
            )
            for index, row in enumerate(parsed.rows, start=1)
        ]

        written = 0
        try:
            for start in range(0, len(rows), INSERT_CHUNK):
                written += self.plane.append_bronze(table, rows[start : start + INSERT_CHUNK])
        except psycopg.Error as exc:
            self._fail(batch_id, upload, f"{type(exc).__name__}: {exc}")
            raise LandingFailure(str(exc)) from exc

        counts = RunCounts(records_in=parsed.row_count, records_out=written)
        if not counts.balanced:
            # Refuse to keep rows we cannot account for.
            self.conn.rollback()
            self._fail(
                batch_id,
                upload,
                f"balance failed: in={counts.records_in} out={counts.records_out}",
            )
            raise LandingFailure("balance equation failed")

        # The original moves only once its rows are safely in Bronze.
        processed_key = self.filestore.move(upload.landing_key, Folder.PROCESSED)
        self.store.set_landing_key(upload.upload_id, processed_key)
        self.store.put_lineage(
            batch_id=batch_id,
            upload_id=upload.upload_id,
            fingerprint=upload.fingerprint,
            landing_key=processed_key,
            bronze_table=table.qualified,
        )
        self.store.finish_run(batch_id=batch_id, counts=counts)
        self.store.set_status(upload.upload_id, UploadStatus.LANDED)

        log.info(
            "landed %s rows to %s for batch %s", written, table.qualified, batch_id
        )
        return LandingOutcome(
            batch_id=batch_id,
            bronze_table=table.qualified,
            counts=counts,
            landing_key=processed_key,
        )

    # ------------------------------------------------------------ promote_silver
    def promote_silver(
        self,
        *,
        batch_id: str,
        mapping: MappingVersion,
        canonical: CanonicalModel,
        upload: Upload,
    ) -> PromotionOutcome:
        """An approved mapping over a whole Bronze batch -> Silver Raw.

        The same executor the analyst previewed with, over every row instead of a
        sample. Nothing here decides anything: what to write was decided at G2, and
        this only writes it, quarantines what it cannot write, and refuses to
        finish if the two do not add up to what it read.
        """
        bronze = contract.bronze_table(mapping.feed)
        if not self.plane.table_exists(bronze):
            raise PromotionFailure(f"no Bronze table for feed {mapping.feed}")

        entities = self._silver_tables(mapping, canonical)
        if not entities:
            raise PromotionFailure("the approved mapping targets no canonical entity")
        primary = self._primary_entity(mapping, entities)
        quarantine = contract.quarantine_table(mapping.feed, schema=self.s.silver_schema)

        rows_in = self.plane.count_rows(bronze, batch_id)
        self.store.open_run(
            batch_id=batch_id,
            upload_id=upload.upload_id,
            feed=mapping.feed,
            kind="promote_silver",
            mapping_version=mapping.version,
        )
        self.store.set_run_state(batch_id, RunState.IN_PROGRESS, kind="promote_silver")
        self.conn.commit()  # the run outlives a rollback of the row work

        for table in entities.values():
            self.plane.ensure_table(table)
        self.plane.ensure_table(quarantine)

        # Replay: this batch's Silver and quarantine are rebuilt, and only this
        # batch's. Bronze is never touched - it refuses deletion at the database.
        rebuilt = False
        for table in (*entities.values(), quarantine):
            if self.plane.delete_batch(table, batch_id):
                rebuilt = True

        written: dict[str, int] = dict.fromkeys(entities, 0)
        promoted = quarantined = empty = 0
        try:
            for offset in range(0, rows_in, PROMOTE_PAGE):
                bronze_rows = self.plane.read_rows(
                    bronze, batch_id, limit=PROMOTE_PAGE, offset=offset
                )
                result = execute_spec(
                    mapping.spec, [dict(row["raw_row"]) for row in bronze_rows], detail=True
                )
                page = self._write_page(
                    result=result,
                    rows=bronze_rows,
                    entities=entities,
                    primary=primary,
                    quarantine=quarantine,
                    mapping=mapping,
                    batch_id=batch_id,
                    upload=upload,
                )
                for name, count in page.written.items():
                    written[name] += count
                promoted += page.promoted
                quarantined += page.quarantined
                empty += page.empty
        except WriteShortfall as exc:
            self.conn.rollback()
            self._fail_promotion(batch_id, f"balance failed: {exc}")
            raise PromotionFailure(f"balance failed: {exc}") from exc
        except psycopg.Error as exc:
            self._fail_promotion(batch_id, f"{type(exc).__name__}: {exc}")
            raise PromotionFailure(str(exc)) from exc

        counts = RunCounts(
            records_in=rows_in,
            records_out=promoted,
            quarantined=quarantined,
            attributed_drops=empty,
        )
        if not counts.balanced:
            self.conn.rollback()
            self._fail_promotion(
                batch_id,
                f"balance failed: in={counts.records_in} out={counts.records_out} "
                f"quarantined={counts.quarantined} drops={counts.attributed_drops}",
            )
            raise PromotionFailure("balance equation failed")

        self.store.put_lineage(
            batch_id=batch_id,
            upload_id=upload.upload_id,
            fingerprint=upload.fingerprint,
            landing_key=upload.landing_key,
            mapping_version=mapping.version,
            silver_table=entities[primary].qualified,
            silver_tables={entities[n].qualified: c for n, c in written.items()},
        )
        self.store.finish_run(batch_id=batch_id, counts=counts, kind="promote_silver")

        log.info(
            "promoted batch %s with %s v%s: in=%s out=%s quarantined=%s drops=%s tables=%s",
            batch_id,
            mapping.feed,
            mapping.version,
            counts.records_in,
            counts.records_out,
            counts.quarantined,
            counts.attributed_drops,
            written,
        )
        return PromotionOutcome(
            batch_id=batch_id,
            feed=mapping.feed,
            mapping_version=mapping.version,
            counts=counts,
            silver_tables={entities[n].qualified: c for n, c in written.items()},
            quarantine_table=quarantine.qualified,
            quarantined=quarantined,
            rebuilt=rebuilt,
        )

    # ------------------------------------------------------- promotion internals
    def _silver_tables(
        self, mapping: MappingVersion, canonical: CanonicalModel
    ) -> dict[str, Table]:
        """One rendered table per canonical entity the spec targets."""
        wanted = {t.split(".", 1)[0] for t in mapping.spec.targets if "." in t}
        return {
            name: contract.silver_table(
                name,
                canonical.fields_of(name),
                schema=self.s.silver_schema,
                phi_fields=canonical.phi_of(name),
            )
            for name in sorted(wanted)
            if name in canonical.primary_keys
        }

    @staticmethod
    def _primary_entity(mapping: MappingVersion, entities: dict[str, Table]) -> str:
        declared = mapping.spec.target_table.split(".")[-1]
        return declared if declared in entities else next(iter(entities))

    def _write_page(
        self,
        *,
        result: ExecutionResult,
        rows: list[dict],
        entities: dict[str, Table],
        primary: str,
        quarantine: Table,
        mapping: MappingVersion,
        batch_id: str,
        upload: Upload,
    ) -> PageResult:
        """Write one page of the batch and account for every row in it."""
        pending: dict[str, list[dict[str, object]]] = {name: [] for name in entities}
        refused: list[dict[str, object]] = []
        promoted = empty = 0

        for outcome, source in zip(result.rows, rows, strict=True):
            row_number = source["row_number"]
            if outcome.outcome in ("failure", "quarantined", "rejected"):
                refused.append(
                    {
                        "quarantine_id": str(uuid.uuid4()),
                        "feed_id": mapping.feed,
                        "row_number": row_number,
                        "mapping_version": mapping.version,
                        "outcome": outcome.outcome,
                        "reasons": row_reasons(outcome),
                        "raw_row": dict(source["raw_row"]),
                        "record_hash": source["record_hash"],
                    }
                )
                continue

            grouped = group_by_entity(outcome.mapped)
            member_key = grouped.get(primary, {}).get(MEMBER_KEY)
            wrote_any = False
            for name, values in grouped.items():
                table = entities.get(name)
                if table is None:  # pragma: no cover - validate_spec refuses these
                    continue
                # For a child, a row carrying nothing but the propagated member key
                # is not a record. For the primary entity, the identifier *is* the
                # record, so nothing is ignored.
                ignoring = frozenset() if name == primary else frozenset({MEMBER_KEY})
                if is_empty(values, ignoring=ignoring):
                    continue
                # The canonical key of every child entity includes the member's
                # source identifier, so it travels with the row it came from.
                if name != primary and MEMBER_KEY in table.column_names:
                    values.setdefault(MEMBER_KEY, None)
                    if not values[MEMBER_KEY]:
                        values[MEMBER_KEY] = member_key
                pending[name].append({**values, "record_hash": contract.record_hash(values)})
                wrote_any = True

            if wrote_any:
                promoted += 1
            else:
                # Nothing failed; the row simply carried nothing to write.
                empty += 1
                log.debug("row %s of batch %s mapped to no values", row_number, batch_id)

        # A second opinion on the fan-out, computed from the field outcomes rather
        # than from the grouping above: without it, a fan-out defect that dropped
        # every address row would still satisfy the source-row balance equation.
        expected = expected_entity_rows(result, primary=primary, member_key=MEMBER_KEY)
        for name in entities:
            if len(pending[name]) != expected.get(name, 0):
                raise WriteShortfall(
                    f"{entities[name].qualified} prepared {len(pending[name])} rows "
                    f"where the mapped values account for {expected.get(name, 0)}"
                )

        written: dict[str, int] = {}
        for name, entity_rows in pending.items():
            written[name] = self._write(entities[name], entity_rows, upload, batch_id)
        self._write(quarantine, refused, upload, batch_id)
        return PageResult(
            written=written, promoted=promoted, quarantined=len(refused), empty=empty
        )

    def _write(
        self, table: Table, rows: list[dict[str, object]], upload: Upload, batch_id: str
    ) -> int:
        """Write and reconcile. Every row handed over must be accounted for."""
        count = self.plane.write_rows(
            table, rows, source_system=upload.source_system, batch_id=batch_id
        )
        if count != len(rows):
            raise WriteShortfall(f"{table.qualified} wrote {count} of {len(rows)} rows")
        return count

    def _fail_promotion(self, batch_id: str, error: str) -> None:
        self.conn.rollback()
        WorkflowStore(self.conn, self.s).finish_run(
            batch_id=batch_id, error=error, kind="promote_silver"
        )
        self.conn.commit()
        log.warning("promotion failed for batch %s: %s", batch_id, error)

    # -------------------------------------------------------------------- reject
    def reject(self, upload: Upload) -> str:
        """A rejected file moves out of `incoming`. Nothing is written to the plane."""
        new_key = self.filestore.move(upload.landing_key, Folder.REJECTED)
        self.store.set_landing_key(upload.upload_id, new_key)
        log.info("rejected %s -> %s", upload.upload_id, new_key)
        return new_key

    # -------------------------------------------------------------------- helper
    def _fail(self, batch_id: str, upload: Upload, error: str) -> None:
        """Record the failure on a connection the caller's rollback cannot discard."""
        self.conn.rollback()
        store = WorkflowStore(self.conn, self.s)
        store.finish_run(batch_id=batch_id, error=error)
        store.set_status(upload.upload_id, UploadStatus.LAND_FAILED, error=error)
        self.conn.commit()
        log.warning("landing failed for %s: %s", upload.upload_id, error)
