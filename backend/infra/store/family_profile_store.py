"""
FamilyProfileStore -- file-based persistence for family profiles
================================================================
Implements domain.protocols.FamilyProfileProtocol by delegating
to FileStore with collection="profiles".

Storage layout (via FileStore)::

    data/profiles/{sha256(family_id)[:16]}.json

Invariant #5: All file IO through infra/store.
Design doc: docs/design/06-family-profile.md section 八
"""
from __future__ import annotations

from typing import Dict, List, Optional

from infra.store.file_store import FileStore


_JsonDict = Dict[str, object]

# Collection name matches the design doc: data/profiles/{family_id}.json
_COLLECTION = "profiles"


class FamilyProfileStore:
    """File-based family profile store satisfying FamilyProfileProtocol.

    Delegates all IO to FileStore (atomic writes, .bak recovery, SHA256 key hashing).
    """

    __slots__ = ("_store",)

    def __init__(self, store: Optional[FileStore] = None) -> None:
        self._store = store if store is not None else FileStore()

    def load(self, family_id: str) -> Optional[_JsonDict]:
        """Load a family profile. Returns None if not found."""
        return self._store.read(_COLLECTION, family_id)

    def save(self, family_id: str, data: _JsonDict) -> None:
        """Save (create or overwrite) a family profile."""
        self._store.write(_COLLECTION, family_id, data)

    def exists(self, family_id: str) -> bool:
        """Check if a family profile exists without loading it."""
        return self._store.exists(_COLLECTION, family_id)

    def list_families(self) -> List[str]:
        """List all stored family IDs (hashed keys)."""
        return self._store.list_keys(_COLLECTION)
