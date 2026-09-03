"""Topic mapping.promote - runs an approved mapping over a whole Bronze batch.

Deterministic end to end, for the same reason the preview is: this worker never
constructs an AgentRuntime and never imports `intelligence`. What to write was
decided by an analyst at G2; this only carries the decision out.
"""

from __future__ import annotations

import logging

import psycopg

from cinqflow.engine.runner import PipelineRunner, PromotionFailure
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.export import export_approved_mapping
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.store import WorkflowStore

log = logging.getLogger(__name__)
TOPIC = "mapping.promote"


def handle(conn: psycopg.Connection, payload: dict, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    store = WorkflowStore(conn, s)

    feed = payload["feed"]
    version = int(payload["version"])
    batch_id = payload["batch_id"]

    mapping = store.get_mapping_version(feed, version)
    if mapping is None:
        raise RuntimeError(f"unknown mapping version: {feed} v{version}")
    if mapping.status != "approved":
        # Only G2 authorises a Silver write, and only for the version it froze.
        log.warning("refusing to promote %s v%s in status %s", feed, version, mapping.status)
        return {"feed": feed, "version": version, "promoted": False, "reason": mapping.status}

    landing = store.get_run(batch_id, kind="land_bronze")
    if landing is None:
        raise RuntimeError(f"unknown batch: {batch_id}")
    upload = store.get_upload(landing.upload_id)

    canonical = load_canonical(YamlKnowledgeProvider(s), mapping.domain)
    try:
        outcome = PipelineRunner(conn, s).promote_silver(
            batch_id=batch_id, mapping=mapping, canonical=canonical, upload=upload
        )
    except PromotionFailure as exc:
        return {
            "feed": feed,
            "version": version,
            "batch_id": batch_id,
            "promoted": False,
            "error": str(exc),
        }

    # The decision becomes knowledge once it has demonstrably run. Failing to
    # write the file must not undo a completed, balanced promotion.
    exported: str | None = None
    approval = store.approval_for_mapping(feed=feed, version=version)
    try:
        exported = str(
            export_approved_mapping(
                mapping,
                approver=approval.approver if approval else "unknown",
                batch_id=batch_id,
                settings=s,
            )
        )
    except OSError as exc:  # pragma: no cover - filesystem specific
        log.warning("could not export approved mapping for %s v%s: %s", feed, version, exc)

    return {
        "feed": feed,
        "version": version,
        "batch_id": batch_id,
        "promoted": True,
        "counts": outcome.counts.model_dump(),
        "silver_tables": outcome.silver_tables,
        "quarantined": outcome.quarantined,
        "rebuilt": outcome.rebuilt,
        "knowledge": exported,
    }
