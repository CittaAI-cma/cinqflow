"""Rung 0.5+ — a real, OpenAI-compatible endpoint. THE credential lives here.

    "Real from Wave 0 (ADR-0003), safe because development holds zero PHI
     (ADR-0016)."

One adapter, three deployments: a subscription at rungs 0.5-2, Azure AI Foundry
at rung 3, and any OpenAI-compatible gateway a tenant already runs. The swap is
an endpoint and a credential in the connection profile.
"""

from cinqflow.adapters.openai_compatible import llm

__all__ = ["llm"]
