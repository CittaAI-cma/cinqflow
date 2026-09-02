"""sftp-poller — a real SFTP client behind the `connector` pin. ADR-0023.

    "Writing `sftp-poller` later is replacing `os.scandir` with a client —
     the verbs, the key composition and the landing behaviour are already
     proven by the contract suite this adapter passes."
    — `ports/connector.py`, and `FolderDropConnector`'s own docstring

`FolderDropConnector` is the template this adapter follows exactly:
`list_available`/`fetch` pull from the remote; `deliver` lands into the
landing zone through the identical body every connector adapter shares
(`landing_key` → `storage.exists` → `AlreadyDeliveredError` →
`verify_manifest` → `storage.place` → `Delivery`). The only real difference
is that "the remote" is a live SSH server instead of a local directory.

`asyncssh` is async-only; `ConnectorPort`'s methods are sync. Rather than
holding a persistent event loop and connection pool, each call opens one
short-lived SSH session via `asyncio.run(...)`. That is the right tradeoff at
a payer's actual cadence (Fidelis polls monthly) — pooling would be solving
a problem this feed does not have.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import asyncssh

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

__all__ = ["SftpPollerConnector"]


@port("connector", "sftp-poller")
class SftpPollerConnector:
    """A real SFTP client. Reads a remote directory; delivers into the
    landing zone."""

    def __init__(
        self,
        storage: LocalFsStorage,
        *,
        host: str,
        username: str,
        password: str | None = None,
        port: int = 22,
        remote_root: str = ".",
        source: str = "sftp-poller",
        known_hosts: object | None = None,
        connect_timeout: float = 10.0,
    ) -> None:
        self.storage = storage
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._remote_root = remote_root.rstrip("/") or "."
        self._source = source
        # `None` disables host-key checking — the right default against the
        # local/dev simulator, whose host key is not persisted across
        # container recreations. A real client-tenant deployment names a
        # real known_hosts file via the profile, same as every other pin.
        self._known_hosts = known_hosts
        self._timeout = connect_timeout

    @property
    def source(self) -> str:
        return self._source

    def _connect_kwargs(self) -> dict[str, object]:
        return {
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "known_hosts": self._known_hosts,
            "connect_timeout": self._timeout,
        }

    def connect(self) -> ConnectionCheck:
        try:
            asyncio.run(self._check())
        except (OSError, asyncssh.Error) as failure:
            return ConnectionCheck(
                reachable=False,
                source=self._source,
                detail=f"{self._host}:{self._port} refused the connection: {failure}",
            )
        return ConnectionCheck(reachable=True, source=self._source)

    async def _check(self) -> None:
        async with asyncssh.connect(**self._connect_kwargs()) as conn:
            async with conn.start_sftp_client():
                pass

    def list_available(self, *, since: datetime | None = None) -> Iterator[RemoteFile]:
        try:
            found = asyncio.run(self._list_available(since))
        except (OSError, asyncssh.Error) as failure:
            raise UnreachableSourceError(
                f"{self._host}:{self._port}{self._remote_root} could not be listed: {failure}"
            ) from None
        return iter(found)

    async def _list_available(self, since: datetime | None) -> list[RemoteFile]:
        found: list[RemoteFile] = []
        async with asyncssh.connect(**self._connect_kwargs()) as conn:
            async with conn.start_sftp_client() as sftp:
                for name in sorted(await sftp.listdir(self._remote_root)):
                    if name in (".", ".."):
                        continue
                    remote_path = f"{self._remote_root}/{name}"
                    attrs = await sftp.stat(remote_path)
                    if not attrs.type or attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                        continue
                    modified = datetime.fromtimestamp(attrs.mtime or 0, tz=UTC)
                    if since is not None and modified < since:
                        continue
                    found.append(
                        RemoteFile(
                            remote_key=remote_path, size_bytes=attrs.size or 0, modified_ts=modified
                        )
                    )
        return found

    def fetch(self, remote: RemoteFile) -> bytes:
        try:
            return asyncio.run(self._fetch(remote))
        except (OSError, asyncssh.Error) as failure:
            raise UnreachableSourceError(
                f"{remote.remote_key} could not be read from {self._host}: {failure}"
            ) from None

    async def _fetch(self, remote: RemoteFile) -> bytes:
        async with asyncssh.connect(**self._connect_kwargs()) as conn:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(remote.remote_key, "rb") as handle:
                    content = await handle.read()
        return bytes(content)

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
