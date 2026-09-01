"""The guardrail that has to hold BY CONSTRUCTION.

    "no model credentials exist outside the LLM gateway"
    "code attempts a direct model call around the gateway -> it fails by
     construction"
    — docs/architecture/INVARIANTS.md · CF-V0-E16-01, guardrail

A test that merely calls a model around the gateway and asserts a failure would
prove that ONE path fails. This one is structural: it walks every module's AST
and asserts that the vendor SDK and the environment are reachable from exactly
the layers permitted to touch them. A future module that imports `openai`
directly fails here, whether or not anyone thought to test it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.lane1]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "cinqflow"

MODEL_SDKS = frozenset({"openai", "anthropic", "azure", "langchain_core", "langsmith"})

#: The ONE module allowed to import a model SDK. Widening this list is a
#: deliberate act with a review attached, which is the point of a list.
SDK_HOLDERS = frozenset({"adapters/openai_compatible/llm.py"})

#: Reading the environment is the secrets adapter's job and the Postgres
#: adapter's DSN resolution. Everything else receives values it was given.
#:
#: `adapters/langgraph/agent_runtime.py` WRITES `os.environ` rather than
#: reading it — `_silence_telemetry()`, ADR-0018. That is a narrower act than
#: it looks: langsmith/langchain sniff their OWN env vars at import time,
#: before the connection profile is even loaded, so pinning them off through
#: the profile/secrets pin would run too late to matter. Widening this list is
#: a deliberate act with a review attached, which is the point of a list.
ENV_READERS = frozenset(
    {
        "adapters/mock/secrets.py",
        "adapters/local/secrets.py",
        "adapters/local/pg_control.py",
        "adapters/langgraph/agent_runtime.py",
    }
)


def _modules() -> list[tuple[str, ast.Module]]:
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        found.append((relative, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return found


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _reads_environment(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            return True
        if isinstance(node, ast.Name) and node.id in {"getenv", "environ"}:
            return True
    return False


def test_no_module_outside_the_llm_adapter_imports_a_model_sdk() -> None:
    offenders = sorted(
        f"{name}: {', '.join(sorted(_imported_roots(tree) & MODEL_SDKS))}"
        for name, tree in _modules()
        if _imported_roots(tree) & MODEL_SDKS and name not in SDK_HOLDERS
    )
    assert offenders == [], (
        "a model SDK reachable from a second place is a second place a credential can live"
    )


def test_the_llm_adapter_imports_its_sdk_lazily() -> None:
    """A module-level import would make Lane 1 depend on the vendor it avoids."""
    path = SOURCE / "adapters/openai_compatible/llm.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not top_level & MODEL_SDKS


def test_only_the_secrets_and_dsn_paths_read_the_environment() -> None:
    offenders = sorted(
        name for name, tree in _modules() if _reads_environment(tree) and name not in ENV_READERS
    )
    assert offenders == [], (
        "everything environment-specific lives in the connection profile and reaches "
        "the code through the secrets pin — a module reading os.environ has invented "
        "a second configuration channel"
    )


def test_no_module_in_core_ports_or_intelligence_reads_the_environment() -> None:
    """Stated separately from the list above so the list cannot quietly grow to
    cover a layer that must never read the environment at all."""
    offenders = sorted(
        name
        for name, tree in _modules()
        if _reads_environment(tree)
        and name.startswith(("core/", "ports/", "intelligence/", "api/"))
    )
    assert offenders == []
