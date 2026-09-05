"""The workflow, declared once.

Eight steps, three scopes, two of them human. This is the single source that
the step ledger (`workflow/store.py: StepLedger`), the worker's bookkeeping
(`queue/worker.py: run_once`), the progress payloads (`workflow/models.py:
build_step_progress`) and - through `GET /api/workflow` and every `/progress`
endpoint - the frontend's run rail all read. It replaces two hand-maintained
lists: the `Stage` construction in `build_upload_progress` (kept alongside
until PR-4 migrates the last screen off it) and `RUN_STEPS` in
`frontend/lib/runStep.ts` (now derived from `steps[]`).

Deliberately declarative: nothing here imports the rest of the package, so
anything may import this. Topics are string literals rather than the workers'
`TOPIC` constants for the same reason; `tests/unit/test_dag.py` asserts they
match the handler registry.

Scopes. `upload` is what happens to one file: profile, interpret, the G1
decision, landing. `batch` is what happens to what landed: Bronze analysis,
and promotion. `feed_version` is what belongs to a mapping version regardless
of batch: the preview and the G2 decision. The plan (§6.1) sketched promotion
under `feed_version`; it is `batch` here because a promotion writes *one
batch's* Silver rows and PR-3's re-run of it "rebuilds this batch only" - a
second batch promoted under the same approved version is a different step
run, not generation 2 of the first.

Generations and attempts. A step that is run again after finishing (a replay,
or a PR-3 re-run) gets a new `generation`; the queue's own retries of one
message increment `attempts` on the same generation. Gates have neither: a
human decides once.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

StepScope = Literal["upload", "batch", "feed_version"]
StepState = Literal["pending", "running", "done", "failed", "skipped"]

SCOPES: tuple[str, ...] = ("upload", "batch", "feed_version")
STEP_STATES: tuple[str, ...] = ("pending", "running", "done", "failed", "skipped")
#: States in which nothing further will happen to this generation on its own.
TERMINAL_STATES: frozenset[str] = frozenset({"done", "failed", "skipped"})


@dataclass(frozen=True)
class StepDef:
    key: str
    label: str
    scope: StepScope
    #: The queue topic whose handler performs the step; None for a gate, which
    #: a person performs through the API.
    topic: str | None
    depends_on: tuple[str, ...] = ()
    gate: bool = False


WORKFLOW: tuple[StepDef, ...] = (
    StepDef("profile", "Parse and profile", "upload", "upload.profile"),
    StepDef("interpret", "AI interpretation", "upload", "upload.interpret", ("profile",)),
    StepDef("gate_g1", "Decision — G1", "upload", None, ("interpret",), gate=True),
    StepDef("land", "Land to Bronze", "upload", "batch.land_bronze", ("gate_g1",)),
    StepDef("analyze", "Bronze analysis", "batch", "bronze.analyze", ("land",)),
    StepDef("preview", "Deterministic preview", "feed_version", "mapping.preview", ("analyze",)),
    StepDef("gate_g2", "Decision — G2", "feed_version", None, ("preview",), gate=True),
    StepDef("promote", "Promote to Silver", "batch", "mapping.promote", ("gate_g2",)),
)

STEPS: dict[str, StepDef] = {step.key: step for step in WORKFLOW}

#: PR-3: the ledger states a step may be re-run from. `pending`/`running` is
#: already going to run - re-queueing it would race itself; `failed`/`done`/
#: `skipped` may run again, as a new generation. A gate never: a person
#: decides once, and a rejected G1 is terminal.
RERUNNABLE: dict[str, frozenset[str]] = {
    step.key: frozenset() if step.gate else frozenset({"failed", "done", "skipped"})
    for step in WORKFLOW
}
STEP_ORDER: dict[str, int] = {step.key: index for index, step in enumerate(WORKFLOW)}
_BY_TOPIC: dict[str, StepDef] = {step.topic: step for step in WORKFLOW if step.topic}


def step_for_topic(topic: str) -> StepDef | None:
    """The step a queue topic performs, or None for housekeeping topics such as
    `upload.reject` (a consequence of the G1 decision, not a step of its own)."""
    return _BY_TOPIC.get(topic)


def feed_version_scope(feed: str, version: int | str) -> str:
    """`scope_id` for the `feed_version` scope. One format, used everywhere."""
    return f"{feed}:v{int(version)}"


def parse_feed_version_scope(scope_id: str) -> tuple[str, int]:
    """The inverse of `feed_version_scope`. A feed name may itself contain ':',
    so the split is on the last ':v'."""
    feed, sep, version = scope_id.rpartition(":v")
    if not sep or not version.isdigit():
        raise ValueError(f"not a feed_version scope id: {scope_id!r}")
    return feed, int(version)


def scope_id_for(step: StepDef, payload: dict[str, Any]) -> str:
    """Which scope a queue message is about, read off the payload every worker
    already receives (templates.md §4) - so the worker loop can open the step
    without knowing anything about the handler."""
    if step.scope == "upload":
        return str(payload["upload_id"])
    if step.scope == "batch":
        return str(payload["batch_id"])
    return feed_version_scope(str(payload["feed"]), payload["version"])


def downstream_gate(step: StepDef) -> StepDef | None:
    """The gate that opens when this step finishes - `gate_g1` after
    `interpret`, `gate_g2` after `preview`. Only a gate in the *same* scope
    qualifies: it is the same object the person is deciding about."""
    return next(
        (
            candidate
            for candidate in WORKFLOW
            if candidate.gate and step.key in candidate.depends_on and candidate.scope == step.scope
        ),
        None,
    )


def as_dicts() -> list[dict[str, Any]]:
    """The declaration as data, for `GET /api/workflow`."""
    return [asdict(step) for step in WORKFLOW]


def validate(workflow: tuple[StepDef, ...] = WORKFLOW) -> None:
    """A malformed declaration fails at import, not at the first poll."""
    keys = [step.key for step in workflow]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate step keys: {sorted(k for k in keys if keys.count(k) > 1)}")
    known = set(keys)
    for step in workflow:
        missing = [dep for dep in step.depends_on if dep not in known]
        if missing:
            raise ValueError(f"step '{step.key}' depends on unknown step(s): {missing}")
        if step.gate and step.topic is not None:
            raise ValueError(f"gate '{step.key}' must not have a topic (a person performs it)")
        if not step.gate and step.topic is None:
            raise ValueError(f"step '{step.key}' has no topic and is not a gate")
        if step.scope not in SCOPES:
            raise ValueError(f"step '{step.key}' has unknown scope '{step.scope}'")

    # Kahn's algorithm: every step must eventually have all its dependencies
    # resolved, or the declaration contains a cycle.
    remaining = {step.key: set(step.depends_on) for step in workflow}
    while remaining:
        ready = [key for key, deps in remaining.items() if not deps]
        if not ready:
            raise ValueError(f"the workflow has a cycle among: {sorted(remaining)}")
        for key in ready:
            del remaining[key]
        for deps in remaining.values():
            deps.difference_update(ready)


validate()
