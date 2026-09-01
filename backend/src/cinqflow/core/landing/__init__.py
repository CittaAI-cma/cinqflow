"""CF-V0-E8-02 — Landing Zone Controls. The single trust boundary.

    "Landing is the Control Entry Point: structural validation only, no
     semantic validation. Files are immutable; only ACCEPTED files proceed to
     Bronze. Every arriving file is registered — INCLUDING UNEXPECTED ONES,
     which are parked and surfaced, never ignored."
    — memory/05-ground-truth/00-control-plane.md

    outcomes: [ACCEPTED->processed/, REJECTED->rejected/(reason),
               UNEXPECTED->parked]
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

`classify` is a PURE FUNCTION: a file, the registered feeds, and whether this
content has been seen before, in — a decision out. All I/O lives in the worker
that wires it to the storage and control-table pins, which is what lets the
whole trust boundary be tested exhaustively in milliseconds with no services
running.

Every connector normalises to this one contract (ADR-0011), so no delivery path
can bypass registration, fingerprinting or validation. There is no second door.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique

from cinqflow.core.model.files import FileRef
from cinqflow.core.model.vocabulary import LandingFolder


@unique
class LandingOutcome(StrEnum):
    """The four things that can happen to an arriving file.

    Note there is no "ignored". That absence is the point: the incumbent
    platform's failure mode was files that simply did not appear anywhere.
    """

    ACCEPTED = "ACCEPTED"  # matched a feed, passed pre-flight   -> processed/
    REJECTED = "REJECTED"  # matched a feed, failed a check      -> rejected/
    UNEXPECTED = "UNEXPECTED"  # matched no feed                     -> parked/
    SKIPPED = "SKIPPED"  # this content has been seen before   -> archive/


@dataclass(frozen=True)
class RegisteredFeed:
    """What landing needs to know about a feed. Six fields and two bounds.

    Deliberately the minimum: landing does structural validation only, so it
    has no business knowing about contracts, mappings or rules.
    """

    feed_id: str
    feed_version: int
    landing_path: str
    file_pattern: str
    file_format: str
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    # Incident #1. The Fidelis roster GENUINELY starts with an underscore, so
    # the check cannot be "reject all underscores" — that would have "fixed"
    # the incident by breaking production. It is "the reader was told to expect
    # one", declared per feed.
    allows_leading_underscore: bool = True


@dataclass(frozen=True)
class LandingDecision:
    """What landing decided, and why.

    `check_name` is not optional decoration on a rejection: a rejection with no
    named check is an unattributed drop wearing a different hat, and the whole
    programme exists to make those impossible.
    """

    outcome: LandingOutcome
    move_to: LandingFolder
    feed_id: str | None = None
    feed_version: int | None = None
    reason: str | None = None
    check_name: str | None = None
    registered: bool = True  # ALWAYS. 100% of arriving files get a row.
    audit_required: bool = False  # a skip must leave a trace, or it is silent


def classify(
    file: FileRef, *, feeds: tuple[RegisteredFeed, ...], fingerprint_seen: bool
) -> LandingDecision:
    """Decide what happens to one arriving file.

    ORDER IS A GUARANTEE, not an implementation detail:

      1. Already seen?  -> SKIPPED, with an audit entry. Before anything else,
         because re-judging a file the platform has already loaded means a
         pattern change could reject something Bronze already holds — and then
         the drop ledger and Bronze disagree, permanently.
      2. Matches a feed? -> if not, UNEXPECTED and parked. Never dropped.
      3. Pre-flight     -> the seeded failure library, as permanent checks.
      4. Otherwise      -> ACCEPTED.
    """
    if fingerprint_seen:
        return LandingDecision(
            outcome=LandingOutcome.SKIPPED,
            move_to=LandingFolder.ARCHIVE,
            reason=(f"already processed: fingerprint {file.fingerprint} is in the input registry"),
            check_name="fingerprint_replay",
            audit_required=True,
        )

    matched = _match(file, feeds)
    if matched is None:
        return LandingDecision(
            outcome=LandingOutcome.UNEXPECTED,
            move_to=LandingFolder.PARKED,
            reason=_why_nothing_matched(file, feeds),
            check_name="feed_pattern",
        )

    for check in (_leading_underscore, _size_bounds):
        failure = check(file, matched)
        if failure is not None:
            return failure

    return LandingDecision(
        outcome=LandingOutcome.ACCEPTED,
        move_to=LandingFolder.PROCESSED,
        feed_id=matched.feed_id,
        feed_version=matched.feed_version,
    )


# ── matching ─────────────────────────────────────────────────────────────────
def _match(file: FileRef, feeds: tuple[RegisteredFeed, ...]) -> RegisteredFeed | None:
    """A pattern is scoped to a landing path.

    Without the path scope, one payer's file name could claim another payer's
    feed — and the Fidelis estate alone has 26 patterns across five claim types.
    """
    for feed in feeds:
        in_path = f"/{feed.landing_path}/" in f"/{file.key}" or file.key.startswith(
            f"{feed.landing_path}/"
        )
        if in_path and re.fullmatch(feed.file_pattern, file.filename):
            return feed
    return None


def _why_nothing_matched(file: FileRef, feeds: tuple[RegisteredFeed, ...]) -> str:
    """Name the closest feed, so a mystery becomes a typo.

    An operator seeing "unexpected" with no detail has to go and read the
    registry. Seeing "landed under fidelis-downstate-roster, but the name does
    not match its pattern" usually ends the investigation on the spot.
    """
    in_path = [f for f in feeds if f"/{f.landing_path}/" in f"/{file.key}"]
    if in_path:
        candidates = ", ".join(f"{f.feed_id} (pattern: {f.file_pattern})" for f in in_path)
        return (
            f"{file.filename!r} landed in a registered path but matched no pattern there: "
            f"{candidates}"
        )
    return f"{file.filename!r} matched no registered feed, and its path is not a registered one"


# ── the seeded pre-flight checks ─────────────────────────────────────────────
def _leading_underscore(file: FileRef, feed: RegisteredFeed) -> LandingDecision | None:
    """INCIDENT #1, as a permanent check.

    A Fidelis file named `_CINQDOWNSTATE_Member_Roster_*.xlsx` once broke the
    Excel reader, and it was fixed reactively. The lesson is not "underscores
    are bad" — that filename is correct and still in production. The lesson is
    that a reader must be TOLD to expect one, so the feed declares it and an
    undeclared underscore is refused before it reaches a parser.
    """
    if file.starts_with_underscore and not feed.allows_leading_underscore:
        return LandingDecision(
            outcome=LandingOutcome.REJECTED,
            move_to=LandingFolder.REJECTED,
            feed_id=feed.feed_id,
            feed_version=feed.feed_version,
            reason=(
                f"{file.filename!r} starts with an underscore, and {feed.feed_id} does not "
                "declare `allows_leading_underscore`. A leading underscore once broke the "
                "Excel reader; declare it on the feed if it is expected."
            ),
            check_name="leading_underscore",
        )
    return None


def _size_bounds(file: FileRef, feed: RegisteredFeed) -> LandingDecision | None:
    """Truncated and oversized deliveries, caught before parsing.

    A roster at a tenth of its usual size parses perfectly and quietly halves a
    member population — which is exactly the class of failure that reaches
    production because nothing structural objected.
    """
    if file.size_bytes == 0:
        return _too(file, feed, "empty", "the file has no content")

    if feed.min_size_bytes is not None and file.size_bytes < feed.min_size_bytes:
        return _too(
            file,
            feed,
            "truncated",
            f"{file.size_bytes:,} bytes is below {feed.feed_id}'s minimum of "
            f"{feed.min_size_bytes:,}",
        )

    if feed.max_size_bytes is not None and file.size_bytes > feed.max_size_bytes:
        return _too(
            file,
            feed,
            "oversized",
            f"{file.size_bytes:,} bytes exceeds {feed.feed_id}'s maximum of "
            f"{feed.max_size_bytes:,}",
        )
    return None


def _too(file: FileRef, feed: RegisteredFeed, kind: str, detail: str) -> LandingDecision:
    return LandingDecision(
        outcome=LandingOutcome.REJECTED,
        move_to=LandingFolder.REJECTED,
        feed_id=feed.feed_id,
        feed_version=feed.feed_version,
        reason=f"{file.filename!r} is {kind}: {detail}",
        check_name="size_bounds",
    )
