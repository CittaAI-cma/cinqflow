"""CF-V2-E12-05 — alerts that explain themselves, wired.

    "no supported hypothesis exists the alert still ships, carrying
     'cause: under investigation'"
    "an uncited hypothesis is refused before dispatch"
    "enrichment never replaces the existing channels"
    — platformdata/wave2.md §4.5, §CF-V2-E12-05

The graph is data in `core/agents/alert_enrichment/graph.py`; these are the
node implementations, which touch pins. Four properties are enforced here and
tested independently:

  1. `_group`, `_retrieve` and `_compose` reach NO model. A test walks this
     module's AST and asserts none of their bodies reach the gateway.
  2. `_hypothesise` may skip its own call entirely — when `_retrieve` found
     nothing to ground a hypothesis in, asking the model would only spend
     money to produce something `_compose` would have to discard anyway.
  3. `_compose` ALWAYS produces a complete answer. Whatever `_hypothesise`
     did — succeeded, returned an uncited guess, failed outright, or never
     ran at all — `_compose` runs next regardless (it is not behind a
     conditional edge; every path through this graph reaches it), and its
     output always carries a non-blank `cause`. THE FALLBACK IS THE LITERAL
     STRING `graph.CAUSE_UNDER_INVESTIGATION`, always, with no citations
     attached to it — and `EnrichedAlert.manual_path` says so plainly, so a
     degraded run can never be mistaken for a well-supported one.
  4. THIS MODULE WRITES NOTHING. There is no `core.proposals.submit` call and
     no `metadata.record_proposal` call anywhere in it — checked over the
     import graph, not reasoned about from a docstring — because R0 is a
     HARDER constraint than R2, not a lighter one.

Nothing this agent returns is ever dispatched anywhere: `EnrichedAlert` is a
value a caller reads, exactly like `pipeline_insight.Answer`. The alert
ITSELF was already raised by `workers.sla.SlaWorker.sweep`, over the SAME
notification channel that exists today — enrichment adds to what a person
reads; it does not gate whether they are paged at all.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cinqflow.core import sla as sla_core
from cinqflow.core.agents.alert_enrichment.graph import (
    ACTION,
    AGENT,
    CAUSE_UNDER_INVESTIGATION,
    MAX_GROUP_FEEDS,
    NODE_COMPOSE,
    NODE_GROUP,
    NODE_HYPOTHESISE,
    NODE_RETRIEVE,
    RETRIEVE_TOOLS,
    RISK_CLASS,
)
from cinqflow.core.citations import CitationId
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor
from cinqflow.core.tools import ToolError
from cinqflow.intelligence.action_gateway import ActionGateway
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.intelligence.tools import ToolContext, ToolResult, invoke
from cinqflow.ports.agent_runtime import AgentRuntimePort, Edge, GraphSpec

#: `retrieve`'s narrower-than-the-catalogue whitelist. Read-only either way,
#: but calls here are hardcoded rather than model-planned, so there is no
#: reason to grant the whole certified surface to a node that never chooses
#: a tool at call time.
_RETRIEVE_GATEWAY = ActionGateway(whitelist=frozenset(RETRIEVE_TOOLS), risk_class=RISK_CLASS.name)

#: Rank for picking the group's worst severity — matches `workers.sla`'s own
#: `_RANK`, kept local rather than imported: that dict is a worker-module
#: implementation detail of building a `notification.Alert`, not a shared
#: vocabulary this agent should depend on.
_SEVERITY_RANK: dict[sla_core.AlertSeverity, int] = {
    sla_core.AlertSeverity.INFO: 0,
    sla_core.AlertSeverity.WARNING: 1,
    sla_core.AlertSeverity.CRITICAL: 2,
}


@dataclass(frozen=True)
class EnrichedAlert:
    """One grouped alert, explained — or honestly not.

    `citations` is the group's OWN evidence — each member alert's own
    citation, always present, whatever `hypothesise` did. `cause_citations`
    is what specifically grounds `cause`, and is EMPTY exactly when
    `manual_path` is True: the two can never disagree, because `_compose`
    sets both from the same decision, in the same place.
    """

    group_key: str
    feed_ids: tuple[str, ...]
    severity: str
    facts: tuple[str, ...]
    cause: str
    citations: tuple[CitationId, ...] = ()
    cause_citations: tuple[CitationId, ...] = ()
    #: True whenever `cause` is the platform's own fallback rather than a
    #: grounded hypothesis. A FIRST-CLASS FIELD, for the reason
    #: `mapping_suggestion.SuggestionResult.manual_path` and
    #: `fingerprint_match.FingerprintMatchResult.manual_path` both state:
    #: without it, a degraded run is indistinguishable from a careful one.
    manual_path: bool = False
    #: Whether `hypothesise` attempted a model call at all — False when
    #: `retrieve` found nothing to ground a hypothesis in, in which case
    #: nothing was ever asked and nothing was ever spent.
    model_called: bool = False
    refusals: tuple[str, ...] = ()
    cost_usd: str = "0"

    def as_text(self) -> str:
        header = f"{len(self.feed_ids)} feed(s) affected: {'; '.join(self.facts)}"
        return f"{header}\ncause: {self.cause}"


@dataclass
class AlertEnrichmentAgent:
    """One gateway, one tool context, one runtime. No credentials of its own."""

    llm: LlmGateway
    tools: ToolContext
    runtime: AgentRuntimePort
    action_gateway: ActionGateway = field(default_factory=lambda: _RETRIEVE_GATEWAY)

    # ── the graph ────────────────────────────────────────────────────────────

    def graph(self) -> GraphSpec:
        """`Edge`/`GraphSpec` are built HERE, not in `core/agents/...` — see
        `core.agents.alert_enrichment.graph`'s own note on the `layers`
        contract. Linear: every group reaches every node, in order — there is
        no branch, unlike `fingerprint_match`."""
        return GraphSpec(
            name=AGENT,
            nodes={
                NODE_GROUP: self._group,
                NODE_RETRIEVE: self._retrieve,
                NODE_HYPOTHESISE: self._hypothesise,
                NODE_COMPOSE: self._compose,
            },
            edges=(
                Edge(NODE_GROUP, NODE_RETRIEVE),
                Edge(NODE_RETRIEVE, NODE_HYPOTHESISE),
                Edge(NODE_HYPOTHESISE, NODE_COMPOSE),
            ),
            entrypoint=NODE_GROUP,
            terminal=NODE_COMPOSE,
        )

    def enrich(
        self,
        group_key: str,
        alerts: tuple[sla_core.SlaAlert, ...],
        *,
        caller: Actor,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> EnrichedAlert:
        """A GROUP of alerts in — `core.sla.grouped`'s own output shape — one
        explanation out. Never a proposal, never a write.

        An empty group is a caller error, not a case this agent interprets:
        there is nothing to explain and no evidence to have retrieved,
        exactly like `fingerprint_match.propose`'s refusal of an incident
        with no signature.
        """
        if not alerts:
            raise ValueError("alert_enrichment.enrich: an empty group is not a group")

        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)
        self.tools.run_id = run
        self.tools.now = stamp

        result = self.runtime.execute(
            self.graph(),
            {"group_key": group_key, "alerts": alerts, "run_id": run, "caller": caller},
        )
        enriched: EnrichedAlert = result.state["enriched"]
        for refusal in enriched.refusals:
            self._record(run, caller, stamp, ActionOutcome.ESCALATED_TO_MANUAL, refusal)
        return enriched

    # ── node 1 · group (NO MODEL) ─────────────────────────────────────────────

    def _group(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic. ADAPTS the already-grouped alerts `core.sla.grouped`
        handed this agent into this agent's own state shape — it does not
        recompute the partition, and it does not re-derive severity or
        summaries `core.sla.alerts_for` already computed.

        `feed_ids` is de-duplicated but ORDER-PRESERVING — `sorted` would be
        simpler but would silently discard the order `core.sla.grouped`
        already chose for its members.
        """
        alerts: tuple[sla_core.SlaAlert, ...] = state["alerts"]
        feed_ids = tuple(dict.fromkeys(alert.feed_id for alert in alerts))
        severity = max(alerts, key=lambda alert: _SEVERITY_RANK[alert.severity]).severity.value
        citations = tuple(dict.fromkeys(c for alert in alerts for c in alert.citations))
        return {
            "feed_ids": feed_ids,
            "severity": severity,
            "facts": tuple(alert.summary for alert in alerts),
            "base_citations": citations,
        }

    # ── node 2 · retrieve (NO MODEL) ──────────────────────────────────────────

    def _retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic. `RETRIEVE_TOOLS` is fixed — never model-planned —
        so there is no plan step where a model could ask for a fourth tool.

        Bounded to `MAX_GROUP_FEEDS` feeds: history and a reliability score
        per feed, plus ONE fleet-wide `list_incidents` call filtered locally
        to this group's feeds — cheaper than one call per feed for a tool
        whose own contract is already fleet-wide (`scoped_by_feed=False`).

        Every note is prefixed with the citation that backs it, so
        `hypothesise`'s grounding text and its available-citations set can
        never name two different things.
        """
        feed_ids: tuple[str, ...] = state["feed_ids"]
        citations: list[CitationId] = []
        notes: list[str] = []

        for feed_id in feed_ids[:MAX_GROUP_FEEDS]:
            history = self._call("get_sla_history", {"feed_id": feed_id})
            if not history.is_empty:
                not_on_time = sum(1 for row in history.rows if row["sla_status"] != "On-Time")
                notes.append(
                    f"[{history.citations[0]}] {feed_id}: {not_on_time} of "
                    f"{len(history.rows)} recent cycles were not on time"
                )
                citations.extend(history.citations)

            score = self._call("get_reliability_score", {"feed_id": feed_id})
            measured = [row for row in score.rows if row["measured"]]
            if measured:
                weakest = min(measured, key=lambda row: row["value"] * row["weight"])
                notes.append(
                    f"[{score.citations[0]}] {feed_id} reliability: {score.note}; "
                    f"weakest signal {weakest['signal']} — {weakest['evidence']}"
                )
                citations.extend(score.citations)

        incidents = self._call("list_incidents", {})
        for row, citation in zip(incidents.rows, incidents.citations, strict=True):
            if row.get("feed_id") not in feed_ids:
                continue
            notes.append(f"[{citation}] open incident on {row['feed_id']}: {row['state']}")
            citations.append(citation)

        return {
            "retrieved_notes": tuple(notes),
            "retrieved_citations": tuple(dict.fromkeys(citations)),
        }

    def _call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        permission = self.action_gateway.permit(name)
        if not permission:  # pragma: no cover - defensive; RETRIEVE_TOOLS is fixed and read-only
            self._record(
                self.tools.run_id,
                self.tools.actor,
                self.tools.now,
                permission.outcome,
                f"{name}: {permission.reason}",
            )
            return ToolResult(tool=name)
        try:
            return invoke(self.tools, name, arguments)
        except ToolError:
            # A platform defect in the arguments THIS deterministic node
            # built, not data to guess around. `invoke` already wrote the
            # audit row; degrade to "nothing retrieved" rather than crash a
            # run over one feed's history lookup.
            return ToolResult(tool=name)

    # ── node 3 · hypothesise (MAY call a model) ───────────────────────────────

    def _hypothesise(self, state: dict[str, Any]) -> dict[str, Any]:
        """The one node that may reach a model — and it may also choose not
        to. Skips its own call when `retrieve` found nothing to ground a
        hypothesis in: asking the model to explain zero evidence only
        produces something `compose` would have to discard anyway, at the
        cost of a real call nobody needed.

        A schema failure or timeout is caught HERE, not left to propagate —
        `compose` must run regardless, which is the whole point of the
        exception path this agent exists to honour.
        """
        available: tuple[CitationId, ...] = state.get("retrieved_citations", ())
        raw: dict[str, Any] | None = None
        called = False
        failed = False

        if available:
            called = True
            try:
                completed = self.llm.complete(
                    agent=AGENT,
                    run_id=state["run_id"],
                    prompt_id="alert-enrichment.hypothesise",
                    caller=state["caller"],
                    context=_pack_retrieval(state),
                    input_text=_facts_text(state),
                )
            except ManualPathRequiredError:
                failed = True
            else:
                raw = completed.value if isinstance(completed.value, dict) else None

        return {
            "hypothesis_raw": raw,
            "hypothesise_called": called,
            "hypothesise_failed": failed,
            # Read in `compose`, which may not touch `self.llm` at all — see
            # `graph.DETERMINISTIC_NODES`. Computed here, where the budget
            # actually moved, rather than re-derived from nothing there.
            "cost_usd": str(self.llm.spent_this_run(state["run_id"])),
        }

    # ── node 4 · compose (NO MODEL) ───────────────────────────────────────────

    def _compose(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic, and the terminal node. ALWAYS runs, and ALWAYS
        produces a complete answer — whatever `hypothesise` did or did not
        manage. This is the literal reading of wave2.md's own line: 'a
        composer that needs the model cannot degrade.'
        """
        available: tuple[CitationId, ...] = state.get("retrieved_citations", ())
        cause, cause_citations, refusals = _build_cause(state.get("hypothesis_raw"), available)

        if state.get("hypothesise_failed"):
            refusals = (
                f"{state['group_key']}: the model could not produce a valid cause hypothesis — "
                f"the alert ships as {CAUSE_UNDER_INVESTIGATION!r} instead",
                *refusals,
            )

        enriched = EnrichedAlert(
            group_key=state["group_key"],
            feed_ids=state["feed_ids"],
            severity=state["severity"],
            facts=state["facts"],
            cause=cause,
            citations=state.get("base_citations", ()),
            cause_citations=cause_citations,
            manual_path=cause == CAUSE_UNDER_INVESTIGATION,
            model_called=bool(state.get("hypothesise_called")),
            refusals=refusals,
            cost_usd=str(state.get("cost_usd", "0")),
        )
        return {"enriched": enriched}

    # ── audit ────────────────────────────────────────────────────────────────

    def _record(
        self, run_id: str, caller: Actor, now: datetime, outcome: ActionOutcome, detail: str
    ) -> None:
        self.tools.metadata.append_agent_action(
            AgentAction(
                run_id=run_id,
                agent=AGENT,
                action=ACTION,
                outcome=outcome,
                actor=caller,
                occurred_ts=now,
                risk_class=RISK_CLASS.name,
                detail=detail,
            )
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _pack_retrieval(state: dict[str, Any]) -> str:
    notes: tuple[str, ...] = state.get("retrieved_notes", ())
    return "\n".join(notes) or "nothing retrieved — no history, score or incident to ground a cause"


def _facts_text(state: dict[str, Any]) -> str:
    return json.dumps(
        {
            "group_key": state["group_key"],
            "feed_ids": state["feed_ids"],
            "severity": state["severity"],
            "facts": state["facts"],
        },
        default=str,
        sort_keys=True,
    )


def _build_cause(
    raw: dict[str, Any] | None, available: tuple[CitationId, ...]
) -> tuple[str, tuple[CitationId, ...], tuple[str, ...]]:
    """The PLATFORM decides what survives, not the model — the same
    discipline `pipeline_insight._keep_only_grounded` and
    `fingerprint_match._build_guide` both apply to their own model output.

    Returns the fallback, with NO citations, whenever `raw` is absent, blank,
    or cites nothing `retrieve` actually returned. There is no fourth outcome:
    a hypothesis either survives whole, with at least one real citation, or
    it does not survive at all.
    """
    if not raw:
        return CAUSE_UNDER_INVESTIGATION, (), ()

    cause = str(raw.get("cause", "")).strip()
    if not cause:
        return CAUSE_UNDER_INVESTIGATION, (), ()

    lookup = {str(c): c for c in available}
    cited: list[CitationId] = []
    refusals: list[str] = []
    for candidate in raw.get("citations", []):
        key = str(candidate)
        if key in lookup:
            cited.append(lookup[key])
        else:
            refusals.append(
                f"a hypothesis citing {candidate!r} was discarded — no tool returned that citation"
            )

    if not cited:
        refusals.append(
            f"a hypothesis ({cause!r}) carried no valid citation and was refused before "
            f"dispatch — the alert ships as {CAUSE_UNDER_INVESTIGATION!r} instead"
        )
        return CAUSE_UNDER_INVESTIGATION, (), tuple(refusals)

    return cause, tuple(dict.fromkeys(cited)), tuple(refusals)


__all__ = ["AlertEnrichmentAgent", "EnrichedAlert"]
