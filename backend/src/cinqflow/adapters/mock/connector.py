"""A scripted connector. The `connector` pin's mock seat.

    connector: {mock: scripted}
    — docs/architecture/plates/04-pin-out-map.md

Delivers into a `MemFsStorage`, so a Lane-1 test can exercise the whole
delivery path — key composition, checksum verification, replay refusal — with
no filesystem and no credentials.

`offer()` is what makes it scriptable: a test says what the remote is holding,
and `list_available`/`fetch` answer from that. A pull connector's behaviour is
then testable without an SFTP server, which is the whole reason the mock seat
exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

from cinqflow.adapters.mock.storage import MemFsStorage
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

__all__ = ["ScriptedConnector"]


@port("connector", "mock")
class ScriptedConnector:
    """A connector whose remote is a dictionary."""

    def __init__(
        self,
        storage: MemFsStorage | None = None,
        *,
        source: str = "scripted-source",
        reachable: bool = True,
        unreachable_because: str = "the scripted source was told to be down",
    ) -> None:
        self.storage = storage if storage is not None else MemFsStorage()
        self._source = source
        self._reachable = reachable
        self._unreachable_because = unreachable_because
        self._offered: dict[str, tuple[RemoteFile, bytes]] = {}

    # ── scripting ────────────────────────────────────────────────────────────
    def offer(
        self,
        remote_key: str,
        content: bytes,
        *,
        modified_ts: datetime | None = None,
        declared_checksum: str | None = None,
    ) -> RemoteFile:
        """Put a file at the remote, for `list_available` to find."""
        remote = RemoteFile(
            remote_key=remote_key,
            size_bytes=len(content),
            modified_ts=modified_ts or datetime(2026, 8, 30, 6, 0, tzinfo=UTC),
            declared_checksum=declared_checksum,
        )
        self._offered[remote_key] = (remote, content)
        return remote

    # ── the port ─────────────────────────────────────────────────────────────
    @property
    def source(self) -> str:
        return self._source

    def connect(self) -> ConnectionCheck:
        if not self._reachable:
            return ConnectionCheck(
                reachable=False, source=self._source, detail=self._unreachable_because
            )
        return ConnectionCheck(reachable=True, source=self._source)

    def list_available(self, *, since: datetime | None = None) -> Iterator[RemoteFile]:
        if not self._reachable:
            raise UnreachableSourceError(self._unreachable_because)
        for remote, _ in sorted(self._offered.values(), key=lambda pair: pair[0].remote_key):
            if since is None or remote.modified_ts >= since:
                yield remote

    def fetch(self, remote: RemoteFile) -> bytes:
        if not self._reachable:
            raise UnreachableSourceError(self._unreachable_because)
        held = self._offered.get(remote.remote_key)
        if held is None:
            raise UnreachableSourceError(f"{remote.remote_key} is not at {self._source}")
        return held[1]

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
        # BEFORE the write, not after: the storage pin has no delete verb, so a
        # mismatch discovered post-write would leave damaged bytes in the zone
        # with no way to take them back out.
        verify_manifest(manifest, fingerprint=fingerprint_of(content))
        placed = self.storage.place(key, content, modified_ts=now)
        return Delivery(
            file=replace(placed, fingerprint=self.storage.fingerprint(key)),
            feed_id=feed_id,
            business_date=business_date,
            delivered_by=self._source,
            manifest=manifest,
        )
