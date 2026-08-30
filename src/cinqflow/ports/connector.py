"""The `connector` pin — how a file gets INTO the landing zone.

    verb: connect/list/fetch/deliver   mock: scripted   dev: upload|folder-drop
    target: sftp-poller|api-puller|storage-event
    — docs/architecture/plates/04-pin-out-map.md

    connectors: [sftp-poller, api-puller, fhir-puller, storage-event,
                 db-extractor, upload-endpoint, stream-batcher]
    connector_conformance: [connect, list, fetch, checksum_match, move,
                            retry_etiquette]
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

THE PIN THAT WAS MISSING. Plate 09 named seven connectors from the beginning
and plate 04 gave none of them a pin, so the platform could read a landing zone
it had no way to fill. `storage` is the zone's READER — list, fingerprint,
move, and deliberately no write verb, because a port that cannot express
deletion is a stronger guarantee than one that documents not deleting. This is
the zone's WRITER, and it is a separate pin for the same reason they are
separate concerns: everything that reads the zone should stay unable to write
to it.

WHY `deliver` LIVES HERE AND NOT ON `storage`. Adding a write verb to storage
would have been three lines and would have opened the second door ADR-0011
forbids: every component holding the storage pin — the profiler, the pipeline
runner, the explorer — would have gained the ability to put files in the
landing zone, and "no delivery path bypasses registration" would have become a
convention rather than a property. Only a connector delivers, only the
delivery worker holds a connector, and the worker calls `core.landing.classify`
on every single thing it lands.

PUSH AND PULL, ONE PORT. An upload endpoint is pushed at; an SFTP poller
pulls. The verbs accommodate both without pretending they are the same:

    connect()          is the remote reachable, are the credentials good
    list_available()   what is out there — EMPTY for a push connector
    fetch()            bytes for one remote file — unused by a push connector
    deliver()          put these bytes in the zone. EVERY connector does this.

`deliver` is the one every adapter implements and the only one that writes, so
the contract suite can hold every adapter to the same landing behaviour while
letting a push connector honestly report that it lists nothing.

RETRY ETIQUETTE IS A REFUSAL, NOT A LOOP. Plate 09's conformance list ends with
`retry_etiquette`, and what it means here is that a connector NEVER re-delivers
content it has already delivered — it raises `AlreadyDeliveredError` and lets
the caller ask the input registry. A connector that retried by re-uploading
would defeat the fingerprint check by racing it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from cinqflow.core.delivery import NO_MANIFEST, Delivery, Manifest

__all__ = [
    "AlreadyDeliveredError",
    "ConnectionCheck",
    "ConnectorError",
    "ConnectorPort",
    "RemoteFile",
    "UnreachableSourceError",
]


class ConnectorError(RuntimeError):
    """The adapter could not honour the verb. Never silently swallowed."""


class UnreachableSourceError(ConnectorError):
    """The remote is down, or the credentials are wrong.

    Named separately from `ConnectorError` because "the payer's SFTP is down"
    and "the payer's SFTP rejected our key" are different incidents with
    different people to wake, and reporting them as one is how a credential
    expiry spends a night being investigated as an outage.
    """


class AlreadyDeliveredError(ConnectorError):
    """This content is already in the zone.

    Raised rather than returning the existing receipt, because a caller that
    treats re-delivery as success will happily loop. The fingerprint is in
    `control.input_registry`; the answer to "what happened to it" is there,
    not in a second copy of the file.
    """


@dataclass(frozen=True)
class ConnectionCheck:
    """Whether the source can be reached, in a shape a person can act on.

    `detail` is required on a failure for the same reason a landing rejection
    needs a named check: "connection failed" sends somebody to read logs, and
    "host key changed" sends them to the right person in a minute.
    """

    reachable: bool
    source: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.reachable and not self.detail.strip():
            raise ConnectorError(
                "an unreachable source must say why — a connection check with no detail "
                "is an alert nobody can act on"
            )


@dataclass(frozen=True)
class RemoteFile:
    """One file visible at the source, before anything is fetched.

    Deliberately NOT a `FileRef`: a `FileRef` is a file in the landing zone,
    with a fingerprint the platform computed. This is a file somebody else
    holds, whose size they report and whose checksum they may or may not
    declare. Conflating the two is how a remote's claim about its own content
    ends up in the input registry as though the platform had verified it.
    """

    remote_key: str
    size_bytes: int
    modified_ts: datetime
    declared_checksum: str | None = None

    @property
    def filename(self) -> str:
        return self.remote_key.rsplit("/", 1)[-1]


@runtime_checkable
class ConnectorPort(Protocol):
    """connect · list_available · fetch · deliver.

    Four verbs, and only the last one writes.
    """

    @property
    def source(self) -> str:
        """What this connector talks to, for an audit row and an alert.

        A NAME, never a URL or a credential: `fidelis-sftp`, not
        `sftp://user:key@10.2.…`. The address lives in the connection profile
        and the secret behind `secret://`, which is what keeps every adapter's
        identity printable in a log.
        """
        ...

    def connect(self) -> ConnectionCheck:
        """Is the source reachable and are the credentials accepted?

        Separate from `list_available` so a health check costs nothing and
        says something specific. A platform that can only discover an expired
        key by failing to list a directory reports a data problem for a
        credentials problem.
        """
        ...

    def list_available(self, *, since: datetime | None = None) -> Iterator[RemoteFile]:
        """What the source is offering. EMPTY for a push connector, always.

        A push connector returning an empty iterator is not a stub — it is the
        truth. Nothing is available at an upload endpoint until somebody
        uploads, and the poller that drives pull connectors must be able to ask
        every connector this question and get an honest answer.
        """
        ...

    def fetch(self, remote: RemoteFile) -> bytes:
        """The bytes of one remote file. Not yet landed, not yet registered."""
        ...

    def deliver(
        self,
        content: bytes,
        *,
        filename: str,
        feed_id: str,
        landing_path: str,
        business_date: str,
        manifest: Manifest = NO_MANIFEST,
        now: datetime | None = None,
    ) -> Delivery:
        """Put these bytes in the landing zone. THE ONLY WRITE IN THE PLATFORM.

        The adapter composes no path of its own: it calls
        `core.delivery.landing_key`, which is the one place the layout is
        spelled, so a file delivered by upload and the same file delivered by
        a poller land in the same folder under the same name and fingerprint
        identically.

        It does NOT classify, register or move — landing controls do that, on
        everything, from one place. A connector that decided a file was
        acceptable would be the second door.

        Raises `AlreadyDeliveredError` when this exact content is already
        present, and `core.delivery.ChecksumMismatchError` when the manifest
        and the bytes disagree.
        """
        ...
