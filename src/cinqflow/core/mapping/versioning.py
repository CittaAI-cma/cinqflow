"""CF-V1-E6-04 — comparing two mapping versions, and the loss nobody sees.

    "Mapping approval, versioning, version compare, impact analysis before
     changing a published mapping"
    "A published mapping is executable config; changing one without
     blast-radius is how silent row loss happened historically."
    — CF-V1-E6-04

THE HISTORICAL FAILURE IS NAMED IN THE STORY, AND IT IS NOT A CRASH. A mapping
change that drops a line does not break the pipeline: the batch runs, the row
counts reconcile, the ledger balances, and one canonical field quietly arrives
NULL on every record from that day forward. Nothing fails. Somebody notices in
March that a report has been wrong since November.

So the comparison this module computes is not a diff of the stored JSON.
`core.impact.diff_bodies` would render a mapping change as
`lines: [ ... ] -> [ ... ]` — technically complete, and useless to the person
signing it. What an approver needs is one question answered per target field:

    does this field still have a source, and is it the same one?

`FieldChange.loses_its_source` is that answer, and it is what the approval gate
reads. Everything else in the comparison is context.

THE GATE IS AN ACKNOWLEDGEMENT, NOT A REFUSAL. Removing a source is sometimes
exactly right — a payer stops sending a field, and the honest mapping says so
with a reason. What must not happen is removing one BY ACCIDENT. So a change
that empties a field of a PUBLISHED mapping requires the approver to name that
field: not a checkbox, which is clicked, but the names, which have to be read.

DRAFT PREDECESSORS ARE NOT GATED. A field that was never live cannot stop being
live, and asking somebody to acknowledge the loss of something nothing consumed
would train them to acknowledge everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from cinqflow.core.mapping import FeedMapping, LineStatus, MappingError, MappingLine


class UnacknowledgedLossError(RuntimeError):
    """A mapping change would empty a live field, and nobody said so.

    Raised on the approval path. The message names every field, because the
    whole point of the gate is that somebody reads the list.
    """


@unique
class ChangeKind(StrEnum):
    """What happened to one target field between two mapping versions."""

    UNCHANGED = "unchanged"
    ADDED = "added"
    #: The field is gone from the mapping entirely. Its column stops being
    #: written — the quietest of the four.
    REMOVED = "removed"
    #: Still present, and something about how it is populated changed: a
    #: different source, a different transform, a different null policy.
    REDIRECTED = "redirected"
    #: Present, and now explicitly UNMAPPED. A decision somebody made, with a
    #: reason — and still a loss if the field used to be populated.
    UNMAPPED = "unmapped"


@dataclass(frozen=True)
class FieldChange:
    """One target field, before and after.

    `explain` is a full sentence rather than a shorthand because this text goes
    on the approval screen, and an approver reading `null_policy: 1 -> 3` has
    been shown a diff rather than told a fact.
    """

    address: str
    kind: ChangeKind
    before: str = ""
    after: str = ""
    lost_sources: tuple[str, ...] = ()

    @property
    def loses_its_source(self) -> bool:
        """THE QUESTION THE APPROVAL GATE ASKS.

        True when this field had something populating it and now has less: it
        is gone, it is explicitly unmapped, or a source column it read from is
        no longer read. A redirect that swaps one live source for another is
        NOT a loss — the field still arrives populated, and treating it as a
        loss would bury the real ones.
        """
        return self.kind in {ChangeKind.REMOVED, ChangeKind.UNMAPPED} or bool(self.lost_sources)

    def explain(self) -> str:
        match self.kind:
            case ChangeKind.ADDED:
                return f"{self.address} is newly mapped: {self.after}"
            case ChangeKind.REMOVED:
                return (
                    f"{self.address} is no longer mapped at all. It was: {self.before}. "
                    "This column will arrive empty on every row from the next batch."
                )
            case ChangeKind.UNMAPPED:
                return (
                    f"{self.address} is now explicitly unmapped: {self.after}. "
                    "It was: {before}. This column will arrive empty on every row."
                ).replace("{before}", self.before)
            case ChangeKind.REDIRECTED:
                lost = (
                    f" It no longer reads {', '.join(self.lost_sources)}."
                    if self.lost_sources
                    else ""
                )
                return f"{self.address} changed: {self.before} -> {self.after}.{lost}"
            case ChangeKind.UNCHANGED:
                return f"{self.address} is unchanged."


@dataclass(frozen=True)
class MappingDiff:
    """Two mapping versions, compared field by field."""

    feed_id: str
    from_version: int
    to_version: int
    changes: tuple[FieldChange, ...] = ()
    #: True when the EARLIER version was live. Only then is a loss a loss —
    #: a field that was never published cannot stop being published.
    from_published: bool = False

    @property
    def changed(self) -> tuple[FieldChange, ...]:
        return tuple(c for c in self.changes if c.kind is not ChangeKind.UNCHANGED)

    @property
    def losses(self) -> tuple[FieldChange, ...]:
        """The blast radius. What an approver is actually signing for."""
        return tuple(c for c in self.changes if c.loses_its_source)

    @property
    def fields_losing_their_source(self) -> tuple[str, ...]:
        return tuple(c.address for c in self.losses)

    @property
    def is_empty(self) -> bool:
        return not self.changed

    def summary(self) -> str:
        """One line, in the words an approver should read first."""
        if self.is_empty:
            return f"{self.feed_id} v{self.from_version} and v{self.to_version} are identical."
        losses = self.losses
        head = (
            f"{len(self.changed)} field(s) changed between "
            f"{self.feed_id} v{self.from_version} and v{self.to_version}"
        )
        if not losses:
            return f"{head}. No field loses its source."
        return (
            f"{head}, and {len(losses)} STOP BEING POPULATED: "
            f"{', '.join(c.address for c in losses)}. "
            "Nothing will fail — the batch will run, the counts will reconcile, and those "
            "columns will be empty."
        )


def compare(
    before: FeedMapping, after: FeedMapping, *, from_published: bool = False
) -> MappingDiff:
    """Compare two mapping versions by TARGET FIELD.

    By target field, not by line index and not by JSON: a mapping's lines are
    unordered as far as meaning goes, so a reordered list is not a change and a
    positional diff would report it as forty.
    """
    keyed_before = {line.address.lower(): line for line in before.lines}
    keyed_after = {line.address.lower(): line for line in after.lines}

    changes: list[FieldChange] = []
    for address in sorted(keyed_before.keys() | keyed_after.keys()):
        old = keyed_before.get(address)
        new = keyed_after.get(address)
        changes.append(_change(address, old, new))

    return MappingDiff(
        feed_id=after.feed_id,
        from_version=before.version,
        to_version=after.version,
        changes=tuple(changes),
        from_published=from_published,
    )


def _change(address: str, old: MappingLine | None, new: MappingLine | None) -> FieldChange:
    if old is None and new is not None:
        return FieldChange(address=new.address, kind=ChangeKind.ADDED, after=new.describe())
    if new is None and old is not None:
        return FieldChange(
            address=old.address,
            kind=ChangeKind.REMOVED,
            before=old.describe(),
            lost_sources=old.source_columns if old.is_mapped else (),
        )
    if old is None or new is None:
        # Unreachable: `address` comes from the union of both key sets, so at
        # least one side has it, and the two branches above took the cases
        # where exactly one does. Raised rather than asserted so the impossible
        # case is still a named failure if the caller is ever changed.
        raise MappingError(f"{address} is in neither version — the comparison lost a key")

    if old.describe() == new.describe():
        return FieldChange(address=new.address, kind=ChangeKind.UNCHANGED)

    lost = tuple(c for c in old.source_columns if c not in set(new.source_columns))
    if old.is_mapped and new.status is LineStatus.UNMAPPED:
        return FieldChange(
            address=new.address,
            kind=ChangeKind.UNMAPPED,
            before=old.describe(),
            after=new.describe(),
            lost_sources=old.source_columns,
        )
    return FieldChange(
        address=new.address,
        kind=ChangeKind.REDIRECTED,
        before=old.describe(),
        after=new.describe(),
        lost_sources=lost,
    )


def refuse_unacknowledged_loss(diff: MappingDiff, acknowledged: tuple[str, ...] = ()) -> None:
    """The gate CF-V1-E6-04 exists for. Called before an approval may land.

    THE APPROVER NAMES THE FIELDS. Not a checkbox — a checkbox is clicked, and
    the failure this gate prevents is precisely the change nobody looked at.
    Typing `patient.date_of_birth` is a small tax on the legitimate case (a
    payer stopped sending a field, and the mapping honestly says so) and the
    only available defence against the illegitimate one.

    A DRAFT PREDECESSOR IS NOT GATED. A field that was never live cannot stop
    being live, and asking for an acknowledgement there would train people to
    acknowledge everything — which is how a control becomes a formality.
    """
    if not diff.from_published:
        return
    named = {name.strip().lower() for name in acknowledged}
    missing = [c for c in diff.losses if c.address.lower() not in named]
    if not missing:
        return
    raise UnacknowledgedLossError(
        f"{len(missing)} field(s) would stop being populated by this change, and the "
        "approval does not name them:\n\n"
        + "\n".join(f"  • {c.explain()}" for c in missing)
        + "\n\nNothing will fail. The batch will run, the row counts will reconcile and "
        "the ledger will balance — those columns will simply be empty from the next "
        "delivery onward, which is how this went unnoticed for a quarter last time.\n"
        "If that is intended, name each field in `accepts_loss` to say so."
    )
