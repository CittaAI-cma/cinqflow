"""CF-V1-E8-09's exception path — `DeliveryWorker.deliver_available`, retried.

    "Exception — Given a remote fetch fails mid-transfer, when the connector
     retries per etiquette, then partial files never enter incoming/, the
     retry is logged, and repeated failure raises one SLA incident — not a
     file mystery."
    — CF-V1-E8-09, acceptance criteria

Every scenario here uses the SAME connector every other pull adapter passes
(`ScriptedConnector`), wrapped by a tiny fake that fails `fetch()` a scripted
number of times per remote — so what is under test is the WORKER's retry
policy, not a new adapter's behaviour.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest

from cinqflow.adapters.mock.connector import ScriptedConnector
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.core.delivery import RetryPolicy
from cinqflow.core.registry.golden_fidelis import FEED, FEED_VERSION
from cinqflow.ports.connector import ConnectionCheck, RemoteFile, UnreachableSourceError
from cinqflow.workers.delivery import DeliveryWorker, PollFailedError

pytestmark = pytest.mark.contract

NOW = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
DATE = "2026-09-01"


class _FlakyConnector:
    """A `ScriptedConnector` whose `fetch` fails N times per remote key before
    delegating — the transient-then-recovers shape a real SFTP hiccup has."""

    def __init__(self, inner: ScriptedConnector, *, fail_first: dict[str, int]) -> None:
        self._inner = inner
        self._remaining = dict(fail_first)
        self.fetch_calls: list[str] = []

    @property
    def source(self) -> str:
        return self._inner.source

    def connect(self) -> ConnectionCheck:
        return self._inner.connect()

    def list_available(self, *, since: datetime | None = None) -> Iterator[RemoteFile]:
        return self._inner.list_available(since=since)

    def fetch(self, remote: RemoteFile) -> bytes:
        self.fetch_calls.append(remote.remote_key)
        left = self._remaining.get(remote.remote_key, 0)
        if left > 0:
            self._remaining[remote.remote_key] = left - 1
            raise UnreachableSourceError(f"{remote.remote_key}: simulated transient failure")
        return self._inner.fetch(remote)

    def deliver(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        return self._inner.deliver(*args, **kwargs)  # type: ignore[arg-type]


def _worker(connector: _FlakyConnector) -> DeliveryWorker:
    return DeliveryWorker(
        connector=connector,
        storage=connector._inner.storage,
        control=MemStoreControlTables(),
    )


def _sleeper() -> tuple[list[float], Callable[[float], None]]:
    delays: list[float] = []

    def sleep(seconds: float) -> None:
        delays.append(seconds)

    return delays, sleep


def test_a_transient_failure_is_retried_and_the_file_still_lands() -> None:
    """Two failures, then a third attempt that succeeds — within the default
    policy's three attempts — lands the file exactly as a clean fetch would."""
    inner = ScriptedConnector()
    inner.offer("_CINQDOWNSTATE_Member_Roster_202609.csv", b"MemberID\nM001\n", modified_ts=NOW)
    connector = _FlakyConnector(inner, fail_first={"_CINQDOWNSTATE_Member_Roster_202609.csv": 2})
    delays, sleep = _sleeper()

    outcomes = _worker(connector).deliver_available(
        feed=FEED, feed_version=FEED_VERSION, business_date=DATE, sleep=sleep
    )

    assert len(outcomes) == 1
    assert outcomes[0].delivery.file.filename == "_CINQDOWNSTATE_Member_Roster_202609.csv"
    assert connector.fetch_calls == ["_CINQDOWNSTATE_Member_Roster_202609.csv"] * 3
    # attempt 2 waits base_seconds (1.0), attempt 3 waits base_seconds*2 (2.0) —
    # RetryPolicy's own default schedule, exercised end to end through the
    # worker rather than asserted only on the pure function.
    assert delays == [1.0, 2.0]


def test_repeated_failure_raises_one_error_naming_the_file_while_others_still_land() -> None:
    """A file that never recovers does not stop its neighbours: `ok.csv`
    lands, and `PollFailedError` names only the file that never came back —
    ONE exception, not a flood, and not a mystery about which file it was."""
    inner = ScriptedConnector()
    inner.offer("ok.csv", b"MemberID\nM001\n", modified_ts=NOW)
    inner.offer("gone.csv", b"MemberID\nM002\n", modified_ts=NOW)
    connector = _FlakyConnector(inner, fail_first={"gone.csv": 99})
    _, sleep = _sleeper()

    with pytest.raises(PollFailedError) as failure:
        _worker(connector).deliver_available(
            feed=FEED,
            feed_version=FEED_VERSION,
            business_date=DATE,
            retry=RetryPolicy(max_attempts=2, base_seconds=0.0),
            sleep=sleep,
        )

    assert [key for key, _ in failure.value.failures] == ["gone.csv"]
    assert len(failure.value.landed) == 1
    assert failure.value.landed[0].delivery.file.filename == "ok.csv"


def test_a_file_that_never_recovers_never_lands_a_partial_copy() -> None:
    """`fetch()` is exhausted entirely in memory before `deliver()` is ever
    called for that remote — a file that fails every attempt has no key in
    the landing zone and no row in the input registry at all."""
    inner = ScriptedConnector()
    inner.offer("gone.csv", b"MemberID\nM002\n", modified_ts=NOW)
    connector = _FlakyConnector(inner, fail_first={"gone.csv": 99})
    worker = _worker(connector)
    _, sleep = _sleeper()

    with pytest.raises(PollFailedError):
        worker.deliver_available(
            feed=FEED,
            feed_version=FEED_VERSION,
            business_date=DATE,
            retry=RetryPolicy(max_attempts=2, base_seconds=0.0),
            sleep=sleep,
        )

    assert list(inner.storage.list_files("")) == []
    assert worker.control.find_input_by_fingerprint("sha256-anything") is None


def test_an_error_other_than_unreachable_is_not_retried() -> None:
    """Only `UnreachableSourceError` names a transient failure. Anything else
    `fetch()` raises is a defect, and retrying it would turn a real bug into
    a misleading `PollFailedError` about flakiness that was never there."""

    class _Broken(_FlakyConnector):
        def fetch(self, remote: RemoteFile) -> bytes:
            raise ValueError("not a connector problem")

    inner = ScriptedConnector()
    inner.offer("roster.csv", b"MemberID\nM001\n", modified_ts=NOW)
    connector = _Broken(inner, fail_first={})
    _, sleep = _sleeper()

    with pytest.raises(ValueError, match="not a connector problem"):
        _worker(connector).deliver_available(
            feed=FEED, feed_version=FEED_VERSION, business_date=DATE, sleep=sleep
        )
