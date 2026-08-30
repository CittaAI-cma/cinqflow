"""CF-V2-E12-04 — the 3 AM question changes from 'what is this?' to 'apply the
known fix?'.

    "I want every failure to be automatically clustered into an incident,
     matched against the library of known failure fingerprints, and presented
     with the matching recovery guide, prior occurrence count and average fix
     time, so that repair time for known failure classes drops from days of
     archaeology to minutes."
    "Claim a match without showing the fingerprint evidence."
    "Auto-apply any fix in this story."
    — CF-V2-E12-04, and two of its don'ts

THE FINGERPRINT IS COMPUTED, NOT INFERRED. That is the whole architecture of
this story, and it is the same decision `core/profiling` made: facts by
computation, never by guess. A model asked "have we seen this before?" will
answer yes with a plausible guide attached, and the ≥95% precision gate would
be measuring how confidently it invents. So the SIGNATURE of a failure is a
deterministic normalisation of the error the engine already logged, the match
against the library is an exact lookup on that signature, and the model is
never asked whether two failures are the same.

What the model IS asked — in `core.agents.failure_fingerprint` — is the
question computation cannot answer: what to say to a human about a failure that
matched NOTHING. That is the story's exception, it is R0 (it explains, it never
proposes a fix for an unmatched failure), and it is the only place a model
appears in this feature.

NORMALISATION IS WHERE PRECISION LIVES, AND OVER-COLLAPSING IS THE DANGEROUS
DIRECTION. Two errors must share a signature when the same fix applies, and
must not otherwise. So the volatile parts go — batch ids, timestamps, row
counts, paths, quoted values — and the SHAPE stays:

    "evaluate_bronze_load: required key 'business_date' absent in XCom
     from upstream validate_input"
                              ↓
    "evaluate_bronze_load: required key <v> absent in xcom from upstream
     validate_input"

The quoted key becomes `<v>` deliberately: a missing `business_date` and a
missing `run_date` are the same failure class with the same fix, and splitting
them would make the library's "14 prior occurrences" read as two counts of
seven. The TASK NAMES stay, because `evaluate_bronze_load` failing and
`resolve_identity` failing are different problems for different people.

A MATCH SHOWS ITS WORK OR IT IS NOT A MATCH. `GuideMatch` carries the signature
that matched, the error rows it was computed from, and the prior incidents it
counted — because the don't says so, and because an operator at 3 AM applying a
guide on the platform's say-so is the exact failure mode that made the
incumbent's "known fixes" folder untrustworthy.

NOTHING HERE EXECUTES ANYTHING. There is no `apply`, no `run`, no `fix`. The
proposed remedy is an `OpsAction` and a target — a thing the operator presses
on CF-V2-E12-03's surface, with its allowed-state matrix, its approval
identifier and its verify-after-execute. A one-click fix that ran from here
would be a second path around all of that.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import ErrorCategory, Layer, StatusWord
from cinqflow.core.operations.actions import OpsAction
from cinqflow.core.operations.monitor import Cascade, ErrorLike, ErrorView, separate_cascade


class FingerprintError(RuntimeError):
    """A fingerprint that could not be computed from what it was given."""


# ── normalising an error message ─────────────────────────────────────────────
#
# Ordered, and the order matters: the quoted-value rule must run before the
# bare-number rule, or `'2026-08-30'` loses its quotes and then its digits and
# stops looking like one value.

_VOLATILE: tuple[tuple[re.Pattern[str], str], ...] = (
    # Quoted values — the parameter, the column, the code. The SHAPE of the
    # failure is "a required key was absent", not which key.
    (re.compile(r"'[^']*'"), "<v>"),
    (re.compile(r'"[^"]*"'), "<v>"),
    # UUIDs and hex digests — batch ids, error hashes, fingerprints.
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<id>"),
    (re.compile(r"\bsha256-[0-9a-f]+\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{16,}\b"), "<id>"),
    # Timestamps and dates, before bare numbers eat their pieces.
    # `[Tt ]` because `normalise` lowercases FIRST — matching only `T` here
    # silently stopped recognising every ISO timestamp in the estate, and the
    # bare-number rule then ate the pieces one at a time.
    (re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(:\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<date>"),
    # Paths and URIs — environment difference, which never identifies a class.
    (re.compile(r"\b\w+://\S+"), "<uri>"),
    (re.compile(r"(?<![\w<])/[\w./-]{2,}"), "<path>"),
    # Row counts, byte sizes, line numbers.
    (re.compile(r"\b\d[\d,_]*(\.\d+)?\b"), "<n>"),
)

_WHITESPACE = re.compile(r"\s+")

#: How many tokens of the normalised message take part in the signature.
#:
#: A cap, because stack traces and SQL echoes append unbounded detail that
#: varies between runs of the same failure — and a signature that included all
#: of it would give every occurrence its own fingerprint, which is the failure
#: mode that makes a library useless while looking full.
SIGNATURE_TOKENS = 24


def normalise(message: str) -> str:
    """Strip what varies between two occurrences of the same failure.

    Public because it is the most reviewable part of this module: an engineer
    tuning precision reads this function and its tests, not a hash.
    """
    text = message.strip().lower()
    for pattern, placeholder in _VOLATILE:
        text = pattern.sub(placeholder, text)
    return _WHITESPACE.sub(" ", text).strip()


def signature(
    *, stage: Layer, category: ErrorCategory, message: str, rule_id: str | None = None
) -> str:
    """The deterministic signature of one failure.

    Stage and category are part of it: the same words at Bronze and at Silver
    ODS are different problems for different people, and a signature over the
    message alone would merge them.

    `rule_id` is included when present and is the STRONGEST component — a DQ
    rule failing is precisely identified by which rule, and the message is
    decoration.
    """
    tokens = normalise(message).split()[:SIGNATURE_TOKENS]
    material = "|".join([stage.value, category.value, rule_id or "-", " ".join(tokens)])
    return "fp-" + hashlib.sha256(material.encode()).hexdigest()[:16]


# ── the library ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PriorIncident:
    """One time this failure happened before, and how long it took to fix.

    `fix_minutes` is None when an incident closed without anyone recording the
    duration. Counted in the occurrence total and EXCLUDED from the mean —
    averaging over a missing number is how "mean fix 18 minutes" becomes a
    figure nobody trusts twice.
    """

    incident_id: str
    occurred_ts: datetime
    fix_minutes: int | None = None
    batch_id: str | None = None

    @property
    def citation(self) -> CitationId:
        return (
            CitationId(kind=CitationKind.BATCH, subject=self.batch_id)
            if self.batch_id
            else CitationId(kind=CitationKind.ERROR, subject=self.incident_id)
        )


@dataclass(frozen=True)
class RecoveryGuide:
    """A known failure class and what to do about it.

    A governed RUNBOOK in the registry (CF-V2-E16-07 keeps them current); this
    is the shape the matcher works with. `steps` are prose for a human — there
    is deliberately no executable field, for the reason `core.mapping` carries
    no expression: a guide a machine could run is a guide nobody reviews.
    """

    guide_id: str
    title: str
    #: Every signature this guide covers. A guide legitimately covers several —
    #: the same fix applies to the missing-key failure at three different
    #: tasks — and listing them is what keeps the match an exact lookup rather
    #: than a similarity score.
    signatures: frozenset[str] = frozenset()
    steps: tuple[str, ...] = ()
    #: Proposed remedy, expressed as something the ACTION SURFACE can do.
    #: `None` where the fix is human work with no platform action behind it.
    remedy: OpsAction | None = None
    is_transient: bool = False
    #: A guide whose linked feed has retired is flagged in the very alert that
    #: cites it — the incident library's own rule.
    stale: bool = False

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.RULE, subject=self.guide_id)


@dataclass(frozen=True)
class GuideMatch:
    """A matched guide, with the evidence that justifies the claim.

        "Cite every piece of evidence — the error rows, the matched
         fingerprint, the prior incidents."
        "Claim a match without showing the fingerprint evidence." — the don't

    `__post_init__` refuses a match with no evidence, which is the don't made
    unbuildable rather than reviewed for.
    """

    guide: RecoveryGuide
    signature: str
    matched_errors: tuple[str, ...] = ()
    priors: tuple[PriorIncident, ...] = ()

    def __post_init__(self) -> None:
        if not self.signature.strip():
            raise FingerprintError(
                f"{self.guide.guide_id} was matched with no fingerprint. A claim with no "
                "evidence is what made the incumbent's known-fixes folder untrustworthy."
            )
        if not self.matched_errors:
            raise FingerprintError(
                f"{self.guide.guide_id} was matched against no error rows. The evidence IS "
                "the error, and a match without it cannot be checked by the person applying "
                "it at 3 AM."
            )

    @property
    def occurrences(self) -> int:
        return len(self.priors)

    @property
    def mean_fix_minutes(self) -> int | None:
        """The average, over incidents that recorded one.

        None rather than zero when nobody recorded a duration: zero reads as
        "instant" on a screen, and an operator planning their morning around a
        zero-minute fix has been told something false.
        """
        recorded = [p.fix_minutes for p in self.priors if p.fix_minutes is not None]
        return round(sum(recorded) / len(recorded)) if recorded else None

    @property
    def citations(self) -> tuple[CitationId, ...]:
        """Everything a reviewer can open. The guide, then the priors."""
        return (self.guide.citation, *(prior.citation for prior in self.priors))

    def summary(self) -> str:
        """The story's own sentence, verbatim: '14 prior occurrences, mean fix
        18 minutes'.

        Singular and plural are handled rather than parenthesised, because '1
        prior occurrence(s)' on an incident card reads as a machine nobody
        proofread — and this is the line an operator sees at 3 AM, when their
        confidence in the platform is being decided.
        """
        occurrence = "occurrence" if self.occurrences == 1 else "occurrences"
        mean = (
            f"mean fix {self.mean_fix_minutes} minutes"
            if self.mean_fix_minutes is not None
            else "no fix duration recorded"
        )
        stale = " · GUIDE MARKED STALE" if self.guide.stale else ""
        return f"{self.occurrences} prior {occurrence}, {mean}{stale}"

    def explain(self) -> str:
        """The same sentence, with the guide named — for the incident header."""
        stale = (
            " (this guide's feed has retired — check it still applies)" if self.guide.stale else ""
        )
        return f"{self.guide.guide_id} — {self.guide.title}. {self.summary()}.{stale}"


# ── the incident ─────────────────────────────────────────────────────────────
@unique
class IncidentKind(StrEnum):
    """Whether the platform recognised this failure. Two, and the second is an
    honest answer rather than a degraded one."""

    KNOWN = "known"
    NOVEL = "novel"

    @property
    def status_word(self) -> StatusWord:
        return StatusWord.NEEDS_ATTENTION


@unique
class IncidentState(StrEnum):
    """AN INCIDENT IS NOT A GOVERNED OBJECT, and this machine is why it needs
    its own.

    ADR-0006's one lifecycle governs CONFIGURATION — things authored, reviewed
    and published. An incident is an operational fact: it happened. Pushing it
    through Draft → In Review → Approved would require somebody to approve that
    a batch failed.

    What DOES travel the governed lifecycle is the RUNBOOK an incident
    produces, which is exactly why CF-V2-E16-07 says only CLOSED narratives
    become knowledge: the incident closes, and a governed object begins.
    """

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CLOSED = "closed"


_TRANSITIONS: dict[IncidentState, frozenset[IncidentState]] = {
    IncidentState.OPEN: frozenset({IncidentState.ACKNOWLEDGED, IncidentState.RESOLVED}),
    IncidentState.ACKNOWLEDGED: frozenset({IncidentState.RESOLVED}),
    IncidentState.RESOLVED: frozenset({IncidentState.CLOSED}),
    IncidentState.CLOSED: frozenset(),
}


class IncidentTransitionError(RuntimeError):
    """A move the incident's state machine does not permit."""


