"""Upload lifecycle. Legal transitions are declared here and enforced by the store."""

from __future__ import annotations

from enum import StrEnum


class UploadStatus(StrEnum):
    RECEIVED = "received"
    PROFILING = "profiling"
    PROFILED = "profiled"
    INTERPRETING = "interpreting"
    INTERPRETED = "interpreted"
    PROFILE_FAILED = "profile_failed"
    INTERPRET_FAILED = "interpret_failed"
    # Stage 2: the analyst decision at G1, and what follows it
    APPROVED = "approved"
    REJECTED = "rejected"
    LANDING = "landing"
    LANDED = "landed"
    LAND_FAILED = "land_failed"


class RunState(StrEnum):
    """A batch's own lifecycle, separate from the upload's."""

    RECEIVED = "received"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


#: Stage 1 scope. Retry from a failed state re-enters the work state it failed in.
#:
#: A failure is recorded *after* the worker rolls back its transaction, which also
#: discards the in-progress marker it wrote. So `*_FAILED` is reachable both from
#: the in-progress state and from the state the work began in.
LEGAL_TRANSITIONS: dict[UploadStatus, frozenset[UploadStatus]] = {
    UploadStatus.RECEIVED: frozenset({UploadStatus.PROFILING, UploadStatus.PROFILE_FAILED}),
    UploadStatus.PROFILING: frozenset({UploadStatus.PROFILED, UploadStatus.PROFILE_FAILED}),
    UploadStatus.PROFILED: frozenset({UploadStatus.INTERPRETING, UploadStatus.INTERPRET_FAILED}),
    UploadStatus.INTERPRETING: frozenset({UploadStatus.INTERPRETED, UploadStatus.INTERPRET_FAILED}),
    # G1: the analyst decides. Nothing reaches the data plane without this.
    UploadStatus.INTERPRETED: frozenset({UploadStatus.APPROVED, UploadStatus.REJECTED}),
    UploadStatus.APPROVED: frozenset({UploadStatus.LANDING, UploadStatus.LAND_FAILED}),
    UploadStatus.LANDING: frozenset({UploadStatus.LANDED, UploadStatus.LAND_FAILED}),
    # Replay: a landed upload may be re-landed from its preserved original. Bronze
    # is append-only, so this adds a new batch and leaves the earlier one intact.
    UploadStatus.LANDED: frozenset({UploadStatus.LANDING}),
    UploadStatus.REJECTED: frozenset(),
    UploadStatus.LAND_FAILED: frozenset({UploadStatus.LANDING}),
    UploadStatus.PROFILE_FAILED: frozenset({UploadStatus.PROFILING}),
    UploadStatus.INTERPRET_FAILED: frozenset({UploadStatus.INTERPRETING}),
}


class IllegalTransition(Exception):
    def __init__(self, current: str, requested: str) -> None:
        super().__init__(f"illegal upload transition: {current} -> {requested}")
        self.current = current
        self.requested = requested


def assert_transition(current: UploadStatus | str, requested: UploadStatus | str) -> None:
    current, requested = UploadStatus(current), UploadStatus(requested)
    if requested not in LEGAL_TRANSITIONS[current]:
        raise IllegalTransition(current, requested)
