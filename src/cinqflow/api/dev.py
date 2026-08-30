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

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.intelligence.demo import (
    BATCH_ID,
    BUDGET,
    agent_for,
    fingerprint_match_agent_for,
    plane,
    schema_inference_for,
)
from cinqflow.workers.incidents import IncidentWorker

#: The same root `profiles/local.yaml` names, so a file delivered through the
#: dev server and one delivered by `cinqflow ingest` land in one zone.
DEFAULT_LANDING_ROOT = ".cinqflow/landing"


def build(landing_root: str | None = None) -> Any:
    """The mock socket, with ONE real seat: the landing zone.

    CF-V1-E3-05. A memfs landing zone would let a BA upload a file, watch it
    land, and find nothing there after a restart — so the dev server fits the
    REAL localfs storage and the REAL upload connector, rooted in a directory
    somebody can open. Everything else stays in memory, which is what rung 0
    is for: the delivery path is the one thing you cannot honestly demonstrate
    without a disk.
    """
    store, control = plane()
    # W2-33 (CF-V2-E12-04). `plane()` seeds the anchor batch's ERROR; it does
    # not open its incident, because `IncidentWorker` lives in `workers/` — a
    # layer `intelligence/demo.py` may not import (`lint-imports`'s own
    # "api -> workers/installer/simulator -> intelligence" contract says so).
    # `api/dev.py` sits above that line, so the wiring happens here instead.
    # Without it `GET /api/operations/incidents` — which reads the LEDGER,
    # deliberately, never recomputed evidence — would show nothing for a batch
    # that visibly failed, and nothing on the incidents screen could ever be
    # acknowledged, resolved or closed.
    #
    # W2-38: the same call now also carries a real `FingerprintMatchAgent` —
    # built by `fingerprint_match_agent_for`, the demo plane's own scripted
    # stand-in, exactly the way `agent_for` builds `PipelineInsightAgent`. The
    # anchor batch's error matches no seeded guide, so this is the NOVEL path
    # exercised for real on every dev-server start: one drafted proposal,
    # from the scripted model, with no credential involved.
    IncidentWorker(
        control=control,
        metadata=store,
        fingerprint_agent=fingerprint_match_agent_for(control, store),
    ).on_batch_failed(BATCH_ID)
    landing = LocalFsStorage(root=landing_root or DEFAULT_LANDING_ROOT)
    return create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        storage=landing,
        connector=UploadConnector(landing),
        agent_factory=agent_for,
        schema_inference_factory=schema_inference_for,
        budget=BUDGET,
    )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--landing-root",
        default=DEFAULT_LANDING_ROOT,
        help="Where delivered files land. A real directory, so you can open it.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    arguments = parser.parse_args()
    uvicorn.run(
        build(arguments.landing_root),
        host=arguments.host,
        port=arguments.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
