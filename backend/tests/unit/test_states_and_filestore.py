from __future__ import annotations

import pytest

from cinqflow.dataplane.filestore import (
    FileStore,
    Folder,
    UnsafeFilename,
    fingerprint_bytes,
    landing_key,
    safe_filename,
)
from cinqflow.settings import Settings
from cinqflow.workflow.states import IllegalTransition, UploadStatus, assert_transition


def test_legal_transitions_follow_the_stage_1_lifecycle():
    assert_transition(UploadStatus.RECEIVED, UploadStatus.PROFILING)
    assert_transition(UploadStatus.PROFILING, UploadStatus.PROFILED)
    assert_transition(UploadStatus.PROFILED, UploadStatus.INTERPRETING)
    assert_transition(UploadStatus.INTERPRETING, UploadStatus.INTERPRETED)


def test_failures_are_retryable():
    assert_transition(UploadStatus.PROFILING, UploadStatus.PROFILE_FAILED)
    assert_transition(UploadStatus.PROFILE_FAILED, UploadStatus.PROFILING)
    assert_transition(UploadStatus.INTERPRET_FAILED, UploadStatus.INTERPRETING)


def test_skipping_a_stage_is_refused():
    with pytest.raises(IllegalTransition):
        assert_transition(UploadStatus.RECEIVED, UploadStatus.INTERPRETED)
    with pytest.raises(IllegalTransition):
        assert_transition(UploadStatus.INTERPRETED, UploadStatus.PROFILING)


def test_landing_key_layout_is_the_documented_one():
    key = landing_key(
        domain="enrollments",
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        folder=Folder.INCOMING,
        business_date="2026-06-01",
        filename="_CINQDOWNSTATE_Member_Roster_202606.csv",
    )
    assert key == (
        "enrollments/fidelis_ny_upstate/member_roster/incoming/2026-06-01/"
        "_CINQDOWNSTATE_Member_Roster_202606.csv"
    )


def test_leading_underscore_filenames_are_allowed():
    assert safe_filename("_CINQDOWNSTATE_Member_Roster_202606.csv").startswith("_CINQ")


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.csv", "", "..", "dir\\file.csv"])
def test_unsafe_filenames_are_refused(bad):
    with pytest.raises(UnsafeFilename):
        safe_filename(bad)


def test_fingerprint_is_content_addressed():
    assert fingerprint_bytes(b"abc") == fingerprint_bytes(b"abc")
    assert fingerprint_bytes(b"abc") != fingerprint_bytes(b"abd")
    assert fingerprint_bytes(b"abc").startswith("sha256-")


def test_original_is_written_once_and_never_overwritten(tmp_path):
    store = FileStore(Settings(landing_root=tmp_path))
    key = "d/s/f/incoming/2026-06-01/x.csv"
    store.place(key, b"one")
    with pytest.raises(FileExistsError):
        store.place(key, b"two")
    assert store.read_bytes(key) == b"one"


def test_move_preserves_layout_and_changes_only_the_folder(tmp_path):
    store = FileStore(Settings(landing_root=tmp_path))
    key = "d/s/f/incoming/2026-06-01/x.csv"
    store.place(key, b"payload")
    new_key = store.move(key, Folder.PROCESSED)
    assert new_key == "d/s/f/processed/2026-06-01/x.csv"
    assert store.read_bytes(new_key) == b"payload"
    assert not store.exists(key)
