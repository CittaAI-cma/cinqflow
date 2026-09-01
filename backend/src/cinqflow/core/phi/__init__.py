"""CF-V1-E5-03 — what each column holds, and whether it is protected.

    "PHI & healthcare code-set detection (Presidio + glossary flags + pattern
     library) ... Feeds E2's masking and E7's rule suggestions; PHI must be
     known at contract time, not discovered in production."
    — CF-V1-E5-03

    "100% recall on glossary-flagged PHI (missing PHI is the failure that
     matters) · uncertain => safer classification (free-text 'notes' treated
     PHI until steward decides) · downgrade-by-AI refused"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

THE ASYMMETRY THAT DEFINES THIS MODULE, and it runs the OPPOSITE WAY from
CF-V1-E5-02's:

    Schema inference, faced with a column it cannot settle, REFUSES TO GUESS
    and writes "needs your input". PHI detection, faced with the same column,
    CLASSIFIES IT AS PHI and tells a steward.

Both are the safe answer to "we do not know", and they differ because the two
mistakes cost differently. A field typed wrongly is caught by the next load; a
field unmasked wrongly is a disclosure that cannot be recalled. So the default
here is protection, and `Basis.PRECAUTION` says on the screen — in as many
words — that the platform is protecting a column it has not identified, which
is a decision a steward can review rather than a silence they cannot.

FIVE BASES, IN STRICT PRECEDENCE. Each says WHY, and the order is what makes
the 100% recall gate structural rather than measured:

  1. GLOSSARY    the client's own analysts flagged this column. Authoritative,
                 and consulted first, so a flagged column is decided before
                 any other evidence is looked at and before any model is
                 consulted. Recall against the glossary is therefore 1.0 by
                 construction — the eval measures it, but the code guarantees
                 it.
  2. COMPUTATION a value shape that could not have fitted by accident: an NPI
                 whose Luhn check passes, an MBI's positional alphabet, an
                 email address. Arithmetic, not opinion.
  3. SCRUB       the PHI scrubber found named entities in the sampled values.
                 Evidence about content, from a local model, and it may RAISE
                 a flag but never clear one.
  4. INFERENCE   the model read the column's name and statistics and said so.
                 The weakest basis, and the only one a human is asked to
                 confirm before it can reduce protection — which it cannot.
  5. PRECAUTION  nothing above settled it. PHI, pending a steward.

A DOWNGRADE IS NOT AVAILABLE TO ANY AGENT, at any confidence. `reclassify`
raises for a machine actor, and `merge_inference` refuses a model's attempt and
returns the attempt as a refusal for the review screen. R2 is a proposal class;
lowering the protection on a field is not a proposal the platform accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum, unique
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.patterns import BY_ID, CodeSet, IdentifierShape
from cinqflow.core.profiling import ColumnProfile, FileProfile
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm


class PhiClassificationError(RuntimeError):
    """A classification the platform will not make."""


class PhiDowngradeRefusedError(PhiClassificationError):
    """Something tried to reduce a column's protection without a steward.

    Named for what it refuses rather than for what went wrong, because this is
    the exception a reader will meet in an audit row. `core.registry.glossary`
    raises its own `PhiDowngradeError` for the same act on a TERM; this one is
    about a COLUMN of a feed, and both exist because the two are different
    objects that a single guard could not cover.
    """


@unique
class PhiKind(StrEnum):
    """Which HIPAA identifier a column carries, where the platform can say.

    Not the full eighteen. These are the ones that appear in a payer roster or
    claims extract and that this platform can establish from a column — a
    photograph and a fingerprint are identifiers too, and no CSV of this
    estate has ever held one.
    """

    NAME = "name"
    SSN = "ssn"
    MEDICARE_ID = "medicare_id"
    MEMBER_ID = "member_id"
    MEDICAL_RECORD_NUMBER = "medical_record_number"
    ACCOUNT_NUMBER = "account_number"
    DATE = "date"
    GEOGRAPHY = "geography"
    PHONE = "phone"
    EMAIL = "email"
    IP_ADDRESS = "ip_address"
    FREE_TEXT = "free_text"
    #: IDENTIFIED, AND NOT AN IDENTIFIER. "This is the plan's line of business,
    #: and a line of business names no member."
    #:
    #: Added because the Lane-3 run found the gap: given `LOB`, the model
    #: correctly answered "a plan attribute, not a clinical code set" and then
    #: had nowhere to put it — the only remaining value was `UNSPECIFIED`,
    #: which reads as "no idea". A vocabulary that cannot express a correct
    #: answer collects wrong ones.
    #:
    #: Note what this does NOT do: unprotect the column. Only a steward can,
    #: and this is the sentence that lets them.
    NOT_AN_IDENTIFIER = "not_an_identifier"
    #: Flagged, and the platform cannot say what it is. Honest, and it is what
    #: a `PRECAUTION` classification carries until somebody says otherwise.
    UNSPECIFIED = "unspecified"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")


@unique
class Basis(StrEnum):
    """WHY the platform says what it says. Shown on the review screen, always.

    A flag with no basis is a flag nobody can argue with, and a steward who
    cannot argue with a flag stops reading them.
    """

    GLOSSARY = "glossary"
    COMPUTATION = "computation"
    SCRUB = "scrub"
    INFERENCE = "inference"
    PRECAUTION = "precaution"

    @property
    def is_deterministic(self) -> bool:
        """True where no model was involved. The eval reports the shares apart
        for the same reason CF-V1-E5-02 does."""
        return self in {Basis.GLOSSARY, Basis.COMPUTATION, Basis.PRECAUTION}

    @property
    def settles(self) -> bool:
        """Whether reaching this basis ends the question.

        PRECAUTION does not settle: it is the safe holding position for a
        column nothing identified, and it is exactly the column a model may
        still have something useful to say about.
        """
        return self in {Basis.GLOSSARY, Basis.COMPUTATION, Basis.SCRUB}


#: Which identifier shapes carry PHI, and what they are. Every shape naming a
#: PERSON is here; the code sets are not, because a diagnosis code identifies a
#: DISEASE and masking it breaks every clinical report the platform exists to
#: produce. See `CodeSet.is_phi`.
_SHAPE_KIND: dict[IdentifierShape, PhiKind] = {
    IdentifierShape.SSN: PhiKind.SSN,
    IdentifierShape.MBI: PhiKind.MEDICARE_ID,
    IdentifierShape.HICN: PhiKind.MEDICARE_ID,
    IdentifierShape.EMAIL: PhiKind.EMAIL,
    IdentifierShape.PHONE_US: PhiKind.PHONE,
    IdentifierShape.IP_ADDRESS: PhiKind.IP_ADDRESS,
    # ZIP+4 addresses a delivery point — a household. Five-digit ZIP is not in
    # this table because it is not a decisive shape at all (it is also a CPT
    # code), so it never reaches a computed classification.
    IdentifierShape.POSTAL_CODE_PLUS_FOUR: PhiKind.GEOGRAPHY,
}

#: Entities that do NOT on their own mean a person is identifiable.
#:
#: AN EXCLUSION LIST, NOT AN ALLOW LIST, and the direction is the decision. An
#: allow list of "entities that count as PHI" goes stale the day the scrubber
#: gains a recogniser — Presidio ships new ones, and the mock adapter already
#: emits `MEMBER_ID` and `MEDICARE_ID`, which no list written against
#: Presidio's catalogue would have contained. Recall would then drop silently,
#: which is the one failure this story exists to prevent. So every entity the
#: scrubber names is PHI evidence unless it is named here, and adding a member
#: here is a deliberate act somebody has to justify.
NOT_ALONE_PHI_ENTITIES: frozenset[str] = frozenset({"URL", "CRYPTO"})

#: Scrubber entity -> the identifier it evidences. Covers Presidio's catalogue
#: and the mock adapter's additions. Anything not named still raises the flag;
#: it just cannot say which kind, which costs a label and never a disclosure.
_ENTITY_KIND: dict[str, PhiKind] = {
    "PERSON": PhiKind.NAME,
    "US_SSN": PhiKind.SSN,
    "US_ITIN": PhiKind.SSN,
    "EMAIL_ADDRESS": PhiKind.EMAIL,
    "PHONE_NUMBER": PhiKind.PHONE,
    "IP_ADDRESS": PhiKind.IP_ADDRESS,
    "LOCATION": PhiKind.GEOGRAPHY,
    "DATE_TIME": PhiKind.DATE,
    "DATE_OF_BIRTH": PhiKind.DATE,
    "US_BANK_NUMBER": PhiKind.ACCOUNT_NUMBER,
    "CREDIT_CARD": PhiKind.ACCOUNT_NUMBER,
    "MEMBER_ID": PhiKind.MEMBER_ID,
    "MEDICARE_ID": PhiKind.MEDICARE_ID,
}

#: A scrub hit on a single stray value is noise; a scrub hit on most sampled
#: values is a finding. Below this share the entities are reported as evidence
#: and the column stays an open question rather than becoming a claim.
SCRUB_SHARE_FLOOR = 0.5


@dataclass(frozen=True)
class ScrubEvidence:
    """What the PHI scrubber saw in one column's sampled values.

    COUNTS AND ENTITY NAMES ONLY. `core.model.phi.Finding` already refuses to
    carry a detected value, and this aggregate refuses to carry even the
    positions — a position plus the sample is the value again.
    """

    entities: tuple[str, ...] = ()
    values_scanned: int = 0
    values_with_entities: int = 0

    @property
    def share(self) -> float:
        return self.values_with_entities / self.values_scanned if self.values_scanned else 0.0

    @property
    def is_conclusive(self) -> bool:
        return bool(self.entities) and self.share >= SCRUB_SHARE_FLOOR

    @property
    def phi_entities(self) -> tuple[str, ...]:
        return tuple(e for e in self.entities if e not in NOT_ALONE_PHI_ENTITIES)


@dataclass(frozen=True)
class ColumnClassification:
    """One column's verdict, with the basis it rests on and the evidence under it."""

    source_name: str
    position: int
    is_phi: bool
    basis: Basis
    phi_kind: PhiKind | None = None
    code_set: CodeSet | None = None
    confidence: float = 1.0
    needs_steward_review: bool = False
    rationale: str = ""
    citations: tuple[CitationId, ...] = ()
    evidence: tuple[str, ...] = ()
    glossary_id: str | None = None

    @property
    def settled(self) -> bool:
        """Whether this column has an answer no model would improve on."""
        return self.basis.settles

    @property
    def is_protected_without_being_identified(self) -> bool:
        """The honest name for a PRECAUTION flag, and what the screen says.

        Kept as a property rather than left implicit so the review queue can
        sort by it: these are the columns a steward's half-hour is worth most
        on, and they are indistinguishable from confident flags unless
        something makes the distinction.
        """
        return self.is_phi and self.basis is Basis.PRECAUTION


