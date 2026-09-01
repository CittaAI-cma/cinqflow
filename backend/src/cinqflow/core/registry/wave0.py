"""The Wave-0 execution-plane register, declared once and checked by CI.

    "the Definition of Done requires that field on every story"
    — memory/04-corpus/02-known-discrepancies.md, D16

Thirteen stories, thirteen contracts. Written as DATA rather than prose so the
DoD gate is `missing_contracts()` returning empty — a review question turned
into an assertion.

The unknowns here are the real ones. They are the Databricks/Airflow facts the
programme has not been able to confirm (memory/08-open), and they are recorded
against the story they will actually hurt, with the person who can answer them.
Wave 0 runs entirely on the Postgres plane, so none of them BLOCK Wave 0 — but
they all block the socket-ladder rung that assumes them, and this is where a
rung-3 installation goes to look.
"""

from __future__ import annotations

from cinqflow.core.registry.execution_plane import (
    ExecutionPlaneContract,
    ExecutionPlaneRegister,
    PlaneObject,
    PlaneObjectKind,
    Unknown,
)

WAVE_0_STORIES: tuple[str, ...] = (
    "CF-V0-E1-01",
    "CF-V0-E2-01",
    "CF-V0-E3-01",
    "CF-V0-E8-01",
    "CF-V0-E8-02",
    "CF-V0-E8-07",
    "CF-V0-E8-08",
    "CF-V0-E13-01",
    "CF-V0-E16-01",
    "CF-V0-E16-02",
    "CF-V0-E16-09",
    "CF-V0-E16-10",
    "CF-V0-E16-11",
)

_CONTROL_TABLES = (
    ("control.feed_sla_config", "expected arrival windows per feed per cycle"),
    ("control.input_registry", "every file that ever arrived, with its fingerprint"),
    ("control.schema_registry", "the contract each feed version was loaded against"),
    ("control.schema_drift_log", "observed structure vs the contract, per batch"),
    ("control.batch_control", "one row per run: state, feed version, business date"),
    ("control.batch_stage_status", "per-stage progress; what a restart resumes from"),
    ("control.error_log", "deterministically hashed errors, replay-idempotent"),
    ("control.quarantine_records", "rows held back, with the rule that held them"),
    ("control.batch_reconciliation", "the balance equation, per stage, per batch"),
    ("control.sla_instance", "one expected arrival, and what became of it"),
    ("control.sla_alerts", "the Missing that somebody was told about"),
)

_OBJECTS: tuple[PlaneObject, ...] = (
    *(
        PlaneObject(object_id=name, kind=PlaneObjectKind.CONTROL_TABLE, description=desc)
        for name, desc in _CONTROL_TABLES
    ),
    PlaneObject(
        object_id="landing_ctl.landing_event",
        kind=PlaneObjectKind.CONTROL_TABLE,
        description="every arrival decision, including files nobody expected",
    ),
    PlaneObject(
        object_id="bronze.members_raw",
        kind=PlaneObjectKind.DATA_LAYER,
        description="the untouched copy of the source, append-only at the database layer",
    ),
    PlaneObject(
        object_id="silver_raw.members",
        kind=PlaneObjectKind.DATA_LAYER,
        description="typed, mapped, rule-evaluated rows — the Wave-0 terminus",
    ),
    PlaneObject(
        object_id="quarantine.quarantined_rows",
        kind=PlaneObjectKind.DATA_LAYER,
        description="rows that failed a rule, retained rather than dropped",
    ),
    PlaneObject(
        object_id="recon.recon_history",
        kind=PlaneObjectKind.DATA_LAYER,
        description="the balance equation as it stood at each stage of each batch",
    ),
    PlaneObject(
        object_id="registry.governed_object",
        kind=PlaneObjectKind.REGISTRY_TABLE,
        description="every governed object, every version, one lifecycle",
    ),
    PlaneObject(
        object_id="governance.audit_ledger",
        kind=PlaneObjectKind.REGISTRY_TABLE,
        description="append-only; no deletion path exists for anyone",
    ),
    PlaneObject(
        object_id="audit.agent_action",
        kind=PlaneObjectKind.REGISTRY_TABLE,
        description="every tool invocation an agent made, and every one it was refused",
    ),
    PlaneObject(
        object_id="knowledge.reference",
        kind=PlaneObjectKind.REGISTRY_TABLE,
        description="171 glossary terms and 110 DQ-rule descriptions, tsvector-searchable",
    ),
    PlaneObject(
        object_id="platform.storage",
        kind=PlaneObjectKind.PLATFORM_API,
        description="the storage pin — landing zone reads and archive moves",
    ),
    PlaneObject(
        object_id="platform.compute_job",
        kind=PlaneObjectKind.PLATFORM_API,
        description="the compute pin — the pin the plan is rendered against",
    ),
    PlaneObject(
        object_id="platform.orchestration",
        kind=PlaneObjectKind.PLATFORM_API,
        description="the orchestration pin — schedules and triggers a run",
    ),
    PlaneObject(
        object_id="platform.authn",
        kind=PlaneObjectKind.PLATFORM_API,
        description="the authn pin — verifies a token somebody else issued",
    ),
    PlaneObject(
        object_id="platform.llm",
        kind=PlaneObjectKind.PLATFORM_API,
        description="the llm pin — the only place a model credential exists",
    ),
    PlaneObject(
        object_id="databricks.unity_catalog",
        kind=PlaneObjectKind.EXTERNAL_SYSTEM,
        description="the client's Unity Catalog metastore, unseen by the programme",
    ),
    PlaneObject(
        object_id="airflow.scheduler",
        kind=PlaneObjectKind.EXTERNAL_SYSTEM,
        description="the client's Airflow, unseen by the programme",
    ),
)

