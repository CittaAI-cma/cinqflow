"""CF-V1-E6-01 — the canonical model browser: domains, entities, fields.

    "Canonical target browser — domains → entities → fields with glossary
     definitions inline"
    "You cannot map to a model you cannot see; doubles as the thin cut of E10
     and E14."
    — CF-V1-E6-01

    "generated from deployed model (drift impossible by construction) ·
     business-term search · 'definition missing' visible"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

"DRIFT IMPOSSIBLE BY CONSTRUCTION" IS A CLAIM ABOUT WHERE THE DATA COMES FROM,
and it is the whole design. Both halves of this browser are GENERATED:

  • the DEPLOYED half from `core.schema_spec` — the same declaration the DDL
    is rendered from, and the one the conformance kit compares the introspected
    database against. If the database and this page disagreed, conformance
    would already be red.
  • the DECLARED half from the client's own 171-term glossary, which records
    for every term which domains and tables carry it — `BG-004 Member Date of
    Birth` names Members, Claim_IPHeader, Claim_Pharmacy and DailyCensus.

There is no third list. A hand-maintained data dictionary is precisely the
artefact this platform exists to retire, and one embedded in the tool that
replaced it would be worse than the spreadsheet — because it would look
authoritative.

THE GAP IS A FEATURE, NOT AN EMBARRASSMENT. The client has designed three
domains and twenty entities; Wave 0 deployed one of them. A browser that showed
only what is deployed would hide the roadmap, and one that showed only what is
declared would imply things exist that do not. So every entity says which it
is, and `CanonicalModel.gap` is the list somebody can plan against.

"DEFINITION MISSING" IS SHOWN, NOT SUPPRESSED. A deployed column no glossary
term claims has no business definition, and the honest rendering of that is the
words "definition missing" — not a blank cell, and certainly not the column's
name repeated back as if it were an explanation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Schema, TypeName

#: What a field with no glossary term shows. The exact string, so a screen, a
#: test and a steward's coverage report all mean the same thing by it.
DEFINITION_MISSING = "definition missing"


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


#: A word inside an identifier: a run of letters or digits, with camelCase
#: boundaries counted. `Patient_dob` -> (patient, dob); `MemberDateOfBirth` ->
#: (member, date, of, birth).
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+")


def _words(name: str) -> frozenset[str]:
    """The words a name is made of, for whole-word synonym matching.

    Whole words rather than substrings, deliberately. `DOB` must find
    `Patient_dob`, but a substring match would make `id` find every column in
    the estate — and a search that returns everything has answered nothing.
    """
    return frozenset(part.lower() for part in _WORD.findall(name))


@dataclass(frozen=True)
class CanonicalField:
    """One field of the canonical model, with its definition inline.

    `definition` empty means no glossary term claims this column — which is a
    finding, not a formatting problem. `is_defined` is what the screen and the
    coverage metric both read, so neither can drift from the other.
    """

    name: str
    entity: str
    domains: tuple[str, ...] = ()
    definition: str = ""
    glossary_id: str | None = None
    term: str = ""
    #: Every spelling this concept has arrived under, from the client's own
    #: analysts. Carried so that an engineer typing `MBR_DOB` finds
    #: `Member_Date_Of_Birth` — the synonym set is the bridge between the
    #: payer's vocabulary and the canonical one, and a browser that ignored it
    #: would answer only the BA's half of the question.
    synonyms: tuple[str, ...] = ()
    is_phi: bool = False
    #: From the DEPLOYED spec, and therefore None for a field the client has
    #: designed but nothing has provisioned yet.
    type: TypeName | None = None
    nullable: bool | None = None
    deployed: bool = False

    @property
    def is_defined(self) -> bool:
        return bool(self.definition.strip())

    @property
    def shown_definition(self) -> str:
        return self.definition if self.is_defined else DEFINITION_MISSING


@dataclass(frozen=True)
class CanonicalEntity:
    """One entity — a table in the canonical model — and its fields.

    KEYED BY TABLE, CARRYING ITS DOMAINS. The client's glossary records that a
    TERM belongs to several domains and several tables; it does not say which
    domain pairs with which table. Building entities from that cross product
    produced 44 entities where the estate has 20 — `Claim_IPHeader` filed
    under Census, Claims and Enrollment as if they were three different
    tables. So an entity is a table, and it lists every domain that claims it.
    That is also simply true: `Members` really is used by Enrollment and by
    Claims.

    `deployed` distinguishes "this exists in the database" from "the client has
    designed this". Both belong in the browser; conflating them is how a
    mapping gets written against a table nobody has created.
    """

    name: str
    domains: tuple[str, ...] = ()
    fields: tuple[CanonicalField, ...] = ()
    schema: str = ""
    deployed: bool = False
    comment: str = ""

    @property
    def defined_fields(self) -> tuple[CanonicalField, ...]:
        return tuple(f for f in self.fields if f.is_defined)

    @property
    def undefined_fields(self) -> tuple[CanonicalField, ...]:
        """The steward's worklist for this entity, and it is a real one."""
        return tuple(f for f in self.fields if not f.is_defined)

    @property
    def coverage(self) -> tuple[int, int]:
        """(defined, total). Two integers, so a reader can recompute the rate
        rather than trust a rounded percentage."""
        return len(self.defined_fields), len(self.fields)

    @property
    def phi_fields(self) -> tuple[CanonicalField, ...]:
        return tuple(f for f in self.fields if f.is_phi)

    def field(self, name: str) -> CanonicalField | None:
        target = _normalise(name)
        for candidate in self.fields:
            if _normalise(candidate.name) == target:
                return candidate
        return None


