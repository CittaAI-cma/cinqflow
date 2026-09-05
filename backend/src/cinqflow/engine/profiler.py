"""Deterministic profiling: arithmetic and pattern matching only, no model.

profile_id is the hash of the computed facts, so the same bytes always produce the
same profile identity and re-profiling is idempotent.

v2 (PR-5) adds, per column: a deterministic *role hint* (`identifier` · `measure` ·
`dimension` · `date` · `technical` · `unclassified` - PHI stays a flag, not a
role), `null_ratio`, `min`/`max` (parsed numeric and date values only, and never
for a PHI column), `top_values` (value -> count, capped, never for a PHI column),
`constant`, and `sentinel_count` (RR-23: `1900-01-01`, `9999-12-31`,
`0000-00-00`, all-zero / all-nine strings). At facts level, `time_coverage` over
the date columns that are neither PHI (a date of birth is not the data's period)
nor technical (a load timestamp is the platform's, not the file's).

Hints are observations, not classifications. The rules are named, ordered and
cheap; the model (PR-6) classifies *against* them and must say why when it
disagrees. `bronze_profiler.py` runs this same function over what landed, so the
S4 reconciliation gets the same facts on both sides.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime

from cinqflow.engine.parsers import ParsedFile
from cinqflow.settings import Settings, get_settings
from cinqflow.workflow.models import (
    ColumnFacts,
    ColumnRoleHint,
    ProfileFacts,
    SheetFacts,
    TimeCoverage,
    TopValue,
)

PROFILER_VERSION = "2"

#: `top_values` is for reading; ten is enough to show a distribution's shape.
TOP_VALUES_CAP = 10

#: A column with this many distinct values or fewer reads as a dimension; so does
#: one whose distinct values are under this share of the rows.
DIMENSION_MAX_DISTINCT = 50
DIMENSION_MAX_RATIO = 0.05

_INT = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?\d*\.\d+$")
_BOOL = {"true", "false", "t", "f", "yes", "no", "y", "n", "0", "1"}
_DATE_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
    re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$"),
    re.compile(r"^\d{8}$"),
)
#: Hours may lack a leading zero in exports (`2025-09-01 0:00:00`).
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}")
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
    "death",
    "deceased",
)

#: Role-hint vocabulary (§7.1). Whole-token matches on the column name, split on
#: anything that is not a letter or digit, so `tin` does not fire on `destination`.
_IDENTIFIER_TOKENS = frozenset({"id", "npi", "tin", "mrn", "ssn", "key", "identifier"})
#: Platform/audit columns: the canonical models' `system_populated` lists
#: (knowledge/canonical/*.yaml) plus the usual load-time bookkeeping.
_TECHNICAL_TOKENS = frozenset(
    {"created", "updated", "modified", "batch", "lsdeleted", "etl", "ingested", "loaded"}
)
_TECHNICAL_PHRASES = (
    "record_hash",
    "row_number",
    "source_system",
    "secure_id",
    "record_creation",
    "load_date",
    "load_ts",
)
#: Numeric-looking columns that are labels, not quantities - and never
#: identifiers by uniqueness alone (a phone number is unique per row too).
_LABEL_TOKENS = frozenset(
    {
        "zip",
        "postal",
        "phone",
        "mobile",
        "fax",
        "number",
        "code",
        "flag",
        "status",
        "type",
        "indicator",
        "segtype",
    }
)
#: A period column stored as a number (`member_month` = 202402) is a date.
_PERIOD_TOKENS = frozenset({"month", "year", "period", "quarter", "yyyymm"})

#: RR-23 sentinels: placeholder values a source uses for "unknown", which read as
#: real dates or numbers to anything that does not know better.
_SENTINEL_VALUES = frozenset(
    {
        "1900-01-01",
        "9999-12-31",
        "0000-00-00",
        "1900-01-01 00:00:00",
        "9999-12-31 00:00:00",
        "01/01/1900",
        "12/31/9999",
        "19000101",
        "99991231",
    }
)
_ALL_ZERO = re.compile(r"^0{4,}$")
_ALL_NINE = re.compile(r"^9{4,}$")

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y%m%d")


def _is_date(value: str) -> bool:
    return any(p.match(value) for p in _DATE_PATTERNS)


def _phi_candidate(column: str) -> bool:
    lowered = column.lower().replace(" ", "_")
    return any(token in lowered for token in _PHI_TOKENS)


def _name_tokens(column: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", column.lower()) if t}


def _is_sentinel(value: str) -> bool:
    v = value.strip()
    return v in _SENTINEL_VALUES or bool(_ALL_ZERO.match(v)) or bool(_ALL_NINE.match(v))


def _parse_date(value: str) -> date | None:
    v = value.strip()
    if _TIMESTAMP.match(v):
        v = v[:10]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


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


def role_hint(
    name: str,
    inferred: str,
    patterns: dict[str, float | bool],
    *,
    distinct: int,
    row_count: int,
    in_candidate_key: bool,
) -> ColumnRoleHint:
    """The §7.1 table, in precedence order. Technical first: a `created_at`
    timestamp is the platform's, not a date in the data. Then date, identifier
    (a key column, an id-named column, or a code that is unique per row), measure
    (a quantity - numeric and not a label), dimension (few distinct values), else
    unclassified. PHI is not a role; it stays the `phi_candidate` flag."""
    lowered = name.lower().replace(" ", "_")
    tokens = _name_tokens(name)
    if tokens & _TECHNICAL_TOKENS or any(p in lowered for p in _TECHNICAL_PHRASES):
        return "technical"
    if distinct == 0:
        # Every value empty: no evidence for any role (and an anomaly, PR-6).
        return "unclassified"
    if inferred in ("date", "timestamp"):
        return "date"
    if inferred == "int" and tokens & _PERIOD_TOKENS:
        return "date"
    if tokens & _IDENTIFIER_TOKENS:
        return "identifier"
    # Unique per row - a key column, or unique code-like values - is an identifier
    # unless the name says label: a phone number is unique per row too.
    unique = in_candidate_key or bool(
        patterns.get("code_like") and row_count and distinct == row_count
    )
    if unique and not tokens & _LABEL_TOKENS:
        return "identifier"
    if inferred in ("int", "decimal") and not tokens & _LABEL_TOKENS:
        return "measure"
    few = distinct <= DIMENSION_MAX_DISTINCT or (
        row_count > 0 and distinct / row_count < DIMENSION_MAX_RATIO
    )
    if inferred in ("string", "code", "bool", "int") and few:
        return "dimension"
    return "unclassified"


def _bounds(inferred: str, counter: Counter[str]) -> tuple[str | None, str | None]:
    """min/max as strings: the original numeric text, or an ISO date. Sentinels
    are excluded - `9999-12-31` is not a maximum, it is a placeholder."""
    if inferred in ("int", "decimal"):
        numeric = [
            (float(v), v)
            for v in counter
            if not _is_sentinel(v) and (_INT.match(v) or _DECIMAL.match(v))
        ]
        if not numeric:
            return None, None
        return min(numeric)[1], max(numeric)[1]
    if inferred in ("date", "timestamp"):
        parsed = [d for d in (_parse_date(v) for v in counter if not _is_sentinel(v)) if d]
        if not parsed:
            return None, None
        return min(parsed).isoformat(), max(parsed).isoformat()
    return None, None


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


def _time_coverage(columns: list[ColumnFacts]) -> TimeCoverage | None:
    """The data's own period: min/max across real date/timestamp columns that
    are neither PHI nor technical and have a parsed bound. A period stored as a
    number (`member_month` = 202402) is a `date` hint but not an ISO bound, so
    it stays out."""
    dated = [
        c
        for c in columns
        if c.hint == "date"
        and c.inferred_type in ("date", "timestamp")
        and not c.phi_candidate
        and c.min is not None
        and c.max is not None
    ]
    if not dated:
        return None
    return TimeCoverage(
        columns=[c.name for c in dated],
        min=min(c.min for c in dated if c.min),
        max=max(c.max for c in dated if c.max),
    )


def profile(parsed: ParsedFile, settings: Settings | None = None) -> ProfileFacts:
    s = settings or get_settings()
    row_count = parsed.row_count

    per_column: dict[str, list[str]] = {col: [] for col in parsed.columns}
    for row in parsed.rows:
        for col in parsed.columns:
            value = row.get(col, "")
            if value != "":
                per_column[col].append(value)

    candidate_keys = _candidate_keys(parsed, per_column)
    key_columns = {c for key in candidate_keys for c in key}

    columns: list[ColumnFacts] = []
    for col in parsed.columns:
        values = per_column[col]
        inferred, patterns = _infer_type(values)
        counter = Counter(values)
        distinct = sorted(counter)
        phi = _phi_candidate(col)
        null_count = row_count - len(values)
        low, high = (None, None) if phi else _bounds(inferred, counter)
        columns.append(
            ColumnFacts(
                name=col,
                inferred_type=inferred,
                null_count=null_count,
                distinct_count=len(distinct),
                sample_values=distinct[: s.profile_sample_values],
                patterns=patterns,
                phi_candidate=phi,
                hint=role_hint(
                    col,
                    inferred,
                    patterns,
                    distinct=len(distinct),
                    row_count=row_count,
                    in_candidate_key=col in key_columns,
                ),
                null_ratio=round(null_count / row_count, 4) if row_count else 0.0,
                min=low,
                max=high,
                # Value frequencies are values: never for a PHI column.
                top_values=[]
                if phi
                else [TopValue(value=v, count=n) for v, n in counter.most_common(TOP_VALUES_CAP)],
                constant=len(distinct) == 1,
                sentinel_count=sum(n for v, n in counter.items() if _is_sentinel(v)),
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
        row_count=row_count,
        columns=columns,
        candidate_keys=candidate_keys,
        duplicate_rows=duplicates,
        phi_candidates=[c.name for c in columns if c.phi_candidate],
        sheets=[SheetFacts(name=name, rows=rows) for name, rows in parsed.sheets],
        sample_rows=parsed.rows[: s.profile_sample_rows],
        time_coverage=_time_coverage(columns),
    )


def profile_id(facts: ProfileFacts) -> str:
    """Identity is the content of the facts, excluding nothing that was observed."""
    payload = json.dumps(facts.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
