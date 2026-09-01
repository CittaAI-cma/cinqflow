"""The `cache` pin — an optional read cache.

    verb: optional_read_cache   mock: none   dev: none
    target: redis_if_measured
    — docs/architecture/plates/04-pin-out-map.md

DELIBERATELY UNIMPLEMENTED, at every rung including production (ADR-0014).

This is not an oversight and not a TODO. A cache is a correctness hazard with a
performance benefit, and nothing has yet been measured that needs one. The seat
exists so that adding one later is an adapter behind an existing port —
contract suite already written — rather than an argument about where to put it.

The NullCache below is the honest implementation of "there is no cache": it
answers every read with a miss, so code written against this port is correct
whether or not a cache is ever fitted.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CachePort(Protocol):
    def get(self, key: str) -> Any | None:
        """None means MISS. A caller must always be able to recompute."""
        ...

    def set(self, key: str, value: Any, *, ttl_s: int = 300) -> None:
        """May legitimately do nothing. A cache that drops everything is a
        valid cache; a cache that returns something stale is not."""
        ...

    def invalidate(self, key: str) -> None: ...
