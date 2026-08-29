"""The Law-1 gate, tested before there is any code to violate it.

INVARIANTS.md, quoted verbatim so a failure explains itself:

    "core imports no vendor SDK, no URL, no path, no credential   # lint-enforced, CI gate"

conformance/lint_core_purity.py is the half of that rule import-linter cannot
see: string literals. These tests make the attempt and assert the refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conformance.lint_core_purity import Violation, lint_path

FIXTURES = Path(__file__).parent.parent / "fixtures"

INVARIANT = "core imports no vendor SDK, no URL, no path, no credential"


def _kinds(violations: list[Violation]) -> set[str]:
    return {v.kind for v in violations}


@pytest.mark.unit
def test_a_pure_module_passes() -> None:
    """A lint that rejects everything is not a lint."""
    assert lint_path(FIXTURES / "pure") == [], (
        "A module importing only pydantic, re, datetime and decimal is exactly "
        "what core/ is allowed to look like."
    )


@pytest.mark.unit
def test_vendor_import_into_core_is_refused() -> None:
    violations = lint_path(FIXTURES / "impure" / "vendor_import.py")
    assert "vendor-import" in _kinds(violations), INVARIANT
    assert any("psycopg" in v.detail for v in violations)


@pytest.mark.unit
def test_io_import_and_open_call_are_refused() -> None:
    """core/ PERFORMS NO I/O — parsers receive bytes from the storage adapter."""
    violations = lint_path(FIXTURES / "impure" / "does_io.py")
    assert "vendor-import" in _kinds(violations), "pathlib is I/O"
    assert "io-call" in _kinds(violations), "a bare open() in core/ is I/O"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "needle"),
    [
        ("hardcoded-url", "https://"),
        ("hardcoded-path", "/mnt/adls"),
        ("region-name", "eastus"),
        ("credential", "sk-live-"),
    ],
)
def test_environment_difference_in_core_is_refused(kind: str, needle: str) -> None:
    """All environment difference lives in the connection profile. Nowhere else."""
    violations = lint_path(FIXTURES / "impure" / "hardcoded_url.py")
    assert kind in _kinds(violations), f"{INVARIANT} — missed {needle}"


@pytest.mark.unit
def test_the_real_core_package_is_pure() -> None:
    """The gate itself, on the actual tree. This is the assertion CI cares about."""
    core = Path(__file__).parent.parent.parent / "src" / "cinqflow" / "core"
    violations = lint_path(core)
    assert violations == [], "\n".join(str(v) for v in violations)
