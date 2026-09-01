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
from typing import Protocol, runtime_checkable

# MatchOutcome and CrosswalkEntry live in core/identity, not here: Wave 3's
# G4 accounting (core.identity.IdentityDisposition) needs to reason about
# them, and core may never import a port (Law 1's layering, the other
# direction — ports may depend on core, core may not depend on ports). A pin
# still NAMES its verbs and errors here; the data shapes that cross it are
# core's, the same way schema_spec.TypeName is core's and every adapter
# imports it, never the reverse.
from cinqflow.core.identity import CrosswalkEntry, MatchOutcome

__all__ = [
    "CrosswalkEntry",
    "IdentityError",
    "IdentityPort",
    "MatchOutcome",
    "UnapprovedMergeError",
]


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
