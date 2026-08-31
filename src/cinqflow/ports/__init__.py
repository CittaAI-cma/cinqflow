"""The twenty-one pins.

    "Every touch of the outside world goes through a port interface, in
     business language."
    — memory/03-directives/01-definition-of-done.md, the chip test

    "every port has real | dev_standin | mock, all passing ONE contract suite"
    — docs/architecture/INVARIANTS.md

This module is the pin-out map as data (FIG 04). It exists so that two things
are mechanically true rather than conventionally true:

  1. A pin is declared once. Its verb, its ladder of implementations and the
     plate it comes from live in one place, so a pin cannot quietly acquire a
     second definition.

  2. An adapter registers against a pin, and the ONE contract suite for that
     pin iterates over everything fitted. That is what stops three
     implementations drifting onto three suites — the failure the invariant
     names, and the failure that makes a socket climb expensive.

Adding an adapter is a decorator. Adding a PIN is a plate change, and
test_port_registry.py will fail until the plate and this file agree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

_PLATE = "docs/architecture/plates/04-pin-out-map.md"

T = TypeVar("T")


@dataclass(frozen=True)
class PortSpec:
    """One pin: what the core asks for, and who can answer.

    `verb` is deliberately business language. `execute_sql` describes an
    adapter; `governed_readonly_query` describes what the platform wants, and
    only the second survives a socket climb without a core change.
    """

    name: str
    verb: str
    what_it_is_for: str
    mock: str
    dev: str
    target: str
    plate: str = _PLATE
    # Populated by @port at import time. The one mutable thing here, because
    # adapters register themselves and the spec must stay otherwise frozen.
    adapters: dict[str, Callable[..., Any]] = field(default_factory=dict, compare=False)


def _spec(name: str, verb: str, what_it_is_for: str, mock: str, dev: str, target: str) -> PortSpec:
    return PortSpec(
        name=name, verb=verb, what_it_is_for=what_it_is_for, mock=mock, dev=dev, target=target
    )


# ── group A · the data plane ─────────────────────────────────────────────────
_GROUP_A: tuple[PortSpec, ...] = (
    _spec(
        "compute_job",
        "run_job_and_report_progress",
        "Render a compiled plan and run it. pg-compute today, Databricks Jobs later — "
        "one plan, two renderings, compared on OUTPUT DATA, never on generated code.",
        mock="scripted",
        dev="pg_plane",
        target="databricks_jobs",
    ),
    _spec(
        "orchestration",
        "schedule_trigger_and_pause",
        "Run feeds on their registry schedules and pause downstream work when upstream "
        "fails. ONE generic DAG parameterised by feed_id — a DAG per feed would "
        "re-introduce per-feed code, just hidden in Airflow.",
        mock="inproc",
        dev="pg_scheduler",
        target="airflow_rest",
    ),
    _spec(
        "storage",
        "list_fingerprint_and_move_files",
        "The landing zone. One adapter, three URL schemes: file:// at rung 0.5, "
        "s3:// at rung 1, abfs:// at rung 3 — which is where 'Azure Blob is not "
        "S3-compatible' gets absorbed instead of discovered.",
        mock="memfs",
        dev="localfs_or_minio",
        target="adls_gen2",
    ),
    _spec(
        "connector",
        "deliver_a_file_into_the_landing_zone",
        "How a file gets IN. Plate 09 named seven connectors from the start and gave "
        "none of them a pin, so the platform could read a zone it had no way to fill — "
        "the only paths to a landed file were the simulator and a CLI that generated "
        "its own roster. `storage` reads the zone and has no write verb on purpose; "
        "this writes it, and is separate for exactly that reason. Only a connector "
        "delivers, and everything it delivers goes through landing controls, so ADR-0011's "
        "'there is no second door' stays a property rather than a convention.",
        mock="scripted",
        dev="upload_or_folder_drop",
        target="sftp_poller_or_storage_event",
    ),
    _spec(
        "control_tables",
        "read_and_write_the_eleven_control_tables",
        "The 11 control tables, all joined on batch_id. Their DDL is declared ONCE in a "
        "portable spec and rendered per engine, so conformance compares each engine to "
        "the SPEC and a drift is attributed to one engine immediately.",
        mock="memstore",
        dev="postgres_schemas",
        target="delta_sql_warehouse",
    ),
    _spec(
        "catalog",
        "describe_schemas_lineage_and_grants",
        "What exists, where it came from, who may read it.",
        mock="dict",
        dev="information_schema",
        target="unity_catalog",
    ),
    _spec(
        "sql_query",
        "governed_readonly_query",
        "Read-only, governed. NOTE: this verb is NOT on any Wave-0 agent whitelist — "
        "agents call certified tools by name; free-form NL->SQL is CF-V4-E14-04 and "
        "arrives only with full RBAC and masking.",
        mock="canned",
        dev="postgres",
        target="serverless_sql_warehouse",
    ),
    _spec(
        "identity",
        "submit_resolve_and_crosswalk_members",
        "Verato. R4 throughout: a merge or split is human-steward-always, never "
        "automated, not configurable — at any confidence.",
        mock="scenarios",
        dev="spec_exact_mock",
        target="verato_api",
    ),
    _spec(
        "legacy_readonly",
        "compare_against_the_incumbent",
        "Parity comparison during parallel run. Wave 5 only, and REMOVED at cutover — "
        "a port with a scheduled end date.",
        mock="seeded_db",
        dev="seeded_db",
        target="jdbc_readonly",
    ),
    _spec(
        "document_parse",
        "parse_layout_aware",
        "The 22nd pin, added by CF-V1-E16-04. Turns a payer companion guide or a "
        "client spec's bytes into text with page anchors and whole tables — the "
        "missing half of 'Inbox -> Parse -> Chunk' that let core.knowledge chunk "
        "only objects that were already parsed Python values.",
        mock="canned",
        dev="local_pypdf_docx",
        target="local_pypdf_docx_or_azure_doc_intelligence",
    ),
)

# ── group B · the platform ───────────────────────────────────────────────────
_GROUP_B: tuple[PortSpec, ...] = (
    _spec(
        "metadata_db",
        "persist_governed_objects_and_audit",
        "The registry, governance and audit schemas. Postgres at every rung — the "
        "tenant's Flexible Server speaks the same protocol to the same driver, so this "
        "pin costs a connection string.",
        mock="sqlite_memory",
        dev="postgres",
        target="azure_pg_flexible",
    ),
    _spec(
        "queue",
        "enqueue_and_consume_work",
        "SELECT ... FOR UPDATE SKIP LOCKED on the Postgres we already run. IDENTICAL at "
        "rung 4 (ADR-0014): volume is thousands of messages a day, and a proprietary "
        "broker SDK would silently weld the platform to one cloud.",
        mock="memq",
        dev="pg_skip_locked",
        target="pg_skip_locked",
    ),
    _spec(
        "cache",
        "optional_read_cache",
        "Deliberately unimplemented. ADR-0014: no cache until measurement demands one. "
        "The seat exists so that adding one later is an adapter, not an argument.",
        mock="none",
        dev="none",
        target="redis_if_measured",
    ),
    _spec(
        "authn",
        "identify_the_caller_and_their_scopes",
        "OIDC is OIDC. Keycloak at rung 1, Entra ID at rung 3 — same libraries, same "
        "code path; the profile carries the discovery URL and the claim mapping.",
        mock="static",
        dev="keycloak_oidc",
        target="entra_oidc",
    ),
    _spec(
        "secrets",
        "fetch_a_secret_by_name",
        "Everything is a `secret://name` REFERENCE in the profile. Resolution is the "
        "adapter's job, and the reference format never changes.",
        mock="mem",
        dev="dotenv",
        target="key_vault",
    ),
    _spec(
        "llm",
        "complete_embed_and_route",
        "The ONLY place model credentials exist. Routing small<->large, per-agent "
        "budgets, version pinning, metering and refusal of undeclared endpoints are "
        "ours; the SDK behind this pin is a transport.",
        mock="scripted_and_replay",
        dev="real_subscription",
        target="azure_ai_foundry",
    ),
    _spec(
        "vector",
        "index_and_retrieve_chunks",
        "pgvector, in the same Postgres as the registry. Provisioned in Wave 0 and "
        "left EMPTY — the knowledge plane is Wave 1.",
        mock="list",
        dev="pgvector",
        target="pgvector_or_ai_search",
    ),
    _spec(
        "phi_scrub",
        "detect_and_mask_phi",
        "Presidio in BOTH worlds — identical software locally and in the tenant, so "
        "there is no behavioural gap to discover late. Runs BEFORE any prompt, and the "
        "ordering has its own test.",
        mock="patterns",
        dev="presidio",
        target="presidio",
    ),
    _spec(
        "notification",
        "alert_by_severity",
        "Slack, Teams, ticketing and generic webhooks are all an HTTP POST. No vendor "
        "SDK earns its place here.",
        mock="console",
        dev="slack_webhook",
        target="slack_email_ticket_webhook",
    ),
    _spec(
        "observability",
        "emit_logs_metrics_and_traces",
        "The app emits plain OTLP at EVERY rung; a collector forwards to Grafana in the "
        "twin and Azure Monitor in the tenant. The environment difference lands in "
        "chart values, and this pin costs zero Python dependencies at rung 3.",
        mock="noop",
        dev="otel_collector",
        target="otel_log_analytics",
    ),
    _spec(
        "http_edge",
        "expose_the_api_and_ui",
        "uvicorn locally, tenant ingress at rung 3. Never designed against API "
        "Management: design against it and only one gateway works.",
        mock="testclient",
        dev="uvicorn",
        target="tenant_ingress",
    ),
    _spec(
        "agent_runtime",
        "execute_an_agent_graph",
        "Executes graphs and NOTHING else. It never owns the model call, the prompt "
        "text, the tool whitelist, the risk class or any lifecycle state — those are "
        "governed product. LangGraph is a Wave-2 seat.",
        mock="inproc",
        dev="inproc",
        target="langgraph_from_wave2",
    ),
)

PIN_GROUPS: dict[str, tuple[str, ...]] = {
    "A_data_plane": tuple(s.name for s in _GROUP_A),
    "B_platform": tuple(s.name for s in _GROUP_B),
}

PORTS: dict[str, PortSpec] = {s.name: s for s in (*_GROUP_A, *_GROUP_B)}


def port(pin: str, adapter: str) -> Callable[[type[T]], type[T]]:
    """Fit an adapter to a pin.

    Refuses two things, both of which are silent failures otherwise: fitting to
    a pin that does not exist, and fitting two adapters under one name — which
    is how the one contract suite quietly runs the same implementation twice
    and reports full coverage.
    """

    def decorate(cls: type[T]) -> type[T]:
        if pin not in PORTS:
            raise KeyError(
                f"{pin!r} is not a pin. The twenty-one pins are declared on {_PLATE}; "
                "a twenty-second is a plate change, not a registration."
            )
        adapters = PORTS[pin].adapters
        if adapter in adapters:
            raise ValueError(f"{pin}/{adapter} is already fitted by {adapters[adapter]!r}")
        adapters[adapter] = cls
        return cls

    return decorate


def fitted(pin: str) -> dict[str, Callable[..., Any]]:
    """Every adapter currently fitted to a pin.

    This is what the ONE contract suite iterates over, which is the whole
    mechanism: a second adapter becomes a CERTIFICATION rather than a migration.
    """
    return dict(PORTS[pin].adapters)
