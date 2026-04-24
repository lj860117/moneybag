"""
StoreProtocol -- persistence contract
======================================
Abstracts file-based persistence currently handled by:
  - services/persistence.py (user JSON files with atomic write + .bak recovery)
  - 16 other files that directly touch DATA_DIR

The collection/key model maps to the existing directory layout:
  - collection="users", key=sha256(user_id) -> data/users/{hash}.json
  - collection="precomputed", key="signal_2026-04-24" -> data/precomputed/{key}.json
  - collection="receipts", key=receipt_id -> data/receipts/{id}.json

Implementations (planned):
  - infra.store.file_store.FileStore     (M1 Day 1)
  - infra.store.sqlite_store.SqliteStore (post-M2)

Design doc: docs/design/12-framework-refactor.md
Invariant #5: All file IO through infra/store.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class StoreProtocol(Protocol):
    """Structural interface for key-value persistence.

    All methods are synchronous (the app is sync-first today).
    """

    def read(self, collection: str, key: str) -> Optional[Dict]:
        """Read a JSON document. Returns None if not found.

        Implementations MUST handle corruption gracefully
        (e.g., fall back to .bak file, return None, never raise).
        """
        ...

    def write(self, collection: str, key: str, data: Dict) -> None:
        """Write a JSON document atomically.

        Implementations MUST guarantee atomic writes (tmp + rename pattern)
        and create parent directories as needed.
        """
        ...

    def delete(self, collection: str, key: str) -> bool:
        """Delete a document. Returns True if it existed, False otherwise."""
        ...

    def exists(self, collection: str, key: str) -> bool:
        """Check if a document exists without reading it."""
        ...

    def list_keys(self, collection: str) -> List[str]:
        """List all keys in a collection.

        Returns empty list if collection doesn't exist.
        """
        ...
