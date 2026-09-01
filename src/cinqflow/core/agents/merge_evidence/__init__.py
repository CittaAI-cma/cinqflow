"""CF-V3-E9-03 — the merge evidence card agent's shape, as data.

The wired implementation lives in `cinqflow.intelligence.agents.merge_evidence`
— this package holds only what core owns: node names, the risk class, and the
response schema. See `graph.py` for why the schema has no decision field.
"""

from cinqflow.core.agents.merge_evidence.graph import (
    AGENT,
    CAPABILITY,
    DETERMINISTIC_NODES,
    NARRATE_SCHEMA,
    NODE_GATHER,
    NODE_NARRATE,
    NODES,
    RISK_CLASS,
)

__all__ = [
    "AGENT",
    "CAPABILITY",
    "DETERMINISTIC_NODES",
    "NARRATE_SCHEMA",
    "NODES",
    "NODE_GATHER",
    "NODE_NARRATE",
    "RISK_CLASS",
]