@dataclass(frozen=True)
class CanonicalModel:
    """Every domain, entity and field the estate has — designed or deployed."""

    entities: tuple[CanonicalEntity, ...] = ()
    #: Tables the DEPLOYED schemas hold that no glossary domain claims. Kept
    #: rather than hidden: a table nobody's business vocabulary mentions is
    #: exactly the thing a steward should look at.
    unclaimed_tables: tuple[str, ...] = field(default_factory=tuple)

    @property
    def domains(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for entity in self.entities:
            for domain in entity.domains:
                seen.setdefault(domain, None)
        return tuple(sorted(seen))

    def in_domain(self, domain: str) -> tuple[CanonicalEntity, ...]:
        """Entities this domain claims. One entity may appear under two, which
        is not a bug — `Members` really is used by Enrollment and by Claims."""
        return tuple(e for e in self.entities if domain in e.domains)

    def entity(self, name: str) -> CanonicalEntity | None:
        target = _normalise(name)
        for candidate in self.entities:
            if _normalise(candidate.name) == target:
                return candidate
        return None

    @property
    def deployed(self) -> tuple[CanonicalEntity, ...]:
        return tuple(e for e in self.entities if e.deployed)

    @property
    def gap(self) -> tuple[CanonicalEntity, ...]:
        """Designed, not yet provisioned. The roadmap, computed.

        Shown rather than hidden: a browser listing only deployed entities
        would let a BA conclude the estate is smaller than it is, and one
        listing everything without the distinction would let them map to a
        table that does not exist.
        """
        return tuple(e for e in self.entities if not e.deployed)

    @property
    def coverage(self) -> tuple[int, int]:
        defined = sum(len(e.defined_fields) for e in self.entities)
        return defined, sum(len(e.fields) for e in self.entities)

    def search(self, text: str) -> tuple[CanonicalField, ...]:
        """Business term OR column name. CF-V1-E6-01's search requirement.

        "date of birth" must find `date_of_birth`, and `MBR_DOB` must find
        Member Date of Birth — the two directions are the same question asked
        by a BA and by an engineer, and a browser that only answered one of
        them would send the other back to the spreadsheet.
        """
        needle = _normalise(text)
        if not needle:
            return ()
        return tuple(
            f
            for entity in self.entities
            for f in entity.fields
            if needle in _normalise(f.name)
            or needle in _normalise(f.term)
            or needle in _normalise(f.definition)
            or needle in _normalise(f.entity)
            # The synonym set is the bridge. Without it `DOB` finds nothing,
            # because the canonical name is `Member_Date_Of_Birth` — and an
            # engineer holding a payer's file header would be sent back to the
            # spreadsheet by the very screen built to replace it.
            or any(needle == _normalise(s) or text.strip().lower() in _words(s) for s in f.synonyms)
        )


def canonical_schemas() -> tuple[Schema, ...]:
    """The schemas that ARE the canonical target. Declared here, once.

    `silver_ods` is the member-centric canonical model and is provisioned
    EMPTY until identity resolution lands in Wave 3; `silver_raw` holds the
    typed, contracted landing of a feed. Both are legitimate mapping targets
    today, and naming them in one place is what stops the browser and a future
    mapping validator disagreeing about what "canonical" means.

    Deliberately NOT every schema: `control`, `audit` and `queue` are the
    platform's own plumbing, and a BA offered `batch_stage_status` as a mapping
    target has been shown the wrong thing.
    """
    from cinqflow.core.schema_spec import SILVER_ODS_SCHEMA, SILVER_RAW_SCHEMA

    return (SILVER_RAW_SCHEMA, SILVER_ODS_SCHEMA)


def build(schemas: Sequence[Schema], glossary: Glossary) -> CanonicalModel:
    """Generate the browser from the deployed spec and the client's glossary.

    Neither input is authored for this screen, which is what makes drift
    impossible: changing the model means changing the DDL spec (and the
    conformance kit checks the database against it) or changing the glossary
    (a governed object with its own lifecycle).
    """
    deployed = _deployed_tables(schemas)
    fields_by_entity: dict[str, dict[str, CanonicalField]] = {}
    domains_by_entity: dict[str, dict[str, None]] = {}
    claimed: set[str] = set()

    # ── the DECLARED model, from the client's own vocabulary ────────────────
    for term in glossary.terms:
        for table_name in term.mapped_tables:
            claimed.add(_normalise(table_name))
            domains = domains_by_entity.setdefault(table_name, {})
            for domain in term.mapped_domains:
                domains.setdefault(domain, None)
            fields = fields_by_entity.setdefault(table_name, {})
            for column_name in _canonical_names(term):
                spec = deployed.get((_normalise(table_name), _normalise(column_name)))
                fields[_normalise(column_name)] = CanonicalField(
                    name=column_name,
                    entity=table_name,
                    domains=tuple(term.mapped_domains),
                    definition=term.definition,
                    glossary_id=term.glossary_id,
                    term=term.term,
                    synonyms=term.synonyms,
                    is_phi=term.is_phi,
                    type=spec[0] if spec else None,
                    nullable=spec[1] if spec else None,
                    deployed=spec is not None,
                )

    # ── the DEPLOYED model, from the spec the DDL is rendered from ──────────
    #
    # Every provisioned column appears, INCLUDING the ones no term claims —
    # those are what "definition missing" is for, and hiding them would make
    # the coverage number flattering and useless.
    for schema in schemas:
        for table in schema.tables:
            name = _canonical_table_name(table.name, fields_by_entity)
            fields = fields_by_entity.setdefault(name, {})
            domains = domains_by_entity.setdefault(name, {})
            if not domains:
                domains.setdefault(_domain_of(table.name, glossary), None)
            for column in table.columns:
                if _normalise(column.name) in fields:
                    continue
                terms = glossary.for_column(column.name)
                claimed_by = terms[0] if len(terms) == 1 else None
                fields[_normalise(column.name)] = CanonicalField(
                    name=column.name,
                    entity=name,
                    domains=tuple(domains),
                    definition=claimed_by.definition if claimed_by else "",
                    glossary_id=claimed_by.glossary_id if claimed_by else None,
                    term=claimed_by.term if claimed_by else "",
                    synonyms=claimed_by.synonyms if claimed_by else (),
                    is_phi=column.is_phi or bool(claimed_by and claimed_by.is_phi),
                    type=column.type,
                    nullable=column.nullable,
                    deployed=True,
                )

    built = tuple(
        CanonicalEntity(
            name=name,
            domains=tuple(sorted(domains_by_entity.get(name, {}))),
            fields=tuple(sorted(fields.values(), key=lambda f: f.name)),
            schema=_schema_of(name, schemas),
            deployed=any(_normalise(t.name) == _normalise(name) for s in schemas for t in s.tables),
            comment=_comment_of(name, schemas),
        )
        for name, fields in sorted(fields_by_entity.items())
    )
    return CanonicalModel(
        entities=built,
        unclaimed_tables=tuple(
            sorted(t.name for s in schemas for t in s.tables if _normalise(t.name) not in claimed)
        ),
    )


def _canonical_table_name(table_name: str, known: dict[str, dict[str, CanonicalField]]) -> str:
    """The glossary's spelling of a deployed table, where it has one.

    `silver_raw.members` and the glossary's `Members` are the same entity, and
    listing both would put the estate's most important table on the screen
    twice with half its fields each. The glossary's spelling wins because it is
    the business vocabulary, which is what a canonical MODEL browser is for.
    """
    target = _normalise(table_name)
    for existing in known:
        if _normalise(existing) == target:
            return existing
    return table_name


def _canonical_names(term: GlossaryTerm) -> tuple[str, ...]:
    """The corrected column names a term supplies, or its slug if it has none.

    The CORRECTED names, deliberately: `mapped_columns_original` is the
    payer-side vocabulary a mapping reads FROM, and putting it in the canonical
    browser would offer a BA a target that is really a source.
    """
    if term.mapped_columns_corrected:
        return term.mapped_columns_corrected
    return (term.slug.replace("-", "_"),)


def _deployed_tables(
    schemas: Sequence[Schema],
) -> dict[tuple[str, str], tuple[TypeName, bool]]:
    return {
        (_normalise(table.name), _normalise(column.name)): (column.type, column.nullable)
        for schema in schemas
        for table in schema.tables
        for column in table.columns
    }


def _domain_of(table_name: str, glossary: Glossary) -> str:
    """Which business domain a deployed table belongs to, per the glossary.

    A table the vocabulary does not mention gets `unmapped` rather than being
    dropped: a provisioned table nobody's business language names is a finding
    worth a steward's attention, not a row to hide.
    """
    target = _normalise(table_name)
    for term in glossary.terms:
        if any(_normalise(t) == target for t in term.mapped_tables):
            return term.mapped_domains[0] if term.mapped_domains else "unmapped"
    return "unmapped"


def _schema_of(table_name: str, schemas: Sequence[Schema]) -> str:
    target = _normalise(table_name)
    for schema in schemas:
        for table in schema.tables:
            if _normalise(table.name) == target:
                return schema.name
    return ""


def _comment_of(table_name: str, schemas: Sequence[Schema]) -> str:
    target = _normalise(table_name)
    for schema in schemas:
        for table in schema.tables:
            if _normalise(table.name) == target:
                return table.comment
    return ""
