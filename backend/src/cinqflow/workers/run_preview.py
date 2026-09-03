"""Topic mapping.preview - executes a mapping spec against a Bronze sample.

Deterministic end to end: this worker never constructs an AgentRuntime and never
imports `intelligence`, so a preview cannot depend on a model being available or
on it answering the same way twice.
"""

from __future__ import annotations

import logging

import psycopg

from cinqflow.dataplane.contract import bronze_table
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine.mapping_exec import (
    DEFAULT_STRATEGY,
    SAMPLE_STRATEGIES,
    execute_spec,
    sample_selector,
    sample_stride,
    spec_fingerprint,
)
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import (
    PreviewAggregates,
    PreviewFieldResult,
    PreviewRowResult,
    PreviewSample,
)
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "mapping.preview"

#: A preview is for reading, so it stays small enough to render and to store.
DEFAULT_SAMPLE_ROWS = 200
MAX_SAMPLE_ROWS = 1000


def handle(conn: psycopg.Connection, payload: dict, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    store = WorkflowStore(conn, s)

    feed = payload["feed"]
    version = int(payload["version"])
    limit = min(int(payload.get("rows", DEFAULT_SAMPLE_ROWS)), MAX_SAMPLE_ROWS)
    strategy = str(payload.get("strategy", DEFAULT_STRATEGY))
    if strategy not in SAMPLE_STRATEGIES:
        raise RuntimeError(f"unknown sample strategy: {strategy}")

    mapping = store.get_mapping_version(feed, version)
    if mapping is None:
        raise RuntimeError(f"unknown mapping version: {feed} v{version}")

    batch_id = payload.get("batch_id")
    if not batch_id:
        run = store.latest_batch_for_feed(feed)
        if run is None:
            log.warning("no completed batch for feed %s; nothing to preview against", feed)
            return {"feed": feed, "version": version, "previewed": False, "reason": "no batch"}
        batch_id = run.batch_id

    table = bronze_table(feed)
    plane = PostgresDataPlane(conn)
    if not plane.table_exists(table):
        return {"feed": feed, "version": version, "previewed": False, "reason": "no bronze table"}

    rows_in_batch = plane.count_rows(table, batch_id)
    stride = sample_stride(strategy, limit=limit, rows_in_batch=rows_in_batch)
    bronze_rows = plane.read_rows(table, batch_id, limit=limit, stride=stride)
    sample_rows = [dict(row["raw_row"]) for row in bronze_rows]

    # The whole of Stage 5, in one deterministic call.
    result = execute_spec(mapping.spec, sample_rows, detail=True)

    counts = result.counts
    aggregates = PreviewAggregates(
        rows_previewed=counts["rows_previewed"],
        rows_ok=counts.get("rows_ok", 0),
        rows_with_failures=counts.get("rows_failure", 0),
        rows_quarantined=counts.get("rows_quarantined", 0),
        rows_rejected=counts.get("rows_rejected", 0),
        failures_by_rule=result.failures_by_rule,
        null_or_invalid=result.null_or_invalid,
        affected_sources=result.affected_sources,
    )
    preview = store.put_preview(
        feed=feed,
        version=version,
        spec_fingerprint=spec_fingerprint(mapping.spec),
        sample=PreviewSample(
            batch_id=batch_id,
            bronze_table=table.qualified,
            rows=len(sample_rows),
            rows_in_batch=rows_in_batch,
            selector=sample_selector(limit, strategy),
        ),
        aggregates=aggregates,
        # The row number is the one the row has in the batch, not its position in
        # the sample: with a spread sample the two differ, and "row 2" would send
        # the analyst to the wrong row of the file.
        row_results=[
            PreviewRowResult(
                row_number=source["row_number"],
                outcome=row.outcome,
                fields=[PreviewFieldResult(**outcome.as_dict()) for outcome in row.fields],
            )
            for row, source in zip(result.rows, bronze_rows, strict=True)
        ],
    )

    # A previewed draft is still editable; editing it makes this preview stale
    # rather than deleting it, because the fingerprints stop matching.
    if mapping.status == "draft":
        store.set_mapping_status(feed=feed, version=version, status="previewed")

    log.info(
        "previewed %s v%s over %s rows (%s) of batch %s: "
        "ok=%s failures=%s quarantined=%s rejected=%s",
        feed,
        version,
        len(sample_rows),
        sample_selector(limit, strategy),
        batch_id,
        aggregates.rows_ok,
        aggregates.rows_with_failures,
        aggregates.rows_quarantined,
        aggregates.rows_rejected,
    )
    return {
        "feed": feed,
        "version": version,
        "previewed": True,
        "preview_id": preview.preview_id,
        "batch_id": batch_id,
        "aggregates": aggregates.model_dump(),
    }