@dataclass(frozen=True)
class Incident:
    """One failure, clustered, matched and presented.

    ONE incident per batch, not one per error. "Three errors logged; two are
    consequences of the first" is the story's own sentence, and the clustering
    that makes it true is `core.operations.monitor.separate_cascade` — imported
    rather than repeated, so the monitor and the incident can never count the
    same batch differently.
    """

    incident_id: str
    batch_id: str
    feed_id: str
    opened_ts: datetime
    cascade: Cascade = field(default_factory=Cascade)
    signature: str = ""
    match: GuideMatch | None = None
    #: The operational machine. Immutable like everything else here: a
    #: transition returns a NEW incident, so "what did this look like when it
    #: was acknowledged" is a fact the ledger can hold rather than a version
    #: somebody overwrote.
    state: IncidentState = IncidentState.OPEN
    acknowledged_by: str = ""
    assigned_to: str = ""
    resolution: str = ""
    resolved_ts: datetime | None = None

    @property
    def kind(self) -> IncidentKind:
        return IncidentKind.KNOWN if self.match else IncidentKind.NOVEL

    @property
    def status(self) -> StatusWord:
        return self.kind.status_word

    @property
    def root_cause(self) -> ErrorView | None:
        return self.cascade.first

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=CitationKind.BATCH, subject=self.batch_id)

    @property
    def proposed_remedy(self) -> OpsAction | None:
        """What the operator would press — on the ACTION SURFACE, not here.

        None for a novel failure and for a guide whose fix is human work. A
        remedy offered without a matched guide would be the auto-apply the
        don't refuses, one indirection removed.
        """
        return self.match.guide.remedy if self.match else None

    def evidence_bundle(self) -> dict[str, object]:
        """Everything a human needs, organised.

            "presents the evidence bundle organized for a human"

        Built for BOTH kinds. A novel failure gets the same bundle a known one
        does minus the guide — which is the story's exception: the platform
        says honestly that it does not recognise this, and then hands over
        everything it does know rather than an empty screen.
        """
        root = self.root_cause
        return {
            "incident_id": self.incident_id,
            "batch_id": self.batch_id,
            "feed_id": self.feed_id,
            "kind": self.kind.value,
            "signature": self.signature,
            "root_cause": (
                {
                    "error_id_hash": root.error_id_hash,
                    "stage": root.stage.value,
                    "category": root.category.value,
                    "message": root.message,
                    "rule_id": root.rule_id,
                }
                if root
                else None
            ),
            "consequences": [
                {"error_id_hash": e.error_id_hash, "message": e.message}
                for e in self.cascade.consequences
            ],
            "other_actionable": [
                {"error_id_hash": e.error_id_hash, "message": e.message}
                for e in self.cascade.actionable[1:]
            ],
            "guide": self.match.guide.guide_id if self.match else None,
            "prior_occurrences": self.match.occurrences if self.match else 0,
            "mean_fix_minutes": self.match.mean_fix_minutes if self.match else None,
            "citations": [str(c) for c in (self.match.citations if self.match else ())],
        }

    def explain(self) -> str:
        if not self.root_cause:
            return f"{self.incident_id}: no errors were logged for {self.batch_id}."
        head = (
            f"{self.incident_id} on {self.batch_id} — {self.cascade.explain()} "
            f"Root cause at {self.root_cause.stage.value}: {self.root_cause.message}"
        )
        if self.match:
            return f"{head}\n{self.match.explain()}"
        return (
            f"{head}\nThis failure matches nothing in the recovery library. The evidence "
            "below is everything the platform knows about it; when you resolve it, the "
            "resolution can be saved as a new draft guide."
        )

    # ── the operational machine ──────────────────────────────────────────────
    def _moved(self, target: IncidentState, **changes: object) -> Incident:
        permitted = _TRANSITIONS[self.state]
        if target not in permitted:
            allowed = ", ".join(sorted(state.value for state in permitted)) or "nothing (terminal)"
            raise IncidentTransitionError(
                f"incident {self.incident_id} cannot go {self.state.value} -> {target.value}. "
                f"Permitted: {allowed}."
            )
        return replace(self, state=target, **changes)  # type: ignore[arg-type]

    def acknowledge(self, *, by: str, assigned_to: str = "") -> Incident:
        if not by.strip():
            raise IncidentTransitionError(
                "an acknowledgement names a person, or it is not an acknowledgement"
            )
        return self._moved(
            IncidentState.ACKNOWLEDGED,
            acknowledged_by=by.strip(),
            assigned_to=assigned_to.strip() or self.assigned_to,
        )

    def resolve(self, *, resolution: str, at: datetime) -> Incident:
        """Resolution text is REQUIRED, and CF-V2-E16-07 starts here.

        The narrative embedded on close is this string plus the evidence. A
        resolution nobody wrote is a guide nobody can retrieve — which is why
        the loop's median-lag measurement is taken from this moment.
        """
        if not resolution.strip():
            raise IncidentTransitionError(
                f"incident {self.incident_id} needs a resolution that says what was done. "
                "It becomes the narrative the next matching incident retrieves, and an "
                "empty one teaches nothing."
            )
        return self._moved(IncidentState.RESOLVED, resolution=resolution.strip(), resolved_ts=at)

    def close(self) -> Incident:
        """Closing is what makes the narrative embeddable.

        "Embed an unresolved incident's speculation — only closed
         narratives become knowledge."
        — CF-V2-E16-07's don't
        """
        return self._moved(IncidentState.CLOSED)

    def duration(self) -> timedelta | None:
        return None if self.resolved_ts is None else self.resolved_ts - self.opened_ts

    @property
    def embeddable(self) -> bool:
        """The gate CF-V2-E16-07's write side asks before it embeds anything."""
        return self.state is IncidentState.CLOSED and bool(self.resolution)

    def narrative(self) -> str:
        """The chunk the knowledge loop embeds.

        FACTS AND THE HUMAN'S RESOLUTION — never a model's speculation, which
        is not stored on the incident at all. That is what makes the loop safe
        to run automatically: there is nothing here a model wrote.
        """
        if not self.embeddable:
            raise IncidentTransitionError(
                f"incident {self.incident_id} is {self.state.value} — only closed narratives "
                "become knowledge"
            )
        root = self.root_cause
        took = self.duration()
        lines = [
            f"# Incident {self.incident_id} · feed {self.feed_id} · batch {self.batch_id}",
            f"Signature: {self.signature}",
        ]
        if root is not None:
            lines.append(
                f"Root cause ({root.category.value} at {root.stage.value}): {root.message}"
            )
        lines.append(self.cascade.explain())
        lines.append(f"Resolution: {self.resolution}")
        if took is not None:
            lines.append(f"Time to resolve: {int(took.total_seconds() // 60)} minutes.")
        return "\n".join(lines)