@dataclass(frozen=True)
class Classification:
    """Every column of one file, classified."""

    feed_id: str
    profile_id: str
    columns: tuple[ColumnClassification, ...] = ()

    def column(self, source_name: str) -> ColumnClassification | None:
        for candidate in self.columns:
            if candidate.source_name == source_name:
                return candidate
        return None

    @property
    def phi_columns(self) -> tuple[ColumnClassification, ...]:
        return tuple(c for c in self.columns if c.is_phi)

    @property
    def open_questions(self) -> tuple[ColumnClassification, ...]:
        """The columns a model is asked about — everything nothing settled.

        A file whose columns the glossary and the shapes between them account
        for costs zero tokens, exactly as in CF-V1-E5-02.
        """
        return tuple(c for c in self.columns if not c.settled)

    @property
    def needs_no_model(self) -> bool:
        return not self.open_questions

    @property
    def needs_steward_review(self) -> tuple[ColumnClassification, ...]:
        return tuple(c for c in self.columns if c.needs_steward_review)

    # ── the gate ─────────────────────────────────────────────────────────────
    def missed_phi(self, glossary: Glossary) -> tuple[str, ...]:
        """Glossary-flagged columns this classification did NOT protect.

        THE GATE IS THAT THIS IS EMPTY. Recall, not accuracy: a column the
        platform over-protects costs a steward a click, and a column it
        under-protects is a disclosure. The eval fails on any member here, and
        the API refuses to record a classification that has one — belt and
        braces, because the two guards fail for different reasons.
        """
        return tuple(
            c.source_name
            for c in self.columns
            if glossary.is_phi_column(c.source_name) and not c.is_phi
        )

    def recall_against(self, glossary: Glossary) -> tuple[int, int]:
        """(protected, expected) over the columns the glossary flags.

        Returned as two integers rather than a float for the same reason
        `TypeCandidate` carries two: a rate a reviewer cannot recompute is a
        rate they have to trust.
        """
        expected = [c for c in self.columns if glossary.is_phi_column(c.source_name)]
        return sum(1 for c in expected if c.is_phi), len(expected)

    def over_flagged(self, glossary: Glossary) -> tuple[str, ...]:
        """Columns flagged PHI that the glossary does not flag.

        REPORTED, NEVER GATED. Over-flagging is the safe direction and the
        blueprint asks for it explicitly — but a steward still deserves to see
        how much of it there is, because a detector that flags everything has
        told them nothing while appearing to work.
        """
        return tuple(
            c.source_name
            for c in self.columns
            if c.is_phi and not glossary.is_phi_column(c.source_name)
        )


