"""The ONE contract suite for the `storage` pin.

memfs today; localfs and MinIO at rung 1; ADLS Gen2 at rung 3. Every one of
them runs THIS file. A behaviour asserted here is a behaviour the tenant's
storage must have, and a behaviour absent here is one nobody can rely on.

The guarantees under test are the landing zone's, not a filesystem's:

    "the same file presented twice is skipped, with an audit entry"
    "original source files are immutable and archived; nothing is ever
     silently dropped"
    — docs/architecture/INVARIANTS.md, data plane
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from cinqflow.core.model.vocabulary import LandingFolder
from cinqflow.ports.storage import FileNotFoundInStorageError, StoragePort

from .conftest import adapters_for

ROSTER = (
    "enrollments/fidelis_downstate/roster/incoming/2026-08-01/"
    "_CINQDOWNSTATE_Member_Roster_202608.xlsx"
)
CONTENT = b"member_id,first_name,date_of_birth\nMBR000001,ARUN,1990-01-01\n"

pytestmark = pytest.mark.contract


@pytest.fixture(params=adapters_for("storage"))
def storage(request: pytest.FixtureRequest, make: Callable[..., Any]) -> StoragePort:
    adapter = make(request.param)
    adapter.place(ROSTER, CONTENT, modified_ts=datetime(2026, 8, 1, 3, 14, tzinfo=UTC))
    return adapter


def test_it_lists_what_is_there_in_a_stable_order(storage: StoragePort) -> None:
    """Stable order matters: an unordered listing makes golden comparisons
    flaky, and a flaky golden set teaches people to re-run CI."""
    listed = list(storage.list_files("enrollments/"))
    assert [f.key for f in listed] == [ROSTER]
    assert listed[0].size_bytes == len(CONTENT)


def test_a_fingerprint_is_content_addressed_and_survives_a_move(storage: StoragePort) -> None:
    """This is what makes exactly-once real.

    If a fingerprint changed when a file was archived after processing, replay
    refusal would silently stop working the day archiving was introduced — and
    the duplicate Feb-2025 Fidelis roster would load twice.
    """
    before = storage.fingerprint(ROSTER)
    moved = storage.move(ROSTER, LandingFolder.ARCHIVE)
    assert storage.fingerprint(moved.key) == before


def test_moving_a_file_preserves_its_bytes_exactly(storage: StoragePort) -> None:
    """ "Delete or modify an original source file" is a documented don't."""
    moved = storage.move(ROSTER, LandingFolder.PROCESSED)
    assert storage.read_bytes(moved.key) == CONTENT


def test_a_move_reports_where_the_file_went(storage: StoragePort) -> None:
    """A move with no recorded destination is how files become mysteries."""
    moved = storage.move(ROSTER, LandingFolder.REJECTED)
    assert LandingFolder.REJECTED.value in moved.key
    assert LandingFolder.INCOMING.value not in moved.key
    assert not storage.exists(ROSTER)


def test_an_unexpected_file_can_be_parked_not_dropped(storage: StoragePort) -> None:
    """ "registered as Unexpected, parked unprocessed ... nothing disappears
    silently" — CF-V0-E8-02, exception"""
    parked = storage.move(ROSTER, LandingFolder.PARKED)
    assert storage.exists(parked.key)
    assert storage.read_bytes(parked.key) == CONTENT


def test_the_port_offers_no_way_to_delete_or_overwrite(storage: StoragePort) -> None:
    """A port that cannot express deletion is a stronger guarantee than one
    that documents not deleting."""
    for forbidden in ("delete", "remove", "unlink", "write", "overwrite", "truncate"):
        assert not hasattr(storage, forbidden), f"storage exposes {forbidden}()"


def test_a_missing_file_is_a_distinct_failure(storage: StoragePort) -> None:
    """ "the file is gone" and "the store is down" are different incidents and
    must not be reported as one."""
    with pytest.raises(FileNotFoundInStorageError):
        storage.read_bytes("enrollments/nope/incoming/2026-08-01/absent.xlsx")
    assert storage.exists("enrollments/nope/incoming/2026-08-01/absent.xlsx") is False


def test_the_underscore_filename_is_visible_to_the_preflight_check(storage: StoragePort) -> None:
    """Incident #1: a Fidelis file named `_CINQDOWNSTATE_Member_Roster_*.xlsx`
    once broke the Excel reader. The platform never re-learns an old lesson, so
    the property is on the type rather than in a validator somewhere."""
    ref = next(storage.list_files("enrollments/"))
    assert ref.filename.startswith("_")
    assert ref.starts_with_underscore is True
