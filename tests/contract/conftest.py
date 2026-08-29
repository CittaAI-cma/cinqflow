"""Fit every mock adapter, then run ONE contract suite against all of them.

    "Any new/changed port has a real adapter (or a documented seat for it), a
     local stand-in, and a mock — and ONE contract suite all three pass."
    — memory/03-directives/01-definition-of-done.md, the chip test

Adapters register themselves on import, so composing the socket is an import.
That is the whole ceremony: importing a different adapter package is what
"climbing a rung changes only the profile" looks like from inside the tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import cinqflow.adapters.mock  # noqa: F401  — fits every pin at rung 0
from cinqflow.ports import fitted


def adapters_for(pin: str) -> list[pytest.ParameterSet]:
    """Every adapter fitted to a pin, as parameters for the one suite.

    A suite written with this iterates over whatever is fitted, so adding a
    second adapter makes it a CERTIFICATION rather than a migration — nobody
    writes a second suite, because there is nowhere to put one.
    """
    return [
        pytest.param(factory, id=f"{pin}/{name}") for name, factory in sorted(fitted(pin).items())
    ]


@pytest.fixture
def make() -> Callable[[Callable[..., Any]], Any]:
    """Build an adapter from its factory, with no arguments.

    Every adapter must be constructible with defaults. An adapter that needs
    arguments to exist is an adapter the installer cannot stand up from a
    profile alone.
    """

    def build(factory: Callable[..., Any]) -> Any:
        return factory()

    return build
