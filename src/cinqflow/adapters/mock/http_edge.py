"""testclient — the API in-process, with no socket."""

from __future__ import annotations

from typing import Any

from cinqflow.ports import port
from cinqflow.ports.http_edge import EdgeConfig


@port("http_edge", "mock")
class TestClientHttpEdge:
    """Holds the app without binding a port.

    Which is what lets the API's contract tests run in the seconds-long lane
    alongside the unit tests, rather than needing a server.
    """

    def __init__(self) -> None:
        self.app: Any | None = None
        self._config = EdgeConfig(host="testclient", port=0)

    def serve(self, app: Any, config: EdgeConfig) -> None:
        self.app = app
        self._config = config

    def base_url(self) -> str:
        return "http://testclient"
