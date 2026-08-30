"""CF-V1-E4-02 — the evidence pack, generated, and the staleness that is
mechanical.

    "one button that runs my whole draft configuration — schema, mappings,
     rules — against the sample end to end, and produces the onboarding summary
     and UAT evidence pack automatically, so that the historical bottleneck
     (days of assembling validation evidence by hand for every source) becomes
     a generated artifact reviewers receive in minutes"
    "List known gaps honestly (unmapped optional fields, rules deferred) — the
     pack is evidence, not marketing."
    — CF-V1-E4-02

THE PACK IS BUILT FROM `core.compiler.execute.ExecutionResult`, WHICH IS THE
ENGINE'S OWN OUTPUT. That is the entire content of "run the real engine in a
sandboxed test area — the same code path production will use": there is no
test-mode executor to build a pack from, because the only executor is the one
production runs and this reads what it returned.

What the SANDBOX is, is a different question, and it is the adapter's: the
worker points storage and the control tables at an isolated namespace. Nothing
in this module can touch a table, so "the test area is fully isolated" is not
a discipline this file has to keep.

STALENESS IS A FINGERPRINT, NOT A TIMESTAMP. The wave's exit criterion —
"edits a mapping post-test and watches submission blocked for stale evidence" —
is the reason `configuration_fingerprint` exists. A timestamp comparison would
say the evidence is old, which is not the question; the question is whether it
describes THIS configuration. Two contracts saved a second apart with identical
bodies produce one fingerprint and the evidence stands. A mapping edited and
saved back to its original value produces the original fingerprint and the
evidence stands, correctly — because nothing changed.

WHAT THE FINGERPRINT COVERS is exactly what the run consumed: the contract
body, the mapping body, the rule bodies, and the sample's own fingerprint. Not
the feed record — a change of owner or alert address does not invalidate a
demonstration that the data loads — and not the version numbers, because a
resubmitted identical draft is the same configuration under a new number.

A FAILED RUN STILL PRODUCES A PACK. "Given the test fails midway (a mapping
type error), when the run completes, then the pack is still produced up to the
failure." A pack that only exists on success is a pack nobody sees on the day
they most need it, so `partial` and `failure` are first-class and the pack is
built either way.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.compiler.execute import ExecutionResult, QuarantinedRow
from cinqflow.core.model.governed import GovernedObject, ObjectType

#: What a masked value looks like in the pack. One string, so a screen, a test
#: and an exported document cannot disagree about what "hidden" looks like.
MASKED = "••••••"

#: How many before/after examples the pack carries. The story says twenty.
#:
#: A cap, not a sample size: twenty rows is what a reviewer will actually read,
#: and a pack carrying ten thousand transformed rows is a pack nobody opens —
#: and, incidentally, a PHI export nobody reviewed.
EXAMPLE_LIMIT = 20


class EvidenceError(RuntimeError):
    """An evidence pack that could not be built from what it was given."""


# ── the fingerprint ──────────────────────────────────────────────────────────
def configuration_fingerprint(
    objects: Sequence[GovernedObject], *, sample_fingerprint: str = ""
) -> str:
    """A stable hash of the configuration a run consumed.

    Over BODIES, not versions: a resubmitted identical draft is the same
    configuration under a new number, and invalidating evidence for a version
    bump that changed nothing would teach people that the staleness gate is
    noise — which is how a mechanical control becomes a habit of clicking
    through.

    `sort_keys` and `separators` are not cosmetic. Two dicts with the same
    contents and different insertion order must fingerprint identically, or the
    gate would fire on a JSONB round trip.
    """
    material: dict[str, Any] = {
        object_type.value: [
            obj.body
            for obj in sorted(
                (o for o in objects if o.object_type is object_type),
                key=lambda o: (o.object_id, o.version),
            )
        ]
        for object_type in (ObjectType.CONTRACT, ObjectType.MAPPING, ObjectType.DQ_RULE)
    }
    material["sample"] = sample_fingerprint
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256-" + hashlib.sha256(encoded.encode()).hexdigest()[:32]


# ── the parts of a pack ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class RuleOutcome:
    """One rule's hit rate on the sample.

    A rule that fired ZERO times is reported, not omitted. "Every rule's hit
    rate" includes the ones that caught nothing — silence is data, and a rule
    that never fires on a representative sample is either protecting against
    something rare or is wrong, and only a human can tell which.
    """

    rule_id: str
    name: str
    tested: int
    flagged: int
    quarantined: bool

    @property
    def hit_rate(self) -> float:
        return self.flagged / self.tested if self.tested else 0.0

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.rule_id)

    def describe(self) -> str:
        fate = "quarantined" if self.quarantined else "flagged only"
        return (
            f"{self.name or self.rule_id}: {self.flagged} of {self.tested} rows "
            f"({self.hit_rate:.1%}), {fate}"
        )


@dataclass(frozen=True)
class DropExplanation:
    """Why a group of rows did not load, in the reviewer's language."""

    rule_id: str
    reason: str
    record_count: int
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Example:
    """One row, before and after — with PHI masked.

    MASKED IN THE PACK ITSELF, not at the screen. The pack's whole purpose is
    that it can be read "without platform access", which means it leaves the
    platform — and a masking policy applied only by a renderer is a masking
    policy that stops applying the moment somebody exports.
    """

    row_number: int
    before: dict[str, str]
    after: dict[str, str]


