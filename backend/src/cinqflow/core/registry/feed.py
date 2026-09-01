"""CF-V0-E3-01 — the minimal feed record the engine can run from.

    "I want a small registry screen where a feed is described with just what
     the engine needs — domain, file format, landing folder, file-name pattern,
     and schedule, so that THE VERY FIRST PIPELINE RUNS FROM STORED METADATA
     INSTEAD OF CODE, proving the platform's central idea before we invest in
     the full registry."

Six fields. That restraint is the story, not a shortcut: if the engine needs
more than six fields to run a feed, the engine has feed-specific code in it
somewhere. The full Epic-3 registry (owners, SLAs, holiday calendars, alert
routing, volumes) is CF-V1-E3-02 and adds governance around this, not new
inputs to the engine.

The feed is a GovernedObject like every other, so it inherits the one lifecycle,
the audit trail and both universal negatives (ADR-0006) — there is no private
`is_active` flag here, deliberately.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.landing import RegisteredFeed
from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    ObjectType,
)


class FeedValidationError(ValueError):
    """A feed record the engine could not run from."""


class PatternSampleMismatchError(FeedValidationError):
    """The declared pattern does not match the sample filename.

        "Given the entered file pattern does not match the provided sample file
         name, when the engineer saves, then the save is BLOCKED with a
         side-by-side view of pattern vs sample showing exactly where they
         differ."
        — CF-V0-E3-01, exception

    Blocked at save, not discovered at 3am when the file lands and matches
    nothing — which is the same failure as an unexpected file, arriving by a
    route nobody would think to check.
    """

    def __init__(self, pattern: str, sample: str, explanation: str) -> None:
        super().__init__(explanation)
        self.pattern = pattern
        self.sample = sample


@dataclass(frozen=True)
class FeedRecord:
    """The six fields, plus the bounds landing needs to reject a truncation."""

    feed_id: str
    domain: str
    source_system: str
    file_format: str
    landing_path: str
    file_pattern: str
    schedule_cron: str
    sample_filename: str
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    allows_leading_underscore: bool = True
    #: CF-V1-E8-03 — the feeds this one waits for. APPROVED CONFIGURATION, so
    #: it travels the lifecycle, appears in the approval packet's diff, and
    #: cannot be changed without somebody signing for it. `core.scheduling`
    #: reads it off the governed body and answers "may this run start?"; the
    #: names are not resolved here because a feed may legitimately be
    #: registered before the one it depends on, and a dependency that is named
    #: but not yet published is reported as "upstream not arrived" rather than
    #: silently dropped.
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("feed_id", "domain", "source_system", "file_format", "landing_path"):
            if not str(getattr(self, name)).strip():
                raise FeedValidationError(f"{name} is required — the engine reads it")

        try:
            compiled = re.compile(self.file_pattern)
        except re.error as exc:
            raise FeedValidationError(
                f"{self.file_pattern!r} is not a valid pattern: {exc}"
            ) from None

        # Validated against a REAL sample name before saving. A pattern that
        # matches nothing is indistinguishable from a feed that never arrives.
        if not compiled.fullmatch(self.sample_filename):
            raise PatternSampleMismatchError(
                self.file_pattern,
                self.sample_filename,
                explain_mismatch(self.file_pattern, self.sample_filename),
            )

        if self.feed_id in self.depends_on:
            raise FeedValidationError(
                f"{self.feed_id} declares itself as its own upstream. It would wait "
                "forever for a batch it is not allowed to start."
            )
        if len(set(self.depends_on)) != len(self.depends_on):
            raise FeedValidationError(
                f"{self.feed_id} names the same upstream twice. A duplicate edge changes "
                "nothing about when the feed runs and doubles every hold it reports."
            )

        if (
            self.min_size_bytes is not None
            and self.max_size_bytes is not None
            and self.min_size_bytes > self.max_size_bytes
        ):
            raise FeedValidationError(
                f"{self.feed_id}: min_size_bytes exceeds max_size_bytes — no file could pass"
            )

    def as_governed(
        self, *, author: Actor, version: int = 1, created_ts: datetime | None = None
    ) -> GovernedObject:
        """Every feed is a governed object. There is no other way to hold one."""
        from datetime import UTC

        return GovernedObject(
            object_type=ObjectType.FEED,
            object_id=self.feed_id,
            version=version,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=author,
            created_ts=created_ts or datetime.now(UTC),
            body=self.__dict__.copy(),
        )

    def for_landing(self, version: int) -> RegisteredFeed:
        """What landing controls need. A projection, never a second source.

        Landing does structural validation only, so it gets the structural
        fields and nothing else — it has no business knowing the schedule.
        """
        return RegisteredFeed(
            feed_id=self.feed_id,
            feed_version=version,
            landing_path=self.landing_path,
            file_pattern=self.file_pattern,
            file_format=self.file_format,
            min_size_bytes=self.min_size_bytes,
            max_size_bytes=self.max_size_bytes,
            allows_leading_underscore=self.allows_leading_underscore,
        )

    def citation(self, version: int) -> CitationId:
        return CitationId(kind=CitationKind.FEED, subject=self.feed_id, version=version)


def from_governed(obj: GovernedObject) -> FeedRecord:
    """Read a feed back out of its governed wrapper.

    Refuses anything that is not Published, because that is what "the engine
    reads approved metadata" means in code. An engine that could read a Draft
    would make the approval gate decorative.
    """
    if obj.object_type is not ObjectType.FEED:
        raise FeedValidationError(f"{obj.object_type} is not a feed")
    # Only the fields the ENGINE reads. CF-V1-E3-02 stores the operational
    # envelope — owners, SLA, calendars, volumes, alert chain — in the same
    # body under `operations`, and the engine record must ignore it rather
    # than choke on it: a `FeedRecord(**body)` here would make adding any
    # organisational field to the registry a breaking change to the loader.
    fields = {f.name for f in dataclasses.fields(FeedRecord)}
    return FeedRecord(**{key: value for key, value in obj.body.items() if key in fields})


def executable(obj: GovernedObject) -> FeedRecord:
    """The engine's door. Published only."""
    if not obj.is_executable:
        raise FeedValidationError(
            f"feed:{obj.object_id}@v{obj.version} is {obj.lifecycle_state.value}, not published. "
            "The engine runs approved metadata; nothing else is executable."
        )
    return from_governed(obj)


def explain_mismatch(pattern: str, sample: str) -> str:
    """Show the engineer WHERE the pattern and the sample diverge.

    The story asks for a side-by-side view showing exactly where they differ.
    The useful information is the longest prefix that still matches — that
    almost always lands on the character that is wrong, which turns "the
    pattern does not match" into "you typed a letter O where a zero goes".
    """
    matched = 0
    for index in range(1, len(sample) + 1):
        try:
            if re.compile(pattern).match(sample[:index]) or re.compile(
                pattern[: min(index, len(pattern))]
            ).match(sample[:index]):
                matched = index
        except re.error:  # pragma: no cover - a partial pattern may not compile
            break

    marker = " " * matched + "^"
    return (
        f"the pattern does not match the sample:\n"
        f"  pattern : {pattern}\n"
        f"  sample  : {sample}\n"
        f"            {marker} diverges here (matched {matched} of {len(sample)} characters)"
    )