_CONTRACTS: tuple[ExecutionPlaneContract, ...] = (
    ExecutionPlaneContract(
        story_id="CF-V0-E1-01",
        reads=frozenset({"registry.governed_object"}),
        writes=frozenset({"registry.governed_object", "governance.audit_ledger"}),
        unknowns=(
            Unknown(
                question="Is the story template's execution-plane field enforced anywhere "
                "outside this register?",
                owner="delivery lead",
            ),
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E2-01",
        reads=frozenset({"platform.authn", "governance.audit_ledger"}),
        writes=frozenset({"governance.audit_ledger"}),
        unknowns=(
            Unknown(
                question="Which Entra ID group claim carries CINQFLOW roles at rung 3, and is "
                "it emitted in the access token or only the ID token?",
                owner="CINQCARE identity team",
            ),
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E3-01",
        reads=frozenset({"registry.governed_object"}),
        writes=frozenset({"registry.governed_object", "governance.audit_ledger"}),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E8-01",
        reads=frozenset(
            {
                "registry.governed_object",
                "control.schema_registry",
                "control.batch_stage_status",
                "platform.compute_job",
                "platform.orchestration",
            }
        ),
        writes=frozenset(
            {
                "control.batch_control",
                "control.batch_stage_status",
                "control.error_log",
                "control.quarantine_records",
                "control.schema_drift_log",
                "bronze.members_raw",
                "silver_raw.members",
                "quarantine.quarantined_rows",
            }
        ),
        unknowns=(
            Unknown(
                question="Does the client's Databricks workspace permit COPY INTO from the "
                "landing container, or must ingestion go through Auto Loader?",
                owner="CINQCARE data engineering",
            ),
            Unknown(
                question="Can an Airflow task restart from an arbitrary stage, or does the "
                "client's DAG pattern only support whole-run reruns?",
                owner="CINQCARE platform team",
            ),
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E8-02",
        reads=frozenset({"platform.storage", "control.input_registry"}),
        writes=frozenset({"control.input_registry", "landing_ctl.landing_event"}),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E8-07",
        reads=frozenset({"platform.compute_job"}),
        writes=frozenset(
            {name for name, _ in _CONTROL_TABLES}
            | {
                "landing_ctl.landing_event",
                "bronze.members_raw",
                "silver_raw.members",
                "quarantine.quarantined_rows",
                "recon.recon_history",
            }
        ),
        unknowns=(
            Unknown(
                question="Is Unity Catalog's managed-table default acceptable for Bronze, given "
                "immutability is enforced by grants rather than a trigger there?",
                owner="CINQCARE data engineering",
            ),
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E8-08",
        reads=frozenset({"registry.governed_object"}),
        writes=frozenset({"platform.storage"}),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E13-01",
        reads=frozenset({"control.batch_stage_status", "control.quarantine_records"}),
        writes=frozenset(
            {"control.batch_reconciliation", "recon.recon_history", "control.error_log"}
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E16-01",
        reads=frozenset({"platform.llm", "registry.governed_object"}),
        writes=frozenset({"audit.agent_action"}),
        unknowns=(
            Unknown(
                question="Which Azure AI Foundry deployment names will carry the small and large "
                "models at rung 3, and are they in the same region as the data?",
                owner="CINQCARE cloud team",
            ),
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E16-02",
        reads=frozenset({"registry.governed_object"}),
        writes=frozenset({"registry.governed_object", "governance.audit_ledger"}),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E16-09",
        reads=frozenset(
            {
                "registry.governed_object",
                "knowledge.reference",
                "control.batch_control",
                "control.batch_stage_status",
                "control.batch_reconciliation",
                "control.quarantine_records",
                "control.input_registry",
                "control.schema_drift_log",
                "control.sla_instance",
                "control.error_log",
            }
        ),
        writes=frozenset({"audit.agent_action"}),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E16-10",
        reads=frozenset({"platform.llm", "knowledge.reference"}),
        writes=frozenset({"audit.agent_action"}),
        unknowns=(
            Unknown(
                question="Does the client accept operational metadata leaving the tenant for the "
                "Wave-0 model endpoint, or must rung 3 be in-tenant from day one?",
                owner="CINQCARE security",
                blocks=False,
            ),
        ),
    ),
    ExecutionPlaneContract(
        story_id="CF-V0-E16-11",
        reads=frozenset({"registry.governed_object"}),
        writes=frozenset({"audit.agent_action"}),
    ),
)


def wave_0_register() -> ExecutionPlaneRegister:
    """The register, built fresh. Never a module-level singleton — a mutable
    register shared between tests is a register whose state depends on
    collection order."""
    return ExecutionPlaneRegister.of(_OBJECTS, _CONTRACTS)
