"""A development server on the mock socket. Rung 0 — nothing runs but Python.

    0: {socket: mock, cost: 0, proves: core_logic_in_ci_seconds}
    — docs/architecture/plates/05-socket-ladder.md

This exists so the UI's end-to-end tests need no database, no container and no
credential — and so a person can see the whole workspace with one command. It
seeds the Fidelis anchor through the SIMULATOR's own layout rather than by
hand, because the demo places no files by hand, anywhere.

It is not a deployment. `cinqflow install --profile profiles/local.yaml` stands
up the real rung-0.5 plane; this stands up a picture of one.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.api import create_app
from cinqflow.core.agents.pipeline_insight.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState
from cinqflow.core.model.vocabulary import ActorType, BatchState, ErrorCategory, FileState, Layer
from cinqflow.core.registry import contract as contract_registry
from cinqflow.core.registry.contract import ContractColumn, DqRule, SchemaContract, Severity
from cinqflow.core.registry.feed import FeedRecord
from cinqflow.core.schema_spec import TypeName
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
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

FEED_ID = "fidelis-downstate-roster"
BATCH_ID = "8842"

AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun Menon")
REVIEWER = Actor(
    subject="priya@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Priya Nair"
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
                            name="source_member_id", type=TypeName.STRING, nullable=False,
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
    for template in TEMPLATES:
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


def build() -> Any:
    store = MemMetadataDb()
    control = MemStoreControlTables()
    seed(store, control)
    budget = Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5.00"))

    def agent_factory(principal, control_port, metadata_port):  # type: ignore[no-untyped-def]
        """One agent per caller. A shared agent would be a shared scope."""
        return PipelineInsightAgent(
            llm=LlmGateway(
                llm=ScriptedLlm(responder=_scripted),
                phi_scrub=PatternPhiScrub(),
                metadata_db=metadata_port,
                observability=NoopObservability(),
                budget=budget,
                routing=Routing(small="mock-small", large="mock-large"),
            ),
            tools=ToolContext(
                principal=principal, control=control_port, metadata=metadata_port
            ),
            runtime=InProcAgentRuntime(),
        )

    return create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        agent_factory=agent_factory,
        budget=budget,
    )


def _scripted(prompt: str, task_class: Any) -> str:
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

    citations = sorted(set(re.findall(r"\b(?:feed|plan|contract|batch|recon|error|file|rule|term):"
                                      r"[A-Za-z0-9][\w.@#-]*", prompt)))
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


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    arguments = parser.parse_args()
    uvicorn.run(build(), host=arguments.host, port=arguments.port, log_level="warning")


if __name__ == "__main__":
    main()
