"""Reading the governed canonical model.

Both the AI side (context assembly) and the deterministic side (mapping-spec
validation) need to know which targets are legal and what type each declares.
That derivation lives here, next to the provider, so `engine/` never has to
import `intelligence/` to get at it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from cinqflow.knowledge.provider import KnowledgeProvider


@dataclass(frozen=True)
class CanonicalModel:
    """A flattened, read-only view of one domain's governed target model."""

    domain: str
    citation: str
    #: `table.field` -> declared type, excluding platform-populated columns
    types: dict[str, str] = field(default_factory=dict)
    #: entity table -> declared primary key columns
    primary_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    system_populated: frozenset[str] = frozenset()
    contested: frozenset[str] = frozenset()
    #: `table.field` for every field the model marks as PHI
    phi: frozenset[str] = frozenset()

    @property
    def legal_targets(self) -> frozenset[str]:
        return frozenset(self.types)

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(sorted(self.primary_keys))

    def type_of(self, target: str) -> str | None:
        return self.types.get(target)

    def fields_of(self, table: str) -> dict[str, str]:
        """Every legal field of one entity, in declaration order: name -> type.

        Silver Raw is rendered from this, so a table carries the whole entity and
        not merely the fields today's mapping happens to fill.
        """
        prefix = f"{table}."
        return {
            target[len(prefix) :]: declared
            for target, declared in self.types.items()
            if target.startswith(prefix)
        }

    def phi_of(self, table: str) -> frozenset[str]:
        prefix = f"{table}."
        return frozenset(t[len(prefix) :] for t in self.phi if t.startswith(prefix))

    def table_of(self, target: str) -> str | None:
        return target.split(".", 1)[0] if "." in target else None

    def required_targets(self, tables: Iterable[str]) -> tuple[str, ...]:
        """The minimum a spec touching these entities must map.

        Only an entity whose primary key is exactly *one* mappable column counts
        - that column is the entity's identity, and a Silver row without it is
        unidentifiable regardless of feed (e.g. `members.source_system_id`). A
        composite key (e.g. `members_enrollment_segments`'s
        `[member_plan, member_payor, insurance_id, source_system_id]`) is not
        enforced component by component: which of those a given feed can even
        supply is itself feed-dependent judgment, not a blanket rule - exactly
        the "no defensible candidate, never guessed" principle this platform
        already applies to the AI side. `source_system`-style key columns the
        DDL marks system-populated never count: a mapping never fills them in.

        Derived entirely from `primary_key`, already governed data every
        canonical YAML declares - no new authoring, no new knowledge shape.
        """
        result: list[str] = []
        for table in tables:
            mappable = [
                f"{table}.{column}"
                for column in self.primary_keys.get(table, ())
                if f"{table}.{column}" in self.types
            ]
            if len(mappable) == 1:
                result.append(mappable[0])
        return tuple(result)


#: An empty model is not an error: a domain may simply have no governed model yet.
EMPTY = CanonicalModel(domain="", citation="none")


def load_canonical(provider: KnowledgeProvider, domain: str) -> CanonicalModel:
    doc = provider.get_canonical(domain)
    if doc is None:
        return EMPTY

    system = frozenset(doc.content.get("system_populated", []))
    types: dict[str, str] = {}
    primary_keys: dict[str, tuple[str, ...]] = {}
    phi: set[str] = set()

    for entity in doc.content.get("entities", []):
        table = entity.get("table")
        if not table:
            continue
        primary_keys[table] = tuple(entity.get("primary_key", []))
        for declared in entity.get("fields", []):
            name = declared.get("name")
            if name and name not in system:
                types[f"{table}.{name}"] = str(declared.get("type", "string"))
                if declared.get("phi"):
                    phi.add(f"{table}.{name}")

    return CanonicalModel(
        domain=domain,
        citation=doc.citation,
        types=types,
        primary_keys=primary_keys,
        system_populated=system,
        contested=frozenset(
            str(c.get("field")) for c in doc.content.get("contested_fields", []) if c.get("field")
        ),
        phi=frozenset(phi),
    )
