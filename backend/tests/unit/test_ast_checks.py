"""W2-41 — regression tests for `tests.support.ast_checks.assert_deterministic_nodes`.

Until this slab, the walk only ever inspected a node function's OWN body:

    def _group(self, state):
        return self._sneaky_helper(state)   # no `.llm`, no `.complete()` here

    def _sneaky_helper(self, state):
        return self.llm.complete(prompt="oops", task_class=None)

`_group` read as "deterministic" — the check never left `_group`'s own AST
subtree, so a model call one helper away was invisible to it, even though
`_group` is, in fact, one call away from the gateway. This is the reviewer's
own probe (`probe_ast_check_blindspot.py`), kept here as a permanent
regression instead of a throwaway script, plus the companion cases the fix
needs to get right: a bare module-level helper (the shape
`alert_enrichment._compose` -> `_build_cause` actually uses), a benign helper
that must NOT become a false positive (the shape both agents' own `_call`
uses), and the two-hop case the slab deliberately left out of scope.
"""

from __future__ import annotations

import inspect
import types

import pytest

from tests.support.ast_checks import assert_deterministic_nodes

pytestmark = pytest.mark.unit


def _module_from_source(name: str, source: str) -> types.ModuleType:
    """Build a real module object from literal source, executed exactly the
    way a normal import would populate it — `assert_deterministic_nodes`
    itself only ever reads `inspect.getsource(module)`, so the source text is
    the only fixture that actually matters here."""
    module = types.ModuleType(name)
    module.__ast_checks_source__ = source  # type: ignore[attr-defined]
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


@pytest.fixture
def patch_getsource(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route `inspect.getsource` to the literal source stashed by
    `_module_from_source` — a synthetic module built with `types.ModuleType`
    has no real file for `inspect.getsource` to find on its own. Every other
    caller (real agent modules, elsewhere in the suite) is untouched, and
    `monkeypatch` reverts this at the end of the test regardless of outcome.
    """
    original = inspect.getsource

    def fake_getsource(obj: object) -> str:
        stashed = getattr(obj, "__ast_checks_source__", None)
        if stashed is not None:
            return stashed
        return original(obj)

    monkeypatch.setattr(inspect, "getsource", fake_getsource)


def test_a_direct_model_call_is_still_caught(patch_getsource: None) -> None:
    """The walk's original job, unchanged by the one-hop extension: a node
    that reaches `self.llm.complete(...)` in its OWN body was always caught,
    and still is."""
    module = _module_from_source(
        "fake_direct",
        """
class FakeAgent:
    def _draft(self, state):
        return self.llm.complete(prompt="x", task_class=None)
""",
    )
    with pytest.raises(AssertionError, match="reaches the gateway"):
        assert_deterministic_nodes(module, {"_draft"})


def test_a_same_class_helper_indirection_is_now_caught(patch_getsource: None) -> None:
    """THE GAP ITSELF: `_group`'s own body never says `.llm` or
    `.complete()` — it calls `self._sneaky_helper(state)`, and the model call
    lives one hop away, inside that (different) method. Before W2-41 this
    passed `assert_deterministic_nodes` silently."""
    module = _module_from_source(
        "fake_same_class_indirect",
        """
class FakeAgent:
    def _group(self, state):
        # looks deterministic at a glance: no self.llm, no .complete()
        # written here -- but it calls a helper that reaches the model.
        return self._sneaky_helper(state)

    def _sneaky_helper(self, state):
        return self.llm.complete(prompt="oops", task_class=None)
""",
    )
    with pytest.raises(AssertionError, match="reaches the gateway"):
        assert_deterministic_nodes(module, {"_group"})


def test_a_same_module_function_indirection_is_also_caught(patch_getsource: None) -> None:
    """The other half of the fix's own scope claim ("same class/module"): a
    bare, module-level helper call — the exact shape
    `alert_enrichment._compose` uses to reach `_build_cause` — must be
    followed one hop too, not only a `self.`-qualified call."""
    module = _module_from_source(
        "fake_same_module_indirect",
        """
class FakeAgent:
    def _compose(self, state):
        return _sneaky_module_helper(self, state)


def _sneaky_module_helper(agent, state):
    return agent.llm.complete(prompt="oops", task_class=None)
""",
    )
    with pytest.raises(AssertionError, match="reaches the gateway"):
        assert_deterministic_nodes(module, {"_compose"})


def test_a_benign_helper_is_not_a_false_positive(patch_getsource: None) -> None:
    """The one-hop follow must not turn every helper call into a failure —
    only a helper that ACTUALLY reaches `self.llm`/`.complete(...)`. This is
    the real shape both `fingerprint_match._retrieve` and
    `alert_enrichment._retrieve` use via their own `self._call` helper, and
    it must keep passing."""
    module = _module_from_source(
        "fake_benign_helper",
        """
class FakeAgent:
    def _retrieve(self, state):
        return self._call("list_incidents", {})

    def _call(self, name, arguments):
        return {"tool": name, "arguments": arguments}
""",
    )
    assert_deterministic_nodes(module, {"_retrieve"})  # must not raise


def test_a_two_hop_indirection_is_deliberately_out_of_scope(patch_getsource: None) -> None:
    """Documents the boundary drawn on purpose: ONE hop, not a full
    call-graph walk. A model call reached through TWO helper calls away from
    the checked node is not caught — an accepted, narrower scope, not an
    oversight, per the slab that added the one-hop follow. This test exists
    so widening it later is a deliberate decision, not a silent regression."""
    module = _module_from_source(
        "fake_two_hop",
        """
class FakeAgent:
    def _group(self, state):
        return self._first_hop(state)

    def _first_hop(self, state):
        return self._second_hop(state)

    def _second_hop(self, state):
        return self.llm.complete(prompt="oops", task_class=None)
""",
    )
    assert_deterministic_nodes(module, {"_group"})  # does not raise -- out of scope by design