# ── the deterministic pass ───────────────────────────────────────────────────
def classify(
    profile: FileProfile,
    *,
    feed_id: str,
    glossary: Glossary,
    scrub: dict[str, ScrubEvidence] | None = None,
) -> Classification:
    """Everything the glossary, the arithmetic and the scrubber already decide.

    No model, no I/O. `scrub` is passed IN rather than computed here — the
    scrubber is a pin, and core reaches no pin — so this function stays pure
    and testable with a dictionary.
    """
    evidence = scrub or {}
    return Classification(
        feed_id=feed_id,
        profile_id=profile.profile_id,
        columns=tuple(
            _classify_column(profile, column, glossary, evidence.get(column.name))
            for column in profile.columns
        ),
    )


def _classify_column(
    profile: FileProfile,
    column: ColumnProfile,
    glossary: Glossary,
    scrub: ScrubEvidence | None,
) -> ColumnClassification:
    citations: list[CitationId] = [profile.citation_for(column.name)]
    facts = _facts(column)

    # 1 · THE GLOSSARY, FIRST AND ALONE. Consulted before anything else so a
    #     flagged column is decided before any other evidence can dilute it —
    #     which is what makes the recall gate a property of the order of these
    #     branches rather than a number somebody measured afterwards.
    terms = glossary.for_column(column.name)
    if term := _single(terms):
        citations.append(CitationId(kind=CitationKind.TERM, subject=term.slug))
        return ColumnClassification(
            source_name=column.name,
            position=column.position,
            is_phi=term.is_phi,
            basis=Basis.GLOSSARY,
            phi_kind=_kind_for_term(term) if term.is_phi else None,
            confidence=1.0,
            rationale=(
                f"The client's own glossary names this column {term.term!r} "
                f"({term.glossary_id}) and "
                + ("flags it as PHI." if term.is_phi else "does not flag it as PHI.")
            ),
            citations=tuple(citations),
            evidence=facts,
            glossary_id=term.glossary_id,
        )
    if terms:
        # Two terms claim this spelling — a real ambiguity in the client's own
        # glossary. If EITHER is PHI the column is PHI, because the safe
        # reading of an ambiguity is the protective one.
        citations.extend(CitationId(kind=CitationKind.TERM, subject=t.slug) for t in terms)
        flagged = [t for t in terms if t.is_phi]
        return ColumnClassification(
            source_name=column.name,
            position=column.position,
            is_phi=bool(flagged),
            basis=Basis.GLOSSARY if flagged else Basis.PRECAUTION,
            phi_kind=_kind_for_term(flagged[0]) if flagged else None,
            confidence=1.0 if flagged else 0.0,
            needs_steward_review=True,
            rationale=(
                f"{len(terms)} glossary terms claim this column ("
                + ", ".join(f"{t.glossary_id} {t.term!r}" for t in terms)
                + "). "
                + (
                    f"{len(flagged)} of them flag PHI, so the column is protected "
                    "and a steward decides which term it is."
                    if flagged
                    else "None flags PHI, but a steward should say which term this is."
                )
            ),
            citations=tuple(citations),
            evidence=facts,
        )

    # 2 · COMPUTATION. A shape that could not have fitted by accident.
    if decisive := column.decisive_patterns:
        pattern = BY_ID[decisive[0].pattern_id]
        if pattern.identifier is not None:
            kind = _SHAPE_KIND.get(pattern.identifier, PhiKind.UNSPECIFIED)
            return ColumnClassification(
                source_name=column.name,
                position=column.position,
                is_phi=True,
                basis=Basis.COMPUTATION,
                phi_kind=kind,
                confidence=1.0,
                rationale=(
                    f"All {decisive[0].considered} populated values fit the "
                    f"{pattern.label} shape ({pattern.note}). That is measured, "
                    "not inferred."
                ),
                citations=tuple(citations),
                evidence=facts,
            )
        return ColumnClassification(
            source_name=column.name,
            position=column.position,
            # A code set identifies a diagnosis, a drug or a provider — not the
            # member whose record this is. See `CodeSet.is_phi`.
            is_phi=False,
            basis=Basis.COMPUTATION,
            code_set=pattern.code_set,
            confidence=1.0,
            rationale=(
                f"All {decisive[0].considered} populated values are valid "
                f"{pattern.label}s ({pattern.note}). A code set identifies a "
                "clinical concept, not a person."
            ),
            citations=tuple(citations),
            evidence=facts,
        )

    # 3 · THE SCRUB. It may raise a flag; nothing about it can clear one.
    if scrub is not None and scrub.is_conclusive:
        entities = scrub.phi_entities
        if entities:
            return ColumnClassification(
                source_name=column.name,
                position=column.position,
                is_phi=True,
                basis=Basis.SCRUB,
                phi_kind=_ENTITY_KIND.get(entities[0], PhiKind.UNSPECIFIED),
                confidence=round(scrub.share, 2),
                rationale=(
                    f"The PHI scrubber found {', '.join(entities)} in "
                    f"{scrub.values_with_entities} of {scrub.values_scanned} sampled "
                    "values."
                ),
                citations=tuple(citations),
                evidence=(*facts, f"scrub: {', '.join(entities)} in {scrub.share:.0%} of values"),
            )

    # 4 · FREE TEXT. Not asked of the model, because the answer is already the
    #     safe one and a model cannot make it safer. Asked of a STEWARD.
    if column.is_free_text:
        return ColumnClassification(
            source_name=column.name,
            position=column.position,
            is_phi=True,
            basis=Basis.PRECAUTION,
            phi_kind=PhiKind.FREE_TEXT,
            confidence=0.0,
            needs_steward_review=True,
            rationale=(
                f"Free text: values run to {column.max_length} characters and barely "
                f"repeat ({column.distinct_count} distinct in {column.populated_count} "
                "rows). Anything a person types about a member can name them, so it is "
                "treated as PHI until a steward decides otherwise."
            ),
            citations=tuple(citations),
            evidence=facts,
        )

    # 5 · OPEN. Protected in the meantime, and it is the model's question.
    return ColumnClassification(
        source_name=column.name,
        position=column.position,
        is_phi=True,
        basis=Basis.PRECAUTION,
        phi_kind=PhiKind.UNSPECIFIED,
        confidence=0.0,
        needs_steward_review=True,
        rationale=(
            "Nothing here established what this column holds — no glossary term "
            "claims it, no value shape fits every value. It is protected until "
            "somebody says what it is."
        ),
        citations=tuple(citations),
        evidence=facts,
    )