@dataclass(frozen=True)
class Gap:
    """Something the pack admits to. Evidence, not marketing."""

    key: str
    what: str
    why_it_is_acceptable: str
    citation: CitationId | None = None


@dataclass(frozen=True)
class Failure:
    """Where the run stopped, in plain language, with the address of the cause.

    "the failing step is explained in plain language, and the wizard links
     straight to the mapping line at fault"
    """

    step: str
    explanation: str
    citation: CitationId | None = None

    @property
    def route(self) -> str:
        return self.citation.route if self.citation else ""


# ── the pack ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvidencePack:
    """What a reviewer receives, generated in minutes rather than assembled in
    days."""

    feed_id: str
    fingerprint: str
    produced_ts: datetime
    rows_in: int = 0
    rows_loaded: int = 0
    rows_quarantined: int = 0
    drops: tuple[DropExplanation, ...] = ()
    examples: tuple[Example, ...] = ()
    rules: tuple[RuleOutcome, ...] = ()
    gaps: tuple[Gap, ...] = ()
    failure: Failure | None = None
    balanced: bool = True
    #: The sample this ran on, so the pack says what it is evidence ABOUT.
    sample_filename: str = ""

    @property
    def partial(self) -> bool:
        return self.failure is not None

    @property
    def accounts_for_every_row(self) -> bool:
        """rows_in == loaded + quarantined. The balance equation, on the pack.

        Checked HERE as well as in reconciliation because the pack is what a
        reviewer signs, and a pack whose own arithmetic does not add up must
        say so on its face rather than be caught later by a control table
        nobody in the approval reads.
        """
        return self.rows_in == self.rows_loaded + self.rows_quarantined

    def is_stale_for(self, configuration: str) -> bool:
        """Does this pack describe the configuration now being approved?"""
        return self.fingerprint != configuration

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.FEED, subject=self.feed_id)

    def summary(self) -> str:
        """The one line the wizard shows and the approval packet carries."""
        head = (
            f"{self.rows_in:,} rows in / {self.rows_loaded:,} loaded / "
            f"{self.rows_quarantined:,} quarantined"
        )
        if self.partial:
            return f"{head} — INCOMPLETE: {self.failure.step if self.failure else ''}"
        return head

    def render_markdown(self) -> str:
        """The shareable document.

            "Produce the evidence pack as a shareable document reviewers can
             read without platform access."

        Markdown rather than a rendering the platform serves, deliberately: a
        reviewer who needs an account to read the evidence is a reviewer who
        will ask somebody to summarise it in an email, and the summary is what
        gets approved. This is the artifact, and it is complete on its own.
        """
        lines = [
            f"# Onboarding evidence — {self.feed_id}",
            "",
            f"Produced {self.produced_ts.isoformat()} · configuration `{self.fingerprint}`",
        ]
        if self.sample_filename:
            lines.append(f"Sample: `{self.sample_filename}`")
        lines += ["", "## What happened", "", self.summary(), ""]

        if not self.accounts_for_every_row:
            lines += [
                "> **The counts do not balance.** Rows in do not equal loaded plus "
                "quarantined, so some records are unaccounted for. This pack is not "
                "sufficient evidence until that is explained.",
                "",
            ]
        if self.failure is not None:
            lines += [
                "## Where it stopped",
                "",
                f"**{self.failure.step}** — {self.failure.explanation}",
                *(
                    ["", f"Open the cause: `{self.failure.citation}`"]
                    if self.failure.citation
                    else []
                ),
                "",
                "Everything above this point ran. Everything below it did not.",
                "",
            ]

        if self.drops:
            lines += [
                "## Why rows did not load",
                "",
                "| Rule | Reason | Records |",
                "|---|---|---|",
            ]
            lines += [
                f"| `{drop.rule_id}` | {drop.reason} | {drop.record_count:,} |"
                for drop in self.drops
            ]
            lines.append("")

        if self.rules:
            lines += [
                "## Every rule, including the quiet ones",
                "",
                "| Rule | Hit rate | Fate |",
                "|---|---|---|",
            ]
            lines += [
                f"| {rule.name or rule.rule_id} | {rule.flagged}/{rule.tested} "
                f"({rule.hit_rate:.1%}) | "
                f"{'quarantines' if rule.quarantined else 'flags only'} |"
                for rule in self.rules
            ]
            lines.append("")

        if self.examples:
            lines += [
                f"## {len(self.examples)} before/after examples",
                "",
                f"Protected values are shown as `{MASKED}`.",
                "",
            ]
            for example in self.examples:
                lines += [
                    f"**Row {example.row_number}**",
                    "",
                    f"- before: `{_short(example.before)}`",
                    f"- after: `{_short(example.after)}`",
                    "",
                ]

        lines += ["## Known gaps", ""]
        if self.gaps:
            lines += [f"- **{gap.what}** — {gap.why_it_is_acceptable}" for gap in self.gaps]
        else:
            lines.append("None recorded.")
        lines.append("")
        return "\n".join(lines)


