"""The reusable seat on a CINQFLOW data plane. Point it at a profile and work.

THIS MODULE IS THE "MODULAR CONNECTION" — the thing to import next time.

    with open_plane("profiles/local.yaml") as plane:
        plane.control.list_batches("some-feed")
        plane.compute.count_bronze(batch_id)
        plane.reader.census(spec_of(Layer.BRONZE))

WHAT IT DELIBERATELY DOES NOT DO: hold a module-level connection, read an
environment variable of its own, or default to a database. The profile decides
which plane, exactly as it does for `api/local.py` and `cinqflow ingest` — so
populating a second database, or rung 3, is a `--profile` argument and never an
edit here. A populator that hard-coded its DSN would be the one component in
this repo that could not follow the socket ladder.

TWO CONNECTION MODES, and the difference is not cosmetic:

    open_plane(...)                  autocommit — for reads and for the
                                     driver's own bookkeeping
    open_plane(..., atomic=True)     ONE transaction, committed on clean exit
                                     and rolled back on any exception

A pipeline run must be atomic: "a batch is visible downstream in full or not at
all". A 569,000-row load that dies at row 400,000 must leave nothing, because
Bronze is append-only at the database layer and a half-written batch cannot be
deleted afterwards — only the transaction boundary can undo it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def load_dotenv(env_file: Path | None = None) -> None:
    """Resolve `secret://name` references from `.env`, the way the suites do.

    The populator is a script, not a server, so nothing else has loaded them.
    Existing environment wins — an operator exporting a DSN to point at another
    plane must not be silently overridden by a file on disk.
    """
    path = env_file or REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Plane:
    """Every seat a populator needs, fitted from one profile.

    Assembled here rather than by the caller for one reason: the pins have to
    agree. `storage` and `connector` must root at the SAME landing zone, and
    `control`/`compute`/`metadata` must share the SAME connection, or a batch's
    control rows commit while its Bronze rows roll back. Handing a caller four
    constructors is handing them four chances to disagree.
    """

    profile: Any
    connection: Any
    control: Any
    compute: Any
    metadata: Any
    storage: Any
    connector: Any
    #: The W3-01 layer reader — census, masked rows, quarantine, recon. Present
    #: only when the profile fits a `catalog` adapter; None is honest rather
    #: than a stub that would answer about a plane it never read.
    reader: Any | None
    landing_root: str

    @property
    def dsn_label(self) -> str:
        """Which database this is, safe to print — never the credential.

        A populator that logs its DSN puts a password in a terminal scrollback
        and then in a ticket. The database name is the part an operator needs.
        """
        raw = str(self.profile.dsn or "")
        return raw.rsplit("/", 1)[-1] if "/" in raw else "unknown"


@contextmanager
def open_plane(
    profile_path: str | Path = "profiles/local.yaml",
    *,
    atomic: bool = False,
    landing_root: str | None = None,
) -> Iterator[Plane]:
    """Open a plane from a profile. The one entry point.

    `atomic=True` wraps everything in a single transaction — required for a
    pipeline run, wrong for a long read-only survey that should not hold a
    write lock open for minutes.
    """
    from cinqflow.adapters.local.localfs_storage import LocalFsStorage
    from cinqflow.adapters.local.pg_catalog import PostgresCatalog
    from cinqflow.adapters.local.pg_compute import PostgresCompute
    from cinqflow.adapters.local.pg_control import commit, connect
    from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
    from cinqflow.adapters.local.pg_layers import PostgresLayerReader
    from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
    from cinqflow.adapters.local.pg_sql_query import PostgresSqlQuery
    from cinqflow.adapters.local.upload_connector import UploadConnector
    from cinqflow.installer import profile as profile_module

    load_dotenv()
    loaded = profile_module.load(str(profile_path))

    root = landing_root or str(loaded.pins.get("storage", {}).get("root") or ".cinqflow/landing")
    storage = LocalFsStorage(root=root)

    opener = commit(loaded) if atomic else connect(loaded, autocommit=True)
    with opener as connection:
        catalog_fitted = loaded.pins.get("catalog", {}).get("adapter") not in (None, "none")
        yield Plane(
            profile=loaded,
            connection=connection,
            control=PostgresControlTables(connection),
            compute=PostgresCompute(connection),
            metadata=PostgresMetadataDb(connection),
            storage=storage,
            connector=UploadConnector(storage),
            reader=(
                PostgresLayerReader(
                    sql=PostgresSqlQuery(connection), catalog=PostgresCatalog(connection)
                )
                if catalog_fitted
                else None
            ),
            landing_root=root,
        )


def table_counts(plane: Plane) -> dict[str, int]:
    """Every table on the plane and its row count. The populator's own report.

    Exact `count(*)`, not `reltuples` — a population report whose numbers are
    ANALYZE-stale estimates cannot be used to check that a load landed what it
    claimed, which is the only reason to print it.
    """
    rows = plane.connection.fetch_all(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
        "  AND table_type = 'BASE TABLE' "
        "ORDER BY table_schema, table_name"
    )
    counts: dict[str, int] = {}
    for schema, table in rows:
        # Identifiers come from information_schema, never from a caller.
        got = plane.connection.fetch_one(
            # S608: both identifiers came out of information_schema on the line
            # above — there is no caller input anywhere in this statement.
            f'SELECT count(*) FROM "{schema}"."{table}"'  # noqa: S608
        )
        counts[f"{schema}.{table}"] = int(got[0]) if got else 0
    return counts
