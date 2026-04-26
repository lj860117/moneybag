"""
BalanceSheetProtocol -- persistence contract for balance sheets
================================================================
Domain-semantic interface for balance sheet storage.

Implementations (planned):
  - infra.store.balance_sheet_store.BalanceSheetStore  (M2 W3, delegates to FileStore)

Invariant #11: New cross-module interfaces must have a Protocol first.
Invariant #5: All file IO through infra/store.

Design doc: docs/design/06-family-profile.md section 八
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class BalanceSheetProtocol(Protocol):
    """Structural interface for balance sheet persistence.

    Stores one JSON document per family_id.
    """

    def load(self, family_id: str) -> Optional[Dict[str, object]]:
        """Load a balance sheet. Returns None if not found."""
        ...

    def save(self, family_id: str, data: Dict[str, object]) -> None:
        """Save (create or overwrite) a balance sheet."""
        ...

    def exists(self, family_id: str) -> bool:
        """Check if a balance sheet exists without loading it."""
        ...

    def list_families(self) -> List[str]:
        """List all stored family IDs (hashed keys)."""
        ...
