"""
CacheProtocol -- unified caching contract
==========================================
Replaces the 46 module-level ``_cache = {}`` dicts found across services/.

Three-tier cache model (design, not enforced by this Protocol):
  - Tier A: realtime facts (portfolio state) -- event-driven, TTL ~5min
  - Tier B: near-realtime market data        -- TTL 30min-2h
  - Tier C: overnight LLM interpretations    -- TTL 4-8h (precomputed)

Implementations (planned):
  - infra.cache.memory_cache.MemoryCache     (M1 Day 1)
  - infra.cache.disk_cache.DiskCache         (M1 W2)
  - infra.cache.precomputed.PrecomputedCache (M1 W2)

Design doc: docs/design/12-framework-refactor.md
Invariant #4: All cache through infra/cache.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CacheProtocol(Protocol):
    """Structural interface for all cache implementations.

    ``runtime_checkable`` so we can assert ``isinstance(obj, CacheProtocol)``
    in tests and DI wiring without requiring inheritance.
    """

    def get(self, key: str) -> Any:
        """Return cached value, or None if missing / expired."""
        ...

    def set(self, key: str, value: Any, *, ttl: int = ...) -> None:
        """Store value. If ttl is not given, use the implementation default."""
        ...

    def delete(self, key: str) -> None:
        """Remove a single key. No-op if key doesn't exist."""
        ...

    def clear(self) -> None:
        """Remove all entries. Use sparingly -- mainly for tests."""
        ...

    def has(self, key: str) -> bool:
        """Check existence without retrieving value. Respects TTL."""
        ...
