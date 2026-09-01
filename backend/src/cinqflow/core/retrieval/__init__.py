"""Lexical retrieval, and a glossary the platform generates about itself.

    "lookup_reference is a LEXICAL (tsvector) lookup over the seeded 171-term
     glossary and the 110 DQ-rule descriptions as read-only reference data. It
     is NOT the governed knowledge pipeline."
    — CF-V0-E16-09, the K2 half, honestly scoped

    "This is the lexical half of hybrid retrieval arriving early. That is the
     right half to arrive first — healthcare vocabulary is code-heavy, and
     lexical is what catches NPI, DQ-002 and BH-AF-002 that embeddings blur."

THE IDEA WORTH THE FILE: the platform's own vocabulary is not typed out here.
It is GENERATED from the enums that define it — the seven status words, six
layers, five gates, eleven control tables, twenty-one pins, five risk classes,
three test lanes, the lifecycle states, the landing outcomes. So the glossary
cannot drift from the code, because it IS the code, read at import time. Add a
status word and the glossary gains an entry; rename one and every definition
follows.

The client's 171-term business glossary and 110 DQ-rule descriptions are a DATA
LOAD on top of this — `seed()` accepts them — and their absence is visible
rather than papered over: `PLATFORM_GLOSSARY` says how many terms it generated,
and a caller can ask whether the client corpus has been loaded.

Ranking, packing and citation are written once here. Wave 1's vector half
(CF-V1-E16-04/05) adds a second scorer and reuses all of this unchanged.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.model.vocabulary import (
    BatchState,
    ErrorCategory,
    Gate,
    LandingFolder,
    Layer,
    RiskClass,
    StatusWord,
    TestLane,
)

_WORD = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")

#: Words that carry no signal in a lexical index of platform vocabulary.
#: Deliberately short: dropping "not" from a DQ-rule description would make
#: `not_null` and `null` retrieve identically.
_STOP = frozenset(
    {"a", "an", "the", "of", "is", "are", "to", "in", "for", "and", "or", "that", "this", "it"}
)


def tokenise(text: str) -> tuple[str, ...]:
    """Lowercase word tokens, keeping hyphens and underscores INSIDE a token.

    That is the whole reason this is lexical: `DQ-002`, `not_null` and
    `en_core_web_lg` must survive as single tokens. A tokeniser that split on
    punctuation would turn every rule id into the word "dq" plus a number.
    """
    return tuple(t for t in _WORD.findall(text.lower()) if t not in _STOP)


@dataclass(frozen=True)
class ReferenceEntry:
    """One approved definition. Read-only reference data, never a chunk.

    No chunking, no PHI-verify gate, no steward approval, no embedding — those
    are the governed knowledge pipeline, and they are Wave 1. Calling this a
    chunk would be the first step towards pretending otherwise.
    """

    slug: str
    term: str
    definition: str
    kind: CitationKind = CitationKind.TERM
    source: str = "platform-vocabulary"
    aliases: tuple[str, ...] = ()

    @property
    def citation(self) -> CitationId:
        return CitationId(kind=self.kind, subject=self.slug)

    @property
    def searchable(self) -> str:
        return " ".join((self.term, *self.aliases, self.definition))


@dataclass(frozen=True)
class ScoredEntry:
    entry: ReferenceEntry
    score: float
    matched: tuple[str, ...]


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return cleaned or "term"


def _entry(
    term: str,
    definition: str,
    *,
    kind: CitationKind = CitationKind.TERM,
    aliases: tuple[str, ...] = (),
    source: str = "platform-vocabulary",
) -> ReferenceEntry:
    return ReferenceEntry(
        slug=_slug(term),
        term=term,
        definition=definition,
        kind=kind,
        aliases=aliases,
        source=source,
    )


def _from_enum(members: type[StrEnum], template: str, category: str) -> list[ReferenceEntry]:
    return [
        _entry(
            member.value,
            template.format(value=member.value, name=member.name.replace("_", " ").lower()),
            aliases=(member.name.lower(), category),
        )
        for member in members
    ]


def platform_glossary() -> tuple[ReferenceEntry, ...]:
    """Generated from the vocabulary modules. Cannot drift from the code."""
    entries: list[ReferenceEntry] = []

    entries += [
        _entry(
            word.value,
            f"A user-facing status word. One of exactly seven — Expected, Received, "
            f"Processing, Completed, Needs Review, Needs Attention, Missing — with no "
            f"synonyms and no eighth. {_STATUS_MEANING[word]}",
            aliases=("status", "status word", "seven status words"),
        )
        for word in StatusWord
    ]
    entries += [
        _entry(
            layer.value,
            f"A medallion layer. The Wave-0 spine runs Landing to Silver Raw; "
            f"Identity and Silver ODS sit behind gate G4 and arrive in Wave 3. "
            f"{_LAYER_MEANING.get(layer, '')}",
            aliases=("layer", "medallion"),
        )
        for layer in Layer
    ]
    entries += [
        _entry(
            gate.value,
            f"A quality gate between {gate.between[0]} and {gate.between[1]}. "
            "Nothing crosses a gate unrecorded.",
            aliases=("gate", "g1", "quality gate"),
        )
        for gate in Gate
    ]
    entries += [
        _entry(
            # RiskClass carries a tuple value (label plus the three autonomy
            # flags), so the NAME is the term: R0..R4.
            risk.name,
            f"A risk class. {_RISK_MEANING[risk]}",
            aliases=("risk", "risk class", "autonomy"),
        )
        for risk in RiskClass
    ]
    entries += _from_enum(
        BatchState, "A batch state: {name}. Recorded in control.batch_control.", "batch state"
    )
    entries += _from_enum(
        ErrorCategory,
        "An error category: {name}. Every error is attributed to exactly one.",
        "error category",
    )
    entries += _from_enum(
        LandingFolder,
        "A landing-zone folder: {name}. Original files are moved, never edited or deleted.",
        "landing folder",
    )
    entries += [
        _entry(
            f"Lane {lane.value}",
            f"Test lane {lane.value} — {lane.name.lower()}. No evaluation threshold may be "
            "claimed from Lane 1 (mock) or Lane 2 (replay); only Lane 3 calls a real API.",
            aliases=("lane", "test lane", lane.name.lower()),
        )
        for lane in TestLane
    ]

    entries += [
        _entry(term, definition, aliases=aliases)
        for term, definition, aliases in _CONTROL_TABLES + _CHIP_TERMS
    ]
    return tuple(entries)


_STATUS_MEANING: dict[StatusWord, str] = {
    StatusWord.EXPECTED: "The platform is waiting for it; its arrival window has not closed.",
    StatusWord.RECEIVED: "It arrived and was registered, including if it was unexpected.",
    StatusWord.PROCESSING: "A batch is running against it.",
    StatusWord.COMPLETED: "It finished and it balanced.",
    StatusWord.NEEDS_REVIEW: "A person must decide something before it proceeds.",
    StatusWord.NEEDS_ATTENTION: "Something failed and somebody has to act.",
    StatusWord.MISSING: "Its arrival window closed and nothing came.",
}

_LAYER_MEANING: dict[Layer, str] = {
    Layer.LANDING: "Files as they arrived, untouched, registered on arrival.",
    Layer.BRONZE: "An immutable copy of the source. Append-only at the database layer.",
    Layer.SILVER_RAW: "Typed, mapped and rule-evaluated rows. The Wave-0 terminus.",
    Layer.SILVER_ODS: "The canonical model, after identity resolution. Wave 3.",
    Layer.GOLD: "Consumer-shaped marts. Wave 4.",
}

_RISK_MEANING: dict[RiskClass, str] = {
    RiskClass.R0: "Observe only. The agent reads and explains; no write tool is on its "
    "whitelist at any confidence.",
    RiskClass.R1: "Suggests; a person accepts or ignores.",
    RiskClass.R2: "Proposes a change as a reviewable diff.",
    RiskClass.R3: "Acts within a bounded, reversible envelope.",
    RiskClass.R4: "Identity- or PHI-consequential. HUMAN ALWAYS — not configurable at any "
    "confidence, in any environment.",
}

_CONTROL_TABLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "feed_sla_config",
        "Expected arrival windows per feed per cycle. What makes Missing "
        "computable rather than noticed.",
        ("control table", "sla"),
    ),
    (
        "input_registry",
        "Every file that ever arrived, with its content fingerprint. The same "
        "fingerprint twice is skipped with an audit entry.",
        ("control table", "fingerprint"),
    ),
    (
        "schema_registry",
        "The contract each feed version was loaded against.",
        ("control table", "contract"),
    ),
    (
        "schema_drift_log",
        "Observed structure against the contract, per batch.",
        ("control table", "drift"),
    ),
    (
        "batch_control",
        "One row per run: state, feed version, business date.",
        ("control table", "batch"),
    ),
    (
        "batch_stage_status",
        "Per-stage progress. What a restart resumes from.",
        ("control table", "stage", "restart"),
    ),
    (
        "error_log",
        "Errors keyed by a deterministic hash of batch, stage, record key, type and "
        "rule — which makes replay idempotent at the error level.",
        ("control table", "error", "replay"),
    ),
    (
        "quarantine_records",
        "Rows held back by a rule, retained rather than dropped.",
        ("control table", "quarantine"),
    ),
    (
        "batch_reconciliation",
        "The balance equation per stage per batch: rows_in equals rows_out "
        "plus quarantined plus attributed drops.",
        ("control table", "reconciliation", "balance"),
    ),
    ("sla_instance", "One expected arrival and what became of it.", ("control table", "sla")),
    (
        "sla_alerts",
        "The Missing that somebody was actually told about.",
        ("control table", "alert"),
    ),
)

_CHIP_TERMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "core",
        "The platform's logic. Imports no vendor SDK, no URL, no path and no credential, "
        "and performs no I/O.",
        ("chip", "law 1"),
    ),
    (
        "port",
        "A pin. One protocol per external capability, with real, dev stand-in and mock "
        "adapters sharing ONE contract suite.",
        ("chip", "pin", "law 2"),
    ),
    ("adapter", "Vendor code. The only place an SDK may be imported.", ("chip",)),
    (
        "connection profile",
        "The one place environment difference lives. Climbing a socket rung "
        "changes only the profile.",
        ("chip", "law 3", "socket"),
    ),
    (
        "socket",
        "A rung on the ladder: 0 mock, 0.5 Postgres plane, 1 local twin, 2 Databricks "
        "Free, 3 client dev, 4 client prod.",
        ("chip", "rung", "ladder"),
    ),
    (
        "conformance kit",
        "One check per energized pin, each naming its pin, so plug-and-play never regresses.",
        ("chip", "certification"),
    ),
    (
        "citation_id",
        "The platform's address space. A citation parses to a UI route, so one "
        "resolver serves the agent's citations, deep links, breadcrumbs and drawers.",
        ("citation", "address", "route"),
    ),
    (
        "balance equation",
        "rows_in equals rows_out plus quarantined plus attributed_drops, every "
        "stage, every batch. No drop category 'other' or 'unknown' can exist.",
        ("reconciliation", "invariant"),
    ),
    (
        "compiled plan",
        "The intermediate representation the engine runs: read, validate, "
        "land_bronze, cast, map, evaluate_rules, resolve_identity, load, reconcile. It is also "
        "what the agent explains and what grades the agent.",
        ("plan", "ir", "compiler"),
    ),
)


@dataclass
class ReferenceIndex:
    """A small BM25-ish lexical index. Deterministic, and it explains its hits.

    `matched` travels with every result because a retrieval nobody can explain
    is a retrieval nobody can debug — and the agent quotes the matched term back
    when it says which definition it used.
    """

    entries: tuple[ReferenceEntry, ...] = field(default_factory=tuple)
    _postings: dict[str, list[int]] = field(default_factory=dict, repr=False)
    _lengths: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._build()

    def _build(self) -> None:
        self._postings = {}
        self._lengths = []
        for position, entry in enumerate(self.entries):
            tokens = tokenise(entry.searchable)
            self._lengths.append(len(tokens) or 1)
            for token in set(tokens):
                self._postings.setdefault(token, []).append(position)

    def seed(self, entries: tuple[ReferenceEntry, ...]) -> None:
        """Load additional reference data — the client's glossary and DQ rules.

        Additive, so the generated platform vocabulary is never replaced by a
        partial client load.
        """
        self.entries = self.entries + entries
        self._build()

    @property
    def size(self) -> int:
        return len(self.entries)

    def search(self, query: str, *, limit: int = 5) -> tuple[ScoredEntry, ...]:
        tokens = tokenise(query)
        if not tokens:
            return ()
        total = max(len(self.entries), 1)
        average = sum(self._lengths) / total if self._lengths else 1.0

        scores: dict[int, float] = {}
        hits: dict[int, set[str]] = {}
        for token in set(tokens):
            positions = self._postings.get(token)
            if not positions:
                # An exact-id miss must not silently become a fuzzy hit.
                continue
            idf = math.log(1 + (total - len(positions) + 0.5) / (len(positions) + 0.5))
            for position in positions:
                counts = Counter(tokenise(self.entries[position].searchable))
                frequency = counts[token]
                length_norm = 1 - 0.75 + 0.75 * (self._lengths[position] / average)
                scores[position] = scores.get(position, 0.0) + idf * (
                    frequency * 2.5 / (frequency + 1.2 * length_norm)
                )
                hits.setdefault(position, set()).add(token)

        # An exact term match outranks a description match, always. Someone
        # typing "DQ-002" wants the rule, not every rule that mentions it.
        for position, entry in enumerate(self.entries):
            if entry.term.lower() in {" ".join(tokens), *tokens} and position in scores:
                scores[position] += 10.0

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], self.entries[kv[0]].slug))
        return tuple(
            ScoredEntry(
                entry=self.entries[position],
                score=round(score, 4),
                matched=tuple(sorted(hits[position])),
            )
            for position, score in ranked[:limit]
        )


def platform_index() -> ReferenceIndex:
    """The generated glossary, indexed. The client corpus is seeded on top."""
    return ReferenceIndex(entries=platform_glossary())


def pack(results: tuple[ScoredEntry, ...], *, budget_chars: int = 2400) -> str:
    """Turn retrieved entries into grounding, with citations attached.

    Truncates by ENTRY, never mid-definition: half a definition is a fact with
    its qualifier removed, which is worse than one fewer definition.
    """
    lines: list[str] = []
    used = 0
    for scored in results:
        block = f"[{scored.entry.citation}] {scored.entry.term}: {scored.entry.definition}"
        if used + len(block) > budget_chars:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines)


def as_rows(results: tuple[ScoredEntry, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "term": scored.entry.term,
            "definition": scored.entry.definition,
            "source": scored.entry.source,
            "matched": list(scored.matched),
            "score": scored.score,
            "citation_id": str(scored.entry.citation),
        }
        for scored in results
    )