def _incident_id(batch_id: str, signature: str) -> str:
    """Content-addressed, so re-running fingerprinting on the same batch does
    not manufacture a second incident.

    The same discipline `ErrorRecord.error_id_hash` uses — and the reason
    "reprocessing a corrected batch cannot manufacture duplicate incidents" is
    true here too.
    """
    digest = hashlib.sha256(f"{batch_id}|{signature}".encode()).hexdigest()[:12]
    return f"INC-{digest}"


def fingerprint_batch(
    *,
    batch_id: str,
    feed_id: str,
    errors: Sequence[ErrorLike],
    guides: Sequence[RecoveryGuide] = (),
    history: Sequence[PriorIncident] = (),
    now: datetime | None = None,
) -> Incident:
    """Cluster one batch's errors, compute the signature, match the library.

    ENTIRELY DETERMINISTIC. No model is called and none can be: the same errors
    produce the same incident id, the same signature and the same match on
    every machine, which is what makes the ≥95% precision gate a measurement of
    the normalisation rather than of a model's mood.

    The signature is computed from the FIRST ACTIONABLE error only. Computing
    it over all three would make the same failure fingerprint differently
    depending on how many downstream tasks happened to fail behind it — which
    is exactly how a library ends up full and useless.
    """
    stamp = now or datetime.now(UTC)
    cascade = separate_cascade(errors)
    root = cascade.first
    if root is None:
        return Incident(
            incident_id=_incident_id(batch_id, ""),
            batch_id=batch_id,
            feed_id=feed_id,
            opened_ts=stamp,
            cascade=cascade,
        )

    found = signature(
        stage=root.stage,
        category=root.category,
        message=root.message,
        rule_id=root.rule_id,
    )
    return Incident(
        incident_id=_incident_id(batch_id, found),
        batch_id=batch_id,
        feed_id=feed_id,
        opened_ts=stamp,
        cascade=cascade,
        signature=found,
        match=match_guide(found, guides, history=history, errors=cascade.all),
    )


