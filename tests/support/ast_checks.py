"""AST-walk assertions for agent graphs, factored out of the copy each agent's
own contract/unit test had started carrying.

    "graphs are declared in core, never bound to a runtime"
    — .importlinter, the `graphs-are-data` contract

`mapping_suggestion`, `schema_inference`, `rule_authoring`, `phi_detection`
and `fingerprint_match` each wrote their own `ast.parse(inspect.getsource(...))`
walk to prove a node reaches no model — five copies of the same check, free to
drift from each other one word at a time. This module is where the sixth one
(`alert_enrichment`) stops that, by calling this instead of writing a sixth
copy. The five existing copies are unchanged here — retrofitting them is a
separate, deliberately out-of-scope cleanup, not part of this slab.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType


def assert_deterministic_nodes(module: ModuleType, node_names: set[str]) -> None:
    """Walk `module`'s own SOURCE — not the running object — and assert every
    function named in `node_names` neither reaches `self.llm` nor calls
    `.complete(...)` anywhere in its body.

    Reading the source rather than exercising the object matters: a mock that
    happens not to be called on THIS run proves nothing about a node that
    COULD call it on the next one. A node earns "deterministic" by being
    structurally unable to reach a model, not by an test run that didn't ask.
    """
    tree = ast.parse(inspect.getsource(module))
    bodies = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in node_names
    }
    assert set(bodies) == set(node_names), (
        f"expected to find deterministic nodes {sorted(node_names)} in "
        f"{module.__name__}, found {sorted(bodies)}"
    )
    for name, node in bodies.items():
        attributes = {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}
        assert "llm" not in attributes, f"{name} reaches the gateway"
        assert "complete" not in attributes, f"{name} calls a model"


def assert_graph_module_imports_no_runtime(path: str | Path) -> None:
    """`graphs-are-data` as a test as well as an import-linter contract.

    A `core/agents/<name>/graph.py` module must import neither `langgraph`
    nor anything under `cinqflow.adapters` / `cinqflow.ports` — the latter is
    the `layers` contract's own doing: `cinqflow.core` sits below
    `cinqflow.ports`, so even an inert dataclass like `Edge`/`GraphSpec`
    cannot be imported from core, and the real edge spec is assembled one
    layer up instead. See `core.agents.fingerprint_match.graph`'s own
    docstring for the fuller reasoning.
    """
    source = Path(path).read_text()
    tree = ast.parse(source)
    imported_modules = tuple(
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    )
    roots = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {m.split(".")[0] for m in imported_modules}
    assert "langgraph" not in roots, f"{path}: imports langgraph directly"
    leaks_a_layer = any(
        m.startswith(("cinqflow.adapters", "cinqflow.ports")) for m in imported_modules
    )
    assert not leaks_a_layer, f"{path}: imports a runtime or a port — core sits below both"


__all__ = ["assert_deterministic_nodes", "assert_graph_module_imports_no_runtime"]
