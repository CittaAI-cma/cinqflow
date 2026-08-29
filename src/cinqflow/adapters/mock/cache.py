"""none — the honest implementation of "there is no cache" (ADR-0014)."""

from __future__ import annotations

from typing import Any

from cinqflow.ports import port


@port("cache", "mock")
class NullCache:
    """Every read is a miss; every write is discarded.

    This is the implementation at EVERY rung, including production, until
    measurement demands otherwise. Code written against it is correct whether
    or not a cache is ever fitted — which is the property that makes adding one
    later safe.
    """

    def get(self, key: str) -> Any | None:
        _ = key
        return None

    def set(self, key: str, value: Any, *, ttl_s: int = 300) -> None:
        _ = (key, value, ttl_s)

    def invalidate(self, key: str) -> None:
        _ = key
