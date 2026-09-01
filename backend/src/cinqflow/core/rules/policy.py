"""CF-V1-E7-03 — a tested sentence becomes executable pipeline policy.

    "I want each approved rule to be configured with where it runs (Landing,
     Bronze, Silver Raw, Identity, Silver ODS), what happens on failure
     (Information → Warning → Manual review → Quarantine → Reject → Stop
     pipeline), thresholds, execution order and effective dates, so that a
     tested sentence becomes executable pipeline policy — the severity ladder
     is what separates 'interesting' from 'stop everything'."
    "Require test evidence to be attached before approval — no rule publishes
     untested."
    "Given a steward tries to publish a Stop-pipeline rule with no alert
     recipient, when they approve, then publication is blocked with the reason:
     a rule that can stop production must page a human."
    — CF-V1-E7-03

THE CONSEQUENCE IS A SECOND AXIS, NOT A RENAMING OF `Severity`. The story lists
six outcomes; `core.registry.contract.Severity` has four levels — Critical,
High, Medium, Low — harvested from the client's own sheet, where they are an
IMPORTANCE label written by the analyst who wrote the rule. The two are
different questions asked by different people at different times:

    severity     "how much does this matter?"        the author, once
    consequence  "what should the pipeline DO?"      the steward, per feed

Collapsing them looks tempting and is wrong in both directions. The same rule —
"member first name must be populated" — is Quarantine on a roster that feeds
outreach and Warning on a historical backfill nobody contacts. And two rules of
identical Critical severity can want different fates: one stops the pipeline,
one quarantines the row and lets the batch finish. So this is a SECOND AXIS,
exactly as CF-V1-E3-04 made pause a second axis rather than a lifecycle state,
and for the same reason: two facts in one field is one fact lost.

`Severity` is kept and USED — `default_consequence` reads it, so a steward
configuring 110 harvested rules starts from what their own analysts already
decided rather than from a blank ladder. What is refused is inferring the
consequence and never showing it.

THE LADDER IS ORDERED, AND THE ORDER IS LOAD-BEARING. `Consequence.rank` is
what lets the engine ask "is this at least as severe as Quarantine?" without
enumerating members, and what makes the escalation rule — a rule may be
introduced as Warning and HARDENED later, never quietly softened without a new
approval — expressible as a comparison.

WHAT MAKES THIS APPROVABLE IS WHAT IT REFUSES. Three gates, and each one is a
sentence from the story: no rule publishes untested; a rule that can stop
production must page a human; and a rule whose effective window has closed
before it opens would be approved into permanent silence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.model.vocabulary import Layer
from cinqflow.core.registry.contract import Severity
from cinqflow.core.rules import RuleError, RuleSpec


class PolicyError(RuleError):
    """A rule configuration the engine will not run from."""


class UntestedRuleError(PolicyError):
    """Approval attempted with no test evidence.

        "Require test evidence to be attached before approval — no rule
         publishes untested."

    Raised rather than warned. A rule nobody watched run is a rule whose author
    believes it does one thing and whose engine does another, and the batch it
    quarantines is the first place anybody finds out.
    """


class UnpagedStopError(PolicyError):
    """A Stop-pipeline rule with nobody to call.

        "a rule that can stop production must page a human"

    The story's own exception, and the only gate here that is about a PERSON
    rather than about data. A pipeline that stops at 02:00 with no recipient is
    an outage that begins when somebody notices in the morning.
    """


# ── where it runs ────────────────────────────────────────────────────────────
#: The layers a DQ rule may be configured to run at.
#:
#: Not every member of `Layer`. GOLD is out because Wave 1's spine stops at
#: Silver ODS and a policy naming a layer the engine does not reach would be
#: approved configuration that silently never executes — which is worse than
#: being refused, because it looks like protection on the feed profile.
RUNNABLE_LAYERS: tuple[Layer, ...] = (
    Layer.LANDING,
    Layer.BRONZE,
    Layer.SILVER_RAW,
    Layer.IDENTITY,
    Layer.SILVER_ODS,
)


# ── what happens on failure ──────────────────────────────────────────────────
@unique
class Consequence(StrEnum):
    """The six-rung ladder, in the story's own words and its own order.

    Read the gap between WARNING and MANUAL_REVIEW as the important one: below
    it nothing changes about the data or the batch, at and above it somebody's
    day changes. That is where a steward's attention should go, and it is why
    `changes_the_batch` and `needs_a_person` are separate properties rather
    than one `is_serious`.
    """

    #: Recorded and counted. Nobody is told; the trend is the point.
    INFORMATION = "information"
    #: Recorded and surfaced on the feed's DQ trend. The row loads.
    WARNING = "warning"
    #: The row loads and lands on somebody's queue. Used where the data may be
    #: right and only a human can tell — the client's `Reasonableness` rules.
    MANUAL_REVIEW = "manual_review"
    #: The row is held out with its reason and the batch continues. The
    #: canonical fate, and the one the drop ledger is built around.
    QUARANTINE = "quarantine"
    #: The row is refused outright — not held for reprocessing. Distinct from
    #: QUARANTINE because a quarantined row is recoverable and a rejected one
    #: is a record the platform declines to hold at all.
    REJECT = "reject"
    #: The batch stops before publishing anything downstream.
    STOP_PIPELINE = "stop_pipeline"

    @property
    def rank(self) -> int:
        """Position on the ladder. Ordered so severity can be COMPARED."""
        return list(Consequence).index(self)

    @property
    def changes_the_batch(self) -> bool:
        """At and above QUARANTINE, what loads is different."""
        return self.rank >= Consequence.QUARANTINE.rank

    @property
    def needs_a_person(self) -> bool:
        """At and above MANUAL_REVIEW, somebody's day changes."""
        return self.rank >= Consequence.MANUAL_REVIEW.rank

    @property
    def in_plain_language(self) -> str:
        """What the steward is shown AT SELECTION TIME.

            "Make the consequences of each severity level explicit in plain
             language at selection time."

        Not a tooltip and not a help page: the consequence of choosing
        Stop-pipeline has to be readable in the moment somebody chooses it,
        because that is the only moment they are thinking about it.
        """
        return {
            Consequence.INFORMATION: (
                "Counted only. The row loads, nobody is told, and the count appears on this "
                "feed's data-quality trend."
            ),
            Consequence.WARNING: (
                "The row loads and the failure is surfaced on the feed's data-quality trend. "
                "Use this to introduce a rule and watch what it would have caught."
            ),
            Consequence.MANUAL_REVIEW: (
                "The row loads AND lands on somebody's review queue. Choose this where the "
                "data may well be right and only a person can tell."
            ),
            Consequence.QUARANTINE: (
                "The row is held out of the load with its reason recorded, and the batch "
                "finishes. The row is recoverable and can be reprocessed after a fix."
            ),
            Consequence.REJECT: (
                "The row is refused outright and is NOT held for reprocessing. Choose this "
                "only where the record is one the platform should not hold at all."
            ),
            Consequence.STOP_PIPELINE: (
                "The batch STOPS before publishing anything downstream, and a named person "
                "is paged. Nothing further loads from this file until somebody acts."
            ),
        }[self]


