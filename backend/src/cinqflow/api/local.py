"""The real rung-0.5 server: Postgres metadata, Postgres control, a real
landing zone on disk. `cinqflow install --profile profiles/local.yaml` must
have been run first — this wires adapters to EXISTING schemas, it creates
none.

    0.5: {plane: postgres, cost: "a local install", proves: real_persistence}
    — docs/architecture/plates/05-socket-ladder.md

`api.dev` (rung 0) is a PICTURE of the platform: every feed, delivery and
batch it holds evaporates on restart, which is exactly right for a demo that
must run with no database. This is the platform itself — what you register
here is a row in the same Postgres database `cinqflow ingest` and the pipeline
test suite address, and it is still there tomorrow.

ONE CONNECTION, HELD OPEN FOR THE SERVER'S LIFETIME. `PostgresControlTables`
and `PostgresMetadataDb` already take a `Connection` by constructor, not a
profile, and `create_app` already treats `control_tables`/`metadata_db` as ONE
long-lived object per app rather than one per request — the same shape the
mock adapters use. That is honest for what this rung is: a single developer's
local server, not concurrent production traffic. Real concurrent load needs a
connection checked out per request from a pool (`psycopg[pool]` is already a
base dependency — see requirements/base.txt), which is a contained follow-up
here, not a redesign: only this file's wiring would change.

THE INTELLIGENCE PLANE IS REAL HERE NOW, and it is real the same way the data
plane is: read from the profile, not decided in this file. `IntelligencePlane
.from_profile` fits the `llm`, `phi_scrub` and `vector` pins exactly as
`profiles/local.yaml` names them — `openai-compatible` against the endpoint in
`.env`, `presidio` for PHI, `pgvector` on the connection below — and every
agent this server exposes is built from that one plane. Nothing in this module
names an endpoint, a model or a scrubber; switching this deployment to the
client tenant is a profile edit, which is the whole claim the socket ladder
makes.

FOUR CAPABILITIES THAT USED TO ANSWER 503 HERE NOW ANSWER FOR REAL:
`POST /detect-phi` (CF-V1-E5-03), `POST /suggest-mapping` (CF-V1-E6-02),
`POST /author-rules` (CF-V1-E7-01) and the knowledge-ingest worker every
publish hook calls (CF-V1-E16-04). All four were built, tested and routed;
none was ever passed to `create_app` by any server, which is a different
thing from being built — an agent nothing constructs is an agent the platform
does not have.
"""

from __future__ import annotations

import argparse

from fastapi import FastAPI

from cinqflow.adapters.local.file_document_parse import FileDocumentParser
from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.pg_catalog import PostgresCatalog
from cinqflow.adapters.local.pg_control import Connection, connect, connect_data_plane
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_layers import PostgresLayerReader
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.local.pg_orchestration import PostgresOrchestration
from cinqflow.adapters.local.pg_queue import PostgresQueue
from cinqflow.adapters.local.pg_sql_query import PostgresSqlQuery
from cinqflow.adapters.local.secrets import DotenvSecrets
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.notification import ConsoleNotification
from cinqflow.api import create_app
from cinqflow.core.model.profile import ProfileError
from cinqflow.installer import profile as profile_module
from cinqflow.installer.connectors import connectors_from
from cinqflow.installer.ods_model import provision_ods_model
from cinqflow.intelligence.plane import IntelligencePlane
from cinqflow.intelligence.wiring import budget_from
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.workers.knowledge import KnowledgeIngestWorker

DEFAULT_PROFILE = "profiles/local.yaml"
#: A DEFAULT, and deliberately not an environment lookup.
#:
#: `os.environ` here would fail `tests/unit/test_credentials_live_only_in_
#: adapters.py`, and rightly: the profile is the ONE channel environment
#: difference travels through, so a module that also reads the environment has
#: invented a second one. The containerized servers name their profile in the
#: ASGI app string instead — `cinqflow.api.local:build("profiles/dev.yaml")` —
#: which both gunicorn and uvicorn accept, and which puts the choice somewhere
#: a reader of the compose file can see it.
#: The same default `cinqflow ingest`/`cinqflow install` assume — see
#: `installer/cli.py:ingest`. Kept in step there, not re-derived here.
DEFAULT_LANDING_ROOT = ".cinqflow/landing"


