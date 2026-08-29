"""The `compute_job` pin — render a compiled plan and run it.

    verb: run_job/poll/metrics   mock: scripted   dev: pg_plane
    target: databricks_jobs
    — docs/architecture/plates/04-pin-out-map.md

    "Approved metadata compiles to a logical plan, and the plan is rendered by
     whichever compute adapter is fitted. Postgres today, Databricks later —
     one plan, two renderings, compared on OUTPUT DATA."
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

The core hands over a plan, never SQL. That is the whole indirection, and it
buys the two things that matter: engine-specific SQL exists ONLY inside a
compute adapter, and the Databricks rendering inherits every test the Postgres
rendering already passes.

Golden pipelines compare output DATA across engines, never generated code —
comparing code would fail on formatting and pass on semantics, which is exactly
backwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cinqflow.core.model.vocabulary import Layer

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the import graph acyclic
    from cinqflow.core.compiler.plan import LogicalPlan


@dataclass(frozen=True)
class StageResult:
    """What one plan step actually did.

    The counts are the terms of the balance equation, so a renderer that cannot
    report them cannot be certified — it would make reconciliation unprovable.
    """

    stage: Layer
    records_in: int
    records_out: int
    quarantined: int
    attributed_drops: int
    duration_ms: int


@dataclass(frozen=True)
class JobRun:
    """A plan execution in flight or complete."""

    run_id: str
    batch_id: str
    completed_stages: tuple[Layer, ...]
    results: tuple[StageResult, ...]
    failed_stage: Layer | None = None
    failure_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.failed_stage is None

    @property
    def resume_from(self) -> Layer | None:
        """Where a restart picks up: the stage AFTER the last completed one.

        "restart resumes from the last completed stage: no duplicates,
         no skips"
        — docs/architecture/INVARIANTS.md, data plane
        """
        if not self.completed_stages:
            return Layer.LANDING
        return Layer.after(self.completed_stages[-1])


class ComputeError(RuntimeError):
    """The renderer could not execute the plan."""


@runtime_checkable
class ComputeJobPort(Protocol):
    def run(self, plan: LogicalPlan, batch_id: str, resume_from: Layer | None = None) -> JobRun:
        """Render and execute a plan.

        `resume_from` is how restart-from-stage is expressed to the engine.
        Passing it must not re-run earlier stages — Bronze is append-only, so a
        re-run would either duplicate or be refused, and both are defects.
        """
        ...

    def poll(self, run_id: str) -> JobRun: ...

    def metrics(self, run_id: str) -> Sequence[StageResult]: ...
