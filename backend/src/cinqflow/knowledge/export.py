"""Approved analyst decisions become governed knowledge.

The one place in the codebase that writes a knowledge document. Reading stays
behind `KnowledgeProvider`; this writes the file that provider will later read,
in the shape it already reads (`domain` + `decision_sets`), so an approved mapping
becomes an exemplar for the next feed without anyone copying it by hand.

What is exported is the decision, not the run: source column, canonical target,
the named transform, and who decided. No values, so no PHI.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from cinqflow.dataplane.contract import table_identifier
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import MappingVersion

log = logging.getLogger(__name__)

RELATIVE = "mappings/approved"


def decisions_of(mapping: MappingVersion) -> list[dict[str, object]]:
    """One record per mapped field, in spec order."""
    records: list[dict[str, object]] = []
    for field in mapping.spec.fields:
        record: dict[str, object] = {
            "source_field": field.source,
            "target": field.target,
            "decided_by": "analyst" if field.edited else "analyst_accepted_ai",
        }
        if field.cast != "string":
            record["cast"] = field.cast
        if field.transform is not None:
            record["transform"] = {"op": field.transform.op, **field.transform.args}
        if field.value_map:
            record["value_map"] = dict(field.value_map)
        if field.on_null != "pass":
            record["on_null"] = field.on_null
            if field.default is not None:
                record["default"] = field.default
        if field.on_unmapped_value != "pass":
            record["on_unmapped_value"] = field.on_unmapped_value
        if field.note:
            record["note"] = field.note
        records.append(record)
    return records


def export_approved_mapping(
    mapping: MappingVersion,
    *,
    approver: str,
    batch_id: str,
    settings: Settings | None = None,
) -> Path:
    """Write (or add to) `knowledge/mappings/approved/<feed>.yaml`.

    The document's own `version` counts how many times this feed's mapping has
    been approved; each approval appends its decision set rather than replacing
    the last one, so the history of what was decided stays readable.
    """
    s = settings or get_settings()
    path = s.knowledge_root / RELATIVE / f"{table_identifier(mapping.feed)}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)

    document: dict[str, object] = {}
    if path.exists():
        document = yaml.safe_load(path.read_text()) or {}

    sets = [
        entry
        for entry in document.get("decision_sets", [])
        if entry.get("mapping_version") != mapping.version
    ]
    sets.append(
        {
            "source": f"{mapping.feed} (uploaded file, mapped in CINQFLOW)",
            "mapping_version": mapping.version,
            "approved_at": datetime.now(UTC).date().isoformat(),
            "approved_by": approver,
            "batch_id": batch_id,
            "target_table": mapping.spec.target_table,
            "decisions": decisions_of(mapping),
        }
    )

    document.update(
        {
            "version": len(sets),
            "updated": datetime.now(UTC).date().isoformat(),
            "domain": mapping.domain,
            "feed": mapping.feed,
            "generated_by": "cinqflow G2 approval; decisions are the analyst's",
            "decision_sets": sets,
        }
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False, width=100))
    log.info("exported %s v%s to %s", mapping.feed, mapping.version, path)
    return path