#: What a harvested `Severity` means as a consequence, absent a steward's
#: decision. The client's own four levels, mapped once.
#:
#: Deliberately conservative at the top: their `Critical` becomes QUARANTINE,
#: not STOP_PIPELINE. Defaulting 38 of their 110 rules to "stop production"
#: would make the first real batch fail on a rule nobody chose to make
#: blocking — and a steward who has to soften 38 defaults stops reading them.
_DEFAULTS: dict[Severity, Consequence] = {
    Severity.CRITICAL: Consequence.QUARANTINE,
    Severity.HIGH: Consequence.QUARANTINE,
    Severity.MEDIUM: Consequence.WARNING,
    Severity.LOW: Consequence.INFORMATION,
}


def default_consequence(severity: Severity) -> Consequence:
    """Where a steward's ladder STARTS. Always shown, never silently applied."""
    return _DEFAULTS[severity]


# ── the policy ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RulePolicy:
    """One approved rule's execution policy: where, what, how much, when.

    Separate from `RuleSpec` on purpose. A spec is WHAT IS TRUE of the data and
    travels between feeds unchanged — the client's `DQ-002` means the same
    thing everywhere. A policy is what THIS feed does about it, and the same
    rule is Quarantine on a live roster and Warning on a backfill. Folding them
    together would mean cloning a rule to change its threshold, and the
    110-rule library would fork per feed within a month.
    """

    rule_id: str
    layer: Layer
    on_failure: Consequence
    #: The share of rows that may fail before the consequence escalates to
    #: stopping the batch. `None` means the consequence applies per row and
    #: never escalates — the correct answer for INFORMATION and WARNING.
    threshold_percent: Decimal | None = None
    #: Rules run in this order within a layer. Explicit, because a rule that
    #: rejects malformed dates must run before one that compares them, and
    #: leaving that to insertion order makes it depend on who typed first.
    execution_order: int = 100
    effective_from: date | None = None
    effective_to: date | None = None
    #: Required for STOP_PIPELINE. A named person, not a team alias — the same
    #: standard `core.registry.operations` holds owners to, and for the same
    #: reason: an alias is a group in which everyone assumes somebody else read
    #: the page.
    alert_recipient: str = ""
    owner: str = ""
    #: The steward's reason for THIS configuration. Not the rule's rationale —
    #: "why quarantine rather than warn on this feed" is a different sentence
    #: from "why this rule exists", and the second one is already on the spec.
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.layer not in RUNNABLE_LAYERS:
            runnable = ", ".join(layer.value for layer in RUNNABLE_LAYERS)
            raise PolicyError(
                f"{self.rule_id}: rules run at {runnable}. A policy naming {self.layer.value} "
                "would be approved configuration that never executes, which looks like "
                "protection on the feed profile and is not."
            )
        if self.threshold_percent is not None and not (
            Decimal("0") <= self.threshold_percent <= Decimal("100")
        ):
            raise PolicyError(
                f"{self.rule_id}: a threshold of {self.threshold_percent}% is not a share of "
                "the rows. Thresholds are 0-100."
            )
        if self.threshold_percent is not None and not self.on_failure.changes_the_batch:
            raise PolicyError(
                f"{self.rule_id}: a threshold on a {self.on_failure.value} rule changes "
                "nothing — the rows load either way, so the number would sit on the review "
                "screen implying a control that does not exist."
            )
        if self.execution_order < 0:
            raise PolicyError(f"{self.rule_id}: execution order starts at 0")
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise PolicyError(
                f"{self.rule_id}: the effective window closes ({self.effective_to}) before it "
                f"opens ({self.effective_from}), so the rule would never run. A rule approved "
                "into permanent silence is worse than one that was never approved, because "
                "the feed profile shows it as protection."
            )

    # ── when it runs ─────────────────────────────────────────────────────────
    def is_effective_on(self, day: date) -> bool:
        """Whether this policy governs a batch for `day`.

        Inclusive at both ends. A rule effective "to 31 August" that stopped on
        the 30th would be a boundary somebody has to remember, and the client's
        own rule sheet writes windows the way people say them.
        """
        if self.effective_from is not None and day < self.effective_from:
            return False
        return not (self.effective_to is not None and day > self.effective_to)

    @property
    def escalates(self) -> bool:
        """Whether breaching the threshold stops the batch."""
        return self.threshold_percent is not None

    def breaches(self, *, failed: int, tested: int) -> bool:
        """Did this batch cross the threshold?

        A zero-row test does NOT breach. Dividing by nothing and calling the
        result 100% would stop a pipeline because a file was empty, which is a
        landing-control finding with its own reason and its own owner.
        """
        if self.threshold_percent is None or tested <= 0:
            return False
        return (Decimal(failed) / Decimal(tested)) * 100 > self.threshold_percent

    def outcome(self, *, failed: int, tested: int) -> Consequence:
        """What actually happens to this batch — the configured consequence,
        escalated to STOP_PIPELINE if the threshold was breached."""
        if self.breaches(failed=failed, tested=tested):
            return Consequence.STOP_PIPELINE
        return self.on_failure

    def describe(self) -> str:
        """The line on the feed profile.

        "its configuration is visible on the feed profile"
        """
        window = ""
        if self.effective_from or self.effective_to:
            start = self.effective_from.isoformat() if self.effective_from else "always"
            end = self.effective_to.isoformat() if self.effective_to else "no end"
            window = f", effective {start} to {end}"
        threshold = (
            f", stopping the batch above {self.threshold_percent}% of rows"
            if self.threshold_percent is not None
            else ""
        )
        return (
            f"{self.rule_id} runs at {self.layer.value}; a failing row "
            f"{_FATE[self.on_failure]}{threshold}{window}."
        )


