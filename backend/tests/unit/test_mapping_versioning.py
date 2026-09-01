"""CF-V1-E6-04 — version compare, and the loss that does not announce itself.

    "A published mapping is executable config; changing one without
     blast-radius is how silent row loss happened historically."

The failure this suite protects against is not a crash. A mapping change that
drops a line lets the batch run, the counts reconcile and the ledger balance,
and one canonical column arrives NULL on every record from that day. Somebody
notices in March that a report has been wrong since November.

So every test here is about one question per target field — does it still have
a source, and is it the same one — and about the gate that makes an approver
answer it out loud.
"""

from __future__ import annotations

import pytest

from cinqflow.core.mapping import (
    FeedMapping,
    MappingLine,
    NullPolicy,
    Transform,
    TransformKind,
)
from cinqflow.core.mapping.versioning import (
    ChangeKind,
    UnacknowledgedLossError,
    compare,
    refuse_unacknowledged_loss,
)

pytestmark = pytest.mark.unit

FEED = "fidelis-downstate-roster"


def _line(field: str, *sources: str, **kwargs: object) -> MappingLine:
    return MappingLine(
        target_entity="members",
        target_field=field,
        source_columns=tuple(sources),
        **kwargs,  # type: ignore[arg-type]
    )


def _mapping(version: int, *lines: MappingLine) -> FeedMapping:
    return FeedMapping(feed_id=FEED, version=version, lines=lines)


V1 = _mapping(
    1,
    _line("first_name", "First_Name"),
    _line("last_name", "Last_Name"),
    _line("date_of_birth", "DOB"),
)


# ── the comparison is by target field, not by JSON ──────────────────────────


def test_reordering_the_lines_is_not_a_change() -> None:
    """A mapping's lines are unordered as far as meaning goes. A positional
    diff would report a reshuffle as forty changes, and an approver shown forty
    changes reads none of them."""
    reordered = _mapping(2, V1.lines[2], V1.lines[0], V1.lines[1])
    assert compare(V1, reordered).is_empty


def test_an_added_field_is_not_a_loss() -> None:
    added = _mapping(2, *V1.lines, _line("line_of_business", "LOB"))
    diff = compare(V1, added, from_published=True)

    assert [c.kind for c in diff.changed] == [ChangeKind.ADDED]
    assert diff.losses == ()


def test_a_removed_field_is_the_quietest_loss() -> None:
    """Its column stops being written. Nothing fails."""
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    diff = compare(V1, removed, from_published=True)

    (change,) = diff.changed
    assert change.kind is ChangeKind.REMOVED
    assert change.loses_its_source
    assert change.address == "members.date_of_birth"
    assert "arrive empty on every row" in change.explain()


def test_a_field_turned_explicitly_unmapped_is_still_a_loss() -> None:
    """A decision somebody made, with a reason — and the column is just as
    empty as if they had deleted the line."""
    unmapped = _mapping(
        2,
        V1.lines[0],
        V1.lines[1],
        MappingLine(
            target_entity="members",
            target_field="date_of_birth",
            unmapped_reason="Fidelis stopped sending DOB in the August format change.",
        ),
    )
    diff = compare(V1, unmapped, from_published=True)

    (change,) = diff.changed
    assert change.kind is ChangeKind.UNMAPPED
    assert change.loses_its_source
    assert change.lost_sources == ("DOB",)


def test_swapping_one_live_source_for_another_is_not_a_loss() -> None:
    """THE DISTINCTION THAT KEEPS THE LIST READABLE. The field still arrives
    populated; calling that a loss would bury the real ones."""
    redirected = _mapping(2, V1.lines[0], V1.lines[1], _line("date_of_birth", "MBR_DOB"))
    diff = compare(V1, redirected, from_published=True)

    (change,) = diff.changed
    assert change.kind is ChangeKind.REDIRECTED
    assert change.lost_sources == ("DOB",)
    assert change.loses_its_source, "it stopped reading DOB — that IS a loss of that source"


def test_a_transform_change_that_keeps_the_source_is_not_a_loss() -> None:
    cast = _mapping(
        2,
        V1.lines[0],
        V1.lines[1],
        _line(
            "date_of_birth",
            "DOB",
            transform=Transform(kind=TransformKind.SPLIT, separator="T", part=1),
        ),
    )
    diff = compare(V1, cast, from_published=True)

    (change,) = diff.changed
    assert change.kind is ChangeKind.REDIRECTED
    assert not change.loses_its_source
    assert diff.losses == ()


def test_dropping_a_coalesce_fallback_is_a_loss_of_that_source() -> None:
    """The field still populates on most rows — and stops on the ones the
    fallback was there for, which is exactly the kind of loss that hides."""
    with_fallback = _mapping(
        1,
        _line("service_date", "service_date", "service_from_date", null_policy=NullPolicy.COALESCE),
    )
    without = _mapping(2, _line("service_date", "service_date"))
    diff = compare(with_fallback, without, from_published=True)

    (change,) = diff.changed
    assert change.lost_sources == ("service_from_date",)
    assert change.loses_its_source


# ── the summary is what somebody reads first ────────────────────────────────


def test_the_summary_names_the_fields_that_stop_being_populated() -> None:
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    summary = compare(V1, removed, from_published=True).summary()

    assert "members.date_of_birth" in summary
    assert "STOP BEING POPULATED" in summary
    assert "Nothing will fail" in summary


def test_a_change_with_no_losses_says_so_plainly() -> None:
    added = _mapping(2, *V1.lines, _line("line_of_business", "LOB"))
    assert "No field loses its source." in compare(V1, added, from_published=True).summary()


# ── the gate: an acknowledgement, not a refusal ─────────────────────────────


def test_an_unacknowledged_loss_is_refused() -> None:
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    diff = compare(V1, removed, from_published=True)

    with pytest.raises(UnacknowledgedLossError) as refused:
        refuse_unacknowledged_loss(diff)

    assert "members.date_of_birth" in str(refused.value)
    assert "the batch will run" in str(refused.value).lower()


def test_naming_the_field_lets_the_change_through() -> None:
    """Removing a source is sometimes exactly right — a payer stops sending a
    field. What must not happen is removing one by accident."""
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    diff = compare(V1, removed, from_published=True)

    refuse_unacknowledged_loss(diff, ("members.date_of_birth",))


def test_naming_a_different_field_does_not_count() -> None:
    """A checkbox is clicked; a list of names has to be read. Acknowledging
    the wrong field is the shape of somebody clicking through."""
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    diff = compare(V1, removed, from_published=True)

    with pytest.raises(UnacknowledgedLossError):
        refuse_unacknowledged_loss(diff, ("members.last_name",))


def test_a_draft_predecessor_is_not_gated() -> None:
    """A field that was never live cannot stop being live. Asking for an
    acknowledgement here would train people to acknowledge everything, which is
    how a control becomes a formality."""
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    refuse_unacknowledged_loss(compare(V1, removed, from_published=False))


def test_a_change_with_no_loss_needs_no_acknowledgement() -> None:
    added = _mapping(2, *V1.lines, _line("line_of_business", "LOB"))
    refuse_unacknowledged_loss(compare(V1, added, from_published=True))


def test_the_acknowledgement_is_case_insensitive() -> None:
    """`Members.Date_Of_Birth` and `members.date_of_birth` are the same field,
    and a gate that refused over capitalisation would teach people to
    copy-paste rather than read."""
    removed = _mapping(2, V1.lines[0], V1.lines[1])
    diff = compare(V1, removed, from_published=True)
    refuse_unacknowledged_loss(diff, ("Members.Date_Of_Birth ",))
