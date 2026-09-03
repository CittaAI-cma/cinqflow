"""YAML-backed knowledge. The only module in the codebase that reads knowledge files."""

from __future__ import annotations

from pathlib import Path

import yaml

from cinqflow.knowledge.provider import KnowledgeDoc
from cinqflow.knowledge.semantic import ALGORITHM, build_concept_index, find_matches
from cinqflow.settings import Settings, get_settings


class YamlKnowledgeProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self.root: Path = (settings or get_settings()).knowledge_root

    def _load(self, relative: str) -> KnowledgeDoc | None:
        path = self.root / relative
        if not path.exists():
            return None
        content = yaml.safe_load(path.read_text()) or {}
        return KnowledgeDoc(ref=relative, version=int(content.get("version", 0)), content=content)

    def get_source(self, *, source_system: str, feed: str) -> KnowledgeDoc | None:
        for candidate in (f"sources/{source_system}__{feed}.yaml", f"sources/{feed}.yaml"):
            doc = self._load(candidate)
            if doc:
                return doc
        return None

    def get_canonical(self, domain: str) -> KnowledgeDoc | None:
        return self._load(f"canonical/{domain}.yaml")

    def get_domain_knowledge(self, domain: str) -> KnowledgeDoc | None:
        return self._load(f"domains/{domain}.yaml")

    def get_approved_mappings(self, domain: str) -> KnowledgeDoc | None:
        """All approved decision sets for the domain, merged into one document."""
        directory = self.root / "mappings" / "approved"
        if not directory.is_dir():
            return None

        sets: list[dict] = []
        files: list[str] = []
        version = 0
        for path in sorted(directory.glob("*.yaml")):
            content = yaml.safe_load(path.read_text()) or {}
            if content.get("domain") != domain:
                continue
            sets.extend(content.get("decision_sets", []))
            file_version = int(content.get("version", 0))
            files.append(f"{path.name}@{file_version}")
            version = max(version, file_version)

        if not sets:
            return None
        # One citation for the merged view; the contributing files are named inside
        # so provenance can still point at each one.
        return KnowledgeDoc(
            ref="mappings/approved",
            version=version,
            content={"decision_sets": sets, "files": files},
        )

    def get_glossary(self, terms: list[str]) -> KnowledgeDoc | None:
        """Returns only the terms whose term or aliases match the observed
        columns, plus - additively - which of `terms` matched nothing.

        `unmatched_columns` is what lets a caller know which columns need the
        semantic fallback (`get_semantic_candidates`) at all: deterministic
        lookup runs first for every column, always, and only what it could not
        place ever reaches the fallback.
        """
        doc = self._load("glossary.yaml")
        if not doc:
            return None
        normalized = {t: t.lower().replace(" ", "_") for t in terms}
        matched: list[dict] = []
        matched_columns: set[str] = set()
        for entry in doc.content.get("terms", []):
            keys = {
                str(entry.get("term", "")).lower(),
                *[str(a).lower() for a in entry.get("aliases", [])],
            }
            hit = {original for original, norm in normalized.items() if norm in keys}
            if hit:
                matched.append(entry)
                matched_columns |= hit
        unmatched = [t for t in terms if t not in matched_columns]
        return KnowledgeDoc(
            ref=doc.ref,
            version=doc.version,
            content={"terms": matched, "unmatched_columns": unmatched},
        )

    def get_decision_records(self, *, layer: str | None = None) -> KnowledgeDoc | None:
        doc = self._load("decisions/analyst_decisions.yaml")
        if not doc:
            return None
        records = doc.content.get("records", [])
        if layer:
            records = [r for r in records if r.get("layer") == layer]
        if not records:
            return None
        return KnowledgeDoc(ref=doc.ref, version=doc.version, content={"records": records})

    def get_semantic_candidates(self, *, columns: list[str], domain: str) -> KnowledgeDoc | None:
        if not columns:
            return None
        glossary_doc = self._load("glossary.yaml")
        canonical_doc = self.get_canonical(domain)
        if not glossary_doc and not canonical_doc:
            return None

        entries = build_concept_index(
            glossary_terms=(glossary_doc.content.get("terms", []) if glossary_doc else []),
            canonical_entities=(canonical_doc.content.get("entities", []) if canonical_doc else []),
        )
        matches = find_matches(columns=columns, entries=entries)
        if not matches:
            return None

        based_on = [d.citation for d in (glossary_doc, canonical_doc) if d]
        version = max((d.version for d in (glossary_doc, canonical_doc) if d), default=0)
        return KnowledgeDoc(
            ref=f"semantic/{ALGORITHM}",
            version=version,
            content={
                "algorithm": ALGORITHM,
                "based_on": based_on,
                "matches": {
                    column: [
                        {"concept_ref": m.concept_ref, "target": m.target, "score": m.score}
                        for m in ms
                    ]
                    for column, ms in matches.items()
                },
            },
        )
