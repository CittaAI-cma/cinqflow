"""CF-V1-E5-02 — AI schema inference, proposing a data contract.

The shape (`graph`), the deterministic grounding (`grounding`) and the prompt
(`prompts`) live here in `core/`: everything about this agent that can be
reasoned about, versioned and tested without a model or a pin. The node
implementations live in `intelligence/agents/schema_inference.py`.
"""

from cinqflow.core.agents.schema_inference.graph import (
    AGENT,
    CAPABILITY,
    CONFIDENCE_FLOOR,
    DETERMINISTIC_NODES,
    INFER_SCHEMA,
    NEEDS_YOUR_INPUT,
    NODE_ASSEMBLE,
    NODE_GROUND,
    NODE_INFER,
    NODES,
    RISK_CLASS,
)
from cinqflow.core.agents.schema_inference.grounding import (
    GroundedColumn,
    Grounding,
    ground,
    merge,
)

__all__ = [
    "AGENT",
    "CAPABILITY",
    "CONFIDENCE_FLOOR",
    "DETERMINISTIC_NODES",
    "INFER_SCHEMA",
    "NEEDS_YOUR_INPUT",
    "NODES",
    "NODE_ASSEMBLE",
    "NODE_GROUND",
    "NODE_INFER",
    "RISK_CLASS",
    "GroundedColumn",
    "Grounding",
    "ground",
    "merge",
]
