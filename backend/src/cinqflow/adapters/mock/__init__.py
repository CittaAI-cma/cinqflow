"""Rung 0 — the mock socket. Nothing runs but Python.

    0: {socket: mock, cost: 0, proves: core_logic_in_ci_seconds}
    — docs/architecture/plates/05-socket-ladder.md

These are PERMANENT. They are not scaffolding to be deleted once real adapters
exist — they are what keeps pyramid layers 1-2 running in seconds on every
commit with no services started, and what makes "no machinery test may require
Lane 3" enforceable.

Every mock here passes the SAME contract suite as its real counterpart. That is
the whole mechanism: a mock that passes a weaker suite is a mock that lies.

None of these hold a credential. That is deliberate and load-bearing — lanes 1
and 2 hold no live credentials, so a misclassified test fails loudly rather
than quietly reaching a real endpoint.
"""

from cinqflow.adapters.mock import (  # noqa: F401  — imported for registration
    agent_runtime,
    authn,
    cache,
    catalog,
    compute_job,
    connector,
    control_tables,
    document_parse,
    http_edge,
    identity,
    legacy_readonly,
    llm,
    metadata_db,
    notification,
    observability,
    orchestration,
    phi_scrub,
    queue,
    secrets,
    sql_query,
    storage,
    vector,
)
