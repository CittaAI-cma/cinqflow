"""CF-V2-E12-05 — alerts that explain themselves.

The shape (`graph`) and the prompt (`prompts`) live here in core; the node
implementations that touch pins — the gateway, the tool catalogue — are in
`intelligence/agents/alert_enrichment.py`. This agent writes NOTHING: it is
R0, and the alert itself is still raised, deterministically, by
`workers.sla.SlaWorker` exactly as it is today — enrichment adds an
explanation alongside that alert, never in place of it.
"""

from cinqflow.core.agents.alert_enrichment.graph import (
    ACTION,
    AGENT,
    CAUSE_UNDER_INVESTIGATION,
    DETERMINISTIC_NODES,
    HYPOTHESISE_SCHEMA,
    MAX_GROUP_FEEDS,
    NODE_COMPOSE,
    NODE_GROUP,
    NODE_HYPOTHESISE,
    NODE_RETRIEVE,
    NODES,
    RETRIEVE_TOOLS,
    RISK_CLASS,
)

__all__ = [
    "ACTION",
    "AGENT",
    "CAUSE_UNDER_INVESTIGATION",
    "DETERMINISTIC_NODES",
    "HYPOTHESISE_SCHEMA",
    "MAX_GROUP_FEEDS",
    "NODES",
    "NODE_COMPOSE",
    "NODE_GROUP",
    "NODE_HYPOTHESISE",
    "NODE_RETRIEVE",
    "RETRIEVE_TOOLS",
    "RISK_CLASS",
]
