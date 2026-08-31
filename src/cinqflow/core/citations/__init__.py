"""The citation address space — one vocabulary, shared by the agent and the UI.

    "every factual claim carries a resolvable citation; uncited claims are a
     defect class"
    — docs/architecture/INVARIANTS.md, intelligence

CF-V0-E16-09 defines the citation shapes so that operational truth reaches a
model as attributable evidence. The design decision this module makes is to
treat that vocabulary as the PLATFORM's address space rather than the agent's:
a citation_id parses to a UI route, so one resolver serves the agent's
citations, the UI's deep links, the breadcrumb and the drawer.

Three things fall out of that, and they are why it is worth doing here:

  • "clicking a citation opens that registry row" (CF-V0-E16-10, happy path)
    needs no agent-specific plumbing.
  • The Lane-3 gate "citation resolvability = 100%" becomes a test over the
    router — computable with no model in the loop.
  • A citation is shareable. "Look at recon:8842#DQ-002" replaces a screenshot,
    which is exactly the daily_status.xlsx habit the programme exists to retire.

Deliberately absent: any kind addressing a member, a row or a record. No tool
in the catalogue may emit a data-layer row, so no citation may address one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique


class UnresolvableCitationError(ValueError):
    """A citation the UI cannot open.

    Raised rather than tolerated: a malformed citation is worse than no
    citation, because it reads as evidence while resolving to nothing.
    """


@unique
class CitationKind(StrEnum):
    """The whole vocabulary. Adding a member means adding a route and a test."""

    FEED = "feed"
    PLAN = "plan"
    CONTRACT = "contract"
    #: CF-V1-E5-01. An OBSERVATION about a file, not a governed object — so it
    #: carries no version. Its subject is the profile's own fingerprint, which
    #: makes `profile:sha256-…#DOB` an address for one computed fact and gives
    #: CF-V1-E5-02 something to cite for every claim it interprets.
    PROFILE = "profile"
    MAPPING = "mapping"
    BATCH = "batch"
    RECON = "recon"
    ERROR = "error"
    FILE = "file"
    RULE = "rule"
    #: CF-V1-W1-25. A PUBLISHED governed runbook — the K2 content
    #: `core.knowledge.chunk_runbook` cites, one fragment per STEP
    #: (`runbook:RB-1#step-3`), the same "one depth level, like every other
    #: drawer" shape `BATCH`'s panel fragment already uses. Unversioned, like
    #: `RULE`: `RecoveryGuide.citation` (the match-time evidence trail) has
    #: never pinned a version, because a match is against whichever guide is
    #: CURRENTLY published for a signature, not a specific historical one.
    RUNBOOK = "runbook"
    TERM = "term"
    #: CF-V1-E16-04/E16-06. An uploaded document's chunk, cited by PAGE — the
    #: happy path's own example ("the companion guide p.14 defines MBR_DOB as
    #: CCYYMMDD"). Unversioned, like RUNBOOK: a document is superseded by a
    #: whole new upload, not amended in place.
    DOCUMENT = "document"

    @property
    def versioned(self) -> bool:
        """Governed objects carry a version; observations of a run do not.

        A batch is a fact that happened once — `batch:8842@v2` would be a
        category error, so it is refused rather than silently ignored.
        """
        return self in {
            CitationKind.FEED,
            CitationKind.PLAN,
            CitationKind.CONTRACT,
            CitationKind.MAPPING,
        }


# A subject is an identifier, never a path and never free text. The character
# class is what stops `feed:../../etc/passwd` becoming a traversal.
_SUBJECT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_FRAGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_PATTERN = re.compile(
    rf"^(?P<kind>[a-z]+):(?P<subject>{_SUBJECT})(?:@v(?P<version>[1-9][0-9]*))?"
    rf"(?:#(?P<fragment>{_FRAGMENT}))?$"
)


@dataclass(frozen=True, order=True)
class CitationId:
    """One address. Immutable, comparable, hashable — so a set of citations
    deduplicates and an answer's citations sort stably."""

    kind: CitationKind
    subject: str
    version: int | None = None
    fragment: str | None = None

    def __post_init__(self) -> None:
        # Validate on construction, not only on parsing: tool authors build
        # these directly, and that path must refuse exactly as hard.
        if not self.subject or not re.fullmatch(_SUBJECT, self.subject):
            raise UnresolvableCitationError(f"not an identifier: {self.subject!r}")
        if self.fragment is not None and not re.fullmatch(_FRAGMENT, self.fragment):
            raise UnresolvableCitationError(f"not a fragment: {self.fragment!r}")
        if self.version is not None:
            if not self.kind.versioned:
                raise UnresolvableCitationError(
                    f"{self.kind.value} is an observation, not a governed "
                    "object — it has no version"
                )
            if self.version < 1:
                raise UnresolvableCitationError("versions start at 1")

    def __str__(self) -> str:
        text = f"{self.kind.value}:{self.subject}"
        if self.version is not None:
            text += f"@v{self.version}"
        if self.fragment is not None:
            text += f"#{self.fragment}"
        return text

    @property
    def route(self) -> str:
        """The UI route this citation opens.

        Depth is a drawer, never an IA branch: a fragment becomes a panel on the
        same page, so there is exactly one depth level to navigate.
        """
        match self.kind:
            case CitationKind.FEED | CitationKind.CONTRACT | CitationKind.MAPPING:
                suffix = f"?version={self.version}" if self.version else ""
                return f"/data/intake/{self.kind.value}/{self.subject}{suffix}"
            case CitationKind.PLAN:
                suffix = f"?version={self.version}" if self.version else ""
                return f"/data/intake/feed/{self.subject}/plan{suffix}"
            case CitationKind.BATCH:
                panel = f"?panel={self.fragment}" if self.fragment else ""
                return f"/operations/control/batch/{self.subject}{panel}"
            case CitationKind.RECON:
                drop = f"&drop={self.fragment}" if self.fragment else ""
                return f"/operations/control/batch/{self.subject}?panel=recon{drop}"
            case CitationKind.ERROR:
                return f"/operations/control/error/{self.subject}"
            case CitationKind.FILE:
                return f"/data/explorer/landing/{self.subject}"
            case CitationKind.PROFILE:
                # A column is a panel on the profile, not a page of its own —
                # one depth level, like every other drawer.
                column = f"?column={self.fragment}" if self.fragment else ""
                return f"/data/intake/profile/{self.subject}{column}"
            case CitationKind.RULE:
                return f"/data/intake/rule/{self.subject}"
            case CitationKind.RUNBOOK:
                # A step is a panel on the runbook, not a page of its own —
                # the same shape BATCH's fragment already uses.
                panel = f"?panel={self.fragment}" if self.fragment else ""
                return f"/data/intake/runbook/{self.subject}{panel}"
            case CitationKind.TERM:
                return f"/data/intake/glossary/{self.subject}"
            case CitationKind.DOCUMENT:
                # A page is a panel on the document, not a page of its own —
                # the same shape RUNBOOK's step fragment already uses.
                panel = f"?panel={self.fragment}" if self.fragment else ""
                return f"/data/intake/document/{self.subject}{panel}"


def parse(raw: str) -> CitationId:
    """Parse a citation id, or refuse. Never returns a partial result."""
    match = _PATTERN.match(raw.strip()) if raw else None
    if match is None:
        raise UnresolvableCitationError(f"not a citation id: {raw!r}")
    try:
        kind = CitationKind(match["kind"])
    except ValueError:
        known = ", ".join(k.value for k in CitationKind)
        raise UnresolvableCitationError(
            f"{match['kind']!r} is not in the citation vocabulary ({known})"
        ) from None
    version = int(match["version"]) if match["version"] else None
    return CitationId(
        kind=kind, subject=match["subject"], version=version, fragment=match["fragment"]
    )