def _facts(column: ColumnProfile) -> tuple[str, ...]:
    """The computed evidence, as lines. NO VALUES — see `as_prompt_grounding`.

    This is the text a reviewer reads and, unchanged, the text the model is
    shown. Keeping them the same string is deliberate: a reviewer checking the
    agent's reasoning is then looking at exactly what the agent looked at.
    """
    lines = [
        f"rows {column.row_count}, populated {column.populated_count}, "
        f"nulls {column.null_count}, distinct {column.distinct_count}"
        + ("" if column.distinct_is_exact else "+ (exact count exceeded)"),
        f"lengths {column.min_length}-{column.max_length}",
        "types fitting every value: "
        + (", ".join(t.value for t in column.total_match_types) or "none but string"),
    ]
    if column.pattern_matches:
        lines.append(
            "value shapes: "
            + ", ".join(
                f"{m.pattern_id} {m.matched}/{m.considered}" for m in column.pattern_matches
            )
        )
    if column.date_formats:
        lines.append("date formats: " + ", ".join(d.label for d in column.date_formats))
    return tuple(lines)


def _single(terms: tuple[GlossaryTerm, ...]) -> GlossaryTerm | None:
    return terms[0] if len(terms) == 1 else None


#: Words in a business term that say which identifier it is. Only consulted for
#: a term the glossary ALREADY flags as PHI — this never decides whether a
#: column is protected, only how to describe protection that is already
#: decided, so a miss here costs a label and never a disclosure.
_TERM_KIND: tuple[tuple[tuple[str, ...], PhiKind], ...] = (
    (("social security", "ssn"), PhiKind.SSN),
    (("medicare", "mbi", "hicn"), PhiKind.MEDICARE_ID),
    (("medical record", "mrn"), PhiKind.MEDICAL_RECORD_NUMBER),
    (("date of birth", "birth", "dob", "death", "deceased"), PhiKind.DATE),
    (("email",), PhiKind.EMAIL),
    (("phone", "telephone", "mobile", "fax"), PhiKind.PHONE),
    (("address", "zip", "postal", "city", "county", "street"), PhiKind.GEOGRAPHY),
    (("name",), PhiKind.NAME),
    (("account",), PhiKind.ACCOUNT_NUMBER),
    (("member id", "subscriber", "member identifier"), PhiKind.MEMBER_ID),
)


