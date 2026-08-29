"""A development server on the mock socket. Rung 0 — nothing runs but Python.

    0: {socket: mock, cost: 0, proves: core_logic_in_ci_seconds}
    — docs/architecture/plates/05-socket-ladder.md

Thin on purpose. The seeded plane and the agent live in
`intelligence/demo.py`, below this layer, so the CLI can use them without
reaching up into the API for an HTTP server it does not need.
"""

from __future__ import annotations

import argparse
from typing import Any

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.intelligence.demo import BUDGET, agent_for, plane, schema_inference_for


def build() -> Any:
    store, control = plane()
    return create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        agent_factory=agent_for,
        schema_inference_factory=schema_inference_for,
        budget=BUDGET,
    )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    arguments = parser.parse_args()
    uvicorn.run(build(), host=arguments.host, port=arguments.port, log_level="warning")


if __name__ == "__main__":
    main()
