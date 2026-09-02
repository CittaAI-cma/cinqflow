"""One iteration's real adapters, built from one live connection.

`installer.cli.serve_worker` used to construct `PostgresMetadataDb`,
`PostgresControlTables`, `PostgresQueue`, `PostgresOrchestration`,
`PostgresCompute`, the scheduler and the consumer's two handlers all inline,
in one 90-line function body — grown across two milestones, each adding one
more piece to the same function. This module is that construction, pulled
out to where it can be read and tested on its own; `serve_worker` calls
`build()` once per loop iteration and is left with only the loop itself.

WHY THIS DOES NOT DISPATCH ON `profile.adapter_for(...)`, UNLIKE
`phi_scrub_from`/`vector_from`/`connectors_from`. Checked before writing
this: for every pin `build()` touches — `metadata_db`, `control_tables`,
`queue`, `orchestration`, `storage` — Postgres/local is the ONLY real
adapter that exists anywhere in this codebase today (Airflow, MinIO/S3 and
Databricks are compose containers and docstring aspirations with zero Python
behind them). A `match adapter: case "the one real option": ...` is
indirection with no payload, which is the premature abstraction this
platform's own discipline argues against. `_check_adapters` is the honest
middle ground: it REFUSES loudly if a profile names anything else, so a
typo'd profile value fails at startup instead of silently running on
Postgres regardless of what it says — the same "no silent substitution"
`phi_scrub_from` already holds itself to — without pretending a dispatch
this platform cannot yet honour.

`compute_job` is deliberately NOT checked here. Its only registered adapter,
`ScriptedComputeJob`, implements a different interface than `PostgresCompute`
entirely (`run`/`poll`/`metrics` vs. `land_bronze`/`load_silver_raw`) — the
two are not interchangeable even in principle, so the pin does not describe
what this module actually builds. That mismatch is a pre-existing gap
worth its own fix, not something to paper over with a check that would only
ever say "none".
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.pg_compute import PostgresCompute
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.local.pg_orchestration import PostgresOrchestration
from cinqflow.adapters.local.pg_queue import PostgresQueue
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.installer.connectors import connectors_from
from cinqflow.intelligence.plane import IntelligencePlane
from cinqflow.ports.connector import ConnectorPort
from cinqflow.ports.control_tables import ControlTablesPort
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.secrets import SecretsPort
from cinqflow.workers.agent_task import AGENT_TASK_TOPIC, AgentTaskWorker
from cinqflow.workers.consumer import Consumer
from cinqflow.workers.pipeline import PipelineRunner
from cinqflow.workers.run_feed import FeedRunWorker
from cinqflow.workers.scheduler import RUN_FEED_TOPIC, SchedulerWorker

__all__ = ["WorkerAdapters", "build"]

#: The one real adapter `build()` knows how to construct for each pin —
#: see the module docstring for why this is a refusal list, not a dispatch.
_EXPECTED_ADAPTERS: dict[str, str] = {
    "metadata_db": "postgres",
    "control_tables": "pg-control",
    "queue": "pg-skip-locked",
    "orchestration": "pg-scheduler",
    "storage": "localfs",
}


def _check_adapters(profile: Profile) -> None:
    for pin, expected in _EXPECTED_ADAPTERS.items():
        actual = profile.adapter_for(pin)
        if actual != expected:
            raise ProfileError(
                f"{profile.source}: {pin} names {actual!r}, but `cinqflow serve-worker` only "
                f"knows how to build {expected!r} — the only real adapter fitted to this pin "
                "today. A different value here is either a typo or a rung this command does "
                "not support yet."
            )


@dataclass(frozen=True)
class WorkerAdapters:
    """Everything one `serve-worker` pass needs, built from one connection.

    A NEW instance every call, never held across iterations — matching
    `tick`/`work`'s own per-invocation connection lifecycle, which this
    module's caller loops on a timer rather than replaces.
    """

    metadata: MetadataDbPort
    control: ControlTablesPort
    storage: LocalFsStorage
    connectors: dict[str, ConnectorPort]
    scheduler: SchedulerWorker
    consumer: Consumer


def build(
    profile: Profile, secrets: SecretsPort, connection: Connection, storage: LocalFsStorage
) -> WorkerAdapters:
    """Construct one iteration's adapters. Raises `ProfileError` first if
    the profile names anything `serve-worker` cannot actually build (see the
    module docstring) — refusing before any adapter is touched, the same
    "fail before the first byte moves" shape every pin factory in this
    codebase already follows.
    """
    _check_adapters(profile)

    metadata = PostgresMetadataDb(connection)
    control = PostgresControlTables(connection)
    queue = PostgresQueue(connection)
    intelligence = IntelligencePlane.from_profile(profile, secrets, connection=connection)
    connectors = connectors_from(profile, storage=storage, secrets=secrets)

    scheduler = SchedulerWorker(
        orchestration=PostgresOrchestration(connection),
        metadata=metadata,
        control=control,
        queue=queue,
    )

    consumer = Consumer(queue)
    consumer.register(
        RUN_FEED_TOPIC,
        FeedRunWorker(
            metadata=metadata,
            storage=storage,
            runner=PipelineRunner(storage=storage, control=control, compute=PostgresCompute(connection)),
        ).handle,
    )
    consumer.register(
        AGENT_TASK_TOPIC,
        AgentTaskWorker(
            metadata=metadata,
            agent_factories={
                "schema_inference": lambda store: intelligence.schema_inference(store, secrets),
            },
        ).handle,
    )

    return WorkerAdapters(
        metadata=metadata,
        control=control,
        storage=storage,
        connectors=connectors,
        scheduler=scheduler,
        consumer=consumer,
    )
