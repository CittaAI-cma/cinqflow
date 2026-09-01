"""CF-V1-E6-02 — AI source→target mapping.

The shape (`graph`), the value-free grounding (`grounding`) and the prompt
(`prompts`) live here in core; the node implementations that touch pins are in
`intelligence/agents/mapping_suggestion.py`. The mapping OBJECT the agent
proposes into is `core.mapping`, built first by CF-V1-E6-03 so that the agent
speaks a vocabulary a human can already author by hand.
"""

from cinqflow.core.agents.mapping_suggestion.graph import (
    AGENT,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    DETERMINISTIC_NODES,
    NO_CONFIDENT_TARGET,
    NODE_ASSEMBLE,
    NODE_GROUND,
    NODE_SUGGEST,
    NODES,
    RISK_CLASS,
    SUGGEST_SCHEMA,
)
from cinqflow.core.agents.mapping_suggestion.grounding import (
    Exemplar,
    GroundedColumn,
    Grounding,
    TargetVocabulary,
    ground,
)

__all__ = [
    "AGENT",
    "CAPABILITY",
    "CONFIDENCE_FLOOR",
    "DETERMINISTIC_NODES",
    "NODES",
    "NODE_ASSEMBLE",
    "NODE_GROUND",
    "NODE_SUGGEST",
    "NO_CONFIDENT_TARGET",
    "RISK_CLASS",
    "SUGGEST_SCHEMA",
    "Exemplar",
    "GroundedColumn",
    "Grounding",
    "TargetVocabulary",
    "ground",
]
