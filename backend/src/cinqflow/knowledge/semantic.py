"""Lexical semantic-candidate retrieval: the fallback path.

Deterministic lookup (`YamlKnowledgeProvider.get_glossary`, exact canonical
field names, `knowledge/decisions.py`) always runs first. This module only ever
sees the columns that lookup could not place - it exists to help an analyst
understand an otherwise-`unknown` column, never to decide a mapping on its own.
`recommend_mapping._validate` enforces that boundary: a semantic match can only
ever downgrade a column to `ambiguous`, never to `candidate`.

Deliberately lexical, not embeddings. This platform's default LLM provider is an
offline, deterministic stub (`intelligence/llm.py`), and every other knowledge
path here is reproducible byte-for-byte from a YAML file, in-process, with no
network call, no model weights to pin, and no vector index to build or drift.
A pure-stdlib similarity score keeps that property: identical input always
yields an identical score. `knowledge/provider.py` already anticipates swapping
this for a real embedding index later - that means adding another
`KnowledgeProvider` behind the same protocol method, not touching this one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

#: Recorded in every artifact's provenance next to the citation, so a later
#: change to the scoring method is visible in what was actually used.
ALGORITHM = "lexical_v1"

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _token_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _char_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


@dataclass(frozen=True)
class ConceptEntry:
    """One governed concept the index can point at.

    `target` is set only when the entry itself names a legal canonical field (a
    glossary term with `maps_toward`, or a canonical field itself) - a concept
    with no target can still explain a column, never propose a mapping for it.
    """

    ref: str  # e.g. "glossary:member_dob" or "canonical:members.date_of_birth"
    labels: tuple[str, ...]  # short names: term, aliases, canonical field name
    text: str  # `means` / description - longer prose, scored by token overlap
    target: str | None


@dataclass(frozen=True)
class SemanticMatch:
    concept_ref: str
    target: str | None
    score: float


def build_concept_index(
    *, glossary_terms: list[dict], canonical_entities: list[dict]
) -> list[ConceptEntry]:
    """One entry per glossary term and per canonical field.

    Callers pass in whatever `ContextBuilder` already selected for this job -
    the trimmed glossary and canonical view, never the whole knowledge base -
    so the index is exactly as scoped as the rest of the context.
    """
    entries: list[ConceptEntry] = []
    for term in glossary_terms:
        name = str(term.get("term", ""))
        aliases = tuple(str(a) for a in term.get("aliases", []))
        target = str(term.get("maps_toward") or "").strip() or None
        entries.append(
            ConceptEntry(
                ref=f"glossary:{name}",
                labels=(name, *aliases),
                text=str(term.get("means", "")),
                target=target,
            )
        )
    for entity in canonical_entities:
        table = entity.get("table") or entity.get("entity") or ""
        for field in entity.get("fields", []):
            leaf = str(field.get("name", ""))
            target = leaf if "." in leaf else f"{table}.{leaf}"
            entries.append(
                ConceptEntry(
                    # Deliberately just the leaf name, never the qualified
                    # `table.field` target, as a similarity label: almost every
                    # column in one feed shares the entity's own name as a
                    # prefix (`member_*` against `members.*`), which would
                    # otherwise inflate character-similarity for any column
                    # against any field of the entity it happens to feed,
                    # regardless of what either actually means.
                    ref=f"canonical:{target}",
                    labels=(leaf,),
                    text=str(field.get("means", "")),
                    target=target,
                )
            )
    return entries


def _squash(text: str) -> str:
    return "".join(_WORD.findall(text.lower()))


def _score(column: str, column_tokens: set[str], entry: ConceptEntry) -> float:
    label_tokens: set[str] = set()
    for label in entry.labels:
        label_tokens |= _tokens(label)
    token_score = _token_similarity(column_tokens, label_tokens | _tokens(entry.text))

    char_score = 0.0
    if len(column_tokens) <= 1:
        # A column with no internal separator might just be a governed name
        # spelled as one compound word (`dateofbirth` for `date_of_birth`).
        # Squashing both sides the same way before comparing catches exactly
        # that spelling difference - and only that: two multi-word names that
        # merely share a stem (`member_phone` vs `member_first_name`) never
        # reach this branch at all, because a real multi-token column is
        # already well served by `token_score` above, and comparing raw
        # underscored strings character-by-character was inflating similarity
        # from the shared `member_` prefix alone, regardless of meaning.
        squashed_column = _squash(column)
        char_score = max(
            (_char_similarity(squashed_column, _squash(label)) for label in entry.labels),
            default=0.0,
        )
    return max(token_score, char_score)


def find_matches(
    *,
    columns: list[str],
    entries: list[ConceptEntry],
    floor: float = 0.55,
    top_k: int = 3,
) -> dict[str, list[SemanticMatch]]:
    """The best `top_k` concepts for each column, above `floor`.

    Score is the stronger of token overlap and character-sequence similarity: a
    column and a concept rarely share both a vocabulary and a spelling, so
    taking the maximum catches a merged compound (`dateofbirth` vs
    `date_of_birth`, by spelling) and a reordered phrase (`dob_member` vs
    `member dob`, by vocabulary) without inflating scores for the common case
    where a column just plainly isn't any of these concepts.
    """
    matches: dict[str, list[SemanticMatch]] = {}
    for column in columns:
        column_tokens = _tokens(column)
        scored = [
            SemanticMatch(concept_ref=entry.ref, target=entry.target, score=round(score, 3))
            for entry in entries
            if (score := _score(column, column_tokens, entry)) >= floor
        ]
        scored.sort(key=lambda m: -m.score)
        if scored:
            matches[column] = scored[:top_k]
    return matches
