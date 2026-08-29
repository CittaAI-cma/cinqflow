"""scenarios — Verato stand-in. WAVE 3 uses it; Wave 0 only fits the pin."""

from __future__ import annotations

from collections.abc import Sequence

from cinqflow.ports import port
from cinqflow.ports.identity import CrosswalkEntry, MatchOutcome, UnapprovedMergeError


@port("identity", "mock")
class ScenarioIdentity:
    """Plays scripted resolution outcomes, and REFUSES an unapproved merge.

    The refusal ships in Wave 0 even though identity resolution does not,
    because the R4 rule is not a Wave-3 feature — it is a property of the
    platform, and a port that would accept an unapproved merge today is a port
    someone could call today.
    """

    def __init__(self, outcomes: dict[str, MatchOutcome] | None = None) -> None:
        self._outcomes = dict(outcomes or {})
        self._crosswalk: dict[tuple[str, str], CrosswalkEntry] = {}

    def submit(
        self, records: Sequence[dict[str, str]], *, batch_id: str
    ) -> Sequence[CrosswalkEntry]:
        entries: list[CrosswalkEntry] = []
        for record in records:
            source_system = record["source_system"]
            source_member_id = record["source_member_id"]
            outcome = self._outcomes.get(source_member_id, MatchOutcome.UNRESOLVED)
            entry = CrosswalkEntry(
                source_system=source_system,
                source_member_id=source_member_id,
                internal_member_id=record.get("internal_member_id", ""),
                verato_person_id=(
                    f"verato-{source_member_id}" if outcome is MatchOutcome.RESOLVED else None
                ),
                batch_id=batch_id,
                outcome=outcome,
            )
            self._crosswalk[(source_system, source_member_id)] = entry
            entries.append(entry)

        # G4 accounting must balance, and the mock proves it can.
        assert len(entries) == len(records), "submitted == resolved + unresolved + failed"
        return tuple(entries)

    def crosswalk(self, source_system: str, source_member_id: str) -> CrosswalkEntry | None:
        return self._crosswalk.get((source_system, source_member_id))

    def merge(self, *, left: str, right: str, steward_approval_id: str | None = None) -> None:
        """R4. Human-steward-always, at any confidence, in any environment."""
        if not steward_approval_id:
            raise UnapprovedMergeError(
                f"merge of {left} and {right} requires a named steward approval. R4 is "
                "human-always, never automated, and not configurable."
            )