def _kind_for_term(term: GlossaryTerm) -> PhiKind:
    haystack = f"{term.term} {term.sub_category}".lower()
    for needles, kind in _TERM_KIND:
        if any(needle in haystack for needle in needles):
            return kind
    return PhiKind.UNSPECIFIED


# ── the model's answer, folded in ────────────────────────────────────────────
def merge_inference(
    current: ColumnClassification,
    proposed: dict[str, Any],
    *,
    confidence_floor: float,
) -> tuple[ColumnClassification, tuple[str, ...]]:
    """Fold one model answer in, refusing what a model may not say.

    Returns the classification and the REFUSALS — returned rather than logged,
    because "the agent tried to unprotect a column" is a governance event and
    belongs on the review screen, not in a log file nobody opens.

    THREE THINGS A MODEL CANNOT DO HERE:

      • clear a flag. Any flag, on any basis, at any confidence. The column
        stays protected and the attempt is recorded.
      • overturn arithmetic. A column already settled by GLOSSARY, COMPUTATION
        or SCRUB is not shown to the model at all — and if an answer arrives
        for one anyway, it is discarded rather than merged.
      • raise a flag quietly. A model-raised flag carries `Basis.INFERENCE` and
        `needs_steward_review`, so the screen distinguishes "measured" from
        "the model thought so" without the reader having to know which
        columns were which.
    """
    refusals: list[str] = []
    says_phi = bool(proposed.get("is_phi", False))
    confidence = float(proposed.get("confidence", 0.0) or 0.0)

    if current.settled:
        # An answer for a column the model was not asked about. Discarded
        # either way — computed evidence is not up for revision — but only
        # RECORDED where it disagrees. A model that volunteers the same answer
        # the arithmetic reached has done nothing wrong, and filing that as a
        # governance event trains a steward to skim the refusal list.
        if says_phi == current.is_phi:
            return current, ()
        return current, (
            f"{current.source_name}: the agent said is_phi={says_phi} for a column "
            f"already settled as is_phi={current.is_phi} by {current.basis.value}. "
            "Discarded — computed evidence is not up for revision.",
        )

    if current.is_phi and not says_phi:
        refusals.append(
            f"{current.source_name}: the agent proposed clearing the PHI flag. Refused — "
            "a downgrade needs a steward, and no model has one. The column stays "
            "protected and this attempt is on the record."
        )
        return replace(current, needs_steward_review=True), tuple(refusals)

    if not says_phi:
        # The model agrees with a column that was not flagged. Nothing to do —
        # and note this branch is unreachable today, because every unsettled
        # column arrives here already protected by PRECAUTION. It is written
        # anyway so that a future basis which leaves a column unflagged does
        # not fall through into the flag-raising branch below.
        return current, ()

    if confidence < confidence_floor:
        return (
            replace(
                current,
                confidence=confidence,
                needs_steward_review=True,
                rationale=(
                    f"{proposed.get('rationale', '')} (the agent's confidence "
                    f"{confidence:.2f} is below the platform's floor of "
                    f"{confidence_floor:.2f}, so the flag stands but a steward decides "
                    "what this column is)"
                ).strip(),
            ),
            (),
        )

    kind = proposed.get("phi_kind")
    code = proposed.get("code_set")
    return (
        replace(
            current,
            is_phi=True,
            basis=Basis.INFERENCE,
            phi_kind=PhiKind(kind) if kind in set(PhiKind) else current.phi_kind,
            code_set=CodeSet(code) if code in set(CodeSet) else current.code_set,
            confidence=confidence,
            # Raised by a model, so a steward still confirms it. The flag holds
            # in the meantime — confirmation is not a precondition of
            # protection, only of removing it.
            needs_steward_review=True,
            rationale=str(proposed.get("rationale", "")) or current.rationale,
        ),
        (),
    )


