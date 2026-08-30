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

THE AGENTS ARE STILL THE SCRIPTED STAND-IN `intelligence.demo` uses, even
though `profiles/local.yaml` names a real `openai-compatible` endpoint and
`.env` already carries real credentials for it. Wiring the real LLM adapter is
a genuinely separate integration — secret resolution, the per-model price
table `LlmGateway` requires, a PHI-scrub adapter choice (`presidio` exists at
`adapters/local/presidio_scrub.py`, unused here), routing — and deserves its
own pass and its own verification rather than a silent half-wire alongside
feed registration. Real data plane; scripted intelligence plane. Upgrading the
second is `docs/adr/` work, not a flag on this module.
"""

from __future__ import annotations

import argparse

from fastapi import FastAPI

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.pg_control import Connection, connect
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.installer import profile as profile_module
from cinqflow.intelligence.demo import BUDGET, agent_for, schema_inference_for

DEFAULT_PROFILE = "profiles/local.yaml"
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

    app = create_app(
        authn=StaticAuthn(),
        metadata_db=metadata,
        control_tables=control,
        storage=storage,
        connector=UploadConnector(storage),
        agent_factory=agent_for,
        schema_inference_factory=schema_inference_for,
        budget=BUDGET,
        profile=profile,
    )
    app.state.pg_connection = opened  # kept alive only — see note above
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
