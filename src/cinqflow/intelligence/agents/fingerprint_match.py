"""CF-V2-E12-04 — the fingerprint-match agent, wired.

    "a novel fingerprint that matches nothing -> retrieve the nearest prior
     narratives, and propose a draft runbook (R2, human approves)"
    — platformdata/wave2.md §2.2

The graph is data in `core/agents/fingerprint_match/graph.py`; these are the
node implementations, which touch pins. Four properties are enforced here and
tested independently:

  1. `_gather` and `_retrieve` reach NO model. A test walks this module's AST
     and asserts neither body reaches the gateway.
  2. A KNOWN incident (a guide already matched, deterministically, before this
     agent was ever called) never reaches the graph at all — `propose` refuses
     to execute it, so there is no node, no tool call and no model call to
     account for.
  3. `retrieve`'s tool calls are FIXED — `graph.RETRIEVE_TOOLS` — never
     model-planned, and every one is read-only. Nothing this agent does can
     reach a write tool, at any confidence, because nothing here chooses a
     tool at call time for a model to choose wrongly.
  4. THE DRAFT NEVER EXECUTES ANYTHING. A proposed `remedy` is an `OpsAction`
     IDENTIFIER on the drafted guide — a name the ACTION SURFACE understands —
     never a call. `RecoveryGuide` carries no executable field, and this
     module contains no path to one.

Nothing here writes anything but a proposal. The only object this module
constructs and submits is a `core.proposals.Proposal` at R2; the RUNBOOK it
proposes does not exist until a data steward approves it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from cinqflow.core.agents.fingerprint_match.graph import (
    AGENT,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    MAX_NEAR_MISS,
    NODE_DRAFT,
    NODE_GATHER,
    NODE_NARRATE,
    NODE_RETRIEVE,
    RETRIEVE_TOOLS,
    RISK_CLASS,
    STATE_HAS_GROUNDING,
    STATE_NOVEL,
)
from cinqflow.core.citations import CitationId
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.operations import fingerprint as fingerprinting
from cinqflow.core.operations.actions import OpsAction
from cinqflow.core.proposals import Proposal, submit
from cinqflow.core.tools import ToolError
from cinqflow.intelligence.action_gateway import ActionGateway
from cinqflow.intelligence.gateway import LlmGateway, ManualPathRequiredError
from cinqflow.intelligence.tools import ToolContext, ToolResult, invoke
from cinqflow.ports.agent_runtime import AgentRuntimePort, Edge, GraphSpec

#: The actor a proposal is created by. AI, always — same rule every R2 agent
#: follows, and `core.proposals.Proposal.__post_init__` enforces it besides.
AGENT_ACTOR = Actor(subject=AGENT, actor_type=ActorType.AI, display_name="Fingerprint match")

#: `retrieve`'s narrower-than-the-catalogue whitelist. Read-only either way,
#: but there is no reason to grant the whole certified surface to a node whose
#: calls are hardcoded rather than model-planned — least privilege the gateway
#: can express without a story asking it to.
_RETRIEVE_GATEWAY = ActionGateway(whitelist=frozenset(RETRIEVE_TOOLS), risk_class=RISK_CLASS.name)


@dataclass(frozen=True)
class DraftedGuide:
    """The model's candidate, after the platform decided what survives.

    `refusals` is not decoration — a discarded or floor-gated remedy is a
    finding a reviewer should be able to see, exactly like
    `mapping_suggestion.SuggestionResult.refusals`.
    """

    guide: fingerprinting.RecoveryGuide
    confidence: float
    rationale: str
    citations: tuple[CitationId, ...] = ()
    refusals: tuple[str, ...] = ()

    def as_payload(self, incident: fingerprinting.Incident) -> dict[str, Any]:
        """`proposals.Proposal.payload` — one record, the shape every proposal
        uses, so a later eval over corrections needs no second shape."""
        return {
            "key": "guide_id",
            "incident_id": incident.incident_id,
            "signature": incident.signature,
            "records": [
                {
                    "guide_id": self.guide.guide_id,
                    "title": self.guide.title,
                    "steps": list(self.guide.steps),
                    "remedy": self.guide.remedy.value if self.guide.remedy else None,
                    "is_transient": self.guide.is_transient,
                    "signatures": sorted(self.guide.signatures),
                    "confidence": self.confidence,
                    "rationale": self.rationale,
                }
            ],
            "refusals": list(self.refusals),
        }


@dataclass(frozen=True)
class FingerprintMatchResult:
    """What one run produced — or, for a KNOWN incident, did not."""

    incident: fingerprinting.Incident
    proposal: Proposal | None
    drafted: DraftedGuide | None
    model_called: bool
    #: The graph's terminal model call (`draft`) could not be reached even
    #: after the gateway's one repair. A FIRST-CLASS FIELD, for the reason
    #: `mapping_suggestion.SuggestionResult.manual_path` states: without it, a
    #: run that produced nothing is indistinguishable from one with nothing to
    #: propose.
    manual_path: bool = False
    near_miss_count: int = 0


def _no_op(incident: fingerprinting.Incident) -> FingerprintMatchResult:
    return FingerprintMatchResult(
        incident=incident, proposal=None, drafted=None, model_called=False
    )


@dataclass
class FingerprintMatchAgent:
    """One gateway, one tool context, one runtime. No credentials of its own."""

    llm: LlmGateway
    tools: ToolContext
    runtime: AgentRuntimePort
    action_gateway: ActionGateway = field(default_factory=lambda: _RETRIEVE_GATEWAY)
    confidence_floor: float = CONFIDENCE_FLOOR

    # ── the graph ────────────────────────────────────────────────────────────

    def graph(self) -> GraphSpec:
        """`Edge`/`GraphSpec` are built HERE, not in `core/agents/...`. See
        `core.agents.fingerprint_match.graph`'s own note: core sits below
        `cinqflow.ports` in `.importlinter`'s layers, so the port's dataclasses
        cannot be imported there even though they hold no I/O. `pipeline_insight`
        made the identical choice for the identical reason."""
        return GraphSpec(
            name=AGENT,
            nodes={
                NODE_GATHER: self._gather,
                NODE_RETRIEVE: self._retrieve,
                NODE_NARRATE: self._narrate,
                NODE_DRAFT: self._draft,
            },
            edges=(
                Edge(NODE_GATHER, NODE_RETRIEVE),
                Edge(NODE_RETRIEVE, NODE_NARRATE, when=STATE_HAS_GROUNDING),
                Edge(NODE_RETRIEVE, NODE_DRAFT, when=STATE_NOVEL),
                Edge(NODE_NARRATE, NODE_DRAFT),
            ),
            entrypoint=NODE_GATHER,
            terminal=NODE_DRAFT,
        )

    def propose(
        self,
        incident: fingerprinting.Incident,
        *,
        caller: Actor,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> FingerprintMatchResult:
        """A NOVEL incident in, one `proposals.proposal` row out.

        A KNOWN incident — `incident.kind` computed, deterministically, by
        `core.operations.fingerprint.match_guide` before this was ever called
        — is refused before the graph runs at all. Not routed to a `declined`
        state inside the graph, the way Wave 0's agent handles a question it
        will not answer: there is nothing to explain here, no tool call to
        make and no model to ask, so there is no node to run.

        An incident with NO signature at all — a batch flagged failed with
        nothing logged in `control.error_log` — is `IncidentKind.NOVEL` too
        (`match` is `None` either way), but there is no fingerprint to draft a
        guide FOR. Refused the same way: `draft_guide_from` would raise on an
        empty signature, and a data-integrity gap upstream is not this
        agent's don't to interpret.
        """
        if incident.kind is not fingerprinting.IncidentKind.NOVEL or not incident.signature:
            return _no_op(incident)

        run = run_id or str(uuid.uuid4())
        stamp = now or datetime.now(UTC)
        self.tools.run_id = run
        self.tools.now = stamp

        try:
            result = self.runtime.execute(
                self.graph(), {"incident": incident, "run_id": run, "caller": caller}
            )
        except ManualPathRequiredError as escalated:
            self._record(
                run,
                caller,
                stamp,
                ActionOutcome.ESCALATED_TO_MANUAL,
                f"{incident.incident_id}: could not draft a candidate guide — {escalated}",
            )
            return FingerprintMatchResult(
                incident=incident,
                proposal=None,
                drafted=None,
                model_called=True,
                manual_path=True,
            )

        drafted: DraftedGuide = result.state["draft"]
        for refusal in drafted.refusals:
            self._record(run, caller, stamp, ActionOutcome.REFUSED_NOT_WHITELISTED, refusal)

        proposal = submit(
            Proposal(
                proposal_id=str(uuid.uuid4()),
                agent=AGENT,
                capability=CAPABILITY,
                risk_class=RISK_CLASS,
                run_id=run,
                feed_id=incident.feed_id,
                payload=drafted.as_payload(incident),
                created_by=AGENT_ACTOR,
                created_ts=stamp,
                confidence=drafted.confidence,
                grounding_citations=(incident.citation, *drafted.citations),
            ),
            now=stamp,
        )
        stored = self.tools.metadata.record_proposal(proposal)
        return FingerprintMatchResult(
            incident=incident,
            proposal=stored,
            drafted=drafted,
            model_called=True,
            manual_path=bool(result.state.get("narrate_manual_path")),
            near_miss_count=len(result.state.get("near_miss_incidents", ())),
        )

    # ── node 1 · gather (NO MODEL) ────────────────────────────────────────────

    def _gather(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic. This node reaches no model, and a test proves it.

        `evidence_bundle()` already exists on `Incident` — built for exactly
        this: "presents the evidence bundle organized for a human", and this
        agent is the one caller that hands it to a model instead, unchanged.
        """
        incident: fingerprinting.Incident = state["incident"]
        return {"evidence": incident.evidence_bundle()}

    # ── node 2 · retrieve (NO MODEL) ──────────────────────────────────────────

    def _retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        """Deterministic. `RETRIEVE_TOOLS` is fixed — never model-planned — so
        there is no plan step where a model could ask for a fourth tool.

        Two calls, both read-only, both certified:

          - `list_incidents` for a sibling still open with this EXACT
            fingerprint (`get_incident` fills in its detail);
          - `lookup_reference` for what the platform's own glossary and
            DQ-rule catalogue say about the category or rule involved.

        Neither is the semantic retrieval CF-V2-E16-04/07 will eventually add
        over closed incident narratives — that corpus does not exist yet. This
        degrades to what IS certified today, honestly: `has_grounding` is
        False rather than a fabricated signal when nothing is genuinely novel
        to say.

        `has_grounding` is driven by NEAR-MISS SIBLINGS ALONE, not by a
        reference hit. Every `ErrorCategory` and `Layer` has a platform-
        generated glossary definition (`core.retrieval.platform_glossary`), so
        a reference query built from the category ALONE matches something on
        almost every incident — if that counted as grounding, `narrate` would
        run on nearly every draft to say, in effect, nothing. Reference hits
        still reach the model: `_pack_retrieval` folds them into `draft`'s
        context on BOTH branches, unconditionally. What the branch decides is
        only whether there is a genuine "this has already happened elsewhere
        and stayed open" sentence for `narrate` to write.
        """
        incident: fingerprinting.Incident = state["incident"]
        citations: list[CitationId] = []
        near_miss: list[dict[str, Any]] = []

        siblings = self._call("list_incidents", {"feed_id": incident.feed_id})
        same_fingerprint = [
            row
            for row in siblings.rows
            if row.get("incident_id") != incident.incident_id
            and row.get("signature") == incident.signature
        ]
        for row in same_fingerprint[:MAX_NEAR_MISS]:
            detail = self._call("get_incident", {"incident_id": str(row["incident_id"])})
            if not detail.is_empty:
                near_miss.append(detail.rows[0])
                citations.extend(detail.citations)

        query = _reference_query(incident)
        reference = self._call("lookup_reference", {"query": query, "limit": 3}) if query else None
        reference_hits = reference.rows if reference is not None else ()
        if reference is not None:
            citations.extend(reference.citations)

        has_grounding = bool(near_miss)
        return {
            "near_miss_incidents": tuple(near_miss),
            "reference_hits": tuple(reference_hits),
            "retrieved_citations": tuple(citations),
            STATE_HAS_GROUNDING: has_grounding,
            STATE_NOVEL: not has_grounding,
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
            # The tool refused the arguments this deterministic node built —
            # a platform defect, not data to guess around. `invoke` already
            # wrote the audit row; degrade to "nothing retrieved" rather than
            # crash a run over one sibling lookup.
            return ToolResult(tool=name)

    # ── node 3 · narrate (small) ──────────────────────────────────────────────

    def _narrate(self, state: dict[str, Any]) -> dict[str, Any]:
        """Reached only when `retrieve` found something worth a sentence."""
        result_text, manual_path = self._complete_or_degrade(
            prompt_id="fingerprint-match.narrate",
            caller=state["caller"],
            run_id=state["run_id"],
            context=_pack_retrieval(state),
            input_text=_evidence_text(state["evidence"]),
        )
        if manual_path:
            return {"narrative": "", "narrative_citations": (), "narrate_manual_path": True}

        narrated = result_text if isinstance(result_text, dict) else {}
        narrative = str(narrated.get("narrative", "")).strip()
        # Resolved back to the real `CitationId` objects `retrieve` produced —
        # never the model's own strings — the same rule that keeps a claim's
        # citation clickable rather than merely a token the model echoed back.
        available = {str(c): c for c in state.get("retrieved_citations", ())}
        cited = tuple(
            available[raw] for raw in narrated.get("citations", []) if str(raw) in available
        )
        return {"narrative": narrative, "narrative_citations": cited, "narrate_manual_path": False}

    def _complete_or_degrade(
        self, *, prompt_id: str, caller: Actor, run_id: str, context: str, input_text: str
    ) -> tuple[Any, bool]:
        """`narrate` may degrade to the manual path without failing the run —
        `draft` still runs, from the evidence bundle alone. Only `draft`'s OWN
        call is allowed to propagate `ManualPathRequiredError`, because a
        drafted guide is the one thing this agent must produce."""
        try:
            completed = self.llm.complete(
                agent=AGENT,
                run_id=run_id,
                prompt_id=prompt_id,
                caller=caller,
                context=context,
                input_text=input_text,
            )
        except ManualPathRequiredError:
            return None, True
        return completed.value, False

    # ── node 4 · draft (large) ────────────────────────────────────────────────

    def _draft(self, state: dict[str, Any]) -> dict[str, Any]:
        """The terminal node. Reached from EVERY run this agent's `propose`
        starts — with a narrative when `retrieve` found precedent, without one
        when it did not. Either way, a draft is what this agent is for."""
        incident: fingerprinting.Incident = state["incident"]
        grounding = _pack_retrieval(state)
        if state.get("narrative"):
            grounding = f"{grounding}\n\nRETRIEVED NARRATIVE:\n{state['narrative']}"

        # No try/except here: if the model cannot produce a valid draft even
        # after the gateway's one repair, `ManualPathRequiredError` propagates
        # to `propose`, which is the one caller allowed to report "nothing was
        # proposed" rather than let this degrade silently into an empty guide.
        completed = self.llm.complete(
            agent=AGENT,
            run_id=state["run_id"],
            prompt_id="fingerprint-match.draft",
            caller=state["caller"],
            context=grounding,
            input_text=_evidence_text(state["evidence"]),
        )
        drafted_raw = completed.value if isinstance(completed.value, dict) else {}
        guide, confidence, rationale, refusals = _build_guide(
            incident, drafted_raw, floor=self.confidence_floor
        )
        citations = tuple(
            dict.fromkeys(
                (*state.get("retrieved_citations", ()), *state.get("narrative_citations", ()))
            )
        )
        return {
            "draft": DraftedGuide(
                guide=guide,
                confidence=confidence,
                rationale=rationale,
                citations=citations,
                refusals=tuple(refusals),
            )
        }

    # ── audit ────────────────────────────────────────────────────────────────

    def _record(
        self, run_id: str, caller: Actor, now: datetime, outcome: ActionOutcome, detail: str
    ) -> None:
        self.tools.metadata.append_agent_action(
            AgentAction(
                run_id=run_id,
                agent=AGENT,
                action=CAPABILITY,
                outcome=outcome,
                actor=caller,
                occurred_ts=now,
                risk_class=RISK_CLASS.name,
                detail=detail,
            )
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _reference_query(incident: fingerprinting.Incident) -> str:
    """A lexical query built from the SAME normalisation `signature()` uses —
    reused rather than re-derived, so the search terms and the fingerprint
    can never describe two different readings of the same error."""
    root = incident.root_cause
    if root is None:
        return ""
    tokens = fingerprinting.normalise(root.message).split()[: fingerprinting.SIGNATURE_TOKENS]
    terms = [root.category.value, *([root.rule_id] if root.rule_id else []), *tokens]
    return " ".join(term for term in terms if term)


def _pack_retrieval(state: dict[str, Any]) -> str:
    lines: list[str] = []
    for row in state.get("near_miss_incidents", ()):
        lines.append(
            f"[{row.get('citation_id')}] near-miss incident {row.get('incident_id')}: "
            f"{row.get('explanation') or row.get('root_cause_message') or 'no detail recorded'}"
        )
    for row in state.get("reference_hits", ()):
        lines.append(f"[{row.get('citation_id')}] {row.get('term')}: {row.get('definition')}")
    return "\n".join(lines) or "no precedent retrieved — this failure is genuinely novel"


def _evidence_text(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, default=str, sort_keys=True)


def _build_guide(
    incident: fingerprinting.Incident, raw: dict[str, Any], *, floor: float
) -> tuple[fingerprinting.RecoveryGuide, float, str, list[str]]:
    """The PLATFORM decides what survives — same discipline
    `mapping_suggestion._from_inference` applies to a proposed target.

    Built on `fingerprint.draft_guide_from` for the guide id and the frozen
    signature set — the SAME derivation a human's post-resolution draft uses,
    so a human-authored draft and this agent's draft of the SAME incident
    land on the SAME `guide_id` and version each other rather than collide.
    What this agent adds beyond that helper is the one thing a resolution-time
    draft correctly refuses to guess at all: a REMEDY, and only above the
    confidence floor.
    """
    refusals: list[str] = []
    title = str(raw.get("title") or "").strip() or _fallback_title(incident)
    steps = tuple(str(step).strip() for step in raw.get("steps", ()) if str(step).strip())
    if not steps:
        steps = ("No steps were proposed — read the evidence bundle and decide.",)
    confidence = float(raw.get("confidence", 0.0))
    rationale = str(raw.get("rationale", "")).strip()

    base = fingerprinting.draft_guide_from(incident, title=title, steps=steps)

    remedy: OpsAction | None = None
    proposed_remedy = str(raw.get("remedy") or "").strip()
    if proposed_remedy and proposed_remedy not in set(OpsAction):
        refusals.append(
            f"{incident.incident_id}: the model proposed remedy {proposed_remedy!r}, which is "
            "not a certified OpsAction. Discarded — title and steps still stand."
        )
    elif proposed_remedy and confidence < floor:
        refusals.append(
            f"{incident.incident_id}: a remedy was proposed ({proposed_remedy}) but confidence "
            f"{confidence:.2f} is below the platform's floor of {floor:.2f}. Dropped — a wrong "
            "remedy on a guide nobody has seen before is a wrong action wearing the platform's "
            "authority the first time anyone runs it; the title and steps are unaffected."
        )
    elif proposed_remedy:
        remedy = OpsAction(proposed_remedy)

    guide = replace(base, remedy=remedy, is_transient=bool(raw.get("is_transient", False)))
    return guide, confidence, rationale, refusals


def _fallback_title(incident: fingerprinting.Incident) -> str:
    root = incident.root_cause
    if root is None:
        return f"Novel failure on {incident.batch_id}"
    return f"Novel {root.category.value.lower()} failure at {root.stage.value}"


__all__ = [
    "AGENT_ACTOR",
    "DraftedGuide",
    "FingerprintMatchAgent",
    "FingerprintMatchResult",
]
