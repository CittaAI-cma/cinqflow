"""The `identity` pin — submit, resolve and crosswalk members.

    verb: submit/store/crosswalk   mock: scenarios   dev: spec_exact_mock
    target: verato_api
    — docs/architecture/plates/04-pin-out-map.md

    merge_split: {risk_class: R4, rule: human_steward_always,
                  verify: post_change_matches_preview}
    — docs/architecture/plates/10-silver-ods-canonical-model.md

WAVE 0 DOES NOT RESOLVE IDENTITY. The port and its mock exist so the pin is
fitted and the conformance kit can name it; the identity STAGE, the exception
desk and Silver ODS are Wave 3 (CF-V3-E9-*, CF-V3-E10-*).

The one thing that is true from Wave 0 onward: a record whose identity is
unresolved NEVER loads. It waits, visibly. And a merge or split is R4 —
human-steward-always, never automated, not configurable, at any confidence.
There is no autonomy path for this pin, ever.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable


@unique
class MatchOutcome(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"  # waits, visibly. Never loads.
    FAILED = "failed"


@dataclass(frozen=True)
class CrosswalkEntry:
    """bridge_member_source_to_verato — source identifiers retained beside
    every surrogate key, so a row can always be traced to the file it came from."""

    source_system: str
    source_member_id: str
    internal_member_id: str
    verato_person_id: str | None
    batch_id: str
    match_confidence_score: Decimal | None = None
    outcome: MatchOutcome = MatchOutcome.UNRESOLVED


class IdentityError(RuntimeError):
    """The identity service could not be reached or could not be trusted."""


class UnapprovedMergeError(IdentityError):
    """A merge or split attempted without a steward approval record.

    R4 is human-always. The refusal path is a TESTED FEATURE: CI contains a
    test that makes this attempt and asserts refusal plus a security event plus
    a page. A guardrail nobody tries is a comment, not a control.
    """


@runtime_checkable
class IdentityPort(Protocol):
    def submit(
        self, records: Sequence[dict[str, str]], *, batch_id: str
    ) -> Sequence[CrosswalkEntry]:
        """Submit records for resolution.

        Accounting must balance: submitted == resolved + unresolved + failed.
        That is G4, and it is checked rather than assumed.
        """
        ...

    def crosswalk(self, source_system: str, source_member_id: str) -> CrosswalkEntry | None: ...