_FATE: dict[Consequence, str] = {
    Consequence.INFORMATION: "is counted",
    Consequence.WARNING: "is flagged and still loads",
    Consequence.MANUAL_REVIEW: "loads and goes to a review queue",
    Consequence.QUARANTINE: "is quarantined with its reason",
    Consequence.REJECT: "is rejected outright",
    Consequence.STOP_PIPELINE: "stops the batch",
}


# ── the three gates ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PolicyFinding:
    """One thing wrong, or worth knowing, about a rule's configuration.

    Three strings, like `ChecklistItem` and `MappingFinding`: a finding that
    only names a field gets a placeholder typed into it.
    """

    key: str
    rule_id: str
    what: str
    why_it_matters: str
    how_to_fix: str
    blocks: bool = True


def findings_for(
    policies: Sequence[RulePolicy],
    *,
    evidence: dict[str, Any] | None = None,
) -> tuple[PolicyFinding, ...]:
    """Everything standing between this rule set and an approval.

    ONE function, called by the screen and by the gate. That is what stops the
    configuration form showing green while approve returns 409 — the classic
    shape of a validation rule implemented twice, and the reason
    `core.registry.operations.readiness` is written the same way.
    """
    tested = _tested_rule_ids(evidence)
    findings: list[PolicyFinding] = []

    for policy in policies:
        if policy.on_failure is Consequence.STOP_PIPELINE and not policy.alert_recipient.strip():
            findings.append(
                PolicyFinding(
                    key="unpaged_stop",
                    rule_id=policy.rule_id,
                    what="This rule can stop production and names nobody to call.",
                    why_it_matters=(
                        "A pipeline that stops at 02:00 with no recipient is an outage that "
                        "begins when somebody notices in the morning."
                    ),
                    how_to_fix=(
                        "Name a person — not a team alias — as the alert recipient, or "
                        "choose a consequence below Stop-pipeline."
                    ),
                )
            )
        if policy.rule_id not in tested:
            findings.append(
                PolicyFinding(
                    key="untested",
                    rule_id=policy.rule_id,
                    what="No test evidence is attached for this rule.",
                    why_it_matters=(
                        "A rule nobody watched run is a rule whose author believes it does "
                        "one thing and whose engine does another. The batch it quarantines "
                        "is the first place anybody finds out."
                    ),
                    how_to_fix="Run it against the sample file and save the result.",
                )
            )
        elif policy.on_failure.changes_the_batch and _fired_nothing(evidence, policy.rule_id):
            # ADVISORY, deliberately. A rule that caught nothing on a
            # representative sample is either protecting against something rare
            # or is wrong, and only a human can tell which — so this is shown
            # and does not refuse.
            findings.append(
                PolicyFinding(
                    key="never_fired",
                    rule_id=policy.rule_id,
                    what=(
                        "This rule would quarantine or reject rows, and it caught nothing "
                        "on the sample."
                    ),
                    why_it_matters=(
                        "That is either a rule protecting against something rare, or a rule "
                        "that does not do what its sentence says. The sample cannot tell "
                        "you which."
                    ),
                    how_to_fix=(
                        "Check the read-back against your sentence, or introduce it as "
                        "Warning first and harden it once you have seen a month of trend."
                    ),
                    blocks=False,
                )
            )

    findings.extend(_ordering_findings(policies))
    return tuple(findings)


