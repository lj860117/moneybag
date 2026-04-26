"""
FamilyProfileProtocol -- persistence contract for family profiles
==================================================================
Domain-semantic interface for family profile storage.

Implementations (planned):
  - infra.store.family_profile_store.FamilyProfileStore  (M2 W2, delegates to FileStore)

Invariant #11: New cross-module interfaces must have a Protocol first.
Invariant #5: All file IO through infra/store.

Design doc: docs/design/06-family-profile.md
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class FamilyProfileProtocol(Protocol):
    """Structural interface for family profile persistence.

    Stores one JSON document per family_id.
    """

    def load(self, family_id: str) -> Optional[Dict[str, object]]:
        """Load a family profile. Returns None if not found."""
        ...

    def save(self, family_id: str, data: Dict[str, object]) -> None:
        """Save (create or overwrite) a family profile."""
        ...

    def exists(self, family_id: str) -> bool:
        """Check if a family profile exists without loading it."""
        ...

    def list_families(self) -> List[str]:
        """List all stored family IDs (hashed keys)."""
        ...