def match_guide(
    found: str,
    guides: Sequence[RecoveryGuide],
    *,
    history: Sequence[PriorIncident] = (),
    errors: Sequence[ErrorView] = (),
) -> GuideMatch | None:
    """An EXACT lookup on the signature. Never a similarity score.

    A threshold here would be a knob somebody tunes until the demo matches,
    and the ≥95% precision gate would then measure the knob. If two failures
    should match, they should normalise to the same signature — and if they do
    not, the fix is in `normalise`, where an engineer can read it.

    Returns None rather than a low-confidence match. "This matches nothing" is
    the story's own exception and a genuinely useful answer; a 40% match is
    neither.
    """
    for guide in guides:
        if found in guide.signatures:
            return GuideMatch(
                guide=guide,
                signature=found,
                matched_errors=tuple(e.error_id_hash for e in errors) or ("-",),
                priors=tuple(sorted(history, key=lambda p: p.occurred_ts)),
            )
    return None


def library_from(
    guides: Sequence[RecoveryGuide], incidents: Sequence[Incident]
) -> dict[str, tuple[PriorIncident, ...]]:
    """Prior occurrences per signature, from incidents already closed.

    Grouped by SIGNATURE rather than by guide, because a guide covering three
    signatures should report "14 prior occurrences of THIS one" — a count over
    the whole guide would tell an operator that a failure they have never seen
    has happened fourteen times.
    """
    grouped: dict[str, list[PriorIncident]] = {}
    for incident in incidents:
        if not incident.signature:
            continue
        grouped.setdefault(incident.signature, []).append(
            PriorIncident(
                incident_id=incident.incident_id,
                occurred_ts=incident.opened_ts,
                batch_id=incident.batch_id,
            )
        )
    _ = guides
    return {found: tuple(priors) for found, priors in grouped.items()}


