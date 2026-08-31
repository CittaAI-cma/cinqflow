"""The seeded demo plane, and the agent that runs on it. NO CREDENTIALS.

Rung 0 — nothing runs but Python. This exists so the workspace, the CLI and the
UI's end-to-end tests can all show the same Fidelis anchor without a database,
a container or a key.

It lives BELOW the API on purpose. The CLI needs the seeded plane and the agent;
it does not need an HTTP server, and an installer reaching up into `api/` to get
one would invert the layering the whole platform is built on.

It is not a deployment. `cinqflow install --profile profiles/local.yaml` stands
up the real rung-0.5 plane; this stands up a picture of one.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.core import mapping as mapping_core
from cinqflow.core.agents.fingerprint_match.graph import AGENT as FINGERPRINT_MATCH_AGENT_NAME
from cinqflow.core.agents.fingerprint_match.prompts import TEMPLATES as FINGERPRINT_TEMPLATES
from cinqflow.core.agents.mapping_suggestion.graph import (
    AGENT as MAPPING_SUGGESTION_AGENT_NAME,
)
from cinqflow.core.agents.mapping_suggestion.graph import (
    CAPABILITY as MAPPING_SUGGESTION_CAPABILITY,
)
from cinqflow.core.agents.mapping_suggestion.graph import NO_CONFIDENT_TARGET
from cinqflow.core.agents.mapping_suggestion.prompts import TEMPLATES as MAPPING_TEMPLATES
from cinqflow.core.agents.phi_detection.prompts import TEMPLATES as PHI_TEMPLATES
from cinqflow.core.agents.pipeline_insight.prompts import TEMPLATES
from cinqflow.core.agents.schema_inference.prompts import TEMPLATES as SCHEMA_TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.model.vocabulary import (
    ActorType,
    BatchState,
    ErrorCategory,
    FileState,
    Layer,
    RiskClass,
)
from cinqflow.core.proposals import Proposal
from cinqflow.core.proposals import submit as submit_proposal
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry.contract import ContractColumn, DqRule, SchemaContract, Severity
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.schema_spec import TypeName
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
from cinqflow.intelligence.agents.schema_inference import SchemaInferenceAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.tools import ToolContext
from cinqflow.ports.control_tables import (
    BatchControl,
    ControlTablesPort,
    DropLedgerEntry,
    ErrorRecord,
    InputFile,
    QuarantineSummary,
    Reconciliation,
    StageStatus,
)
from cinqflow.ports.metadata_db import MetadataDbPort

BUDGET = Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5.00"))

FEED_ID = "fidelis-downstate-roster"
BATCH_ID = "8842"

#: W1-31 (CF-V1-E6-03) — a SEPARATE feed from `FEED_ID`, deliberately. That
#: feed's absence of a mapping is itself the assertion in
#: `wave1-intake.spec.ts` ("the mapping editor says there is no mapping
#: rather than showing an empty one"), and giving it a published mapping here
#: would turn that assertion false out from under it. The write-capable
#: proposal-review screen needs its own real mapping-suggestion PROPOSAL to
#: act on, so it gets its own feed to act on it against.
MAPPING_REVIEW_FEED_ID = "meridian-member-roster"
MAPPING_PROPOSAL_ID = "mapping-suggestion-meridian-member-roster-1"

AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun Menon")
REVIEWER = Actor(
    subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya Nair"
)
MAPPING_SUGGESTION_ACTOR = Actor(
    subject=MAPPING_SUGGESTION_AGENT_NAME,
    actor_type=ActorType.AI,
    display_name="Mapping suggestion",
)

#: `fingerprint_match_agent_for`'s tool-scope principal. Not duplicated from
#: `workers.incidents.PLATFORM_SUBJECT` — `intelligence` sits BELOW `workers`
#: in the layers contract, so this module may not import that one. Same
#: literal subject, kept in step by convention rather than by import.
PLATFORM_PRINCIPAL = Principal(
    subject="platform@cinqflow", display_name="platform", scopes=Scopes(feeds=frozenset({"*"}))
)


def _published(obj: GovernedObject) -> GovernedObject:
    """Through the real lifecycle, not by setting a field.

    Seeding a Published object directly would bypass the two universal
    negatives, and a demo that bypasses governance is a demo of something else.
    """
    reviewed, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=AUTHOR)
    approved, _ = reviewed.transition_to(LifecycleState.APPROVED, actor=REVIEWER)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=REVIEWER)
    return published


def seed(store: MetadataDbPort, control: ControlTablesPort) -> None:
    now = datetime.now(UTC)

    store.save(
        _published(
            FeedRecord(
                feed_id=FEED_ID,
                domain="membership",
                source_system="fidelis",
                file_format="xlsx",
                landing_path="landing/fidelis/roster",
                # The leading underscore is not a typo. Incident #1.
                file_pattern=r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.xlsx$",
                schedule_cron="0 6 1 * *",
                sample_filename="_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
            ).as_governed(author=AUTHOR)
        )
    )
    store.save(
        _published(
            contract_registry.contract_as_governed(
                SchemaContract(
                    feed_id=FEED_ID,
                    version=1,
                    columns=(
                        ContractColumn(
                            name="source_member_id",
                            type=TypeName.STRING,
                            nullable=False,
                            is_phi=True,
                        ),
                        ContractColumn(name="date_of_birth", type=TypeName.DATE, is_phi=True),
                        ContractColumn(name="plan_code", type=TypeName.STRING),
                        ContractColumn(name="effective_date", type=TypeName.DATE),
                    ),
                    key_columns=("source_member_id",),
                ),
                author=AUTHOR,
            )
        )
    )
    store.save(
        _published(
            contract_registry.rules_as_governed(
                FEED_ID,
                (
                    DqRule(
                        rule_id="DQ-002",
                        name="date_of_birth present",
                        description="Every member must carry a date of birth.",
                        severity=Severity.CRITICAL,
                        columns=("date_of_birth",),
                    ),
                    DqRule(
                        rule_id="DQ-014",
                        name="plan_code known",
                        description="plan_code should match the payer's published plan list.",
                        severity=Severity.LOW,
                        columns=("plan_code",),
                    ),
                ),
                author=AUTHOR,
            )
        )
    )
    # W1-33: `MAPPING_TEMPLATES` joins the loop the moment this plane grows a
    # real caller of `MappingSuggestionAgent.propose` (`workers.drift.
    # propose_mapping_for_unmapped_columns`, via `mapping_suggestion_agent_
    # for` below) — before this slab nothing on ANY plane ever called it, so
    # the gap went unnoticed.
    for template in (
        *TEMPLATES,
        *SCHEMA_TEMPLATES,
        *PHI_TEMPLATES,
        *FINGERPRINT_TEMPLATES,
        *MAPPING_TEMPLATES,
    ):
        store.save(_published(template.as_governed(author=AUTHOR)))

    started = now - timedelta(hours=9)
    control.open_batch(
        BatchControl(
            batch_id=BATCH_ID,
            feed_id=FEED_ID,
            feed_version=1,
            business_date="2026-08-01",
            state=BatchState.COMPLETED,
            started_ts=started,
            completed_ts=started + timedelta(minutes=4),
        )
    )
    for layer, out in ((Layer.BRONZE, 22_000), (Layer.SILVER_RAW, 21_820)):
        control.record_stage(
            StageStatus(
                batch_id=BATCH_ID,
                stage=layer,
                state=BatchState.COMPLETED,
                started_ts=started,
                completed_ts=started + timedelta(minutes=2),
                records_in=22_000,
                records_out=out,
                quarantined=175 if layer is Layer.SILVER_RAW else 0,
                attributed_drops=5 if layer is Layer.SILVER_RAW else 0,
            )
        )
    control.record_reconciliation(
        Reconciliation(
            batch_id=BATCH_ID,
            stage=Layer.SILVER_RAW,
            records_in=22_000,
            records_out=21_820,
            quarantined=175,
            attributed_drops=5,
            drop_ledger=(
                DropLedgerEntry(rule_id="DQ-002", reason="missing date_of_birth", record_count=175),
                DropLedgerEntry(
                    rule_id="STRUCTURE-001", reason="short row: 3 of 4 columns", record_count=5
                ),
            ),
        )
    )
    control.record_quarantine(
        QuarantineSummary(
            batch_id=BATCH_ID,
            stage=Layer.SILVER_RAW,
            rule_id="DQ-002",
            reason="missing date_of_birth",
            column_names=("date_of_birth",),
            record_count=175,
        )
    )
    control.record_error(
        ErrorRecord(
            error_id_hash="c0ffee42",
            batch_id=BATCH_ID,
            stage=Layer.SILVER_RAW,
            category=ErrorCategory.VALIDATION,
            message="rows failed DQ-002 (date_of_birth present)",
            occurred_ts=started + timedelta(minutes=3),
            rule_id="DQ-002",
        )
    )
    control.register_input_file(
        InputFile(
            batch_id=BATCH_ID,
            feed_id=FEED_ID,
            key="landing/fidelis/roster/processed/_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
            filename="_CINQDOWNSTATE_Member_Roster_20260801.xlsx",
            size_bytes=4_120_448,
            fingerprint="a41f9c2e",
            state=FileState.PROCESSED,
            arrived_ts=started,
            record_count=22_000,
        )
    )
    _seed_mapping_review(store, now=now)


def _seed_mapping_review(store: MetadataDbPort, *, now: datetime) -> None:
    """W1-31 (CF-V1-E6-03) — a real, pending mapping-suggestion PROPOSAL, so
    the first write-capable proposal-review screen has something genuine to
    act on: edit a line's target, accept a loss, submit, approve, publish.

    Built the same way `tests/pipeline/test_proposals_on_the_real_plane.py`
    builds its fixtures — a `Proposal` constructed directly and carried to
    PENDING_REVIEW through `proposals.submit`, never through the agent's own
    `propose()` — because `mapping_suggestion_factory` is wired to no LLM pin
    on the dev socket (rung 0's own "nothing runs but Python"), the same
    absence `phi_detection_factory` and `rule_authoring_factory` already have
    here. That gap is a demo-wiring question for a later slab, not this one.

    `MAPPING_REVIEW_FEED_ID` already carries a PUBLISHED v1 (below), so the
    proposal can pose a real CF-V1-E6-04 question: it repeats v1's two settled
    columns verbatim, proposes a fresh (if middling-confidence) target for the
    one v1 left unmapped, and — the point of the exercise — proposes DROPPING
    the source for a field v1 populates. A reviewer keeping that drop is
    exactly the silent-row-loss scenario `accepts_loss` exists to make
    someone name out loud, not click past.
    """
    store.save(
        _published(
            mapping_core.mapping_as_governed(
                mapping_core.FeedMapping(
                    feed_id=MAPPING_REVIEW_FEED_ID,
                    version=1,
                    lines=(
                        mapping_core.MappingLine(
                            target_entity="members",
                            target_field="source_member_id",
                            source_columns=("member_id",),
                            confidence=1.0,
                            notes="Direct match on name and shape.",
                        ),
                        mapping_core.MappingLine(
                            target_entity="members",
                            target_field="date_of_birth",
                            source_columns=("dob",),
                            confidence=1.0,
                            notes="Direct match on name and shape.",
                        ),
                        mapping_core.MappingLine(
                            target_entity="members",
                            target_field="effective_date",
                            source_columns=("eff_dt",),
                            confidence=1.0,
                            notes="Direct match on name and shape.",
                        ),
                        mapping_core.MappingLine(
                            target_entity="members",
                            target_field="line_of_business",
                            unmapped_reason=(
                                "No payer-supplied plan taxonomy at initial onboarding."
                            ),
                        ),
                    ),
                ),
                author=AUTHOR,
            )
        )
    )
    records = [
        {
            "source_column": "member_id",
            "target_entity": "members",
            "target_field": "source_member_id",
            "unmapped": False,
            "unmapped_reason": "",
            "glossary_id": None,
            "confidence": 1.0,
            "settled_by": "published_mapping",
            "rationale": "Matches this feed's own currently published mapping.",
            "like_feed_id": None,
        },
        {
            "source_column": "dob",
            "target_entity": "members",
            "target_field": "date_of_birth",
            "unmapped": False,
            "unmapped_reason": "",
            "glossary_id": None,
            "confidence": 1.0,
            "settled_by": "published_mapping",
            "rationale": "Matches this feed's own currently published mapping.",
            "like_feed_id": None,
        },
        {
            "source_column": "eff_dt",
            "target_entity": "members",
            "target_field": "effective_date",
            "unmapped": True,
            "unmapped_reason": NO_CONFIDENT_TARGET,
            "glossary_id": None,
            "confidence": 0.0,
            "settled_by": "inference",
            "rationale": (
                "The refreshed sample's eff_dt values no longer parse as dates — three of "
                "five rows read 'TBD'."
            ),
            "like_feed_id": None,
        },
        {
            "source_column": "plan_cd",
            "target_entity": "members",
            "target_field": "line_of_business",
            "unmapped": False,
            "unmapped_reason": "",
            "glossary_id": None,
            "confidence": 0.82,
            "settled_by": "inference",
            "rationale": (
                "plan_cd's three-letter values (HMO, PPO, EPO) match the shape "
                "line_of_business takes on every other approved mapping."
            ),
            "like_feed_id": None,
        },
    ]
    store.record_proposal(
        submit_proposal(
            Proposal(
                proposal_id=MAPPING_PROPOSAL_ID,
                agent=MAPPING_SUGGESTION_AGENT_NAME,
                capability=MAPPING_SUGGESTION_CAPABILITY,
                risk_class=RiskClass.R2,
                run_id="seed-mapping-suggestion-1",
                feed_id=MAPPING_REVIEW_FEED_ID,
                payload={"records": records},
                created_by=MAPPING_SUGGESTION_ACTOR,
                created_ts=now - timedelta(hours=2),
                # The weakest column, not the mean — `eff_dt`'s proposed drop
                # is the one line a reviewer must actually look at.
                confidence=0.0,
            ),
            now=now - timedelta(hours=2),
        )
    )


def plane() -> tuple[MemMetadataDb, MemStoreControlTables]:
    store = MemMetadataDb()
    control = MemStoreControlTables()
    seed(store, control)
    return store, control


def agent_for(
    principal: Principal, control: ControlTablesPort, metadata: MetadataDbPort
) -> PipelineInsightAgent:
    """One agent per caller. A shared agent would be a shared scope."""
    return PipelineInsightAgent(
        llm=LlmGateway(
            llm=ScriptedLlm(responder=scripted),
            phi_scrub=PatternPhiScrub(),
            metadata_db=metadata,
            observability=NoopObservability(),
            budget=BUDGET,
            routing=Routing(small="mock-small", large="mock-large"),
        ),
        tools=ToolContext(principal=principal, control=control, metadata=metadata),
        runtime=InProcAgentRuntime(),
    )


def fingerprint_match_agent_for(
    control: ControlTablesPort, metadata: MetadataDbPort
) -> FingerprintMatchAgent:
    """W2-38's real wiring, built the SAME way `agent_for` builds
    `PipelineInsightAgent` — one `LlmGateway` over the one scripted stand-in,
    never a second parallel way of assembling one.

    ONE AGENT, not one per caller, unlike `agent_for`: `IncidentWorker` calls
    this off a batch failure, which has no principal in hand — a batch
    failing is the platform's own signal, not a request a person made.
    `PLATFORM_PRINCIPAL` is the scope this agent's certified tool calls run
    under; `caller` on `propose()` itself is `workers.incidents`'s own
    `PLATFORM_ACTOR`, named at the call site, not here.
    """
    return FingerprintMatchAgent(
        llm=LlmGateway(
            llm=ScriptedLlm(responder=scripted),
            phi_scrub=PatternPhiScrub(),
            metadata_db=metadata,
            observability=NoopObservability(),
            budget=BUDGET,
            routing=Routing(small="mock-small", large="mock-large"),
        ),
        tools=ToolContext(
            principal=PLATFORM_PRINCIPAL,
            control=control,
            metadata=metadata,
            agent=FINGERPRINT_MATCH_AGENT_NAME,
        ),
        runtime=InProcAgentRuntime(),
    )


def mapping_suggestion_agent_for(metadata: MetadataDbPort) -> MappingSuggestionAgent:
    """W1-33's real wiring — the SAME `LlmGateway` shape `fingerprint_match_
    agent_for` builds above, never a second parallel way of assembling one.

    Unlike that agent (and `agent_for`'s `PipelineInsightAgent`),
    `MappingSuggestionAgent` carries no `ToolContext`: `propose`'s own module
    docstring says it plainly — "the only object it constructs is a
    `Proposal`" — there is no certified tool for it to call.

    ONE AGENT per caller of THIS function, matching `fingerprint_match_
    agent_for` rather than `agent_for`: `workers.drift.propose_mapping_for_
    unmapped_columns`, the trigger this feeds, has no principal in hand
    either — an UNMAPPED_COLUMN finding is the platform's own signal off a
    batch that already ran, not a request a person made.
    """
    return MappingSuggestionAgent(
        llm=LlmGateway(
            llm=ScriptedLlm(responder=scripted),
            phi_scrub=PatternPhiScrub(),
            metadata_db=metadata,
            observability=NoopObservability(),
            budget=BUDGET,
            routing=Routing(small="mock-small", large="mock-large"),
        ),
        metadata=metadata,
    )


def schema_inference_for(metadata: MetadataDbPort) -> SchemaInferenceAgent:
    """CF-V1-E5-02 on the dev server, with the same scripted stand-in.

    Worth wiring even against a mock: the DETERMINISTIC half is real, so a
    demo file whose columns the glossary names produces a genuine proposal
    with no model involved at all — which is the story's own argument,
    demonstrable without a credential.
    """
    return SchemaInferenceAgent(
        llm=LlmGateway(
            llm=ScriptedLlm(responder=scripted),
            phi_scrub=PatternPhiScrub(),
            metadata_db=metadata,
            observability=NoopObservability(),
            budget=BUDGET,
            routing=Routing(small="mock-small", large="mock-large"),
        ),
        metadata=metadata,
    )


def scripted(prompt: str, task_class: Any) -> str:
    """A deterministic stand-in so the dev server needs no credential.

    It answers ONLY from the citations present in the grounding — it cannot
    invent one, because it copies them. That is the honest shape for a mock:
    shape-valid and content-free, never plausible-sounding.
    """
    if "Classify the question" in prompt:
        question = prompt.rsplit("# input", 1)[-1].lower()
        if any(word in question for word in ("retry", "pause", "rerun", "reprocess")):
            return json.dumps({"intent": "declined", "declined_capability": "write_action"})
        if "select " in question or " sql" in question:
            return json.dumps({"intent": "declined", "declined_capability": "free_form_sql"})
        if any(word in question for word in ("member", "date of birth", "dob", "ssn")):
            return json.dumps({"intent": "declined", "declined_capability": "member_level_data"})
        if "batch" in question or "row" in question or "lose" in question:
            return json.dumps({"intent": "explain_run", "batch_id": BATCH_ID})
        if "plan" in question:
            return json.dumps({"intent": "explain_plan", "feed_id": FEED_ID})
        if "feed" in question or FEED_ID in question:
            return json.dumps({"intent": "explain_feed", "feed_id": FEED_ID})
        return json.dumps({"intent": "define_term", "term": question.strip()[:60]})

    if "Choose which of the available certified tools" in prompt:
        available = re.search(r"available tools: (.+)", prompt)
        listed = available.group(1) if available else ""
        tools = [name.strip() for name in listed.split(",") if name.strip()]
        return json.dumps({"calls": [{"tool": tool} for tool in tools[:3]]})

    # `fingerprint_match_agent_for`'s two prompts. Matched on each template's
    # own IDENTITY text (`core.agents.fingerprint_match.prompts`), which
    # `core.prompts.assemble` always includes verbatim — not on `task_class`,
    # which this agent shares with no story-specific meaning of its own.
    if "incident narrator" in prompt:
        # Content-free, same discipline as the citations branch below: no
        # near-miss sentence is invented, only ever quoted from `retrieve`'s
        # own findings — and this mock quotes nothing, so it says nothing.
        return json.dumps({"narrative": "", "citations": []})

    if "recovery-guide drafting assistant" in prompt:
        # `remedy` is left unset on purpose: a mock that guessed a real
        # `OpsAction` would be indistinguishable from a model that meant it.
        # `confidence` is a NUMBER, unlike the citations branch's `"medium"`
        # below — `_build_guide` does `float(raw["confidence"])`, and this is
        # the one branch that value must survive.
        return json.dumps(
            {
                "title": "Novel failure — see the evidence bundle",
                "steps": [
                    "Read the incident's evidence bundle.",
                    "This is Lane 1's scripted model — no diagnosis is offered.",
                ],
                "confidence": 0.0,
                "rationale": "Shape-valid, content-free — the same honesty every mock owes here.",
            }
        )

    citations = sorted(
        set(
            re.findall(
                r"\b(?:feed|plan|contract|batch|recon|error|file|rule|term):"
                r"[A-Za-z0-9][\w.@#-]*",
                prompt,
            )
        )
    )
    if not citations:
        return json.dumps({"claims": [], "confidence": "low", "unanswered": ["no grounding"]})
    return json.dumps(
        {
            "claims": [
                {
                    "text": "This is the mock adapter answering — the facts below come from "
                    "the certified tools, and only their citations are quoted.",
                    "citation_ids": citations[:1],
                },
                *(
                    {"text": f"Grounded in {citation}.", "citation_ids": [citation]}
                    for citation in citations[1:4]
                ),
            ],
            "confidence": "medium",
            "unanswered": [
                "answer quality is not claimed here — Lane 1 proves machinery, not quality"
            ],
        }
    )
