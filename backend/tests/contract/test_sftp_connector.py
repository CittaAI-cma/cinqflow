"""`sftp-poller` — proven against a REAL SFTP server, not a mock.

`asyncssh` runs as a server as well as a client, so this starts a real
in-process SFTP server for the module (backed by a temp directory,
authentication-free), and points `SftpPollerConnector` at it — the same
"tested against a live counterpart" discipline `folder-drop`/`upload` get
from `test_connector_contract.py`, without a Docker dependency.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncssh
import pytest

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.sftp.connector import SftpPollerConnector
from cinqflow.core.delivery import Manifest, fingerprint_of
from cinqflow.ports.connector import AlreadyDeliveredError, UnreachableSourceError

pytestmark = pytest.mark.contract


class _NoAuthServer(asyncssh.SSHServer):
    """Accepts any username with no credential — the server side of a test
    double that still speaks the real protocol."""

    def begin_auth(self, username: str) -> bool:
        return False


async def _start_server(remote_root: Path) -> asyncssh.SSHAcceptor:
    host_key = asyncssh.generate_private_key("ssh-rsa")
    return await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        server_factory=_NoAuthServer,
        sftp_factory=lambda conn: asyncssh.SFTPServer(conn, chroot=str(remote_root)),
        allow_scp=False,
    )


@pytest.fixture
def remote_root(tmp_path: Path) -> Path:
    root = tmp_path / "remote"
    root.mkdir()
    return root


@pytest.fixture
def server_port(remote_root: Path) -> Iterator[int]:
    """A real, LISTENING SFTP server for the duration of one test.

    Run on its own event loop, in its own thread: the connector's public
    methods are synchronous and each opens a fresh `asyncio.run(...)` client
    loop, so the server needs its own loop actually spinning concurrently
    (`run_forever`, not a one-shot `run_until_complete`) to accept those
    connections from a separate thread.
    """
    state: dict[str, Any] = {}
    ready = threading.Event()

    def _serve() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state["loop"] = loop
        state["acceptor"] = loop.run_until_complete(_start_server(remote_root))
        ready.set()
        loop.run_forever()
        loop.run_until_complete(state["acceptor"].wait_closed())
        loop.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    if not ready.wait(timeout=5):
        raise RuntimeError("the in-process SFTP server never started")
    port = state["acceptor"].sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        loop = state["loop"]
        loop.call_soon_threadsafe(state["acceptor"].close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


@pytest.fixture
def connector(server_port: int, tmp_path: Path) -> SftpPollerConnector:
    landing = LocalFsStorage(root=str(tmp_path / "landing"))
    return SftpPollerConnector(
        landing,
        host="127.0.0.1",
        port=server_port,
        username="cinqflow",
        known_hosts=None,
        source="fidelis-sftp",
    )


def test_connect_reports_reachable_against_a_real_server(connector: SftpPollerConnector) -> None:
    check = connector.connect()
    assert check.reachable is True
    assert check.source == "fidelis-sftp"


def test_connect_reports_unreachable_against_a_closed_port(tmp_path: Path) -> None:
    landing = LocalFsStorage(root=str(tmp_path / "landing"))
    # Port 1 is privileged and never listening in a test sandbox — a safe
    # stand-in for "nothing is there".
    dead = SftpPollerConnector(
        landing, host="127.0.0.1", port=1, username="cinqflow", known_hosts=None,
        connect_timeout=1.0,
    )
    check = dead.connect()
    assert check.reachable is False
    assert check.detail


def test_list_available_sees_a_file_placed_on_the_remote(
    connector: SftpPollerConnector, remote_root: Path
) -> None:
    (remote_root / "roster.csv").write_bytes(b"MemberID\nMBR1\n")
    found = list(connector.list_available())
    assert [f.remote_key for f in found] == ["./roster.csv"]
    assert found[0].size_bytes == len(b"MemberID\nMBR1\n")


def test_list_available_is_empty_when_nothing_is_there(connector: SftpPollerConnector) -> None:
    assert list(connector.list_available()) == []


def test_fetch_returns_the_real_bytes(connector: SftpPollerConnector, remote_root: Path) -> None:
    (remote_root / "roster.csv").write_bytes(b"MemberID\nMBR1\n")
    (remote,) = list(connector.list_available())
    assert connector.fetch(remote) == b"MemberID\nMBR1\n"


def test_fetch_a_file_that_vanished_is_unreachable_not_a_crash(
    connector: SftpPollerConnector, remote_root: Path
) -> None:
    (remote_root / "roster.csv").write_bytes(b"data")
    (remote,) = list(connector.list_available())
    (remote_root / "roster.csv").unlink()
    with pytest.raises(UnreachableSourceError):
        connector.fetch(remote)


def test_deliver_lands_the_file_and_refuses_a_repeat(connector: SftpPollerConnector) -> None:
    content = b"MemberID,First_Name\nMBR1,Jane\n"
    delivery = connector.deliver(
        content,
        filename="roster.csv",
        feed_id="fidelis-downstate-roster",
        landing_path="enrollments/fidelis_downstate/roster",
        business_date="2026-09-01",
    )
    assert delivery.fingerprint == fingerprint_of(content)
    with pytest.raises(AlreadyDeliveredError):
        connector.deliver(
            content,
            filename="roster.csv",
            feed_id="fidelis-downstate-roster",
            landing_path="enrollments/fidelis_downstate/roster",
            business_date="2026-09-01",
        )


def test_deliver_refuses_a_manifest_that_disagrees_with_the_bytes(
    connector: SftpPollerConnector,
) -> None:
    from cinqflow.core.delivery import ChecksumMismatchError

    with pytest.raises(ChecksumMismatchError):
        connector.deliver(
            b"real bytes",
            filename="roster.csv",
            feed_id="fidelis-downstate-roster",
            landing_path="enrollments/fidelis_downstate/roster",
            business_date="2026-09-01",
            manifest=Manifest(checksum="sha256-0000000000000000000000000000000000000000"),
        )


def test_poll_then_deliver_end_to_end(connector: SftpPollerConnector, remote_root: Path) -> None:
    """The exact sequence `DeliveryWorker.deliver_available` runs: list, fetch,
    deliver — proven here against the real protocol, once, end to end."""
    content = b"MemberID,First_Name\nMBR1,Jane\n"
    (remote_root / "_CINQDOWNSTATE_Member_Roster_202609.csv").write_bytes(content)

    (remote,) = list(connector.list_available())
    fetched = connector.fetch(remote)
    delivery = connector.deliver(
        fetched,
        filename=remote.filename,
        feed_id="fidelis-downstate-roster",
        landing_path="enrollments/fidelis_downstate/roster",
        business_date="2026-09-01",
    )
    assert fetched == content
    assert delivery.file.filename == "_CINQDOWNSTATE_Member_Roster_202609.csv"
