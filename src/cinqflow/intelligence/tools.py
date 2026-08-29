"""CF-V0-E16-09 — the catalogue, executed. Scope inside the query, citation on
every row, an audit entry for every invocation including the refusals.

    "Apply the caller's RBAC scopes INSIDE the tool, before the query runs —
     the restriction lives in the query, not in the answer."
    "an out-of-scope feed_id returns empty with an explicit marker (never a
     partial result, never an error that reveals the feed exists)"
    "Write every tool invocation to audit.agent_action with caller identity,
     tool name, arguments and row count."
    — CF-V0-E16-09

The executor is ONE function with a dispatch table, not fourteen public
methods, because every guarantee this story makes is a guarantee about the
SURFACE: validate, scope, execute, cite, audit — in that order, for all
fourteen, with no path that skips a step. Fourteen public methods would be
fourteen places to forget the audit row.

A note on scoping a batch: a caller asks about `batch:8842` without naming a
feed, so the executor resolves the batch to its feed and checks THAT. The
resolution read never reaches the caller — an out-of-scope batch returns the
same empty, explicitly-marked result as a batch that does not exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.compiler import compile_feed
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.identity import Principal
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
from cinqflow.ports.control_tables import BatchNotFoundError, ControlTablesPort
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
    """Validate, scope, execute, cite, audit. In that order, for all fourteen.

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


# ── the fourteen ─────────────────────────────────────────────────────────────


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
    batch_id = str(args["batch_id"])
    recons = context.control.get_reconciliation(batch_id)
    citation = CitationId(CitationKind.RECON, batch_id)
    rows = tuple(
        {
            "stage": recon.stage.value,
            "records_in": recon.records_in,
            "records_out": recon.records_out,
            "quarantined": recon.quarantined,
            "attributed_drops": recon.attributed_drops,
            "balances": recon.balances,
            "unexplained": recon.unexplained,
            "citation_id": str(citation),
        }
        for recon in recons
    )
    return ToolResult(
        tool="get_reconciliation",
        rows=rows,
        citations=(citation,) if rows else (),
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


def _lookup_reference(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    found = context.reference.search(str(args["query"]), limit=int(args.get("limit", 5)))
    return ToolResult(
        tool="lookup_reference",
        rows=as_rows(found),
        citations=tuple(scored.entry.citation for scored in found),
        note="lexical match over approved reference data; the vector store is EMPTY in Wave 0",
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
    "get_quarantine_summary": _get_quarantine_summary,
    "get_input_registry": _get_input_registry,
    "lookup_reference": _lookup_reference,
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


def dq_rule_entries(rules: Sequence[Any]) -> tuple[ReferenceEntry, ...]:
    """Turn approved DQ rules into reference entries.

    The 110 legacy rules already pair a natural-language description with
    executable SQL and a glossary link — which is exactly this shape.
    """
    return tuple(
        ReferenceEntry(
            slug=rule.rule_id,
            term=rule.rule_id,
            definition=f"{rule.name}: {rule.description} (severity {rule.severity.value})",
            kind=CitationKind.RULE,
            source="dq-rule-registry",
            aliases=("dq rule", "rule", *rule.columns),
        )
        for rule in rules
    )
