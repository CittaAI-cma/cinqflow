"""CF-V2-E12-05 / ADR-0015 — "can I trust this feed?" as a number with a history.

    "Show the score's ingredients on click (DQ 92, SLA 97, reconciliation 99…)
     — A SCORE NO ONE CAN DECOMPOSE IS A RUMOR."
    — CF-V2-E12-05, acceptance criteria

    "Combines the signals the platform already produces — DQ, SLA,
     reconciliation, schema stability, identity health, pipeline health — into
     one feed-level score with configurable weighting. The score must decompose
     on click."
    — ADR-0015, capability 3

SIX SIGNALS THE PLATFORM ALREADY EMITS. Nothing here measures anything new;
that is the design. A reliability score built from a new measurement is a
seventh thing to keep true, and it will drift from the six that operations
actually acts on.

DECOMPOSITION IS THE TYPE, NOT A FEATURE. `Score` holds its components, and
`Score.overall` is computed FROM them on every access rather than stored beside
them. There is no code path that can produce a score whose parts do not add up,
so "decompose on click" is a property of the object rather than a screen that
has to remember to render it.

WEIGHTS LIVE IN THE PROFILE. `Weights` is passed in, never imported: ADR-0015
says the weighting is configurable, and configurable means per-environment,
which means the connection profile and nowhere else.

BANDS ARE ILLUSTRATIVE, AND SAYING SO MATTERS. ADR-0015 calls 90/70 an
illustration. They are defaults here, overridable per feed, because a daily ADT
feed at 100% change rate and a monthly roster do not deserve the same
threshold — and a band nobody can move is one people learn to ignore.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum, unique
from itertools import pairwise

from cinqflow.core.citations import CitationId, CitationKind


@unique
class Signal(StrEnum):
    """The six. Adding a seventh is an ADR, not a commit.

    Each maps to something already written to the control plane:

      DQ              rule results per batch          (E7-05)
      SLA             sla_instance status history     (E12-01)
      RECONCILIATION  batch_reconciliation balances   (E13-01)
      SCHEMA          schema_drift_log severity       (E5-04)
      IDENTITY        resolution rate                 (Wave 3 — reports 100 until then)
      PIPELINE        batch success / restart rate    (E12-02)
    """

    DQ = "dq"
    SLA = "sla"
    RECONCILIATION = "reconciliation"
    SCHEMA = "schema"
    IDENTITY = "identity"
    PIPELINE = "pipeline"


@dataclass(frozen=True)
class Weights:
    """From the connection profile. Must sum to 1.0.

    Refusing an unnormalised set is not pedantry: weights that sum to 1.2
    produce scores above 100, and a score above 100 destroys the bands for
    everyone who has learned to read them.
    """

    dq: float = 0.25
    sla: float = 0.20
    reconciliation: float = 0.25
    schema: float = 0.10
    identity: float = 0.10
    pipeline: float = 0.10

    def __post_init__(self) -> None:
        total = sum(self.as_map().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"reliability weights sum to {total:.3f}, not 1.0 — a score above 100 "
                "destroys the bands for everyone who has learned to read them"
            )

    def as_map(self) -> dict[Signal, float]:
        return {
            Signal.DQ: self.dq,
            Signal.SLA: self.sla,
            Signal.RECONCILIATION: self.reconciliation,
            Signal.SCHEMA: self.schema,
            Signal.IDENTITY: self.identity,
            Signal.PIPELINE: self.pipeline,
        }


@dataclass(frozen=True)
class Component:
    """One signal's contribution, with the evidence behind it.

    `evidence` is the sentence shown on click — '18 of 18 rules passed on the
    last 6 batches' — and `citation` is what opens when the reader wants the
    rows. A component with neither is the rumour the story is guarding against.
    """

    signal: Signal
    value: float
    weight: float
    evidence: str
    citation: CitationId | None = None
    sample_size: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 100.0:
            raise ValueError(f"{self.signal.value}: {self.value} is not a 0-100 score")

    @property
    def contribution(self) -> float:
        return self.value * self.weight

    @property
    def measured(self) -> bool:
        """A signal with no observations is NOT a signal scoring zero.

        Wave 3 brings identity resolution; until then the identity signal has
        a sample size of zero. Scoring it 0 would drag every feed into the
        critical band for a capability that does not exist yet — so unmeasured
        components are excluded and the remaining weights renormalise.
        """
        return self.sample_size > 0


@unique
class Band(StrEnum):
    HEALTHY = "healthy"
    AT_RISK = "at_risk"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Bands:
    """Illustrative defaults, per ADR-0015 — and overridable per feed."""

    healthy_at: float = 90.0
    at_risk_at: float = 70.0

    def band(self, score: float) -> Band:
        if score >= self.healthy_at:
            return Band.HEALTHY
        if score >= self.at_risk_at:
            return Band.AT_RISK
        return Band.CRITICAL


@dataclass(frozen=True)
class Score:
    """One feed's reliability, and every part of it."""

    feed_id: str
    as_of: date
    components: tuple[Component, ...]
    bands: Bands = field(default_factory=Bands)

    @property
    def measured(self) -> tuple[Component, ...]:
        return tuple(c for c in self.components if c.measured)

    @property
    def overall(self) -> float:
        """Computed from the parts on every access. Never stored beside them.

        Renormalises over MEASURED components, so an unavailable signal lowers
        confidence rather than the score.
        """
        parts = self.measured
        if not parts:
            return 0.0
        total_weight = sum(c.weight for c in parts)
        if total_weight <= 0:
            return 0.0
        return round(sum(c.contribution for c in parts) / total_weight, 1)

    @property
    def band(self) -> Band:
        return self.bands.band(self.overall)

    @property
    def confidence(self) -> float:
        """How much of the intended weight was actually measurable, 0 to 1.

        Shown beside the score. A 94 built from four of six signals is a
        different claim from a 94 built from all six, and hiding that
        difference is how a score becomes a rumour by a slower route.
        """
        intended = sum(c.weight for c in self.components)
        if intended <= 0:
            return 0.0
        return round(sum(c.weight for c in self.measured) / intended, 2)

    def decompose(self) -> tuple[tuple[str, float, str], ...]:
        """What the click shows: (label, value, evidence), highest weight first."""
        return tuple(
            (c.signal.value, c.value, c.evidence)
            for c in sorted(self.components, key=lambda c: (-c.weight, c.signal.value))
        )

    def weakest(self) -> Component | None:
        """The one to mention in an enriched alert. Lowest contribution first —
        weighted, because a 60 on a 0.05-weight signal is not the story."""
        parts = self.measured
        if not parts:
            return None
        return min(parts, key=lambda c: c.contribution)

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.FEED, subject=self.feed_id)


