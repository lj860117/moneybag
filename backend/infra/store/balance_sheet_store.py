"""
BalanceSheetStore -- file-based persistence for balance sheets
===============================================================
Implements domain.protocols.BalanceSheetProtocol by delegating
to FileStore with collection="balance_sheets".

Storage layout (via FileStore)::

    data/balance_sheets/{sha256(family_id)[:16]}.json

Invariant #5: All file IO through infra/store.
Design doc: docs/design/06-family-profile.md section 八
"""
from __future__ import annotations

from typing import Dict, List, Optional

from infra.store.file_store import FileStore


_JsonDict = Dict[str, object]

# Collection name for balance sheets
_COLLECTION = "balance_sheets"


class BalanceSheetStore:
    """File-based balance sheet store satisfying BalanceSheetProtocol.

    Delegates all IO to FileStore (atomic writes, .bak recovery, SHA256 key hashing).
    """

    __slots__ = ("_store",)

    def __init__(self, store: Optional[FileStore] = None) -> None:
        self._store = store if store is not None else FileStore()

    def load(self, family_id: str) -> Optional[_JsonDict]:
        """Load a balance sheet. Returns None if not found."""
        return self._store.read(_COLLECTION, family_id)

    def save(self, family_id: str, data: _JsonDict) -> None:
        """Save (create or overwrite) a balance sheet."""
        self._store.write(_COLLECTION, family_id, data)

    def exists(self, family_id: str) -> bool:
        """Check if a balance sheet exists without loading it."""
        return self._store.exists(_COLLECTION, family_id)

    def list_families(self) -> List[str]:
        """List all stored family IDs (hashed keys)."""
        return self._store.list_keys(_COLLECTION)
