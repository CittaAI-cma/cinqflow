"""Rung 0.5-1 — the Postgres plane and the local socket.

    0.5: {socket: postgres_plane, cost: 0, proves: pipeline_semantics_and_control_tables}
    — docs/architecture/plates/05-socket-ladder.md

THE DEFAULT DEVELOPMENT SOCKET. Everything the medallion spine needs — control
tables, immutable Bronze, typed Silver Raw, reconciliation — is exercised here
in seconds, with no cloud account and no container.
"""

from cinqflow.adapters.local import (  # noqa: F401  — imported for registration
    pg_compute,
    pg_control_tables,
    secrets,
)