def score_for(
    *,
    feed_id: str,
    as_of: date,
    observations: dict[Signal, tuple[float, str, int]],
    weights: Weights,
    bands: Bands | None = None,
) -> Score:
    """Assemble a score from six observations.

    `observations` maps signal → (value, evidence, sample_size). A signal
    absent from the map becomes an unmeasured component rather than a zero —
    the distinction `Component.measured` exists to protect.
    """
    weight_map = weights.as_map()
    components = tuple(
        Component(
            signal=signal,
            value=observations.get(signal, (0.0, "not measured", 0))[0],
            weight=weight_map[signal],
            evidence=observations.get(signal, (0.0, "not measured", 0))[1],
            sample_size=observations.get(signal, (0.0, "not measured", 0))[2],
        )
        for signal in Signal
    )
    return Score(feed_id=feed_id, as_of=as_of, components=components, bands=bands or Bands())


def trend(scores: Sequence[Score]) -> tuple[float, ...]:
    """The sparkline. Oldest first, so a chart reads left to right."""
    return tuple(s.overall for s in sorted(scores, key=lambda s: s.as_of))


def deteriorating(scores: Sequence[Score], *, points: int = 3) -> bool:
    """Three consecutive falls. The signal an enriched alert should mention —
    'this feed has been getting worse for three cycles' is actionable in a way
    that a single low number is not."""
    values = trend(scores)[-points:]
    return len(values) == points and all(later < earlier for earlier, later in pairwise(values))
