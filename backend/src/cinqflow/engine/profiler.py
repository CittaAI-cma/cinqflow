"""Deterministic profiling: arithmetic and pattern matching only, no model.

profile_id is the hash of the computed facts, so the same bytes always produce the
same profile identity and re-profiling is idempotent.
"""

from __future__ import annotations

import hashlib
import json
import re

from cinqflow.engine.parsers import ParsedFile
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import ColumnFacts, ProfileFacts, SheetFacts

PROFILER_VERSION = "1"

_INT = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?\d*\.\d+$")
_BOOL = {"true", "false", "t", "f", "yes", "no", "y", "n", "0", "1"}
_DATE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
    re.compile(r"^\d{8}$"),
)
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")

#: Column-name signals for PHI/PII candidates. Values are never used to decide.
_PHI_TOKENS = (
    "name",
    "dob",
    "birth",
    "ssn",
    "mrn",
    "member_id",
    "memberid",
    "patient",
    "address",
    "addr",
    "street",
    "city",
    "zip",
    "postal",
    "phone",
    "email",
    "gender",
    "sex",
    "race",
    "ethnicity",
    "subscriber",
)


def _is_date(value: str) -> bool:
    return any(p.match(value) for p in _DATE_PATTERNS)


def _phi_candidate(column: str) -> bool:
    lowered = column.lower().replace(" ", "_")
    return any(token in lowered for token in _PHI_TOKENS)


def _infer_type(values: list[str]) -> tuple[str, dict[str, float | bool]]:
    """Infer from the non-empty values. Ratios are kept as evidence."""
    total = len(values)
    if total == 0:
        return "string", {"numeric_ratio": 0.0, "date_ratio": 0.0, "code_like": False}

    ints = sum(1 for v in values if _INT.match(v))
    decimals = sum(1 for v in values if _DECIMAL.match(v))
    dates = sum(1 for v in values if _is_date(v))
    timestamps = sum(1 for v in values if _TIMESTAMP.match(v))
    bools = sum(1 for v in values if v.lower() in _BOOL)
    codes = sum(1 for v in values if _CODE.match(v))

    numeric_ratio = (ints + decimals) / total
    date_ratio = dates / total
    patterns: dict[str, float | bool] = {
        "numeric_ratio": round(numeric_ratio, 4),
        "date_ratio": round(date_ratio, 4),
        "code_like": codes / total >= 0.95,
    }

    if timestamps / total >= 0.95:
        return "timestamp", patterns
    if date_ratio >= 0.95:
        return "date", patterns
    if bools / total >= 0.95 and ints / total < 0.95:
        return "bool", patterns
    if decimals / total >= 0.5 and numeric_ratio >= 0.95:
        return "decimal", patterns
    if ints / total >= 0.95:
        return "int", patterns
    if patterns["code_like"]:
        return "code", patterns
    return "string", patterns


def _candidate_keys(parsed: ParsedFile, per_column: dict[str, list[str]]) -> list[list[str]]:
    """Single columns that are complete and unique. Pairs are only tried when no
    single column qualifies, and the search is capped to stay linear-ish."""
    rows = parsed.row_count
    if rows == 0:
        return []

    singles = [
        [col]
        for col in parsed.columns
        if len(per_column[col]) == rows and len(set(per_column[col])) == rows
    ]
    if singles:
        return singles

    candidates: list[list[str]] = []
    complete = [c for c in parsed.columns if len(per_column[c]) == rows][:8]
    for i, left in enumerate(complete):
        for right in complete[i + 1 :]:
            pairs = {(per_column[left][r], per_column[right][r]) for r in range(rows)}
            if len(pairs) == rows:
                candidates.append([left, right])
                if len(candidates) == 3:
                    return candidates
    return candidates


def profile(parsed: ParsedFile, settings: Settings | None = None) -> ProfileFacts:
    s = settings or get_settings()

    per_column: dict[str, list[str]] = {col: [] for col in parsed.columns}
    for row in parsed.rows:
        for col in parsed.columns:
            value = row.get(col, "")
            if value != "":
                per_column[col].append(value)

    columns: list[ColumnFacts] = []
    for col in parsed.columns:
        values = per_column[col]
        inferred, patterns = _infer_type(values)
        distinct = sorted(set(values))
        columns.append(
            ColumnFacts(
                name=col,
                inferred_type=inferred,
                null_count=parsed.row_count - len(values),
                distinct_count=len(distinct),
                sample_values=distinct[: s.profile_sample_values],
                patterns=patterns,
                phi_candidate=_phi_candidate(col),
            )
        )

    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    for row in parsed.rows:
        signature = tuple(row.get(c, "") for c in parsed.columns)
        if signature in seen:
            duplicates += 1
        else:
            seen.add(signature)

    return ProfileFacts(
        row_count=parsed.row_count,
        columns=columns,
        candidate_keys=_candidate_keys(parsed, per_column),
        duplicate_rows=duplicates,
        phi_candidates=[c.name for c in columns if c.phi_candidate],
        sheets=[SheetFacts(name=name, rows=rows) for name, rows in parsed.sheets],
        sample_rows=parsed.rows[: s.profile_sample_rows],
    )


def profile_id(facts: ProfileFacts) -> str:
    """Identity is the content of the facts, excluding nothing that was observed."""
    payload = json.dumps(facts.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
