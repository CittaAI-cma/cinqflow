"""Deterministic parsing of the analyst decision register.

`knowledge/decisions/analyst_decisions.yaml` is a governance log, not a mapping
table: `decision_id`, `context`, `decision` (free text), `rationale`, etc. When a
decision settles which canonical field one source column belongs in, it says so
in prose, inside its own `decision` or `trigger` text - see the file's own
records, e.g. "member_id -> members.source_system_id with on_null reject".

This module extracts that `<source column> -> <table.field>` relationship by
pattern. It is deterministic lookup, not semantic retrieval: nothing here scores
similarity or guesses - a decision is only applied to a column when the
decision's own governed text names that column outright. `recommend_mapping`
uses this to prefer a decision an analyst already approved over asking a model
to re-derive the same answer from scratch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: "member_id -> members.source_system_id", tolerant of the words around it.
_ARROW = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*->\s*([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)")


@dataclass(frozen=True)
class DecisionHint:
    """One column-level relationship a governed decision already settled."""

    decision_id: str
    title: str
    source_column: str  # lowercase, exactly as the decision text named it
    target: str  # table.field, exactly as the decision text named it
    rationale: str
    reversibility: str | None
    generalisable: bool


def parse_decision_hints(records: list[dict]) -> list[DecisionHint]:
    """Every `source -> table.field` relationship stated across the records.

    A record with no such relationship (a constant, a value map, a structural
    call) contributes nothing - this only ever extracts column routing, which is
    the one shape `recommend_mapping` can act on.
    """
    hints: list[DecisionHint] = []
    for record in records:
        text = " ".join(
            str(record.get(key, "")) for key in ("trigger", "decision") if record.get(key)
        )
        for source_column, target in _ARROW.findall(text):
            hints.append(
                DecisionHint(
                    decision_id=str(record.get("decision_id", "")),
                    title=str(record.get("title", "")),
                    source_column=source_column.strip().lower(),
                    target=target.strip(),
                    rationale=str(record.get("rationale", "")),
                    reversibility=record.get("reversibility"),
                    generalisable=bool(record.get("generalisable", False)),
                )
            )
    return hints


def hints_for_columns(hints: list[DecisionHint], columns: list[str]) -> dict[str, DecisionHint]:
    """The one decision that applies to each observed column, if any.

    Matching is by exact column name (case-insensitive) - the same bar the
    decision text itself sets. Where two decisions name the same column, the
    first in file order wins, which is chronological (`decided_by` dates run
    forward through the register); resolving a real `supersedes` chain is a
    known gap, not attempted here.
    """
    by_lower = {c.lower(): c for c in columns}
    selected: dict[str, DecisionHint] = {}
    for hint in hints:
        real_name = by_lower.get(hint.source_column)
        if real_name and real_name not in selected:
            selected[real_name] = hint
    return selected
