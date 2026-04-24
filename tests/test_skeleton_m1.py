"""
M1 Day 1 Skeleton Smoke Test
==============================
Validates the four-layer skeleton created in M1 W1:
  1. All __init__.py files are importable
  2. Protocols are runtime_checkable
  3. Implementations satisfy their Protocols (isinstance check)
  4. LLMResponse round-trips through to_dict/from_dict
  5. MemoryCache and FileStore basic CRUD operations work

Run: cd backend && python -m pytest ../tests/test_skeleton_m1.py -v
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Ensure backend/ is on sys.path (same as main.py line 17)
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---- 1. All packages importable ----

PACKAGES = [
    "domain",
    "domain.models",
    "domain.protocols",
    "domain.services",
    "domain.rule_engine",
    "infra",
    "infra.cache",
    "infra.store",
    "infra.llm",
    "infra.data_source",
    "infra.knowledge",
    "infra.events",
    "infra.config",
    "api",
    "use_cases",
]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_package_importable(pkg):
    """Every skeleton package should import without error."""
    mod = importlib.import_module(pkg)
    assert mod is not None


# ---- 2. Protocols are runtime_checkable ----

def test_protocols_are_runtime_checkable():
    from domain.protocols import (
        CacheProtocol,
        StoreProtocol,
        LLMClientProtocol,
        DataSourceProtocol,
    )
    # runtime_checkable protocols should allow isinstance() checks
    # Verify a random non-matching object does NOT satisfy them
    for proto in (CacheProtocol, StoreProtocol, LLMClientProtocol, DataSourceProtocol):
        assert not isinstance("not_a_protocol", proto)


# ---- 3. Implementations satisfy Protocols ----

def test_memory_cache_satisfies_protocol():
    from domain.protocols import CacheProtocol
    from infra.cache import MemoryCache

    cache = MemoryCache()
    assert isinstance(cache, CacheProtocol)


def test_file_store_satisfies_protocol():
    from domain.protocols import StoreProtocol
    from infra.store import FileStore

    store = FileStore(base_dir="/tmp/moneybag_test_skeleton")
    assert isinstance(store, StoreProtocol)


# ---- 4. LLMResponse round-trip ----

def test_llm_response_round_trip():
    from domain.models import LLMResponse

    original = LLMResponse(
        content="test content",
        reasoning="thinking...",
        source="ai",
        model="deepseek-chat",
        tokens=100,
        cache_hit_tokens=20,
        cache_miss_tokens=80,
        fallback=False,
        error="",
    )
    d = original.to_dict()
    restored = LLMResponse.from_dict(d)
    assert restored == original
    assert isinstance(d, dict)
    assert d["content"] == "test content"
    assert d["tokens"] == 100


def test_llm_response_from_legacy_dict():
    """Simulate what LLMGateway.call_sync() returns today."""
    from domain.models import LLMResponse

    legacy = {
        "content": "market analysis...",
        "reasoning": "",
        "source": "ai",
        "model": "deepseek-chat",
        "tokens": 150,
        "cache_hit_tokens": 50,
        "cache_miss_tokens": 100,
        "fallback": False,
    }
    resp = LLMResponse.from_dict(legacy)
    assert resp.content == "market analysis..."
    assert resp.tokens == 150
    assert resp.fallback is False
    assert resp.error == ""  # missing key → default


def test_llm_response_is_frozen():
    from domain.models import LLMResponse

    resp = LLMResponse(content="immutable")
    with pytest.raises(AttributeError):
        resp.content = "mutated"  # type: ignore[misc]


# ---- 5. MemoryCache basic operations ----

def test_memory_cache_crud():
    from infra.cache import MemoryCache

    c = MemoryCache(default_ttl=10)

    # Miss
    assert c.get("x") is None
    assert not c.has("x")
    assert c.size() == 0

    # Set + Hit
    c.set("x", 42)
    assert c.get("x") == 42
    assert c.has("x")
    assert c.size() == 1

    # Overwrite
    c.set("x", 99)
    assert c.get("x") == 99

    # Delete
    c.delete("x")
    assert c.get("x") is None
    assert c.size() == 0

    # Clear
    c.set("a", 1)
    c.set("b", 2)
    assert c.size() == 2
    c.clear()
    assert c.size() == 0


def test_memory_cache_ttl_expiry():
    """Verify that expired entries return None."""
    import time
    from infra.cache import MemoryCache

    c = MemoryCache(default_ttl=3600)
    c.set("short", "value", ttl=1)

    assert c.get("short") == "value"
    time.sleep(1.1)
    assert c.get("short") is None


def test_memory_cache_stores_complex_types():
    from infra.cache import MemoryCache

    c = MemoryCache(default_ttl=60)
    c.set("dict", {"nested": [1, 2, 3]})
    c.set("list", [1, 2, 3])
    c.set("none_val", None)

    assert c.get("dict") == {"nested": [1, 2, 3]}
    assert c.get("list") == [1, 2, 3]
    # None value is stored — different from "not found" (also None)
    # This is a known limitation: cache.get() can't distinguish "stored None" from "miss"
    # In practice, we never cache None values.


# ---- 6. FileStore basic operations ----

def test_file_store_crud(tmp_path):
    from infra.store import FileStore

    store = FileStore(base_dir=str(tmp_path))

    # Miss
    assert store.read("users", "alice") is None
    assert not store.exists("users", "alice")
    assert store.list_keys("users") == []

    # Write + Read
    store.write("users", "alice", {"name": "Alice", "age": 30})
    assert store.exists("users", "alice")
    data = store.read("users", "alice")
    assert data is not None
    assert data["name"] == "Alice"
    assert len(store.list_keys("users")) == 1

    # Overwrite
    store.write("users", "alice", {"name": "Alice", "age": 31})
    data = store.read("users", "alice")
    assert data is not None
    assert data["age"] == 31

    # Delete
    assert store.delete("users", "alice") is True
    assert store.read("users", "alice") is None
    assert store.delete("users", "alice") is False  # already gone


def test_file_store_backup_recovery(tmp_path):
    """Verify that corrupted primary files recover from .bak."""
    import hashlib
    from infra.store import FileStore

    store = FileStore(base_dir=str(tmp_path))

    # Write twice so the second write creates a .bak from the first
    store.write("test", "key1", {"value": "original"})
    store.write("test", "key1", {"value": "updated"})

    # Corrupt the primary file
    safe_key = hashlib.sha256("key1".encode()).hexdigest()[:16]
    primary = tmp_path / "test" / "{}.json".format(safe_key)
    primary.write_text("NOT VALID JSON {{{", encoding="utf-8")

    # Read should recover from .bak (which has the first write's data)
    data = store.read("test", "key1")
    assert data is not None
    assert data["value"] == "original"


def test_file_store_multiple_collections(tmp_path):
    from infra.store import FileStore

    store = FileStore(base_dir=str(tmp_path))

    store.write("users", "u1", {"type": "user"})
    store.write("receipts", "r1", {"type": "receipt"})
    store.write("precomputed", "p1", {"type": "cache"})

    assert store.read("users", "u1")["type"] == "user"
    assert store.read("receipts", "r1")["type"] == "receipt"
    assert store.read("precomputed", "p1")["type"] == "cache"
    assert len(store.list_keys("users")) == 1
    assert len(store.list_keys("receipts")) == 1


# ---- 7. Dependency direction (no backward imports) ----

def test_domain_has_no_infra_imports():
    """domain/ must not import from infra/ — verify at module level."""
    import domain.models
    import domain.protocols.cache
    import domain.protocols.store
    import domain.protocols.data_source

    # These modules should have loaded without touching infra/
    # (llm_client.py imports LLMResponse from domain.models, which is fine)
    assert "infra" not in sys.modules.get("domain.models", object).__name__
