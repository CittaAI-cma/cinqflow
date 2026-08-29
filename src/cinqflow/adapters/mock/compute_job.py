"""scripted — a compute renderer that reports counts without touching a store."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from cinqflow.core.compiler.plan import LogicalPlan, StepKind
from cinqflow.core.model.vocabulary import Layer
from cinqflow.ports import port
from cinqflow.ports.compute_job import ComputeError, JobRun, StageResult


@port("compute_job", "mock")
class ScriptedComputeJob:
    """Executes the SHAPE of a plan: stage order, restart semantics, counts.

    It deliberately does not process data — that is the Postgres renderer's job
    at rung 0.5, and pretending to here would give a false sense that the
    pipeline worked. What it does prove is the contract every renderer must
    honour, including the one that is easy to get wrong: `resume_from` must not
    re-run earlier stages, because Bronze is append-only and a re-run either
    duplicates or is refused.
    """

    def __init__(self, *, counts: dict[Layer, StageResult] | None = None) -> None:
        self._counts = dict(counts or {})
        self._runs: dict[str, JobRun] = {}

    def run(self, plan: LogicalPlan, batch_id: str, resume_from: Layer | None = None) -> JobRun:
        stages = _stages_of(plan)
        if resume_from is not None:
            if resume_from not in stages:
                raise ComputeError(
                    f"cannot resume at {resume_from.value}: this plan does not write it"
                )
            stages = stages[stages.index(resume_from) :]

        results = tuple(self._counts.get(stage, _zero(stage)) for stage in stages)
        run = JobRun(
            run_id=str(uuid.uuid4()),
            batch_id=batch_id,
            completed_stages=tuple(stages),
            results=results,
        )
        self._runs[run.run_id] = run
        return run

    def poll(self, run_id: str) -> JobRun:
        try:
            return self._runs[run_id]
        except KeyError:
            raise ComputeError(f"no such run: {run_id}") from None

    def metrics(self, run_id: str) -> Sequence[StageResult]:
        return self.poll(run_id).results


def _stages_of(plan: LogicalPlan) -> list[Layer]:
    return [
        step.kind.produces_layer
        for step in plan.steps
        if step.kind.produces_layer is not None and step.kind is not StepKind.RESOLVE_IDENTITY
    ]


def _zero(stage: Layer) -> StageResult:
    return StageResult(
        stage=stage, records_in=0, records_out=0, quarantined=0, attributed_drops=0, duration_ms=0
    )
