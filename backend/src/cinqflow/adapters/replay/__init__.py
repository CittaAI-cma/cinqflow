"""Lane 2 — recorded exchanges, replayed at the port boundary.

Holds no credential and opens no socket, so a Lane-2 test that drifts into
needing a live model fails loudly instead of quietly spending money.
"""

from cinqflow.adapters.replay import llm

__all__ = ["llm"]