def blocking(findings: Sequence[PolicyFinding]) -> tuple[PolicyFinding, ...]:
    return tuple(finding for finding in findings if finding.blocks)


def _ordering_findings(policies: Sequence[RulePolicy]) -> list[PolicyFinding]:
    """Two rules with the same execution order at the same layer.

    Which one runs first would then depend on insertion order — and since the
    engine attributes a dropped row to the FIRST failing rule, the reason on a
    quarantined row would be decided by something nobody approved.
    """
    seen: dict[tuple[str, int], list[str]] = {}
    for policy in policies:
        seen.setdefault((policy.layer.value, policy.execution_order), []).append(policy.rule_id)
    return [
        PolicyFinding(
            key="ambiguous_order",
            rule_id=rule_ids[0],
            what=(f"{', '.join(rule_ids)} all run at {layer} in position {order}."),
            why_it_matters=(
                "A dropped row is attributed to the FIRST rule that failed it, so the reason "
                "on a quarantined row would be decided by insertion order rather than by "
                "anything anyone approved."
            ),
            how_to_fix="Give each rule at this layer its own execution order.",
        )
        for (layer, order), rule_ids in sorted(seen.items())
        if len(rule_ids) > 1
    ]


def _tested_rule_ids(evidence: dict[str, Any] | None) -> frozenset[str]:
    """Which rules have a preview in the stored evidence.

    Reads CF-V1-E7-02's `evidence_pack` shape. A preview that could NOT run
    does not count as tested: `not_previewable` is exactly the case where the
    platform declined to say what the rule does, and treating it as evidence
    would let the one rule nobody could check be the one that publishes.
    """
    if not evidence:
        return frozenset()
    return frozenset(
        str(preview.get("rule_id"))
        for preview in evidence.get("previews", ())
        if isinstance(preview, dict)
        and preview.get("rule_id")
        and not preview.get("not_previewable")
    )


