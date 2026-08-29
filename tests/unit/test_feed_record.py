"""CF-V0-E3-01 — the minimal feed record the engine can run from."""

from __future__ import annotations

import pytest

from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.feed import (
    FeedRecord,
    FeedValidationError,
    PatternSampleMismatchError,
    executable,
    from_governed,
)

pytestmark = pytest.mark.unit

ARUN = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")
STEVE = Actor(subject="steve@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Steve")


def _fidelis(**overrides: object) -> FeedRecord:
    defaults: dict[str, object] = {
        "feed_id": "fidelis-downstate-roster",
        "domain": "enrollments",
        "source_system": "fidelis",
        "file_format": "xlsx",
        "landing_path": "enrollments/fidelis_downstate/roster",
        "file_pattern": r"_CINQDOWNSTATE_Member_Roster_\d{6}\.xlsx",
        "schedule_cron": "0 3 1 * *",
        "sample_filename": "_CINQDOWNSTATE_Member_Roster_202608.xlsx",
        "min_size_bytes": 5_000_000,
        "max_size_bytes": 30_000_000,
    }
    defaults.update(overrides)
    return FeedRecord(**defaults)  # type: ignore[arg-type]


def test_the_real_fidelis_feed_is_six_fields_and_saves_as_version_one() -> None:
    """ "Given the Fidelis downstate roster feed does not exist yet, when an
    engineer enters its six fields and saves, then the record is stored as
    version 1, and the next engine run picks it up WITHOUT ANY CODE
    CHANGE." — CF-V0-E3-01, happy path"""
    governed = _fidelis().as_governed(author=ARUN)
    assert governed.version == 1
    assert governed.object_type is ObjectType.FEED
    assert governed.lifecycle_state is LifecycleState.DRAFT


def test_the_pattern_is_validated_against_a_real_sample_before_saving() -> None:
    """ "Validate the file-name pattern against a real sample name before
    saving."

    A pattern that matches nothing is indistinguishable from a feed that never
    arrives — and it would be discovered at 3am, by a file landing in the
    unexpected pile.
    """
    with pytest.raises(PatternSampleMismatchError):
        _fidelis(sample_filename="_CINQDOWNSTATE_Member_Roster_2026O8.xlsx")  # letter O


def test_the_mismatch_shows_exactly_where_they_diverge() -> None:
    """ "the save is blocked with a SIDE-BY-SIDE VIEW of pattern vs sample
    showing exactly where they differ." — CF-V0-E3-01, exception"""
    with pytest.raises(PatternSampleMismatchError) as caught:
        _fidelis(sample_filename="_CINQDOWNSTATE_Member_Roster_2026O8.xlsx")
    message = str(caught.value)
    assert "pattern :" in message and "sample  :" in message
    assert "diverges here" in message
    assert "^" in message


def test_an_invalid_regex_is_refused_as_such() -> None:
    """ "unterminated group" is a better error than "does not match"."""
    with pytest.raises(FeedValidationError, match="not a valid pattern"):
        _fidelis(file_pattern=r"_CINQDOWNSTATE_(\d{6}\.xlsx")


def test_impossible_size_bounds_are_refused() -> None:
    """A feed no file could satisfy would reject every delivery, forever, with
    a reason nobody would think to question."""
    with pytest.raises(FeedValidationError, match="no file could pass"):
        _fidelis(min_size_bytes=30_000_000, max_size_bytes=5_000_000)


@pytest.mark.parametrize("missing", ["feed_id", "domain", "source_system", "file_format"])
def test_a_field_the_engine_reads_cannot_be_blank(missing: str) -> None:
    with pytest.raises(FeedValidationError, match="required"):
        _fidelis(**{missing: "   "})


def test_the_engine_refuses_to_run_unpublished_metadata() -> None:
    """This is what "no unapproved configuration reaches production" means in
    code: a property of the READER, so the gate cannot be bypassed by a writer
    who skipped it."""
    draft = _fidelis().as_governed(author=ARUN)
    with pytest.raises(FeedValidationError, match="not published"):
        executable(draft)


def test_the_engine_runs_published_metadata_with_no_human_in_the_loop() -> None:
    """ "Be readable by the engine with no human in the loop." — CF-V0-E3-01"""
    published = _publish(_fidelis().as_governed(author=ARUN))
    record = executable(published)
    assert record.feed_id == "fidelis-downstate-roster"
    assert record.schedule_cron == "0 3 1 * *"


def test_a_feed_round_trips_through_its_governed_wrapper() -> None:
    """Storage is the governed object; the record is a view of it. A lossy
    round trip would mean the registry and the engine disagreed."""
    original = _fidelis()
    assert from_governed(original.as_governed(author=ARUN)) == original


def test_the_landing_projection_carries_only_structural_fields() -> None:
    """Landing does STRUCTURAL validation only, so it gets the structural
    fields and nothing else. It has no business knowing the schedule."""
    projection = _fidelis().for_landing(version=1)
    assert projection.feed_id == "fidelis-downstate-roster"
    assert projection.file_pattern == r"_CINQDOWNSTATE_Member_Roster_\d{6}\.xlsx"
    assert not hasattr(projection, "schedule_cron")
    assert not hasattr(projection, "domain")


def test_a_feed_addresses_itself_with_a_citation() -> None:
    """The registry row the agent cites and the UI opens are the same address."""
    assert str(_fidelis().citation(version=3)) == "feed:fidelis-downstate-roster@v3"


def test_the_feed_has_no_private_status_field() -> None:
    """ "Reuse the lifecycle engine, never a private state machine" — archetype A.

    An `is_active` flag here would be a second, ungoverned way to turn a feed
    off, with no approval and no audit row.
    """
    for private in ("status", "state", "is_active", "enabled", "lifecycle"):
        assert not hasattr(_fidelis(), private), f"feed carries a private {private}"


def _publish(obj: object) -> object:
    from cinqflow.core.model.governed import GovernedObject

    assert isinstance(obj, GovernedObject)
    submitted, _ = obj.transition_to(LifecycleState.PENDING_REVIEW, actor=ARUN)
    approved, _ = submitted.transition_to(LifecycleState.APPROVED, actor=STEVE)
    published, _ = approved.transition_to(LifecycleState.PUBLISHED, actor=STEVE)
    return published
