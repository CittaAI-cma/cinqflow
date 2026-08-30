"""CF-V2-E12-04 — the novel case gets a draft.

The shape (`graph`) and the prompts (`prompts`) live here in core; the node
implementations that touch pins — the gateway, the tool catalogue, the
proposal store — are in `intelligence/agents/fingerprint_match.py`. The
governed object this agent drafts INTO is `core.operations.fingerprint`'s own
`RecoveryGuide`, published as `ObjectType.RUNBOOK`.
"""

from cinqflow.core.agents.fingerprint_match.graph import (
    AGENT,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    DETERMINISTIC_NODES,
    DRAFT_SCHEMA,
    MAX_NEAR_MISS,
    NARRATE_SCHEMA,
    NODE_DRAFT,
    NODE_GATHER,
    NODE_NARRATE,
    NODE_RETRIEVE,
    NODES,
    RETRIEVE_TOOLS,
    RISK_CLASS,
    STATE_HAS_GROUNDING,
    STATE_NOVEL,
)

__all__ = [
    "AGENT",
    "CAPABILITY",
    "CONFIDENCE_FLOOR",
    "DETERMINISTIC_NODES",
    "DRAFT_SCHEMA",
    "MAX_NEAR_MISS",
    "NARRATE_SCHEMA",
    "NODES",
    "NODE_DRAFT",
    "NODE_GATHER",
    "NODE_NARRATE",
    "NODE_RETRIEVE",
    "RETRIEVE_TOOLS",
    "RISK_CLASS",
    "STATE_HAS_GROUNDING",
    "STATE_NOVEL",
]
