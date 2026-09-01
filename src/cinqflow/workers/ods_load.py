"""CF-V3-E8-05 — Silver Raw to Silver ODS, for real: I/O and the control rows.

    "Apply approved canonical mappings and identity crosswalks, generate
     surrogate keys, run business deduplication, apply change-capture and
     history rules, and load facts, dimensions and bridges."
    "Happy path — Given a resolved enrollment batch, when the ODS stage
     runs, then members update in place, coverage segments append with
     effective dates, every row carries its batch and source identifiers,
     and the certification gate receives balanced counts."
    — CF-V3-E8-05

WHY THIS IS A WORKER AND NOT A COMPILED PLAN STEP — the SAME reasoning
`workers/identity.py` gives for itself, unchanged: `StepKind.RESOLVE_IDENTITY`
is declared but never compiled or run, and wiring the whole spine into the
plan/`apply()` machinery is its own piece of work this story does not need an
answer to. This worker takes ALREADY-RESOLVED crosswalk entries and ALREADY-
READ Silver Raw rows from its caller, the same way `IdentityWorker.
resolve_batch` takes already-minimized records — it has no opinion about how
either got there.

ONLY `Members` (CURRENT-ONLY / SCD-1) IS ORCHESTRATED HERE. `MEMBER_MAPPING_V1`
(`core.registry.ods_model_member_mapping`) is the only canonical mapping that
exists, because `silver_raw` has no address table to map `Members_Addresses`
from yet — see that module's own docstring. The EFFECTIVE-DATED (SCD-2)
mechanism itself is real and proven — `core.ods_load.plan_effective_dated`
and `OdsLoadPort.current_open_row`/`close_open_row`/`insert_effective_dated_row`
all exist and are contract-tested against live Postgres
(`tests/contract/test_ods_load_contract.py`) — but there is deliberately no
`load_members_addresses`-shaped orchestration method here yet: writing one
against a source table that does not exist would be fabricating the very
thing this session's own discipline refuses to do. Adding one, once a real
address feed lands in `silver_raw`, is a new method beside `load_members`
that reuses the same pieces — never a change to `_load_one_current_only`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

from cinqflow.core.identity import CrosswalkEntry
from cinqflow.core.mapping import FeedMapping
from cinqflow.core.model.vocabulary import BatchState, Layer
from cinqflow.core.ods_load import (
    LoadAction,
    assign_surrogate_key,
    cast_mapped_value,
    compute_record_hash,
    enrich_with_crosswalk,
    plan_current_only,
    stringify_for_mapping,
)
from cinqflow.core.recon import DropReason, StageReconciliation, reconcile
from cinqflow.core.registry.ods_model import OdsEntity, OdsModel
from cinqflow.ports.control_tables import (
    ControlTablesPort,
    DropLedgerEntry,
    Reconciliation,
    StageStatus,
)
from cinqflow.ports.ods_load import OdsLoadPort

#: Written by the pipeline, never read as business content — excluded from
#: the change-detection hash so a row that only moved through another batch,
#: unchanged, reads as unchanged. `SourceSystem` is here for the same reason
#: `BatchId` is: both name WHICH DELIVERY populated the row, not a fact about
#: the member.
_LINEAGE_COLUMNS = frozenset(
    {"FeedName", "SourceSystem", "CreatedBy", "CreatedAt", "UpdatedBy", "UpdatedAt", "BatchId"}
)
#: Preserved from the existing row on an UPDATE rather than re-stamped —
#: "created" must not change just because the row changed again.
_CARRY_FORWARD_ON_UPDATE = frozenset({"CreatedAt", "CreatedBy"})


class OdsLoadWorkerError(RuntimeError):
    """The ODS load worker refused something."""


class OdsLoadWorker:
    def __init__(self, *, ods: OdsLoadPort, control: ControlTablesPort) -> None:
        self._ods = ods
        self._control = control

    def load_members(
        self,
        *,
        batch_id: str,
        model: OdsModel,
        mapping: FeedMapping,
        loadable: Sequence[CrosswalkEntry],
        silver_raw_rows: Sequence[Mapping[str, object]],
        business_date: date,
        job_name: str = "cinqflow.ods_load",
        now: datetime | None = None,
    ) -> StageReconciliation:
        """Load every resolved crosswalk entry's row into `Members`
        (current-only). Returns the balanced accounting; raises
        `UnattributedDropError` if it does not balance — the same posture
        every other stage's own reconciliation takes.
        """
        stamp = now or datetime.now(UTC)
        entity = model.entity("Members")
        by_source_key = {
            (row.get("source_system"), row.get("source_member_id")): row for row in silver_raw_rows
        }

        loaded = 0
        drops: list[DropReason] = []
        for entry in loadable:
            row = by_source_key.get((entry.source_system, entry.source_member_id))
            if row is None:
                drops.append(
                    DropReason(
                        rule_id="ODS-NO-SILVER-RAW-ROW",
                        reason=f"resolved identity {entry.source_system}:{entry.source_member_id} "
                        "has no matching silver_raw row in this batch",
                        record_count=1,
                    )
                )
                continue
            try:
                self._load_one_current_only(
                    entity,
                    mapping,
                    row,
                    entry,
                    feed_name=row.get("feed_id"),
                    job_name=job_name,
                    stamp=stamp,
                )
            except OdsLoadWorkerError as failure:
                drops.append(
                    DropReason(rule_id="ODS-CAST-FAILURE", reason=str(failure), record_count=1)
                )
                continue
            loaded += 1

        recon = reconcile(
            StageReconciliation(
                batch_id=batch_id,
                stage=Layer.SILVER_ODS,
                records_in=len(loadable),
                records_out=loaded,
                drops=tuple(_merged(drops)),
            )
        )
        self._control.record_stage(
            StageStatus(
                batch_id=batch_id,
                stage=Layer.SILVER_ODS,
                state=BatchState.COMPLETED,
                started_ts=stamp,
                completed_ts=stamp,
                records_in=recon.records_in,
                records_out=recon.records_out,
                quarantined=recon.quarantined,
                attributed_drops=recon.attributed_drops,
            )
        )
        self._control.record_reconciliation(
            Reconciliation(
                batch_id=batch_id,
                stage=Layer.SILVER_ODS,
                records_in=recon.records_in,
                records_out=recon.records_out,
                quarantined=recon.quarantined,
                attributed_drops=recon.attributed_drops,
                drop_ledger=tuple(
                    DropLedgerEntry(rule_id=d.rule_id, reason=d.reason, record_count=d.record_count)
                    for d in recon.drops
                ),
            )
        )
        self._control.record_model_version(batch_id, str(model.version))
        return recon

    def _load_one_current_only(
        self,
        entity: OdsEntity,
        mapping: FeedMapping,
        row: Mapping[str, object],
        crosswalk_entry: CrosswalkEntry,
        *,
        feed_name: object,
        job_name: str,
        stamp: datetime,
    ) -> LoadAction:
        surrogate_value = assign_surrogate_key(
            crosswalk_entry.internal_member_id,
            mint=lambda: self._ods.next_surrogate_key(entity.name),
        )
        if not crosswalk_entry.internal_member_id:
            # A freshly minted key is written straight back to the
            # crosswalk — the same idempotency guarantee
            # `IdentityWorker.resolve_batch` already relies on
            # (`list_crosswalk` re-read on every call): without this, a
            # genuinely new member mints a DIFFERENT surrogate key every
            # time this batch is re-processed, because nothing upstream
            # ever learns the empty `internal_member_id` was filled.
            self._control.record_crosswalk(
                replace(crosswalk_entry, internal_member_id=str(surrogate_value))
            )
        existing = self._ods.existing_current_row(
            entity.name, entity.surrogate_key, surrogate_value
        )

        enriched = enrich_with_crosswalk(stringify_for_mapping(row), crosswalk_entry)
        mapped_raw, rejecting_line = mapping.apply_to(enriched)
        if rejecting_line is not None:
            raise OdsLoadWorkerError(
                f"{entity.name}: {rejecting_line.describe()} — a canonical mapping line rejected "
                "its own row, which this loader has nowhere honest to put"
            )

        platform_stamps: dict[str, object] = {
            "FeedName": feed_name,
            "CreatedBy": job_name,
            "CreatedAt": stamp,
            "UpdatedBy": job_name,
            "UpdatedAt": stamp,
            "RecordCreationDate": stamp,
        }

        values: dict[str, object] = {entity.surrogate_key: surrogate_value}
        for column in entity.columns:
            name = column.name
            if name in (entity.surrogate_key, "RecordHash"):
                continue
            if name in _CARRY_FORWARD_ON_UPDATE and existing is not None:
                values[name] = existing.get(name)
                continue
            if name in platform_stamps:
                values[name] = platform_stamps[name]
                continue
            raw = mapped_raw.get(name)
            try:
                values[name] = cast_mapped_value("" if raw is None else str(raw), column)
            except Exception as failure:  # the platform's one caster; whatever it raises is real
                raise OdsLoadWorkerError(f"{entity.name}.{name}: {failure}") from failure

        business_columns = tuple(
            c.name
            for c in entity.columns
            if c.name not in _LINEAGE_COLUMNS and c.name not in (entity.surrogate_key, "RecordHash")
        )
        values["RecordHash"] = compute_record_hash(values, business_columns)

        action = plan_current_only(
            existing.get("RecordHash") if existing is not None else None, str(values["RecordHash"])
        )
        if action is not LoadAction.SKIPPED_UNCHANGED:
            self._ods.upsert_current_row(entity.name, entity.surrogate_key, values)
        return action


def _merged(drops: Sequence[DropReason]) -> list[DropReason]:
    """One `DropReason` per rule, counts summed — "22,000 in = ... + 5
    rejected by structure check" is a count, not five identical lines."""
    by_rule: dict[str, DropReason] = {}
    for drop in drops:
        if drop.rule_id in by_rule:
            existing = by_rule[drop.rule_id]
            by_rule[drop.rule_id] = DropReason(
                rule_id=existing.rule_id,
                reason=existing.reason,
                record_count=existing.record_count + drop.record_count,
            )
        else:
            by_rule[drop.rule_id] = drop
    return list(by_rule.values())
