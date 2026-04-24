"""
MemoryCache -- in-memory cache with TTL
========================================
Drop-in replacement for the 46 module-level ``_cache = {}`` dicts.

Existing pattern (found in 46 files)::

    _cache = {}
    def get_xxx():
        if key in _cache and time.time() - _cache[key]["ts"] < TTL:
            return _cache[key]["data"]
        ...

New pattern::

    cache = MemoryCache(default_ttl=3600)
    def get_xxx():
        hit = cache.get(key)
        if hit is not None:
            return hit
        ...

Thread safety: uses threading.Lock for basic safety (uvicorn single-worker).

Design doc: docs/design/12-framework-refactor.md
Satisfies: domain.protocols.CacheProtocol (structural subtyping, no import needed)
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

_DEFAULT_TTL = 3600  # 1 hour, matches existing llm_gateway.py CACHE_TTL


class _Entry:
    """Single cache entry with expiration tracking."""
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class MemoryCache:
    """Thread-safe in-memory cache with per-key TTL.

    Satisfies domain.protocols.CacheProtocol via structural subtyping.
    """

    __slots__ = ("_data", "_lock", "_default_ttl")

    def __init__(self, *, default_ttl: int = _DEFAULT_TTL) -> None:
        self._data: dict = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    # ---- CacheProtocol methods ----

    def get(self, key: str) -> Any:
        """Return cached value, or None if missing/expired."""
        with self._lock:
            entry = self._data.get(key)  # type: Optional[_Entry]
            if entry is None:
                return None
            if entry.expires_at < time.time():
                del self._data[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, *, ttl: int = 0) -> None:
        """Store value with optional TTL override.

        If ttl is 0 (default), uses the instance's default_ttl.
        If ttl is negative, entry never expires.
        """
        effective_ttl = ttl if ttl != 0 else self._default_ttl
        if effective_ttl < 0:
            expires_at = float("inf")
        else:
            expires_at = time.time() + effective_ttl
        with self._lock:
            self._data[key] = _Entry(value=value, expires_at=expires_at)

    def delete(self, key: str) -> None:
        """Remove a single key."""
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._data.clear()

    def has(self, key: str) -> bool:
        """Check existence without returning value."""
        return self.get(key) is not None

    # ---- Extras (not in Protocol, but useful for monitoring) ----

    def size(self) -> int:
        """Number of non-expired entries."""
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._data.items() if v.expires_at < now]
            for k in expired:
                del self._data[k]
            return len(self._data)
