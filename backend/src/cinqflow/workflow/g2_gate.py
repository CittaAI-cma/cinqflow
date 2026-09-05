"""Every reason G2 is closed, derived once.

The approve handler has always known these rules - it raises a 409 for each of
them. What it did not do was let anyone ask *before* pressing the button, so
the studio reimplemented the same question client-side from `approvable`, which
is literally `is_current` and therefore says nothing at all about unmapped
required fields. The result an analyst sees is an enabled Approve button that
409s: two derivations of one rule, disagreeing.

So the rules live here, and both the read (`GET .../gate`) and the write (the
approve handler) raise from this one list. A blocker is a fact plus somewhere
to go: `anchor` names the thing on the page that has to change, so the UI can
make each reason a jump target rather than prose the analyst has to translate
into an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from cinqflow.engine.mapping_exec import spec_fingerprint


@dataclass(frozen=True)
class Blocker:
    """One reason this version cannot be approved.

    `code` is stable and machine-readable; `message` is what a person reads;
    `anchor` is where to send them. `detail` carries whatever the 409 body
    carried, so the approve handler's responses do not change shape.
    """

    code: str
    message: str
    hint: str | None = None
    anchor: str | None = None
    detail: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Absent keys rather than null ones, so a 409 body raised from a
        blocker is the same shape the approve handler has always returned."""
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint is not None:
            body["hint"] = self.hint
        if self.anchor is not None:
            body["anchor"] = self.anchor
        return {**body, **self.detail}


def g2_blockers(store, canonical, mapping, feed: str, version: int) -> list[Blocker]:
    """In the order the analyst can act on them.

    Terminal states first - "already approved" and "superseded" are not things
    to fix, they are things to know - then the work: map what Silver needs to
    identify a row, then run a preview of what you are about to sign for.

    Deliberately *not* short-circuited. The approve handler raises on the first
    blocker because it must refuse; a person reading the page wants the whole
    list, so they can do two pieces of work in one pass instead of discovering
    the second only after fixing the first.
    """
    blockers: list[Blocker] = []

    decided = store.approval_for_mapping(feed=feed, version=version)
    if decided is not None:
        blockers.append(
            Blocker(
                code="already_approved",
                message=f"{feed} v{version} is already approved",
                detail={
                    "approval_id": decided.approval_id,
                    "approver": decided.approver,
                    "decided_ts": decided.decided_ts.isoformat(),
                },
            )
        )

    if mapping.status == "superseded":
        blockers.append(
            Blocker(
                code="superseded",
                message=f"{feed} v{version} was superseded by a later version",
            )
        )

    # Silver rows have to be identifiable. Only entities this spec actually
    # touches are held to it - mapping nothing into an entity is not a defect.
    touched_tables = {canonical.table_of(t) for t in mapping.spec.targets if t}
    missing_required = sorted(
        target
        for target in canonical.required_targets(t for t in touched_tables if t)
        if target not in mapping.spec.targets
    )
    if missing_required:
        blockers.append(
            Blocker(
                code="missing_required",
                message=(
                    f"{len(missing_required)} required field(s) are not mapped: "
                    + ", ".join(missing_required)
                ),
                hint="map each entity's primary key before approving",
                anchor="unmapped",
                detail={"missing_required": missing_required},
            )
        )

    # Approving a version nobody has seen run is the one thing this gate exists
    # to prevent. `fingerprint` is the execution projection, so writing a note
    # or taking ownership of a field does not reopen this.
    preview = store.get_current_preview(feed, version, spec_fingerprint(mapping.spec))
    if preview is None:
        blockers.append(
            Blocker(
                code="no_current_preview",
                message="this version has no preview of its current spec",
                hint="run a preview and approve what you saw",
                anchor="preview",
            )
        )
    elif store.get_run(preview.sample.batch_id, kind="land_bronze") is None:
        # A preview always samples a batch, so this is a torn state rather than
        # something an analyst did - named anyway, because an unexplained
        # disabled button is worse than an unexpected reason.
        blockers.append(
            Blocker(
                code="no_landing_run",
                message="the previewed batch has no landing run",
            )
        )

    return blockers
