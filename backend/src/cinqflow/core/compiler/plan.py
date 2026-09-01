"""The logical plan (IR) — what approved metadata compiles into.

    input:  {feed_record, schema_contract@v, mapping@v, dq_rules@v}
    output: logical_plan (IR)
    plan_steps: [read, validate, land_bronze, cast, map, evaluate_rules,
                 resolve_identity, load, reconcile]
    — docs/architecture/plates/08-compiler-and-dual-rendering.md

This artifact does three jobs, and the design follows from wanting all three:

  1. The ENGINE runs it. A compute adapter renders it — set-based SQL inside a
     transaction on Postgres today, Spark/Delta via the Jobs API later. One
     plan, two renderings, compared on OUTPUT DATA.

  2. The AGENT explains it. "What will this do to my data before it reaches
     Silver ODS?" stops being a code-reading exercise, which is the whole point
     of CF-V0-E16-10. Hence `narrate()` and the citations on every step.

  3. The EVAL SUITE grades against it. Because the plan is a complete,
     deterministic, machine-readable description of what the pipeline will do,
     "every step present, no step invented" is COMPUTABLE. The golden set is
     the plan itself, so it needs no hand-labelling and grows automatically
     with every feed added — and it tests the exact failure mode that matters:
     confident fabrication.

What is deliberately NOT here: conditionals. A PlanStep has parameters, never a
branch. "Contain any feed-specific code — everything feed-specific must come
from metadata" is a documented don't for CF-V0-E8-01, and an IR that cannot
express a per-feed branch is a stronger guarantee than a review that looks for
one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import Layer


@unique
class StepKind(StrEnum):
    """The nine steps. Their ORDER is the spine, and it is enforced."""

    READ = "read"
    VALIDATE = "validate"
    LAND_BRONZE = "land_bronze"
    CAST = "cast"
    MAP = "map"
    EVALUATE_RULES = "evaluate_rules"
    RESOLVE_IDENTITY = "resolve_identity"
    LOAD = "load"
    RECONCILE = "reconcile"

    @property
    def order(self) -> int:
        return list(StepKind).index(self)

    @property
    def produces_layer(self) -> Layer | None:
        """Which layer this step writes, if any. Used to check that a plan
        stops where its wave says it stops."""
        return {
            StepKind.LAND_BRONZE: Layer.BRONZE,
            StepKind.LOAD: Layer.SILVER_RAW,
            StepKind.RESOLVE_IDENTITY: Layer.IDENTITY,
        }.get(self)


@dataclass(frozen=True)
class NarratedStep:
    """One sentence of the plan, with the citations for what it names."""

    text: str
    citations: tuple[CitationId, ...]


@dataclass(frozen=True)
class PlanStep:
    """One step. Parameters, never a branch.

    `citations` is what lets the explanation be graded: a step that names an
    object the caller can open is checkable, and a step naming nothing is a
    step no one can verify.
    """

    kind: StepKind
    description: str
    citations: tuple[CitationId, ...] = field(default_factory=tuple)
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError(f"{self.kind}: a step with no description cannot be explained")

    def _key(self) -> str:
        params = ";".join(f"{k}={self.parameters[k]!r}" for k in sorted(self.parameters))
        cites = ",".join(str(c) for c in self.citations)
        return f"{self.kind.value}|{self.description}|{cites}|{params}"


@dataclass(frozen=True)
class LogicalPlan:
    """A complete, deterministic description of what a pipeline will do."""

    feed_id: str
    feed_version: int
    steps: tuple[PlanStep, ...]
    terminal_layer: Layer = Layer.SILVER_RAW

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("an empty plan is not a plan")

        orders = [s.kind.order for s in self.steps]
        if orders != sorted(orders):
            got = " -> ".join(s.kind.value for s in self.steps)
            raise ValueError(
                f"plan steps are out of order: {got}. The order is the spine — mapping "
                "before landing would write transformed data into an append-only layer."
            )
        if len(set(orders)) != len(orders):
            raise ValueError("a step appears twice; a plan is a sequence, not a loop")

        if StepKind.RECONCILE not in {s.kind for s in self.steps}:
            raise ValueError(
                "a plan must end by reconciling. Without it the batch cannot fail loudly "
                "on an unbalanced equation, and silent row loss becomes possible again."
            )

        for step in self.steps:
            produced = step.kind.produces_layer
            if produced is not None and _beyond(produced, self.terminal_layer):
                raise ValueError(
                    f"{step.kind.value} writes {produced.value}, which is beyond this plan's "
                    f"terminal layer {self.terminal_layer.value}"
                )

    @property
    def step_kinds(self) -> tuple[StepKind, ...]:
        return tuple(s.kind for s in self.steps)

    @property
    def citation(self) -> CitationId:
        """The plan's own address: `plan:<feed_id>@v<n>`."""
        return CitationId(kind=CitationKind.PLAN, subject=self.feed_id, version=self.feed_version)

    @property
    def fingerprint(self) -> str:
        """Stable across processes, so a plan can be compared after the fact.

        Python's hash() is salted per process; a plan fingerprint that changed
        between runs would make "is this the plan that ran?" unanswerable.
        """
        material = f"{self.feed_id}@v{self.feed_version}|{self.terminal_layer.value}|" + "|".join(
            step._key() for step in self.steps
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def narrate(self) -> tuple[NarratedStep, ...]:
        """The plan in plain English, one sentence per step, each cited.

        This is the grounding the Pipeline Insight Agent explains FROM. It is
        deliberately generated here rather than by the model: the model's job
        is to answer the question asked, not to invent the facts.
        """
        return tuple(
            NarratedStep(text=step.description, citations=step.citations) for step in self.steps
        )


def _beyond(layer: Layer, terminal: Layer) -> bool:
    order = list(Layer)
    return order.index(layer) > order.index(terminal)


def compile_steps(
    *,
    feed_id: str,
    feed_version: int,
    contract_version: int,
    mapping_version: int,
    file_pattern: str,
    column_count: int,
    cast_columns: tuple[str, ...],
    rule_ids: tuple[str, ...],
    target_table: str,
    terminal_layer: Layer = Layer.SILVER_RAW,
) -> tuple[PlanStep, ...]:
    """Build the step sequence from approved metadata.

    Every value here comes from a governed object. There is no branch on
    feed_id anywhere in this function, and that absence is the platform's
    central claim: onboarding a feed is a registry row, not a pipeline.
    """
    feed = CitationId(kind=CitationKind.FEED, subject=feed_id, version=feed_version)
    contract = CitationId(kind=CitationKind.CONTRACT, subject=feed_id, version=contract_version)
    mapping = CitationId(kind=CitationKind.MAPPING, subject=feed_id, version=mapping_version)

    steps: list[PlanStep] = [
        PlanStep(
            kind=StepKind.READ,
            description=f"Reads files matching {file_pattern} from the feed's landing folder.",
            citations=(feed,),
            parameters={"file_pattern": file_pattern},
        ),
        PlanStep(
            kind=StepKind.VALIDATE,
            description=(
                f"Validates {column_count} columns against schema contract v{contract_version}."
            ),
            citations=(contract,),
            parameters={"column_count": column_count},
        ),
        PlanStep(
            kind=StepKind.LAND_BRONZE,
            description="Writes an untouched copy of the source into Bronze, append-only.",
            citations=(feed,),
            parameters={"append_only": True},
        ),
    ]

    if cast_columns:
        steps.append(
            PlanStep(
                kind=StepKind.CAST,
                description=(
                    f"Casts {len(cast_columns)} columns to their contracted types: "
                    f"{', '.join(cast_columns)}."
                ),
                citations=(contract,),
                parameters={"columns": list(cast_columns)},
            )
        )

    steps.append(
        PlanStep(
            kind=StepKind.MAP,
            description=f"Applies mapping v{mapping_version} to the canonical column names.",
            citations=(mapping,),
            parameters={"mapping_version": mapping_version},
        )
    )

    if rule_ids:
        steps.append(
            PlanStep(
                kind=StepKind.EVALUATE_RULES,
                description=(
                    f"Evaluates {len(rule_ids)} data quality rules "
                    f"({', '.join(rule_ids)}); failures are quarantined with their reason."
                ),
                citations=tuple(
                    CitationId(kind=CitationKind.RULE, subject=rule_id) for rule_id in rule_ids
                ),
                parameters={"rule_ids": list(rule_ids)},
            )
        )

    steps.append(
        PlanStep(
            kind=StepKind.LOAD,
            description=f"Loads the surviving records into {target_table}.",
            citations=(mapping,),
            parameters={"target_table": target_table},
        )
    )
    steps.append(
        PlanStep(
            kind=StepKind.RECONCILE,
            description=(
                "Reconciles every stage: rows in must equal rows out plus quarantined "
                "plus attributed drops, or the batch fails."
            ),
            citations=(feed,),
            parameters={"terminal_layer": terminal_layer.value},
        )
    )
    return tuple(steps)