def _fired_nothing(evidence: dict[str, Any] | None, rule_id: str) -> bool:
    if not evidence:
        return False
    for preview in evidence.get("previews", ()):
        if isinstance(preview, dict) and preview.get("rule_id") == rule_id:
            return int(preview.get("failed", 0)) == 0 and int(preview.get("tested", 0)) > 0
    return False


def refuse_unapprovable(
    policies: Sequence[RulePolicy], *, evidence: dict[str, Any] | None = None
) -> None:
    """The gate the approval path calls. Raises the story's own two exceptions.

    Ordered so the message is the one the steward can act on: a missing alert
    recipient is a decision they make in the moment, and missing test evidence
    sends them back to the BA — so the recipient is reported first when both
    are true.
    """
    problems = blocking(findings_for(policies, evidence=evidence))
    if not problems:
        return
    unpaged = [p for p in problems if p.key == "unpaged_stop"]
    if unpaged:
        raise UnpagedStopError(
            f"{unpaged[0].rule_id}: a rule that can stop production must page a human. "
            f"{unpaged[0].how_to_fix}"
        )
    untested = [p for p in problems if p.key == "untested"]
    if untested:
        raise UntestedRuleError(
            f"{untested[0].rule_id} has no test evidence attached. No rule publishes "
            "untested — run it against the sample file and save the result."
        )
    raise PolicyError("\n".join(f"{p.rule_id}: {p.what} {p.how_to_fix}" for p in problems))


def refuse_silent_softening(
    before: Sequence[RulePolicy], after: Sequence[RulePolicy]
) -> tuple[str, ...]:
    """Which rules got LESS strict between two versions.

    Not a refusal — a hardening path is the story's own point ("introduced as
    Warning and hardened later"), and the reverse is sometimes right too. What
    must not happen is a softening that nobody NOTICED, so this returns the
    list for the approval packet's diff to show, in the same spirit as
    CF-V1-E6-04's acknowledged loss: the approver has to read the names.
    """
    was = {policy.rule_id: policy.on_failure for policy in before}
    return tuple(
        sorted(
            policy.rule_id
            for policy in after
            if policy.rule_id in was and policy.on_failure.rank < was[policy.rule_id].rank
        )
    )


# ── policies on the rule set's governed body ─────────────────────────────────
#
# On the DQ_RULE object rather than in an object type of their own. The spec
# and its policy are approved by the same steward in the same act — "the rule
# publishes with version 1, the engine applies it on the next batch" — and two
# governed objects would mean two approvals, two versions to correlate, and a
# window in which a published rule has no policy or a policy no rule.
#
# The key sits BESIDE `rules`, so `core.rules.rule_body` is untouched and a
# rule set written before this story reads back with no policies rather than
# failing — which is the additive-upgrade behaviour every other body key has.

POLICIES_KEY = "policies"


def policy_to_dict(policy: RulePolicy) -> dict[str, Any]:
    return {
        "rule_id": policy.rule_id,
        "layer": policy.layer.value,
        "on_failure": policy.on_failure.value,
        "threshold_percent": (
            str(policy.threshold_percent) if policy.threshold_percent is not None else None
        ),
        "execution_order": policy.execution_order,
        "effective_from": policy.effective_from.isoformat() if policy.effective_from else None,
        "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
        "alert_recipient": policy.alert_recipient,
        "owner": policy.owner,
        "rationale": policy.rationale,
        # DERIVED, and written anyway — so a person reading the stored JSON, or
        # a report listing what a feed enforces, does not have to re-implement
        # the phrasing rules to say what a policy does.
        "describes": policy.describe(),
    }


