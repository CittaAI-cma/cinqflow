"""folder-drop — the pull connector, watching a directory. The `connector` pin.

    simulator: {serves_protocols: [folder_drop, sftp_server, http_api]}
    connectors: [sftp-poller, … storage-event …]
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

THE SHAPE EVERY PULL CONNECTOR HAS. An SFTP poller lists a remote directory,
fetches what is new, and lands it. So does this — the only difference is that
its remote is a local directory, which is exactly what makes it the honest dev
seat for the pin. Writing `sftp-poller` later is replacing `os.scandir` with a
client; the verbs, the key composition and the landing behaviour are already
proven by the contract suite this adapter passes.

IT IS ALSO THE ANSWER TO "CAN I DROP A FILE IN A FOLDER AND WATCH IT WORK".
The drop directory is NOT the landing zone: files are read from it and
delivered INTO the zone, where they are fingerprinted, registered and
classified. Making the drop directory the landing zone would mean anything
that could write a file could also choose its landing key, which is the second
door wearing a friendlier name.

RETRY ETIQUETTE. `list_available` reports what is in the drop directory every
time it is asked; it is `deliver` that refuses to land the same content twice.
The connector does not remember what it has delivered — the input registry
does, and a connector keeping its own memory of that would be a second answer
to "has this been processed", which is how a file gets processed twice after a
restart.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.core.delivery import (
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
    RemoteFile,
    UnreachableSourceError,
)

__all__ = ["FolderDropConnector"]


@port("connector", "folder-drop")
class FolderDropConnector:
    """Reads a drop directory; delivers into the landing zone."""

    def __init__(
        self,
        storage: LocalFsStorage,
        *,
        drop_root: str,
        source: str = "folder-drop",
    ) -> None:
        self.storage = storage
        self._drop = Path(drop_root)
        self._source = source

    @property
    def source(self) -> str:
        return self._source

    @property
    def drop_root(self) -> str:
        """Where a person puts a file for this connector to find.

        Printable on purpose: "put it here" is the first thing anybody asks,
        and an answer that requires reading the profile is an answer nobody
        gets.
        """
        return str(self._drop)

    def connect(self) -> ConnectionCheck:
        if not self._drop.exists():
            return ConnectionCheck(
                reachable=False,
                source=self._source,
                detail=(
                    f"the drop directory {self._drop} does not exist. Create it, or point "
                    "the connector's `drop_root` at the directory files actually arrive in."
                ),
            )
        if not self._drop.is_dir():
            return ConnectionCheck(
                reachable=False,
                source=self._source,
                detail=f"{self._drop} is a file, not a directory to poll",
            )
        if not os.access(self._drop, os.R_OK):
            return ConnectionCheck(
                reachable=False,
                source=self._source,
                detail=f"{self._drop} cannot be read by this process",
            )
        return ConnectionCheck(reachable=True, source=self._source)

    def list_available(self, *, since: datetime | None = None) -> Iterator[RemoteFile]:
        check = self.connect()
        if not check.reachable:
            raise UnreachableSourceError(check.detail)
        for path in sorted(self._drop.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            if since is not None and modified < since:
                continue
            yield RemoteFile(remote_key=path.name, size_bytes=stat.st_size, modified_ts=modified)

    def fetch(self, remote: RemoteFile) -> bytes:
        path = self._drop / remote.filename
        try:
            return path.read_bytes()
        except OSError as failure:
            raise UnreachableSourceError(
                f"{remote.remote_key} could not be read from {self._drop}: {failure}"
            ) from None

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
                "happened to it rather than delivering it again."
            )
        verify_manifest(manifest, fingerprint=fingerprint_of(content))
        placed = self.storage.place(key, content, modified_ts=now)
        return Delivery(
            file=replace(placed, fingerprint=self.storage.fingerprint(key)),
            feed_id=feed_id,
            business_date=business_date,
            delivered_by=self._source,
            manifest=manifest,
        )
