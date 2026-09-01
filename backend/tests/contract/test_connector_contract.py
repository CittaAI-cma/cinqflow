"""ONE suite, every connector. The `connector` pin's contract.

    "A new integration means a new port with three implementations (real, dev
     stand-in, mock) and one shared contract suite."
    — the five rules, rule 2

    connector_conformance: [connect, list, fetch, checksum_match, move,
                            retry_etiquette]
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

Every adapter below is held to the SAME landing behaviour, which is the whole
reason this is a pin and not three convenient classes. An uploaded file and a
polled file must be indistinguishable by the time landing controls see them —
same key, same fingerprint, same refusals — or `sftp-poller` arriving in a
later wave becomes a second ingestion path rather than a second adapter.

The push connector is parametrised in alongside the pull ones deliberately. Its
`list_available` is empty and its `fetch` refuses, and those are ASSERTED
rather than skipped: a port whose verbs some adapters quietly do not implement
is a port that cannot be programmed against.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cinqflow.adapters.local.folder_connector import FolderDropConnector
from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.connector import ScriptedConnector
from cinqflow.core.delivery import (
    ChecksumMismatchError,
    Manifest,
    UnsafeFilenameError,
    fingerprint_of,
)
from cinqflow.ports.connector import (
    AlreadyDeliveredError,
    ConnectorError,
    ConnectorPort,
)

pytestmark = pytest.mark.contract

NOW = datetime(2026, 8, 30, 6, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
LANDING = "enrollments/fidelis_downstate/roster"
DATE = "2026-09-01"
ROSTER = b"MemberID,First_Name,DOB\nM001,Ada,19900101\n"

#: The three seats the pin has to fill: mock, dev push, dev pull.
CONNECTORS: dict[str, Callable[[Path], ConnectorPort]] = {
    "mock": lambda root: ScriptedConnector(),
    "upload": lambda root: UploadConnector(LocalFsStorage(root=str(root / "landing"))),
    "folder-drop": lambda root: FolderDropConnector(
        LocalFsStorage(root=str(root / "landing")),
        drop_root=str(_drop(root)),
    ),
}


def _drop(root: Path) -> Path:
    path = root / "drop"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(params=sorted(CONNECTORS))
def connector(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ConnectorPort]:
    yield CONNECTORS[request.param](tmp_path)


def _deliver(connector: ConnectorPort, content: bytes = ROSTER, **overrides: object):  # type: ignore[no-untyped-def]
    arguments: dict[str, object] = {
        "filename": "_CINQDOWNSTATE_Member_Roster_202609.csv",
        "feed_id": FEED,
        "landing_path": LANDING,
        "business_date": DATE,
        "now": NOW,
    }
    arguments.update(overrides)
    return connector.deliver(content, **arguments)  # type: ignore[arg-type]


# ── the port is satisfied ────────────────────────────────────────────────────


def test_every_adapter_satisfies_the_port(connector: ConnectorPort) -> None:
    assert isinstance(connector, ConnectorPort)


def test_every_adapter_names_its_source_without_naming_a_credential(
    connector: ConnectorPort,
) -> None:
    """A NAME, never a URL. The address lives in the profile and the secret
    behind `secret://`, which is what keeps this printable in an audit row."""
    assert connector.source.strip()
    for leak in ("://", "@", "password", "key="):
        assert leak not in connector.source, connector.source


def test_connect_reports_reachable_when_it_is(connector: ConnectorPort) -> None:
    check = connector.connect()
    assert check.reachable is True
    assert check.source == connector.source


# ── deliver: the verb every adapter has ──────────────────────────────────────


def test_a_delivery_lands_under_the_layout(connector: ConnectorPort) -> None:
    """The one place the layout is spelled, proven identical across adapters.

    A file delivered by upload and the same file polled from a folder must be
    the SAME key, or the two paths are two ingestion schemes.
    """
    delivery = _deliver(connector)
    assert delivery.file.key == (
        f"{LANDING}/incoming/{DATE}/_CINQDOWNSTATE_Member_Roster_202609.csv"
    )
    assert delivery.feed_id == FEED
    assert delivery.business_date == DATE


def test_a_delivery_is_fingerprinted_by_content(connector: ConnectorPort) -> None:
    """Exactly-once ingestion is enforced on this value, so it cannot be absent
    and it must be the content's, not the name's."""
    delivery = _deliver(connector)
    assert delivery.fingerprint == fingerprint_of(ROSTER)
    assert delivery.citation == f"file:{fingerprint_of(ROSTER)}"


def test_the_delivered_bytes_are_readable_back_exactly(connector: ConnectorPort) -> None:
    delivery = _deliver(connector)
    assert connector.storage.read_bytes(delivery.file.key) == ROSTER  # type: ignore[attr-defined]


def test_the_delivery_says_who_made_it(connector: ConnectorPort) -> None:
    """ "Who delivered this" separates a human from a poller without anybody
    inferring it from a timestamp later."""
    assert _deliver(connector).delivered_by == connector.source


# ── retry etiquette: never deliver the same thing twice ──────────────────────


def test_delivering_the_same_file_twice_is_refused(connector: ConnectorPort) -> None:
    """A connector that retried by re-uploading would defeat the fingerprint
    check by racing it."""
    _deliver(connector)
    with pytest.raises(AlreadyDeliveredError) as refused:
        _deliver(connector)
    assert "input registry" in str(refused.value)


def test_a_different_month_is_not_a_duplicate(connector: ConnectorPort) -> None:
    """The business date is part of the key, so August and September coexist."""
    _deliver(connector)
    other = _deliver(connector, business_date="2026-10-01")
    assert "/2026-10-01/" in other.file.key


# ── checksum_match: the one thing refused before landing ─────────────────────


def test_a_manifest_that_matches_is_accepted(connector: ConnectorPort) -> None:
    delivery = _deliver(connector, manifest=Manifest(checksum=fingerprint_of(ROSTER)))
    assert delivery.manifest.checksum


def test_a_manifest_that_disagrees_with_the_bytes_is_refused(
    connector: ConnectorPort,
) -> None:
    """Damaged bytes are the ONE thing not landed-and-rejected. Fingerprinting
    the damage would register it under this delivery's name, and the re-send of
    the correct file would then look like a replay."""
    with pytest.raises(ChecksumMismatchError):
        _deliver(connector, manifest=Manifest(checksum="sha256-" + "0" * 32))


def test_nothing_is_landed_when_the_checksum_is_wrong(connector: ConnectorPort) -> None:
    """The refusal must happen BEFORE the write — the storage pin has no delete
    verb, so bytes discovered to be damaged afterwards could not be removed."""
    with pytest.raises(ChecksumMismatchError):
        _deliver(connector, manifest=Manifest(checksum="sha256-" + "0" * 32))
    key = f"{LANDING}/incoming/{DATE}/_CINQDOWNSTATE_Member_Roster_202609.csv"
    assert not connector.storage.exists(key)  # type: ignore[attr-defined]


def test_a_delivery_with_no_manifest_is_fine(connector: ConnectorPort) -> None:
    """Most payers send a file and nothing else. A platform that required a
    manifest could not accept the deliveries it exists to accept."""
    assert _deliver(connector, manifest=Manifest()).file.key


# ── the door refuses a path, and nothing else ────────────────────────────────


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "roster/2026.csv",
        "..",
        "",
        "   ",
        "roster\x00.csv",
    ],
)
def test_a_filename_that_is_a_path_is_refused_by_every_adapter(
    connector: ConnectorPort, hostile: str
) -> None:
    """A caller choosing the platform's write path, refused in one place before
    any adapter runs."""
    with pytest.raises(UnsafeFilenameError):
        _deliver(connector, filename=hostile)


def test_the_fidelis_leading_underscore_is_still_a_legal_name(
    connector: ConnectorPort,
) -> None:
    """Incident #1 in the other direction. The roster GENUINELY starts with an
    underscore, so the name is legal here; whether this feed expects one is
    landing's question, not the character set's."""
    delivery = _deliver(connector, filename="_CINQDOWNSTATE_Member_Roster_202609.csv")
    assert delivery.file.filename.startswith("_")


def test_an_empty_file_is_landed_rather_than_refused(connector: ConnectorPort) -> None:
    """ADR-0011: every arriving file is registered, including the bad ones.

    An empty delivery is a real thing a payer does, and the platform wants the
    row, the reason and the parked copy. `core.landing.classify` rejects it
    with a named check; the door does not get to delete the evidence.
    """
    delivery = _deliver(connector, content=b"")
    assert delivery.file.size_bytes == 0


def test_a_file_matching_no_feed_pattern_is_landed_rather_than_refused(
    connector: ConnectorPort,
) -> None:
    """It becomes UNEXPECTED and is parked — never ignored. The connector has
    no opinion about whether a name matches a feed, and giving it one would be
    a second place that decision is made."""
    assert _deliver(connector, filename="something_nobody_registered.csv").file.key


# ── list and fetch: honest for both shapes ───────────────────────────────────


def test_a_push_connector_lists_nothing_and_says_so(tmp_path: Path) -> None:
    """Not a stub — the truth. Nothing waits at an upload endpoint."""
    connector = UploadConnector(LocalFsStorage(root=str(tmp_path)))
    assert list(connector.list_available()) == []


def test_a_push_connector_refuses_to_be_pulled_from(tmp_path: Path) -> None:
    connector = UploadConnector(LocalFsStorage(root=str(tmp_path)))
    with pytest.raises(ConnectorError) as refused:
        connector.fetch(
            RemoteFileStub()  # type: ignore[arg-type]
        )
    assert "pushed at, never pulled from" in str(refused.value)


class RemoteFileStub:
    remote_key = "anything.csv"
    filename = "anything.csv"


def test_a_pull_connector_lists_what_is_in_the_drop_directory(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    (drop / "roster.csv").write_bytes(ROSTER)
    (drop / ".hidden").write_bytes(b"ignored")
    connector = FolderDropConnector(
        LocalFsStorage(root=str(tmp_path / "landing")), drop_root=str(drop)
    )
    available = list(connector.list_available())
    assert [remote.filename for remote in available] == ["roster.csv"]
    assert available[0].size_bytes == len(ROSTER)


def test_a_pull_connector_fetches_the_bytes_it_listed(tmp_path: Path) -> None:
    drop = _drop(tmp_path)
    (drop / "roster.csv").write_bytes(ROSTER)
    connector = FolderDropConnector(
        LocalFsStorage(root=str(tmp_path / "landing")), drop_root=str(drop)
    )
    remote = next(iter(connector.list_available()))
    assert connector.fetch(remote) == ROSTER


def test_a_pull_connector_reports_a_missing_drop_directory_actionably(
    tmp_path: Path,
) -> None:
    """ "Connection failed" sends somebody to read logs. This sends them to
    create a directory."""
    connector = FolderDropConnector(
        LocalFsStorage(root=str(tmp_path / "landing")), drop_root=str(tmp_path / "absent")
    )
    check = connector.connect()
    assert check.reachable is False
    assert "does not exist" in check.detail


# ── the fingerprint the platform computes twice must agree ───────────────────


def test_the_precomputed_fingerprint_matches_what_storage_reports(
    connector: ConnectorPort,
) -> None:
    """`core.delivery.fingerprint_of` exists so a manifest can be checked
    BEFORE the write. If it ever drifts from `StoragePort.fingerprint`, a
    manifest would be verified against one identity and the replay refusal
    enforced against another — and a re-sent file would land twice."""
    delivery = _deliver(connector)
    assert fingerprint_of(ROSTER) == connector.storage.fingerprint(delivery.file.key)  # type: ignore[attr-defined]
