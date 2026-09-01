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

from cinqflow.adapters.local.file_document_parse import FileDocumentParser
from cinqflow.adapters.local.folder_connector import FolderDropConnector
from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.intelligence.demo import (
    BATCH_ID,
    BUDGET,
    agent_for,
    alert_enrichment_agent_for,
    fingerprint_match_agent_for,
    layer_reader_for,
    merge_evidence_agent_for,
    plane,
    schema_inference_for,
)
from cinqflow.workers.incidents import IncidentWorker

#: The same root `profiles/local.yaml` names, so a file delivered through the
#: dev server and one delivered by `cinqflow ingest` land in one zone.
DEFAULT_LANDING_ROOT = ".cinqflow/landing"

#: CF-V1-E8-09's named pull route, mirroring `profiles/local.yaml`'s
#: `fidelis-sftp` — a feed registered with that `endpoint_ref` polls this
#: directory instead of using the deployment's default upload connector.
DEFAULT_SFTP_DROP_ROOT = ".cinqflow/drop/fidelis-sftp"


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
        # CF-V1-E8-09. Routed, not one adapter: `default` is what a feed with
        # no (or an unrouted) `endpoint_ref` gets, and `fidelis-sftp` is the
        # named pull route a feed registered with that `endpoint_ref` polls
        # instead — the same two seats `profiles/local.yaml` fits.
        connectors={
            "default": UploadConnector(landing),
            "fidelis-sftp": FolderDropConnector(landing, drop_root=DEFAULT_SFTP_DROP_ROOT),
        },
        document_parse=FileDocumentParser(),
        # W3-01. A seeded medallion plane, so the six-layer screen renders on
        # the mock socket with no database — and masks there too, which is what
        # keeps a UI regression that un-masks a column from passing CI.
        layer_reader=layer_reader_for(),
        agent_factory=agent_for,
        schema_inference_factory=schema_inference_for,
        # CF-V3-E9-03. Same reasoning as schema_inference_for above: `gather`
        # is real, so `POST /api/identity/merge-preview` returns a true plan
        # and comparison on every dev-server start, narrative aside.
        merge_evidence_factory=merge_evidence_agent_for,
        # CF-V2-E12-05. Built once, off the same scripted stand-in every other
        # demo agent uses — `GET /api/operations/alerts` has a real path to
        # exercise on every dev-server start, with no credential involved.
        alert_enrichment_agent=alert_enrichment_agent_for(control, store),
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
