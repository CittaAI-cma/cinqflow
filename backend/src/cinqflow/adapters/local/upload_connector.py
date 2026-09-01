"""upload-endpoint — a person hands the platform a file. The `connector` pin.

    connectors: [… upload-endpoint …]
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

THE PUSH CONNECTOR, and the one the wizard's first step needs. A BA setting up
a new payer has one XLSX in her downloads folder and no SFTP account yet; every
other connector in plate 09 assumes a running integration that does not exist
until after onboarding. Without this one the platform can only be shown data it
generated for itself.

IT IS A CONNECTOR, NOT A SHORTCUT. The temptation is an API route that writes
bytes to disk, which is four lines and opens the second door ADR-0011 forbids.
This lands through the same `deliver` verb as every poller, composes its key
with the same `core.delivery.landing_key`, and hands the result to the same
landing controls — so an uploaded file and an SFTP-fetched file are
indistinguishable by the time anything decides what to do with them. The only
difference this adapter is allowed to have is `delivered_by`, which is how the
audit row says a human did it.

`list_available` RETURNS NOTHING, HONESTLY. Nothing is waiting at an upload
endpoint; a file exists here only in the instant somebody sends one. The poller
that drives pull connectors can ask every connector what is available and this
one truthfully says "nothing", which is better than the port growing a
`is_pollable` flag that every caller would have to remember to check.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.core.delivery import (
    DELIVERED_BY_UPLOAD,
    NO_MANIFEST,
    Delivery,
    Manifest,
    fingerprint_of,
    landing_key,
    verify_manifest,
)
from cinqflow.ports import port
from cinqflow.ports.connector import (
    AlreadyDeliveredError,
    ConnectionCheck,
    ConnectorError,
    RemoteFile,
)

__all__ = ["UploadConnector"]


@port("connector", "upload")
class UploadConnector:
    """Delivers bytes somebody sent, into a real landing zone on disk."""

    def __init__(self, storage: LocalFsStorage, *, source: str = DELIVERED_BY_UPLOAD) -> None:
        self.storage = storage
        self._source = source

    @property
    def source(self) -> str:
        return self._source

    def connect(self) -> ConnectionCheck:
        """The landing zone is the only thing this connector needs to reach.

        Checked by writing nothing and asking the zone to list itself: an
        unwritable or absent root is the one failure this adapter can have, and
        discovering it when somebody uploads a 40MB file is discovering it too
        late.
        """
        try:
            next(iter(self.storage.list_files("")), None)
        except Exception as failure:
            return ConnectionCheck(
                reachable=False,
                source=self._source,
                detail=f"the landing zone could not be listed: {failure}",
            )
        return ConnectionCheck(reachable=True, source=self._source)

    def list_available(self, *, since: datetime | None = None) -> Iterator[RemoteFile]:
        """Nothing is ever waiting at an upload endpoint."""
        return iter(())

    def fetch(self, remote: RemoteFile) -> bytes:
        raise ConnectorError(
            "an upload endpoint is pushed at, never pulled from. There is nothing to "
            "fetch: the bytes arrive with the request, and `deliver` is what lands them."
        )

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
        key = landing_key(landing_path=landing_path, filename=filename, business_date=business_date)
        if self.storage.exists(key):
            raise AlreadyDeliveredError(
                f"{key} is already in the landing zone. Ask the input registry what "
                "happened to it rather than delivering it again — a second copy under "
                "the same name would make the first one unreachable."
            )
        # BEFORE the write. The storage pin has no delete verb, so damaged
        # bytes discovered afterwards could not be taken back out.
        verify_manifest(manifest, fingerprint=fingerprint_of(content))
        placed = self.storage.place(key, content, modified_ts=now)
        return Delivery(
            file=replace(placed, fingerprint=self.storage.fingerprint(key)),
            feed_id=feed_id,
            business_date=business_date,
            delivered_by=self._source,
            manifest=manifest,
        )
