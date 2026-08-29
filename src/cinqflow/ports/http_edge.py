"""The `http_edge` pin — expose the API and UI.

    verb: expose_api_ui   mock: testclient   dev: middleware|nginx
    target: tenant_ingress
    — docs/architecture/plates/04-pin-out-map.md

    "never design against API Management"
    — docs/architecture/plates/04-pin-out-map.md, traps_avoided

API Management is an operational wrapper a tenant MAY put in front of a clean
HTTP + OIDC API. Design against it and only one gateway works; design without
it and any gateway — or none — works. So this pin exposes a clean API and knows
nothing about what sits in front of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class EdgeConfig:
    host: str
    port: int
    root_path: str = ""


@runtime_checkable
class HttpEdgePort(Protocol):
    def serve(self, app: Any, config: EdgeConfig) -> None: ...
    def base_url(self) -> str: ...
