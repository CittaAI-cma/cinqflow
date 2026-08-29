"""The eval gates, COMPUTED from the compiled plan — never annotated.

    | Plan-step coverage | >= 98% | every step in the compiled plan appears in
    |                    |        | the explanation
    | Invented steps     | exactly 0 | a hard zero, not a percentage
    — CF-V0-E16-10, eval gates

The idea this module exists to make real: the IR is what the engine runs, what
the agent explains, AND what grades the agent. So the golden set is generated
from the plans themselves, at ZERO annotation cost, and it grows automatically
with every feed anyone adds. That is why an AI story is affordable in Wave 0 at
all — and it measures the one failure mode that matters, which is confident
fabrication rather than occasional vagueness.

`invented_steps` is a hard zero because a percentage would make one fabricated
step in fifty a pass. A step the engine will not run, described confidently to
an analyst, is the defect; its rate is not the point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from cinqflow.core.citations import UnresolvableCitationError, parse
from cinqflow.core.compiler.plan import LogicalPlan, StepKind

#: Every step name a plan can contain. Anything else claimed as a step is
#: invented — including plausible-sounding ones like "deduplicate" or
#: "enrich", which is exactly the kind of thing a model adds to sound complete.
KNOWN_STEPS: frozenset[str] = frozenset(step.value for step in StepKind)

#: Words an answer may use for a step. Generated from the enum, plus the
#: readable forms a person actually writes.
_ALIASES: dict[str, frozenset[str]] = {
    StepKind.READ.value: frozenset({"read", "reads", "reading"}),
    StepKind.VALIDATE.value: frozenset({"validate", "validates", "validation", "validating"}),
    StepKind.LAND_BRONZE.value: frozenset({"land_bronze", "land", "lands", "bronze"}),
    StepKind.CAST.value: frozenset({"cast", "casts", "casting", "types", "typed"}),
    StepKind.MAP.value: frozenset({"map", "maps", "mapping", "renames", "rename"}),
    StepKind.EVALUATE_RULES.value: frozenset({"evaluate_rules", "rules", "rule", "dq", "quality"}),
    StepKind.RESOLVE_IDENTITY.value: frozenset(
        {"resolve_identity", "identity", "resolves", "match", "matching"}
    ),
    StepKind.LOAD.value: frozenset({"load", "loads", "loading", "writes", "write"}),
    StepKind.RECONCILE.value: frozenset(
        {"reconcile", "reconciles", "reconciliation", "reconciling", "balance", "balances"}
    ),
}

#: Steps a model might invent because they sound like data engineering. Matched
#: as STEMS, so "deduplicates", "deduplicating" and "deduplication" are all
#: caught — an invention that escapes the gate by conjugating is still an
#: invention. Named individually so the failure reports WHAT was fabricated.
_PLAUSIBLE_INVENTIONS: tuple[str, ...] = (
    "dedup",
    "enrich",
    "aggregat",
    "normali",
    "cleans",
    "standardi",
    "imput",
    "partition",
    "encrypt",
    "anonymi",
    "backfill",
    "upsert",
    "pivot",
)

_WORD = re.compile(r"[a-z_]+")


@dataclass(frozen=True)
class PlanFidelity:
    """How faithfully an explanation described the plan the engine will run."""

    covered: tuple[str, ...]
    missed: tuple[str, ...]
    invented: tuple[str, ...]

    @property
    def coverage(self) -> float:
        total = len(self.covered) + len(self.missed)
        return 1.0 if total == 0 else len(self.covered) / total

    @property
    def passes(self) -> bool:
        return self.coverage >= 0.98 and not self.invented

    def explain(self) -> str:
        parts = [f"coverage {self.coverage:.0%}"]
        if self.missed:
            parts.append(f"missed: {', '.join(self.missed)}")
        if self.invented:
            parts.append(f"INVENTED: {', '.join(self.invented)}")
        return " · ".join(parts)


def plan_fidelity(plan: LogicalPlan, explanation: str) -> PlanFidelity:
    """Grade an explanation against the plan, with no annotation and no model."""
    words = set(_WORD.findall(explanation.lower()))
    expected = [step.kind.value for step in plan.steps]

    covered = [name for name in expected if words & _ALIASES.get(name, frozenset({name}))]
    missed = [name for name in expected if name not in covered]

    absent = KNOWN_STEPS - set(expected)
    fabricated = {word for word in words for stem in _PLAUSIBLE_INVENTIONS if word.startswith(stem)}
    invented = sorted(
        {name for name in absent if words & _ALIASES.get(name, frozenset({name}))} | fabricated
    )
    return PlanFidelity(tuple(covered), tuple(missed), tuple(invented))


@dataclass(frozen=True)
class NumericFidelity:
    """Every number in an answer must appear in the grounding it was given.

    100%, not "close enough". A transposed digit in a member count is the same
    class of defect as a fabricated citation — it reads as fact and is wrong.
    """

    quoted: tuple[str, ...]
    unsupported: tuple[str, ...]

    @property
    def passes(self) -> bool:
        return not self.unsupported


_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


def numeric_fidelity(answer: str, grounding: str) -> NumericFidelity:
    def canonical(text: str) -> str:
        return text.replace(",", "").rstrip(".0") or "0"

    supported = {canonical(m) for m in _NUMBER.findall(grounding)}
    quoted = tuple(_NUMBER.findall(answer))
    return NumericFidelity(
        quoted=quoted,
        unsupported=tuple(n for n in quoted if canonical(n) not in supported),
    )


@dataclass(frozen=True)
class CitationFidelity:
    total: int
    resolvable: int
    unresolvable: tuple[str, ...]

    @property
    def passes(self) -> bool:
        """100%. A malformed citation is worse than no citation, because it
        reads as evidence and resolves to nothing."""
        return not self.unresolvable


def citation_fidelity(citation_ids: tuple[str, ...]) -> CitationFidelity:
    bad: list[str] = []
    for raw in citation_ids:
        try:
            parse(raw).route  # noqa: B018 — resolving IS the check
        except UnresolvableCitationError:
            bad.append(raw)
    return CitationFidelity(len(citation_ids), len(citation_ids) - len(bad), tuple(bad))


@dataclass(frozen=True)
class RunBudget:
    """The per-run gates the story states: p95 under 6s, at most $0.05 a run."""

    max_p95_ms: int = 6_000
    max_cost_usd: Decimal = Decimal("0.05")

    def check(
        self, *, latencies_ms: tuple[int, ...], costs: tuple[Decimal, ...]
    ) -> tuple[str, ...]:
        failures: list[str] = []
        if latencies_ms:
            ordered = sorted(latencies_ms)
            index = max(0, min(len(ordered) - 1, round(0.95 * len(ordered)) - 1))
            p95 = ordered[index]
            if p95 > self.max_p95_ms:
                failures.append(f"p95 {p95}ms exceeds {self.max_p95_ms}ms")
        for cost in costs:
            if cost > self.max_cost_usd:
                failures.append(f"a run cost ${cost}, over the ${self.max_cost_usd} cap")
                break
        return tuple(failures)
