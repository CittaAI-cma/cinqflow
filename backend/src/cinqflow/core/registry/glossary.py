"""CF-V1-E14-01 — the business glossary as a live, governed service.

    "I want the existing 171-term business glossary — with its PHI flags —
     loaded as a live service that fields, mappings and rules link to, so that
     the platform speaks CINQFLOW's business language from day one."
    — CF-V1-E14-01

    "29 of 171 terms are PHI-flagged. Those 29 drive both the masking policy
     and the PHI-detection gate — and the gate is 100% recall."
    — memory/05-ground-truth/03-golden-sets.md

THE SYNONYM SET IS THE POINT. `BG-004 Member Date of Birth` records
`Date_of_Birth; Patient_dob; patient_dob; Patient_Date_of_birth;
MemberDateOfBirth` — which is precisely what a semantic mapper needs, and it
was written by the client's own analysts, not invented here. The same rows
serve twice (ADR-0007): as the exam for the PHI-detection gate, and as K2
grounding once the knowledge pipeline embeds them.

A term is a GOVERNED OBJECT (`ObjectType.GLOSSARY_TERM`), so it travels the
one lifecycle like everything else — which is what makes "changing a PHI flag
requires steward approval" a state machine fact rather than a rule in a
document nobody re-reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from cinqflow.core.model.governed import Actor, GovernedObject, LifecycleState, ObjectType

#: Multi-value cells in the source workbook are semicolon-separated.
_SEPARATOR = re.compile(r"\s*;\s*")


def _split(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part for part in _SEPARATOR.split(str(value).strip()) if part)


class PhiDowngradeError(RuntimeError):
    """An attempt to clear a PHI flag outside steward approval.

        "Let anyone change a PHI flag without steward approval — masking
         everywhere depends on these flags." (a documented don't)

    Raised, not warned: the masking policy, the detection gate and the vector
    store's PHI-absence guarantee all read this flag, so a silent downgrade is
    three failures at once.
    """


@dataclass(frozen=True)
class GlossaryTerm:
    """One approved business definition, with the columns that carry it.

    `mapped_columns_original` is the synonym set a mapping agent reasons from;
    `mapped_columns_corrected` is what the canonical model calls it. Keeping
    both is what lets a suggestion say "MBR_DOB maps like Fidelis
    `Date_of_Birth` did" and cite the row it stands on.
    """

    glossary_id: str
    term: str
    definition: str
    domain_category: str = ""
    sub_category: str = ""
    classification: str = ""
    regulatory_reference: str = ""
    mapped_domains: tuple[str, ...] = ()
    mapped_tables: tuple[str, ...] = ()
    mapped_columns_original: tuple[str, ...] = ()
    mapped_columns_corrected: tuple[str, ...] = ()
    sensitivity: str = ""
    is_phi: bool = False
    notes: str = ""
    #: Which workbook row this came from — "preserve source lineage of every
    #: seeded term" is an acceptance criterion, and a seeded fact whose origin
    #: is unrecorded is indistinguishable from one somebody invented.
    source_row: int | None = None

    def __post_init__(self) -> None:
        if not self.glossary_id.strip():
            raise ValueError("a glossary term without an id cannot be cited")
        if not self.definition.strip():
            raise ValueError(
                f"{self.glossary_id}: a term with no definition is a label, not a definition — "
                "and the platform grounds mappings and rules in these"
            )

    @property
    def slug(self) -> str:
        """The `term:<slug>` citation subject. Stable, lowercase, hyphenated —
        it is an address, so it must not change when a definition is edited."""
        return re.sub(r"[^a-z0-9]+", "-", self.term.lower()).strip("-")

    @property
    def synonyms(self) -> tuple[str, ...]:
        """Every column name that has ever carried this concept, original and
        corrected — the payload a semantic mapper matches against."""
        seen: dict[str, None] = {}
        for name in (*self.mapped_columns_original, *self.mapped_columns_corrected):
            seen.setdefault(name, None)
        return tuple(seen)

    def matches_column(self, column_name: str) -> bool:
        """Case- and separator-insensitive, because `Patient_dob`, `patient_dob`
        and `PatientDOB` are the same concept arriving from three payers."""
        target = _normalise(column_name)
        return any(_normalise(name) == target for name in self.synonyms)

    # ── the governed object ──────────────────────────────────────────────────
    def as_governed(self, *, author: Actor, now: datetime | None = None) -> GovernedObject:
        """A Draft term. Nothing arrives Published — not even a seeded one, so
        the 171 are reviewed by the steward who will own them."""
        from datetime import UTC
        from datetime import datetime as _dt

        return GovernedObject(
            object_type=ObjectType.GLOSSARY_TERM,
            object_id=self.glossary_id,
            version=1,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=author,
            created_ts=now or _dt.now(UTC),
            body=self.as_body(),
        )

    def as_body(self) -> dict[str, Any]:
        return {
            "name": self.term,
            "definition": self.definition,
            "domain_category": self.domain_category,
            "sub_category": self.sub_category,
            "classification": self.classification,
            "regulatory_reference": self.regulatory_reference,
            "mapped_domains": list(self.mapped_domains),
            "mapped_tables": list(self.mapped_tables),
            "mapped_columns_original": list(self.mapped_columns_original),
            "mapped_columns_corrected": list(self.mapped_columns_corrected),
            "sensitivity": self.sensitivity,
            "is_phi": self.is_phi,
            "notes": self.notes,
            "source_row": self.source_row,
        }

    @classmethod
    def from_governed(cls, obj: GovernedObject) -> Self:
        body = obj.body
        return cls(
            glossary_id=obj.object_id,
            term=str(body.get("name", "")),
            definition=str(body.get("definition", "")),
            domain_category=str(body.get("domain_category", "")),
            sub_category=str(body.get("sub_category", "")),
            classification=str(body.get("classification", "")),
            regulatory_reference=str(body.get("regulatory_reference", "")),
            mapped_domains=tuple(body.get("mapped_domains") or ()),
            mapped_tables=tuple(body.get("mapped_tables") or ()),
            mapped_columns_original=tuple(body.get("mapped_columns_original") or ()),
            mapped_columns_corrected=tuple(body.get("mapped_columns_corrected") or ()),
            sensitivity=str(body.get("sensitivity", "")),
            is_phi=bool(body.get("is_phi", False)),
            notes=str(body.get("notes", "")),
            source_row=body.get("source_row"),
        )

    @classmethod
    def from_row(cls, row: dict[str, Any], *, source_row: int | None = None) -> Self:
        """One workbook row, by its own header names — so a column reorder in
        the source cannot silently shift a PHI flag onto the wrong term."""
        return cls(
            glossary_id=str(row.get("Glossary_ID", "") or "").strip(),
            term=str(row.get("Business Term", "") or "").strip(),
            definition=str(row.get("Business Definition", "") or "").strip(),
            domain_category=str(row.get("Healthcare Domain Category", "") or "").strip(),
            sub_category=str(row.get("Healthcare Sub-Category", "") or "").strip(),
            classification=str(row.get("Data Classification", "") or "").strip(),
            regulatory_reference=str(row.get("Regulatory / Standard Reference", "") or "").strip(),
            mapped_domains=_split(row.get("Mapped Domain(s)")),
            mapped_tables=_split(row.get("Mapped Table(s)")),
            mapped_columns_original=_split(row.get("Mapped Column(s) - Original")),
            mapped_columns_corrected=_split(row.get("Mapped Column(s) - Corrected")),
            sensitivity=str(row.get("Data Sensitivity", "") or "").strip(),
            # Anything that is not an explicit "Yes" is not PHI — but the
            # DOWNGRADE path is what is guarded (see `amend`), because the
            # dangerous direction is Yes -> No, never the reverse.
            is_phi=str(row.get("PHI Indicator", "") or "").strip().lower() == "yes",
            notes=str(row.get("Notes", "") or "").strip(),
            source_row=source_row,
        )


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class Glossary:
    """The lookup any service can call: term -> definition, PHI flag, fields.

    Held as a value object rather than a service class because every question
    it answers is pure — which is what lets the schema-inference and mapping
    agents ground in it with no I/O anywhere near the model call.
    """

    terms: tuple[GlossaryTerm, ...] = ()
    _by_id: dict[str, GlossaryTerm] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Built as we go, so the check actually fires. Checking against the
        # index BEFORE populating it — the obvious-looking version — compares
        # every term to an empty dict and reports no duplicates, ever.
        duplicates: list[str] = []
        for term in self.terms:
            if term.glossary_id in self._by_id:
                duplicates.append(term.glossary_id)
            self._by_id[term.glossary_id] = term
        if duplicates:
            raise ValueError(
                f"duplicate glossary ids: {', '.join(sorted(set(duplicates)))} — two "
                "definitions of one term is two answers to the same question"
            )

    def get(self, glossary_id: str) -> GlossaryTerm | None:
        return self._by_id.get(glossary_id)

    def for_column(self, column_name: str) -> tuple[GlossaryTerm, ...]:
        """Every term whose synonym set contains this column — the mapping
        agent's first, deterministic question. It runs before any model call,
        so an exact synonym match never costs a token."""
        return tuple(t for t in self.terms if t.matches_column(column_name))

    def phi_columns(self) -> frozenset[str]:
        """Every column name any PHI-flagged term claims.

        This is the PHI-detection gate's answer key (CF-V1-E5-03, 100% recall)
        and the masking policy's input (CF-V4-E2-03). One source, so the two
        cannot disagree.
        """
        return frozenset(
            _normalise(name) for term in self.terms if term.is_phi for name in term.synonyms
        )

    def is_phi_column(self, column_name: str) -> bool:
        return _normalise(column_name) in self.phi_columns()

    @property
    def phi_terms(self) -> tuple[GlossaryTerm, ...]:
        return tuple(t for t in self.terms if t.is_phi)

    def search(self, text: str) -> tuple[GlossaryTerm, ...]:
        """Business term OR column name — "date of birth" finds
        `date_of_birth`, which is CF-V1-E6-01's search requirement."""
        needle = _normalise(text)
        if not needle:
            return ()
        return tuple(
            t
            for t in self.terms
            if needle in _normalise(t.term)
            or any(needle in _normalise(s) for s in t.synonyms)
            or needle in _normalise(t.definition)
        )


def amend(
    current: GlossaryTerm, proposed: GlossaryTerm, *, approved_by_steward: bool
) -> GlossaryTerm:
    """Amend a term, refusing an unapproved PHI downgrade.

    Yes -> No is the only direction guarded, deliberately: raising a flag is
    always safe, and requiring approval to protect MORE data would be a
    control that punishes caution.
    """
    if current.is_phi and not proposed.is_phi and not approved_by_steward:
        raise PhiDowngradeError(
            f"{current.glossary_id} ({current.term}) is PHI-flagged, and clearing that flag "
            "needs steward approval — the masking policy, the detection gate and the vector "
            "store's PHI-absence guarantee all read it."
        )
    return proposed
