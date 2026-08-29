"""A deliberately impure module. It exists to be REJECTED.

Law 1: "core imports no vendor SDK, no URL, no path, no credential."
       — docs/architecture/plates/03-chip-anatomy.md

A guardrail nobody tries is a comment, not a control. This fixture is the
attempt; tests/unit/test_core_purity_lint.py asserts the refusal.
"""

import psycopg  # noqa: F401  — the violation under test


def load(dsn: str) -> None:
    psycopg.connect(dsn)