def reclassify(
    current: ColumnClassification,
    *,
    is_phi: bool,
    steward: Actor,
    rationale: str,
) -> ColumnClassification:
    """A steward's decision. The ONLY path that can reduce protection.

    Refuses a machine actor outright rather than checking a permission, for the
    same reason `GovernedObject.transition_to` does: a role can be
    misconfigured, and an actor type cannot be mistaken for one.
    """
    if steward.actor_type is not ActorType.HUMAN:
        raise PhiDowngradeRefusedError(
            f"{steward.subject} is a {steward.actor_type.value} actor. Clearing a PHI "
            "flag is a steward's decision and a named person's name is on it."
        )
    if current.is_phi and not is_phi and not rationale.strip():
        raise PhiDowngradeRefusedError(
            f"{current.source_name}: clearing a PHI flag needs a reason. Masking "
            "everywhere reads this flag, and an unexplained downgrade is an "
            "unreviewable one."
        )
    return replace(
        current,
        is_phi=is_phi,
        basis=Basis.GLOSSARY if not is_phi else current.basis,
        confidence=1.0,
        needs_steward_review=False,
        rationale=f"{steward.display_name or steward.subject}: {rationale}",
    )


@dataclass(frozen=True)
class MaskingPolicy:
    """What E2 masks, derived from a classification. CF-V4-E2-03's input.

    Built here rather than in the masking story so that "flags flow to
    masking" is a function somebody can call today, and so the flag and the
    thing it drives cannot drift into two lists.
    """

    feed_id: str
    profile_id: str
    masked_columns: tuple[str, ...] = ()
    unmasked_columns: tuple[str, ...] = ()
    pending_steward: tuple[str, ...] = field(default_factory=tuple)

    @property
    def masks_everything_it_should(self) -> bool:
        return not set(self.pending_steward) & set(self.unmasked_columns)


def masking_policy(classification: Classification) -> MaskingPolicy:
    """Every protected column, masked. Including the precautionary ones.

    A column awaiting a steward is masked WHILE it waits — that is the whole
    content of "treated PHI until steward decides". A policy that left it
    unmasked pending review would make the review the control, and reviews
    happen on Thursdays.
    """
    return MaskingPolicy(
        feed_id=classification.feed_id,
        profile_id=classification.profile_id,
        masked_columns=tuple(c.source_name for c in classification.columns if c.is_phi),
        unmasked_columns=tuple(c.source_name for c in classification.columns if not c.is_phi),
        pending_steward=tuple(c.source_name for c in classification.needs_steward_review),
    )