def draft_guide_from(incident: Incident, *, title: str, steps: Sequence[str]) -> RecoveryGuide:
    """Turn a resolved novel incident into a DRAFT guide.

        "offers to save the eventual resolution as a new draft guide"

    Draft, and with no remedy attached. The first version of a guide is one
    person's account of what worked once; binding a platform action to it
    before anybody else has seen it happen is how a wrong fix becomes the
    recommended one.
    """
    if not incident.signature:
        raise FingerprintError(
            f"{incident.incident_id} has no signature — there were no errors to learn from."
        )
    return RecoveryGuide(
        guide_id=f"DRAFT-{incident.signature[3:11]}",
        title=title,
        signatures=frozenset({incident.signature}),
        steps=tuple(steps),
        remedy=None,
    )


# ── the measurable ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Precision:
    """The gate's arithmetic, computed rather than claimed.

        "≥ 95% fingerprint precision on the seeded failure library before the
         feature ships enabled"

    PRECISION, not accuracy, and the distinction is the whole point: a matcher
    that recognises nothing has no false positives and is useless, so `recall`
    is reported beside it and neither number is allowed to travel alone.
    """

    matched: int = 0
    correct: int = 0
    total: int = 0

    @property
    def precision(self) -> float:
        return self.correct / self.matched if self.matched else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def passes(self, threshold: float) -> bool:
        """A zero-match measurement is a FAILURE, not a vacuous pass.

        An eval returning 100% because it matched nothing is the most dangerous
        green there is — the same rule `core.proposals.Acceptance` applies.
        """
        return self.matched > 0 and self.precision >= threshold

    def report(self, threshold: float) -> str:
        return (
            f"{self.correct}/{self.matched} matches correct ({self.precision:.1%}, gate "
            f"{threshold:.0%}); recall {self.correct}/{self.total} ({self.recall:.1%})"
        )


def measure_precision(
    cases: Sequence[tuple[Incident, str | None]], *, expected_unknown: str | None = None
) -> Precision:
    """Grade a run of the seeded failure library.

    Each case is an incident and the guide id it SHOULD have matched (`None`
    for a failure that genuinely has no guide). A match to the wrong guide
    counts against precision; declining to match counts against recall only —
    which is the right asymmetry, because a wrong guide sends somebody to do
    the wrong thing at 3 AM and a missing one sends them to think.
    """
    matched = correct = 0
    total = 0
    for incident, expected in cases:
        if expected is not expected_unknown and expected is not None:
            total += 1
        if incident.match is None:
            continue
        matched += 1
        if incident.match.guide.guide_id == expected:
            correct += 1
    return Precision(matched=matched, correct=correct, total=total)


#: How long fingerprinting is allowed to take before the story's promise breaks.
#: "within a minute the incident shows the root cause" — and the deterministic
#: half has to be a rounding error inside that, since retrieval and the model's
#: explanation of a NOVEL failure also have to fit.
FINGERPRINT_BUDGET = timedelta(seconds=5)