def policy_from_dict(raw: dict[str, Any]) -> RulePolicy:
    threshold = raw.get("threshold_percent")
    return RulePolicy(
        rule_id=str(raw.get("rule_id", "")),
        layer=Layer(raw.get("layer", Layer.SILVER_RAW.value)),
        on_failure=Consequence(raw.get("on_failure", Consequence.WARNING.value)),
        threshold_percent=Decimal(str(threshold)) if threshold is not None else None,
        execution_order=int(raw.get("execution_order", 100)),
        effective_from=_day(raw.get("effective_from")),
        effective_to=_day(raw.get("effective_to")),
        alert_recipient=str(raw.get("alert_recipient", "")),
        owner=str(raw.get("owner", "")),
        rationale=str(raw.get("rationale", "")),
    )


def _day(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def policies_from_body(body: dict[str, Any]) -> tuple[RulePolicy, ...]:
    """Read a feed's configured policies, or none.

    An empty answer for a rule set stored before this story existed. That is
    the correct reading: a rule with no policy is not yet executable pipeline
    policy, and `runnable_at` will simply not return it.
    """
    raw = body.get(POLICIES_KEY) or ()
    return tuple(policy_from_dict(entry) for entry in raw if isinstance(entry, dict))


def with_policies(body: dict[str, Any], policies: Sequence[RulePolicy]) -> dict[str, Any]:
    """The body with its policies replaced. Never mutated in place — a governed
    body is amended by making the next version, not by editing this one."""
    return {**body, POLICIES_KEY: [policy_to_dict(policy) for policy in policies]}


# ── what the engine asks ─────────────────────────────────────────────────────
def runnable_at(
    specs: Sequence[RuleSpec],
    policies: Sequence[RulePolicy],
    *,
    layer: Layer,
    business_day: date | None = None,
) -> tuple[tuple[RuleSpec, RulePolicy], ...]:
    """The rules the engine runs at one layer on one day, in execution order.

    A spec with no policy is NOT returned. That is the whole of "a tested
    sentence becomes executable pipeline policy": the sentence alone does not
    run, because nobody has said where it runs or what happens when it fails.

    Ordered by `execution_order` then `rule_id`, so the tie that
    `_ordering_findings` refuses to let reach approval is still deterministic
    if one somehow does — a non-deterministic engine is a worse failure than a
    badly ordered one.
    """
    day = business_day or datetime.now(UTC).date()
    by_id = {policy.rule_id: policy for policy in policies}
    paired = [
        (spec, by_id[spec.rule_id])
        for spec in specs
        if spec.rule_id in by_id
        and by_id[spec.rule_id].layer is layer
        and by_id[spec.rule_id].is_effective_on(day)
    ]
    return tuple(sorted(paired, key=lambda pair: (pair[1].execution_order, pair[1].rule_id)))


@dataclass(frozen=True)
class LayerOutcome:
    """What one layer's rules did to one batch, and what the batch does next."""

    layer: Layer
    tested: int = 0
    results: tuple[tuple[str, Consequence, int], ...] = field(default_factory=tuple)

    @property
    def stops_the_batch(self) -> bool:
        """ "a threshold breach behaves identically every time" — and the batch
        stops before publishing anything downstream."""
        return any(outcome is Consequence.STOP_PIPELINE for _, outcome, _ in self.results)

    @property
    def stopped_by(self) -> tuple[str, ...]:
        return tuple(
            rule_id for rule_id, outcome, _ in self.results if outcome is Consequence.STOP_PIPELINE
        )

    def explain(self) -> str:
        if not self.stops_the_batch:
            return (
                f"{self.layer.value}: {len(self.results)} rule(s) evaluated; the batch continues."
            )
        named = ", ".join(self.stopped_by)
        return (
            f"{self.layer.value}: the batch stopped before publishing anything downstream — "
            f"{named} breached its threshold."
        )


def evaluate_layer(
    policies: Sequence[RulePolicy],
    *,
    layer: Layer,
    failures: dict[str, int],
    tested: int,
) -> LayerOutcome:
    """Turn per-rule failure counts into the layer's verdict.

    `failures` is what the engine counted; this decides what it MEANS. Keeping
    the arithmetic here rather than in the executor is what lets the evidence
    pack, the operations screen and the pipeline agree about whether a batch
    should have stopped — they call this, rather than each re-deriving it.
    """
    return LayerOutcome(
        layer=layer,
        tested=tested,
        results=tuple(
            (
                policy.rule_id,
                policy.outcome(failed=failures.get(policy.rule_id, 0), tested=tested),
                failures.get(policy.rule_id, 0),
            )
            for policy in sorted(policies, key=lambda p: (p.execution_order, p.rule_id))
            if policy.layer is layer
        ),
    )