def build(profile_path: str = DEFAULT_PROFILE, landing_root: str | None = None) -> FastAPI:
    """The real socket, wired from the profile the CLI already trusts.

    Requires `CINQFLOW_SECRET_PG_DSN` to be set (see `.env`) and the schemas
    to already exist (`cinqflow install --profile <profile_path>`) — this
    function reads an existing plane; it does not stand one up.
    """
    profile = profile_module.load(profile_path)

    # `connect()` is a context manager shaped for a one-shot CLI command that
    # closes on exit; a server has no "exit" to close on, so its `__enter__`
    # is taken directly instead of using `with`. `contextmanager`'s object is
    # backed by a generator, and a generator with no live reference is closed
    # by the garbage collector — which resumes it past its `yield`, running
    # the code that closes the connection. The FIRST version of this function
    # discarded that object and kept only the `Connection` wrapper around it;
    # the wrapper survived, the connection underneath it did not, and every
    # request after whenever GC next ran saw `OperationalError: the
    # connection is closed`. Stashing it on `app.state` ties its lifetime to
    # the app's, exactly as long as the connection needs to live.
    opened = connect(profile, autocommit=True)
    connection: Connection = opened.__enter__()

    metadata = PostgresMetadataDb(connection)
    control = PostgresControlTables(connection)
    root = landing_root or str(profile.pins.get("storage", {}).get("root") or DEFAULT_LANDING_ROOT)
    storage = LocalFsStorage(root=root)
    # W3-01. The medallion screen reads the REAL layers here: `catalog` answers
    # what the plane has, `sql_query` answers how much and which rows, and the
    # reader masks every column the schema contract flags before a row leaves
    # the adapter. Both pins share the one connection above, which is what
    # this rung is: a single developer's local server.
    #
    # PLUG AND PLAY. The reader above faces the DATA plane, which is not
    # necessarily the database this server keeps its own state in. When
    # `profiles/*.yaml` declares a `data_plane`, a SECOND connection is opened
    # to it and the two medallion pins are fitted there instead — the platform
    # then reads a warehouse holding none of its control tables, which is what
    # "connectable to any data plane" has to mean if it means anything.
    # When no `data_plane` is declared, the one connection is reused and this
    # is byte-for-byte the rung-0.5 behaviour it has always had.
    if profile.data_plane_is_separate:
        opened_data = connect_data_plane(profile, autocommit=True)
        data_connection: Connection = opened_data.__enter__()
    else:
        opened_data, data_connection = None, connection
    layer_reader = PostgresLayerReader(
        sql=PostgresSqlQuery(data_connection), catalog=PostgresCatalog(data_connection)
    )

    # CF-V0-E16-01 · CF-V1-E5-02/03 · CF-V1-E6-02 · CF-V1-E7-01 · CF-V1-E16-04.
    # ONE plane, fitted from the SAME profile the data plane above came from,
    # holding the SAME connection so `pgvector` writes into the database this
    # server already has open rather than opening a second one. Every agent
    # below is built off it; none is constructed with an endpoint or a key.
    secrets = DotenvSecrets()
    intelligence = IntelligencePlane.from_profile(profile, secrets, connection=connection)

    def knowledge_ingest(store: MetadataDbPort) -> KnowledgeIngestWorker:
        """CF-V1-E16-04 — composed HERE rather than by `IntelligencePlane`.

        `workers/` sits ABOVE `intelligence/` in `.importlinter`'s layer
        contract, because a knowledge-ingest worker is an Archetype-B pipeline
        stage, not an agent: it chunks, gates on PHI and indexes, and makes no
        judgement a model could make. The plane hands over the three pins it
        needs; this server assembles them, exactly as it assembles
        `ods_model_provisioner` from a connection it already holds.
        """
        if intelligence.vector is None:  # pragma: no cover - local.yaml fits pgvector
            raise ProfileError(
                f"{profile.source}: the vector pin is unfitted, so nothing can be embedded. "
                "Set `vector: {adapter: pgvector}` or remove the knowledge routes."
            )
        return KnowledgeIngestWorker(
            phi_scrub=intelligence.phi_scrub,
            llm=intelligence.gateway(store, secrets),
            vector=intelligence.vector,
            metadata=store,
        )

    app = create_app(
        authn=StaticAuthn(),
        metadata_db=metadata,
        control_tables=control,
        storage=storage,
        # CF-V1-E8-09. Every route the profile fits — `connector.routes` —
        # wired with zero code per source: a new SFTP feed is a registry row
        # naming an `endpoint_ref` and one more entry under `routes` here.
        connectors=connectors_from(profile, storage=storage),
        layer_reader=layer_reader,
        # CF-V1-E16-06. The companion guide's own door needs a parser fitted;
        # `local-pypdf-docx` is what `profiles/local.yaml` names for the
        # `document_parse` pin, and without it wizard step 1 accepted a sample
        # and refused the specification that explains it.
        document_parse=FileDocumentParser(),
        agent_factory=lambda principal, control_tables, store: intelligence.pipeline_insight(
            principal, control_tables, store, secrets
        ),
        schema_inference_factory=lambda store: intelligence.schema_inference(store, secrets),
        # CF-V1-E5-03. Fitted for the first time on any server — `POST
        # /detect-phi` answered 503 everywhere until now.
        phi_detection_factory=lambda store: intelligence.phi_detection(store, secrets),
        # CF-V1-E6-02. Likewise `POST /suggest-mapping`.
        mapping_suggestion_factory=lambda store: intelligence.mapping_suggestion(store, secrets),
        # CF-V1-E7-01 / E7-04. Likewise `POST /author-rules`, including the
        # low-confidence routing that sends a below-threshold rule to
        # technical review rather than to a pipeline.
        rule_authoring_factory=lambda store: intelligence.rule_authoring(store, secrets),
        # CF-V1-E16-04. Publishing a runbook or a companion guide is what makes
        # its text knowledge; with no factory the publish hooks degraded
        # SILENTLY and the vector store stayed empty forever.
        knowledge_ingest_factory=knowledge_ingest,
        merge_evidence_factory=lambda store: intelligence.merge_evidence(store, secrets),
        # CF-V3-E10-01. Closes over the SAME connection every other Postgres
        # pin here shares — publishing an ODS model through the generic
        # governance API provisions its real `silver_ods` tables for real.
        ods_model_provisioner=lambda model: provision_ods_model(connection, model),
        # CF-V3-E10-03. No webhook URL is configured for this rung — the
        # SAME "scripted intelligence, real data plane" posture this
        # module's own docstring already names for the agents below:
        # wiring a real Slack/webhook target is a separate integration
        # pass, not a silent half-wire alongside a governance gate. Prints
        # to stdout (`echo=True`) so a developer running this server sees
        # a batch's publish notify for real, not merely "not configured".
        # CF-V1-E4-03 / CF-V1-E8-03. Publishing a feed now REGISTERS its
        # cron here, and pausing one tells the scheduler. Until this pin
        # was fitted `queue.schedule` was never written by the product, so
        # `due()` returned nothing on a plane full of published feeds and
        # `cinqflow tick` was right to say "nothing due".
        orchestration=PostgresOrchestration(connection),
        # CF-V1-E5-02, backgrounded. The SAME queue `cinqflow serve-worker`
        # drains `pipeline.run_feed` from — `POST /infer-schema` now submits
        # onto it instead of calling the model inline.
        queue=PostgresQueue(connection),
        notify=ConsoleNotification(echo=True),
        alert_enrichment_agent=intelligence.alert_enrichment(control, metadata, secrets),
        # From the profile's own `llm.budgets`, never a module constant — a cap
        # is an environment fact, and this server's cap is `local.yaml`'s.
        budget=budget_from(profile),
        profile=profile,
    )
    app.state.pg_connection = opened  # kept alive only — see note above
    # Same reason, same trap: a data-plane connection whose context manager is
    # garbage-collected is a data-plane connection that closes mid-request.
    app.state.data_plane_connection = opened_data
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--landing-root",
        default=None,
        help="Overrides the profile's storage.root. Where delivered files land.",
    )
    arguments = parser.parse_args()
    uvicorn.run(
        build(arguments.profile, arguments.landing_root),
        host=arguments.host,
        port=arguments.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
