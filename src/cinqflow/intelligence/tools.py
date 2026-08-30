"""CF-V0-E16-09 — the catalogue, executed. Scope inside the query, citation on
every row, an audit entry for every invocation including the refusals.

    "Apply the caller's RBAC scopes INSIDE the tool, before the query runs —
     the restriction lives in the query, not in the answer."
    "an out-of-scope feed_id returns empty with an explicit marker (never a
     partial result, never an error that reveals the feed exists)"
    "Write every tool invocation to audit.agent_action with caller identity,
     tool name, arguments and row count."
    — CF-V0-E16-09

The executor is ONE function with a dispatch table, not sixteen public
methods, because every guarantee this story makes is a guarantee about the
SURFACE: validate, scope, execute, cite, audit — in that order, for all
sixteen, with no path that skips a step. Sixteen public methods would be
sixteen places to forget the audit row.

A note on scoping a batch: a caller asks about `batch:8842` without naming a
feed, so the executor resolves the batch to its feed and checks THAT. The
resolution read never reaches the caller — an out-of-scope batch returns the
same empty, explicitly-marked result as a batch that does not exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from cinqflow.core import certification as batch_certification
from cinqflow.core import reliability
from cinqflow.core import sla as sla_core
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.compiler import compile_feed
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.identity import Principal
from cinqflow.core.model.vocabulary import BatchState
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.core.operations import monitor as ops_monitor
from cinqflow.core.operations.actions import OpsAction
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.retrieval import (
    ReferenceEntry,
    ReferenceIndex,
    as_rows,
    platform_index,
)
from cinqflow.core.tools import (
    CATALOGUE,
    READ_ONLY_WHITELIST,
    ArgumentError,
    ToolError,
    ToolSpec,
    spec_for,
)
from cinqflow.ports.control_tables import (
    BatchControl,
    BatchNotFoundError,
    ControlTablesPort,
)
from cinqflow.ports.control_tables import SlaCycle as SlaCycleRow
from cinqflow.ports.metadata_db import MetadataDbPort, ObjectNotFoundError

#: The one sentence a caller gets for "not yours" and for "not there". Two
#: sentences would be an oracle for which feeds and batches exist.
OUT_OF_SCOPE = "out-of-scope-or-absent"


class ToolNotWhitelistedError(ToolError):
    """A tool the caller's agent may not use.

    At R0 the whitelist is read tools only, so this is what a request to retry
    a batch or edit a mapping becomes — a refusal with a reason, and a row.
    """


@dataclass(frozen=True)
class ToolResult:
    """What a tool returns, and where every value came from.

    `rows` are aggregates: counts, reasons, rule ids, column names, config.
    There is no shape here that can hold a member, and the catalogue-wide
    canary test proves it by seeding one and looking for it in every result.
    """

    tool: str
    rows: tuple[dict[str, Any], ...] = ()
    citations: tuple[CitationId, ...] = ()
    out_of_scope: bool = False
    marker: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.citations and not self.rows:
            raise ToolError(
                f"{self.tool} returned {len(self.citations)} citation(s) and no rows. A "
                "citation with nothing behind it is exactly what an ungrounded claim cites: "
                "the model would see `recon:8842` in the grounding and report a "
                "reconciliation that has not happened yet."
            )

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def as_grounding(self) -> str:
        """The result as evidence, with its citations attached inline."""
        if self.out_of_scope:
            return f"{self.tool}: {OUT_OF_SCOPE} (no data available to this caller)"
        if not self.rows:
            return f"{self.tool}: no rows"
        head = f"{self.tool} [{', '.join(str(c) for c in self.citations)}]"
        body = "\n".join(f"  {row}" for row in self.rows)
        return f"{head}\n{body}"


@dataclass
class ToolContext:
    """Everything a tool invocation needs, and nothing it does not.

    Note the absence of a database connection, an endpoint and a credential.
    A tool reaches operational truth through pins, and a model reaches a tool
    through a name and typed arguments.
    """

    principal: Principal
    control: ControlTablesPort
    metadata: MetadataDbPort
    run_id: str = "adhoc"
    agent: str = "pipeline-insight"
    whitelist: frozenset[str] = READ_ONLY_WHITELIST
    reference: ReferenceIndex = field(default_factory=platform_index)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def actor(self) -> Actor:
        return self.principal.as_actor()


def invoke(context: ToolContext, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
    """Validate, scope, execute, cite, audit. In that order, for all sixteen.

    Every exit from this function writes exactly one `audit.agent_action` row —
    including the refusals, which is the half people leave out. A ledger holding
    only permitted calls cannot answer "what did it try?", and that is the only
    evidence the R0 whitelist ever bound anything.
    """
    supplied = dict(arguments or {})

    if name not in context.whitelist:
        _audit(
            context,
            name,
            supplied,
            ActionOutcome.REFUSED_NOT_WHITELISTED,
            detail=f"{name} is not on this agent's whitelist",
        )
        raise ToolNotWhitelistedError(
            f"{name!r} is not on {context.agent}'s whitelist. This agent runs at R0 — "
            "it observes and explains. No write tool is on its whitelist at any confidence."
        )

    spec = spec_for(name)
    try:
        checked = spec.validate(supplied)
    except ArgumentError as bad:
        _audit(context, name, supplied, ActionOutcome.FAILED_SCHEMA, detail=str(bad))
        raise

    feed_id = _feed_in_scope(context, spec, checked)
    if feed_id is _DENIED:
        _audit(context, name, checked, ActionOutcome.REFUSED_PERMISSION, detail=OUT_OF_SCOPE)
        return ToolResult(tool=name, out_of_scope=True, marker=OUT_OF_SCOPE)

    result = _RUNNERS[name](context, checked)
    _audit(context, name, checked, ActionOutcome.COMPLETED, rows=result.row_count)
    return result


# ── scoping ──────────────────────────────────────────────────────────────────

_DENIED = object()


def _feed_in_scope(context: ToolContext, spec: ToolSpec, args: dict[str, Any]) -> Any:
    """Resolve the feed this call touches and check it BEFORE any query runs."""
    if not spec.scoped_by_feed:
        return None

    feed_id = args.get("feed_id")
    if feed_id is None and "batch_id" in args:
        try:
            feed_id = context.control.get_batch(str(args["batch_id"])).feed_id
        except BatchNotFoundError:
            # Indistinguishable from out-of-scope, deliberately.
            return _DENIED
    if feed_id is None and "error_id_hash" in args:
        # error: and file: citations carry no batch_id — the row itself is
        # the only thing that names a feed, so it has to be fetched once to
        # find out, exactly like the batch_id path above.
        found = context.control.find_error_by_hash(str(args["error_id_hash"]))
        if found is None:
            return _DENIED
        feed_id = context.control.get_batch(found.batch_id).feed_id
    if feed_id is None and "fingerprint" in args:
        input_file = context.control.find_input_by_fingerprint(str(args["fingerprint"]))
        if input_file is None:
            return _DENIED
        feed_id = input_file.feed_id
    if feed_id is None and "incident_id" in args:
        # get_incident carries no feed_id or batch_id of its own — the ledger
        # event is the only thing that names a feed, so it is read once to
        # find out, exactly like the batch_id and error_id_hash paths above.
        try:
            feed_id = context.metadata.get_incident_event(str(args["incident_id"])).feed_id
        except ObjectNotFoundError:
            return _DENIED
    if feed_id is None:
        return None
    return feed_id if context.principal.scopes.covers_feed(str(feed_id)) else _DENIED


def _audit(
    context: ToolContext,
    tool: str,
    arguments: dict[str, Any],
    outcome: ActionOutcome,
    *,
    rows: int = 0,
    detail: str = "",
) -> None:
    context.metadata.append_agent_action(
        AgentAction(
            run_id=context.run_id,
            agent=context.agent,
            action=f"tool:{tool}",
            outcome=outcome,
            actor=context.actor,
            occurred_ts=context.now,
            # Arguments and row count, as the story requires. Arguments are
            # identifiers and enums by construction, so this cannot log a value.
            detail=detail or f"args={arguments} rows={rows}",
        )
    )


def _since(context: ToolContext, args: dict[str, Any]) -> datetime:
    return context.now - timedelta(days=int(args.get("window_days", 30)))


# ── the sixteen ──────────────────────────────────────────────────────────────


def _list_feeds(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    rows = []
    citations = []
    for obj in context.metadata.list(ObjectType.FEED):
        # Filtered where the list is BUILT, never applied to a finished answer.
        if not context.principal.scopes.covers_feed(obj.object_id):
            continue
        if (domain := args.get("domain")) and obj.body.get("domain") != domain:
            continue
        if (state := args.get("state")) and obj.lifecycle_state.value != state:
            continue
        citation = CitationId(CitationKind.FEED, obj.object_id, version=obj.version)
        citations.append(citation)
        rows.append(
            {
                "feed_id": obj.object_id,
                "domain": obj.body.get("domain", ""),
                "source_system": obj.body.get("source_system", ""),
                "version": obj.version,
                "lifecycle_state": obj.lifecycle_state.value,
                "status": obj.lifecycle_state.status_word.value,
                "citation_id": str(citation),
            }
        )
    return ToolResult(tool="list_feeds", rows=tuple(rows), citations=tuple(citations))


def _get_feed(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    obj = _load_feed(context, args)
    if obj is None:
        return _absent("get_feed")
    record = feed_registry.from_governed(obj)
    citation = CitationId(CitationKind.FEED, obj.object_id, version=obj.version)
    return ToolResult(
        tool="get_feed",
        rows=(
            {
                "feed_id": record.feed_id,
                "domain": record.domain,
                "source_system": record.source_system,
                "file_format": record.file_format,
                "landing_path": record.landing_path,
                "file_pattern": record.file_pattern,
                "schedule_cron": record.schedule_cron,
                "version": obj.version,
                "lifecycle_state": obj.lifecycle_state.value,
                "status": obj.lifecycle_state.status_word.value,
                "citation_id": str(citation),
            },
        ),
        citations=(citation,),
    )


def _get_schema_contract(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    obj = _load(context, ObjectType.CONTRACT, str(args["feed_id"]), args.get("version"))
    if obj is None:
        return _absent("get_schema_contract")
    citation = CitationId(CitationKind.CONTRACT, obj.object_id, version=obj.version)
    columns = obj.body.get("columns", [])
    return ToolResult(
        tool="get_schema_contract",
        rows=tuple(
            {
                "column": column.get("name"),
                "type": column.get("type"),
                "nullable": column.get("nullable", True),
                "source_name": column.get("source_name"),
                # Whether a column HOLDS PHI is control-plane metadata about the
                # contract, not a value — so it is safe to state, and useful:
                # it is what an analyst needs to know before asking for a sample.
                "is_phi": column.get("is_phi", False),
                "citation_id": str(citation),
            }
            for column in columns
        ),
        citations=(citation,) if columns else (),
        note=f"{len(columns)} columns under contract v{obj.version}",
    )


def _get_dq_rules(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    obj = _load(context, ObjectType.DQ_RULE, str(args["feed_id"]))
    if obj is None:
        return _absent("get_dq_rules")
    rules = obj.body.get("rules", [])
    citations = tuple(CitationId(CitationKind.RULE, rule["rule_id"]) for rule in rules)
    return ToolResult(
        tool="get_dq_rules",
        rows=tuple(
            {
                "rule_id": rule["rule_id"],
                "name": rule.get("name", ""),
                "description": rule.get("description", ""),
                "severity": rule.get("severity", "low"),
                "columns": rule.get("columns", []),
                "citation_id": f"rule:{rule['rule_id']}",
            }
            for rule in rules
        ),
        citations=citations,
    )


def _get_compiled_plan(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    feed_obj = _load_feed(context, args)
    contract_obj = _load(context, ObjectType.CONTRACT, str(args["feed_id"]))
    rules_obj = _load(context, ObjectType.DQ_RULE, str(args["feed_id"]))
    if feed_obj is None or contract_obj is None:
        return _absent("get_compiled_plan")

    plan = compile_feed(
        feed=feed_registry.from_governed(feed_obj),
        feed_version=feed_obj.version,
        contract=contract_registry.from_governed(contract_obj),
        rules=contract_registry.rules_from_governed(rules_obj) if rules_obj else (),
    )
    citation = CitationId(CitationKind.PLAN, feed_obj.object_id, version=feed_obj.version)
    rows = tuple(
        {
            "position": position,
            "step": step.kind.value,
            "parameters": dict(step.parameters),
            "citation_id": str(citation),
        }
        for position, step in enumerate(plan.steps, start=1)
    )
    return ToolResult(
        tool="get_compiled_plan",
        rows=rows,
        citations=(citation,) if rows else (),
        note="\n".join(step.text for step in plan.narrate()),
    )


def _get_batch(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    try:
        batch = context.control.get_batch(str(args["batch_id"]))
    except BatchNotFoundError:
        return _absent("get_batch")
    citation = CitationId(CitationKind.BATCH, batch.batch_id)
    return ToolResult(
        tool="get_batch",
        rows=(
            {
                "batch_id": batch.batch_id,
                "feed_id": batch.feed_id,
                "feed_version": batch.feed_version,
                "business_date": batch.business_date,
                "state": batch.state.value,
                "status": batch.state.status_word.value,
                "started_ts": batch.started_ts.isoformat(),
                "completed_ts": batch.completed_ts.isoformat() if batch.completed_ts else None,
                "restart_count": batch.restart_count,
                "citation_id": str(citation),
            },
        ),
        citations=(citation,),
    )


def _list_batches(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    since = _since(context, args)
    batches = [
        batch
        for batch in context.control.list_batches(str(args["feed_id"]))
        if batch.started_ts >= since
    ]
    citations = tuple(CitationId(CitationKind.BATCH, b.batch_id) for b in batches)
    return ToolResult(
        tool="list_batches",
        rows=tuple(
            {
                "batch_id": b.batch_id,
                "business_date": b.business_date,
                "state": b.state.value,
                "status": b.state.status_word.value,
                "started_ts": b.started_ts.isoformat(),
                "citation_id": f"batch:{b.batch_id}",
            }
            for b in batches
        ),
        citations=citations,
    )


def _get_stage_status(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    batch_id = str(args["batch_id"])
    stages = context.control.get_stages(batch_id)
    citation = CitationId(CitationKind.BATCH, batch_id, fragment="stages")
    rows = tuple(
        {
            "stage": stage.stage.value,
            "state": stage.state.value,
            "status": stage.state.status_word.value,
            "records_in": stage.records_in,
            "records_out": stage.records_out,
            "quarantined": stage.quarantined,
            "attributed_drops": stage.attributed_drops,
            "completed_ts": stage.completed_ts.isoformat() if stage.completed_ts else None,
            "citation_id": str(citation),
        }
        for stage in stages
    )
    return ToolResult(tool="get_stage_status", rows=rows, citations=(citation,) if rows else ())


def _get_reconciliation(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    """The recon panel's tool: one stage-balance row PLUS one row per named
    drop, so `recon:<batch>#<rule>` (emitted by `get_drop_ledger`, and by the
    per-row citation here) can highlight a row that actually carries that
    rule_id. Homogeneous columns across both row kinds — `rule_id` blank on a
    balance row, `records_in` etc. blank on a drop row — because the UI drawer
    derives its table header from the FIRST row's keys alone; a row with keys
    the header doesn't have renders as a misaligned column, not a defect the
    UI can see.
    """
    batch_id = str(args["batch_id"])
    recons = context.control.get_reconciliation(batch_id)
    stage_citation = CitationId(CitationKind.RECON, batch_id)
    rows: list[dict[str, Any]] = []
    citations: list[CitationId] = [stage_citation] if recons else []
    for recon in recons:
        rows.append(
            {
                "stage": recon.stage.value,
                "records_in": recon.records_in,
                "records_out": recon.records_out,
                "quarantined": recon.quarantined,
                "attributed_drops": recon.attributed_drops,
                "balances": recon.balances,
                "unexplained": recon.unexplained,
                "rule_id": None,
                "reason": None,
                "record_count": None,
                "citation_id": str(stage_citation),
            }
        )
        for entry in recon.drop_ledger:
            drop_citation = CitationId(CitationKind.RECON, batch_id, fragment=entry.rule_id)
            citations.append(drop_citation)
            rows.append(
                {
                    "stage": recon.stage.value,
                    "records_in": None,
                    "records_out": None,
                    "quarantined": None,
                    "attributed_drops": None,
                    "balances": None,
                    "unexplained": None,
                    "rule_id": entry.rule_id,
                    "reason": entry.reason,
                    "record_count": entry.record_count,
                    "citation_id": str(drop_citation),
                }
            )
    return ToolResult(
        tool="get_reconciliation",
        rows=tuple(rows),
        citations=tuple(citations),
        note="rows_in == rows_out + quarantined + attributed_drops, every stage, every batch",
    )


def _get_drop_ledger(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    batch_id = str(args["batch_id"])
    rows: list[dict[str, Any]] = []
    citations: list[CitationId] = []
    for recon in context.control.get_reconciliation(batch_id):
        for entry in recon.drop_ledger:
            citation = CitationId(CitationKind.RECON, batch_id, fragment=entry.rule_id)
            citations.append(citation)
            rows.append(
                {
                    "stage": recon.stage.value,
                    "rule_id": entry.rule_id,
                    "reason": entry.reason,
                    "record_count": entry.record_count,
                    "citation_id": str(citation),
                }
            )
    return ToolResult(
        tool="get_drop_ledger",
        rows=tuple(rows),
        citations=tuple(citations),
        note="every drop is attributed to a named reason; there is no 'other' and no 'unknown'",
    )


def _list_errors(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    batch_id = str(args["batch_id"])
    errors = context.control.list_errors(batch_id)
    category = args.get("category")
    chosen = [e for e in errors if category is None or e.category.value == category]
    citations = tuple(CitationId(CitationKind.ERROR, e.error_id_hash) for e in chosen)
    return ToolResult(
        tool="list_errors",
        rows=tuple(
            {
                "error_id_hash": e.error_id_hash,
                "stage": e.stage.value,
                "category": e.category.value,
                # The message is a platform-authored sentence about a RULE, and
                # `record_key` is deliberately not returned: a key is a member.
                "message": e.message,
                "rule_id": e.rule_id,
                "occurred_ts": e.occurred_ts.isoformat(),
                "citation_id": f"error:{e.error_id_hash}",
            }
            for e in chosen
        ),
        citations=citations,
    )


def _get_quarantine_summary(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    batch_id = str(args["batch_id"])
    summaries = context.control.get_quarantine_summary(batch_id)
    citations = tuple(
        CitationId(CitationKind.RECON, batch_id, fragment=s.rule_id) for s in summaries
    )
    return ToolResult(
        tool="get_quarantine_summary",
        rows=tuple(
            {
                "stage": s.stage.value,
                "rule_id": s.rule_id,
                "reason": s.reason,
                "column_names": list(s.column_names),
                "record_count": s.record_count,
                "citation_id": f"recon:{batch_id}#{s.rule_id}",
            }
            for s in summaries
        ),
        citations=citations,
        note="counts, reasons, rule ids and column names — never row contents",
    )


def _get_error_by_hash(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    error = context.control.find_error_by_hash(str(args["error_id_hash"]))
    if error is None:
        return _absent("get_error_by_hash")
    citation = CitationId(CitationKind.ERROR, error.error_id_hash)
    return ToolResult(
        tool="get_error_by_hash",
        rows=(
            {
                "error_id_hash": error.error_id_hash,
                "batch_id": error.batch_id,
                "stage": error.stage.value,
                "category": error.category.value,
                "message": error.message,
                "rule_id": error.rule_id,
                "occurred_ts": error.occurred_ts.isoformat(),
                "citation_id": str(citation),
            },
        ),
        citations=(citation,),
    )


def _get_file_by_fingerprint(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    found = context.control.find_input_by_fingerprint(str(args["fingerprint"]))
    if found is None:
        return _absent("get_file_by_fingerprint")
    citation = CitationId(CitationKind.FILE, found.fingerprint)
    return ToolResult(
        tool="get_file_by_fingerprint",
        rows=(
            {
                "filename": found.filename,
                "fingerprint": found.fingerprint,
                "feed_id": found.feed_id,
                "batch_id": found.batch_id,
                "size_bytes": found.size_bytes,
                "state": found.state.value,
                "arrived_ts": found.arrived_ts.isoformat(),
                "rejection_reason": found.rejection_reason,
                "record_count": found.record_count,
                "unexpected": found.is_unexpected,
                "citation_id": str(citation),
            },
        ),
        citations=(citation,),
    )


def _get_input_registry(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    since = _since(context, args)
    files = [
        f for f in context.control.get_input_registry(str(args["feed_id"])) if f.arrived_ts >= since
    ]
    citations = tuple(CitationId(CitationKind.FILE, f.fingerprint) for f in files)
    return ToolResult(
        tool="get_input_registry",
        rows=tuple(
            {
                "filename": f.filename,
                "fingerprint": f.fingerprint,
                "size_bytes": f.size_bytes,
                "state": f.state.value,
                "arrived_ts": f.arrived_ts.isoformat(),
                "rejection_reason": f.rejection_reason,
                "record_count": f.record_count,
                "unexpected": f.is_unexpected,
                "citation_id": f"file:{f.fingerprint}",
            }
            for f in files
        ),
        citations=citations,
    )


def _list_batch_inputs(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    batch_id = str(args["batch_id"])
    files = context.control.list_batch_inputs(batch_id)
    citations = tuple(CitationId(CitationKind.FILE, f.fingerprint) for f in files)
    return ToolResult(
        tool="list_batch_inputs",
        rows=tuple(
            {
                "filename": f.filename,
                "fingerprint": f.fingerprint,
                "size_bytes": f.size_bytes,
                "state": f.state.value,
                "arrived_ts": f.arrived_ts.isoformat(),
                "rejection_reason": f.rejection_reason,
                "record_count": f.record_count,
                "unexpected": f.is_unexpected,
                "citation_id": f"file:{f.fingerprint}",
            }
            for f in files
        ),
        citations=citations,
    )


def _lookup_reference(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    found = context.reference.search(str(args["query"]), limit=int(args.get("limit", 5)))
    return ToolResult(
        tool="lookup_reference",
        rows=as_rows(found),
        citations=tuple(scored.entry.citation for scored in found),
        note="lexical match over approved reference data; the vector store is EMPTY in Wave 0",
    )


# ── Wave 2 · the ops ledgers, through the same six steps ─────────────────────
#
# A NOTE ON DUPLICATION. `get_reliability_score`, `get_certification` and
# `get_incident` each mirror wiring that already exists once in
# `api.app` (`_reliability_observations`, `_weights_of`, `_bands_of`,
# `_certification_checks`, `_incident_for`) and once in `workers.incidents`
# (`recovery_guides`, `priors_for`). Importing either would be the honest fix —
# and `.importlinter`'s `layers` contract forbids it both ways: `api` sits
# ABOVE `intelligence`, and `workers` sits ABOVE `intelligence` too, so a tool
# runner can call neither module's helpers, only the same `core` functions and
# port verbs they call. What follows is that same wiring, written once here,
# so a caller asking a model is answered by the identical arithmetic a human
# reading the screen would see — never a second, slightly different, opinion.


def _cycle_date(context: ToolContext, args: dict[str, Any]) -> tuple[date, str]:
    """A plain ISO day, never a free-text range. An omitted or unparseable
    value falls back to today — and says so in the note, because a fallback
    nobody can see is indistinguishable from a wrong answer."""
    raw = args.get("cycle_date")
    if not raw:
        return context.now.date(), ""
    try:
        return date.fromisoformat(str(raw)), ""
    except ValueError:
        return (
            context.now.date(),
            f"{raw!r} is not an ISO date (YYYY-MM-DD); showing today instead",
        )


def _cycle_from_sla_row(row: SlaCycleRow) -> sla_core.Cycle:
    """Mirrors `workers.sla._cycle_from_row` exactly — the adapter-boundary
    conversion this port's own row and `core.sla.Cycle` both need, and one of
    the two places it is written for the layering reason above."""
    return sla_core.Cycle(
        feed_id=row.feed_id,
        cycle_date=row.cycle_date,
        expected_ts=row.expected_ts,
        actual_ts=row.actual_ts,
        batch_id=row.batch_id,
        files_received=1 if row.actual_ts is not None else 0,
    )


def _get_arrival_board(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    feed_id = str(args["feed_id"])
    on, note = _cycle_date(context, args)
    cycles = tuple(
        _cycle_from_sla_row(row)
        for row in context.control.sla_instances(cycle_date=on, feed_ids=(feed_id,))
    )
    citation = CitationId(CitationKind.FEED, feed_id)
    if not cycles:
        return ToolResult(
            tool="get_arrival_board",
            note=note or f"no SLA cycle materialised for {feed_id} on {on.isoformat()}",
        )
    board = sla_core.ArrivalBoard(cycles=cycles, now=context.now)
    counters = board.counters()
    cycle = cycles[0]
    row = {
        "feed_id": feed_id,
        "cycle_date": on.isoformat(),
        **counters,
        "status": cycle.user_status(context.now).value,
        "why": cycle.why(context.now),
        "citation_id": str(citation),
    }
    return ToolResult(tool="get_arrival_board", rows=(row,), citations=(citation,), note=note)


def _get_sla_history(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    feed_id = str(args["feed_id"])
    days = int(args.get("window_days", 30))
    cycles = context.control.sla_history(feed_id, days=days)
    citation = CitationId(CitationKind.FEED, feed_id)
    rows = tuple(
        {
            "cycle_date": c.cycle_date.isoformat(),
            "expected_ts": c.expected_ts.isoformat(),
            "actual_ts": c.actual_ts.isoformat() if c.actual_ts else None,
            "sla_status": c.sla_status,
            "batch_id": c.batch_id,
            "citation_id": str(citation),
        }
        for c in cycles
    )
    return ToolResult(tool="get_sla_history", rows=rows, citations=(citation,) if rows else ())


def _recovery_guides(metadata: MetadataDbPort) -> tuple[fingerprinting.RecoveryGuide, ...]:
    """Mirrors `workers.incidents.recovery_guides` — published runbooks only.
    See the module note above for why this is written here rather than
    imported."""
    return tuple(
        fingerprinting.RecoveryGuide(
            guide_id=obj.object_id,
            title=str(obj.body.get("title") or obj.object_id),
            signatures=frozenset(str(s) for s in obj.body.get("signatures", ())),
            steps=tuple(str(step) for step in obj.body.get("steps", ())),
            remedy=(
                OpsAction(obj.body["remedy"]) if obj.body.get("remedy") in set(OpsAction) else None
            ),
            is_transient=bool(obj.body.get("is_transient", False)),
            stale=bool(obj.body.get("stale", False)),
        )
        for obj in metadata.list(ObjectType.RUNBOOK)
        if obj.lifecycle_state is LifecycleState.PUBLISHED
    )


def _priors_for(
    control: ControlTablesPort,
    *,
    feed_id: str,
    batch_id: str,
    errors: Sequence[ops_monitor.ErrorLike],
) -> tuple[fingerprinting.PriorIncident, ...]:
    """Mirrors `workers.incidents.priors_for` — how often this exact failure
    has happened on this feed before, computed rather than curated. See the
    module note above for why this is written here rather than imported."""
    cascade = ops_monitor.separate_cascade(errors)
    root = cascade.first
    if root is None:
        return ()
    found = fingerprinting.signature(
        stage=root.stage, category=root.category, message=root.message, rule_id=root.rule_id
    )
    priors: list[fingerprinting.PriorIncident] = []
    for prior in control.list_batches(feed_id, 200):
        if prior.batch_id == batch_id:
            continue
        prior_errors = list(control.list_errors(batch_id=prior.batch_id))
        prior_root = ops_monitor.separate_cascade(prior_errors).first
        if prior_root is None:
            continue
        if (
            fingerprinting.signature(
                stage=prior_root.stage,
                category=prior_root.category,
                message=prior_root.message,
                rule_id=prior_root.rule_id,
            )
            != found
        ):
            continue
        priors.append(
            fingerprinting.PriorIncident(
                incident_id=f"INC-{prior.batch_id}",
                occurred_ts=prior.started_ts,
                fix_minutes=(
                    int((prior.completed_ts - prior.started_ts).total_seconds() // 60)
                    if prior.completed_ts
                    else None
                ),
                batch_id=prior.batch_id,
            )
        )
    return tuple(priors)


def _get_incident(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    incident_id = str(args["incident_id"])
    try:
        event = context.metadata.get_incident_event(incident_id)
    except ObjectNotFoundError:
        return _absent("get_incident")

    errors = tuple(context.control.list_errors(batch_id=event.batch_id))
    computed = fingerprinting.fingerprint_batch(
        batch_id=event.batch_id,
        feed_id=event.feed_id,
        errors=errors,
        guides=_recovery_guides(context.metadata),
        history=_priors_for(
            context.control, feed_id=event.feed_id, batch_id=event.batch_id, errors=errors
        ),
        now=context.now,
    )
    try:
        incident = fingerprinting.hydrate(computed, event)
        note = ""
    except fingerprinting.FingerprintError:
        # Today's error log no longer recomputes the same signature the
        # ledger's event was opened with — the evidence below is still
        # honest, but folding decisions onto evidence they were not made
        # against would fabricate history, so they are omitted instead.
        incident = computed
        note = "recomputed evidence no longer matches the ledger's signature; decisions omitted"

    batch_citation = CitationId(CitationKind.BATCH, incident.batch_id)
    citations = [batch_citation]
    root = incident.root_cause
    if root is not None:
        citations.append(CitationId(CitationKind.ERROR, root.error_id_hash))
    row = {
        "incident_id": incident.incident_id,
        "batch_id": incident.batch_id,
        "feed_id": incident.feed_id,
        "kind": incident.kind.value,
        "state": incident.state.value,
        "signature": incident.signature,
        "root_cause_error_id_hash": root.error_id_hash if root else None,
        "root_cause_message": root.message if root else None,
        "consequence_count": len(incident.cascade.consequences),
        "guide_id": incident.match.guide.guide_id if incident.match else None,
        "prior_occurrences": incident.match.occurrences if incident.match else 0,
        "acknowledged_by": incident.acknowledged_by,
        "assigned_to": incident.assigned_to,
        "resolution": incident.resolution,
        "explanation": incident.explain(),
        "citation_id": str(batch_citation),
    }
    return ToolResult(tool="get_incident", rows=(row,), citations=tuple(citations), note=note)


#: "Open" for `list_incidents` — not yet resolved or closed. Acknowledged is
#: still open work; only a human's resolution or close moves an incident out
#: of this list, exactly as `IncidentState`'s own transition graph says.
_OPEN_INCIDENT_STATES = frozenset(
    {fingerprinting.IncidentState.OPEN, fingerprinting.IncidentState.ACKNOWLEDGED}
)


def _list_incidents(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    requested_feed = args.get("feed_id")
    events = context.metadata.list_incident_events(
        feed_id=str(requested_feed) if requested_feed else None, limit=50
    )
    rows = []
    citations = []
    for event in events:
        if event.state not in _OPEN_INCIDENT_STATES:
            continue
        # Filtered where the list is BUILT, never applied to a finished
        # answer — the same discipline `_list_feeds` uses.
        if not context.principal.scopes.covers_feed(event.feed_id):
            continue
        citation = CitationId(CitationKind.BATCH, event.batch_id)
        citations.append(citation)
        rows.append(
            {
                "incident_id": event.incident_id,
                "batch_id": event.batch_id,
                "feed_id": event.feed_id,
                "state": event.state.value,
                "signature": event.signature,
                "assigned_to": event.assigned_to,
                "opened_ts": event.opened_ts.isoformat(),
                "citation_id": str(citation),
            }
        )
    return ToolResult(tool="list_incidents", rows=tuple(rows), citations=tuple(citations))


def _reliability_observations(
    control: ControlTablesPort, feed_id: str
) -> dict[reliability.Signal, tuple[float, str, int]]:
    """Mirrors `api.app._reliability_observations` — six signals, each a
    control-plane query, absent from the map rather than zero when there is
    nothing to read. See the module note above for why this is written here
    rather than imported."""
    observations: dict[reliability.Signal, tuple[float, str, int]] = {}

    rule_history = control.rule_result_history(feed_id, limit=200)
    evaluated = sum(r.evaluated for r in rule_history)
    failed = sum(r.failed for r in rule_history)
    if evaluated > 0:
        observations[reliability.Signal.DQ] = (
            round(100.0 * (evaluated - failed) / evaluated, 1),
            f"{failed:,} of {evaluated:,} evaluations failed across {len(rule_history)} rule runs",
            len(rule_history),
        )

    cycles = control.sla_history(feed_id, days=90)
    if cycles:
        on_time = sum(1 for c in cycles if c.sla_status == "On-Time")
        observations[reliability.Signal.SLA] = (
            round(100.0 * on_time / len(cycles), 1),
            f"{on_time} of {len(cycles)} cycles on time over 90 days",
            len(cycles),
        )

    batches = control.list_batches(feed_id, 30)
    recons = [r for b in batches for r in control.get_reconciliation(b.batch_id)]
    if recons:
        balanced = sum(1 for r in recons if r.balances)
        observations[reliability.Signal.RECONCILIATION] = (
            round(100.0 * balanced / len(recons), 1),
            f"{balanced} of {len(recons)} stage reconciliations balanced",
            len(recons),
        )

    terminal = [b for b in batches if b.state in {BatchState.COMPLETED, BatchState.FAILED}]
    if terminal:
        completed = sum(1 for b in terminal if b.state is BatchState.COMPLETED)
        observations[reliability.Signal.PIPELINE] = (
            round(100.0 * completed / len(terminal), 1),
            f"{completed} of {len(terminal)} recent batches completed",
            len(terminal),
        )

    if batches:
        drifted = sum(
            1 for b in batches if any(d.blocked_batch for d in control.get_schema_drift(b.batch_id))
        )
        observations[reliability.Signal.SCHEMA] = (
            round(100.0 * (len(batches) - drifted) / len(batches), 1),
            f"{drifted} of {len(batches)} recent batches blocked by schema drift",
            len(batches),
        )

    # IDENTITY is deliberately absent until Wave 3 — unmeasured, never zero.
    return observations


def _get_reliability_score(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    feed_id = str(args["feed_id"])
    score = reliability.score_for(
        feed_id=feed_id,
        as_of=context.now.date(),
        observations=_reliability_observations(context.control, feed_id),
        weights=reliability.Weights(),
        bands=reliability.Bands(),
    )
    citation = score.citation
    rows = tuple(
        {
            "signal": component.signal.value,
            "value": component.value,
            "weight": component.weight,
            "measured": component.measured,
            "sample_size": component.sample_size,
            "evidence": component.evidence,
            "citation_id": str(citation),
        }
        for component in score.components
    )
    return ToolResult(
        tool="get_reliability_score",
        rows=rows,
        citations=(citation,) if rows else (),
        note=f"overall {score.overall} ({score.band.value}); confidence {score.confidence:.2f}",
    )


def _certification_checks(
    control: ControlTablesPort, batch: BatchControl
) -> tuple[batch_certification.Check, ...]:
    """Mirrors `api.app._certification_checks` — every check a control-plane
    read, INCOMPLETE (never assumed passed) when nothing is recorded yet. See
    the module note above for why this is written here rather than imported."""
    checks: list[batch_certification.Check] = []
    recons = control.get_reconciliation(batch.batch_id)
    balanced = all(r.balances for r in recons)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.BALANCE,
            passed=bool(recons) and balanced,
            completed=bool(recons),
            evidence=(
                f"rows_in == rows_out + quarantined + attributed_drops on {len(recons)} stage(s)"
                if recons
                else "no reconciliation recorded yet"
            ),
        )
    )
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.RECONCILIATION,
            passed=bool(recons) and all(r.unexplained == 0 for r in recons),
            completed=bool(recons),
            evidence=(
                f"{sum(r.unexplained for r in recons)} unexplained rows across "
                f"{len(recons)} stage(s)"
                if recons
                else "no reconciliation recorded yet"
            ),
        )
    )
    total_drops = sum(entry.record_count for recon in recons for entry in recon.drop_ledger)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.DROP_LEDGER,
            passed=bool(recons)
            and all(
                entry.rule_id not in {"other", "unknown", ""}
                for recon in recons
                for entry in recon.drop_ledger
            ),
            completed=bool(recons),
            evidence=f"{total_drops} excluded row(s), every one attributed to a rule",
        )
    )
    results = control.rule_results(batch.batch_id)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.DQ_RULES,
            passed=bool(results),
            completed=bool(results),
            evidence=(
                f"{len(results)} rule(s) recorded a verdict; "
                f"{sum(r.failed for r in results)} row(s) flagged, all attributed"
                if results
                else "no rule verdicts recorded — silence is not a pass"
            ),
        )
    )
    drift = control.get_schema_drift(batch.batch_id)
    checks.append(
        batch_certification.Check(
            kind=batch_certification.CheckKind.SCHEMA_CONTRACT,
            passed=not any(d.blocked_batch for d in drift),
            completed=True,
            evidence=(
                f"{len(drift)} drift finding(s), none blocking"
                if not any(d.blocked_batch for d in drift)
                else f"blocking drift: {', '.join(d.column_name for d in drift if d.blocked_batch)}"
            ),
        )
    )
    owed = next(
        (
            cycle
            for cycle in control.sla_history(batch.feed_id, days=90)
            if cycle.batch_id == batch.batch_id
        ),
        None,
    )
    if owed is not None:
        checks.append(
            batch_certification.Check(
                kind=batch_certification.CheckKind.SLA_WINDOW,
                passed=owed.sla_status == "On-Time",
                completed=True,
                evidence=f"cycle {owed.cycle_date.isoformat()} was {owed.sla_status}",
            )
        )
    return tuple(checks)


def _get_certification(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    batch_id = str(args["batch_id"])
    try:
        batch = context.control.get_batch(batch_id)
    except BatchNotFoundError:
        return _absent("get_certification")

    verdict = batch_certification.certify(
        batch_id=batch_id,
        feed_id=batch.feed_id,
        checks=_certification_checks(context.control, batch),
        variances=context.metadata.list_variances(batch_id=batch_id),
        now=context.now,
    )
    citation = verdict.citation
    rows = tuple(
        {
            "kind": check.kind.value,
            "passed": check.passed,
            "completed": check.completed,
            "evidence": check.evidence,
            "citation_id": str(citation),
        }
        for check in verdict.checks
    )
    return ToolResult(
        tool="get_certification",
        rows=rows,
        citations=(citation,) if rows else (),
        note=f"verdict {verdict.verdict.value}; publishable={verdict.publishable}",
    )


_RUNNERS = {
    "list_feeds": _list_feeds,
    "get_feed": _get_feed,
    "get_schema_contract": _get_schema_contract,
    "get_dq_rules": _get_dq_rules,
    "get_compiled_plan": _get_compiled_plan,
    "get_batch": _get_batch,
    "list_batches": _list_batches,
    "get_stage_status": _get_stage_status,
    "get_reconciliation": _get_reconciliation,
    "get_drop_ledger": _get_drop_ledger,
    "list_errors": _list_errors,
    "get_error_by_hash": _get_error_by_hash,
    "get_quarantine_summary": _get_quarantine_summary,
    "get_input_registry": _get_input_registry,
    "list_batch_inputs": _list_batch_inputs,
    "get_file_by_fingerprint": _get_file_by_fingerprint,
    "lookup_reference": _lookup_reference,
    "get_arrival_board": _get_arrival_board,
    "get_sla_history": _get_sla_history,
    "get_incident": _get_incident,
    "list_incidents": _list_incidents,
    "get_reliability_score": _get_reliability_score,
    "get_certification": _get_certification,
}

assert set(_RUNNERS) == set(CATALOGUE), "every declared tool has exactly one runner"


def _absent(tool: str) -> ToolResult:
    """The same shape a scope miss produces. Callers cannot tell them apart."""
    return ToolResult(tool=tool, out_of_scope=True, marker=OUT_OF_SCOPE)


def _load_feed(context: ToolContext, args: dict[str, Any]) -> Any:
    return _load(context, ObjectType.FEED, str(args["feed_id"]), args.get("version"))


def _load(
    context: ToolContext, object_type: ObjectType, object_id: str, version: int | None = None
) -> Any:
    try:
        obj = context.metadata.get(object_type, object_id, version)
    except ObjectNotFoundError:
        return None
    # An agent explains what the engine WILL run, and the engine reads published
    # metadata. Explaining a draft would describe a pipeline that cannot run.
    if version is None and obj.lifecycle_state is not LifecycleState.PUBLISHED:
        history = [o for o in context.metadata.history(object_type, object_id) if o.is_executable]
        return history[-1] if history else None
    return obj


def dq_rule_entries(rules: Sequence[dict[str, Any]]) -> tuple[ReferenceEntry, ...]:
    """Turn a feed's stored DQ rules into reference entries.

    `rules` is `GovernedObject(ObjectType.DQ_RULE, feed_id).body["rules"]` —
    the plain-dict shape `_get_dq_rules` already reads, not `DqRule`
    instances; there is no governed row anywhere holding the dataclass. The
    110 legacy rules already pair a natural-language description with
    executable SQL and a glossary link — which is exactly this shape.
    """
    return tuple(
        ReferenceEntry(
            slug=rule["rule_id"],
            term=rule["rule_id"],
            definition=(
                f"{rule.get('name', '')}: {rule.get('description', '')} "
                f"(severity {rule.get('severity', 'low')})"
            ),
            kind=CitationKind.RULE,
            source="dq-rule-registry",
            aliases=("dq rule", "rule", *rule.get("columns", [])),
        )
        for rule in rules
    )


def all_dq_rule_entries(metadata: MetadataDbPort) -> tuple[ReferenceEntry, ...]:
    """Every feed's DQ rules, for seeding a `ReferenceIndex` at request time.

    `lookup_reference` (CF-V0-E16-09) takes no `feed_id` — a rule_id like
    "DQ-002" is meant to resolve on its own — so the index has to hold every
    feed's rules, not just one caller's. Wave 0 runs at a scale (a handful of
    feeds) where re-reading them per request is the honest cost of that,
    rather than a cache invalidation problem to solve early.
    """
    entries: list[ReferenceEntry] = []
    for feed in metadata.list(ObjectType.FEED):
        try:
            rules_obj = metadata.get(ObjectType.DQ_RULE, feed.object_id)
        except ObjectNotFoundError:
            continue
        entries.extend(dq_rule_entries(rules_obj.body.get("rules", [])))
    return tuple(entries)
