"""CF-V1-E7-01 — natural-language rule authoring.

The shape (`graph`), the grounding (`grounding`) and the prompt (`prompts`)
live here in core; the node implementations that touch pins are in
`intelligence/agents/rule_authoring.py`. The RULE the agent proposes into is
`core.rules`, where a check is parameters and never an expression.
"""

from cinqflow.core.agents.rule_authoring.graph import (
    AGENT,
    AUTHOR_SCHEMA,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    DETERMINISTIC_NODES,
    NEEDS_TECHNICAL_REVIEW,
    NODE_ASSEMBLE,
    NODE_AUTHOR,
    NODE_GROUND,
    NODES,
    RISK_CLASS,
)
from cinqflow.core.agents.rule_authoring.grounding import (
    GroundedColumn,
    Grounding,
    Precedent,
    Request,
    ground,
)

__all__ = [
    "AGENT",
    "AUTHOR_SCHEMA",
    "CAPABILITY",
    "CONFIDENCE_FLOOR",
    "DETERMINISTIC_NODES",
    "NEEDS_TECHNICAL_REVIEW",
    "NODES",
    "NODE_ASSEMBLE",
    "NODE_AUTHOR",
    "NODE_GROUND",
    "RISK_CLASS",
    "GroundedColumn",
    "Grounding",
    "Precedent",
    "Request",
    "ground",
]
