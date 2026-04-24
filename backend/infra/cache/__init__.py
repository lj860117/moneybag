"""
Cache infrastructure -- unified caching implementations.
Invariant #4: All cache through infra/cache.
"""
from infra.cache.memory_cache import MemoryCache

__all__ = ["MemoryCache"]