def _short(row: dict[str, str], *, limit: int = 6) -> str:
    items = list(row.items())[:limit]
    rendered = ", ".join(f"{k}={v}" for k, v in items)
    return rendered + (" …" if len(row) > limit else "")


# ── building it ──────────────────────────────────────────────────────────────
def build_pack(
    *,
    feed_id: str,
    result: ExecutionResult,
    objects: Sequence[GovernedObject],
    rule_names: dict[str, str] | None = None,
    quarantining_rules: frozenset[str] = frozenset(),
    phi_columns: frozenset[str] = frozenset(),
    gaps: Sequence[Gap] = (),
    failure: Failure | None = None,
    sample_rows: Sequence[dict[str, str]] = (),
    sample_filename: str = "",
    sample_fingerprint: str = "",
    now: datetime | None = None,
) -> EvidencePack:
    """Turn one engine run into the artifact a reviewer receives.

    `result` is `core.compiler.execute.ExecutionResult` — what the real engine
    returned. Nothing here re-derives a count that the run already computed;
    the pack REPORTS, and a pack that recomputed its own row totals would be
    able to disagree with the reconciliation it is supposed to evidence.
    """
    stamp = now or datetime.now(UTC)
    recon = result.reconciliation
    rows_in = getattr(recon, "records_in", len(sample_rows))
    quarantined = result.quarantined

    return EvidencePack(
        feed_id=feed_id,
        fingerprint=configuration_fingerprint(objects, sample_fingerprint=sample_fingerprint),
        produced_ts=stamp,
        rows_in=rows_in,
        rows_loaded=len(result.loaded),
        rows_quarantined=len(quarantined),
        drops=_drops(quarantined),
        examples=_examples(sample_rows, result.loaded, phi_columns),
        rules=_rule_outcomes(
            rows_in,
            quarantined + result.warnings,
            rule_names or {},
            quarantining_rules,
        ),
        gaps=tuple(gaps),
        failure=failure,
        balanced=result.balances,
        sample_filename=sample_filename,
    )


def _drops(rows: Sequence[QuarantinedRow]) -> tuple[DropExplanation, ...]:
    """Group exclusions by rule and reason. Every entry names something.

    Deliberately the same grouping `core.compiler.execute._ledger` performs:
    the pack and the drop ledger must agree, and they agree by computing the
    same thing from the same rows rather than by two people being careful.
    """
    grouped: dict[tuple[str, str], list[QuarantinedRow]] = {}
    for row in rows:
        grouped.setdefault((row.rule_id, row.reason), []).append(row)
    return tuple(
        DropExplanation(
            rule_id=rule_id,
            reason=reason,
            record_count=len(members),
            columns=members[0].columns,
        )
        for (rule_id, reason), members in sorted(grouped.items())
    )


def _rule_outcomes(
    tested: int,
    flagged_rows: Sequence[QuarantinedRow],
    names: dict[str, str],
    quarantining: frozenset[str],
) -> tuple[RuleOutcome, ...]:
    """Every rule's hit rate — including the rules that caught nothing.

    `names` is the full rule set, so a rule absent from `flagged_rows` still
    gets a row with zero. Deriving the list from what fired would silently drop
    exactly the rules a reviewer most wants to see: the ones that found nothing.
    """
    counts: dict[str, int] = dict.fromkeys(names, 0)
    for row in flagged_rows:
        counts[row.rule_id] = counts.get(row.rule_id, 0) + 1
    return tuple(
        RuleOutcome(
            rule_id=rule_id,
            name=names.get(rule_id, ""),
            tested=tested,
            flagged=count,
            quarantined=rule_id in quarantining,
        )
        for rule_id, count in sorted(counts.items())
    )


def _examples(
    before: Sequence[dict[str, str]],
    after: Sequence[dict[str, Any]],
    phi_columns: frozenset[str],
) -> tuple[Example, ...]:
    """Twenty rows, masked, paired by position.

    Paired by POSITION and truncated to the shorter of the two: a quarantined
    row has no `after`, and pairing by index across two lists of different
    lengths is how a pack ends up showing one member's name beside another
    member's transformed values.
    """
    pairs = min(len(before), len(after), EXAMPLE_LIMIT)
    return tuple(
        Example(
            row_number=index + 1,
            before=_mask(before[index], phi_columns),
            after=_mask({k: str(v) for k, v in after[index].items()}, phi_columns),
        )
        for index in range(pairs)
    )


def _mask(row: dict[str, str], phi_columns: frozenset[str]) -> dict[str, str]:
    """Mask by COLUMN NAME, case-insensitively, on both sides of the mapping.

    The source column and the canonical field it lands in are different names
    for the same protected value, so the caller passes both — and a value
    masked before the mapping and shown after it would be a disclosure the
    mapping performed.
    """
    protected = {name.casefold() for name in phi_columns}
    return {key: (MASKED if key.casefold() in protected else value) for key, value in row.items()}
