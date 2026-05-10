"""
DecisionGuardProtocol -- persistence contract for decision reviews
===================================================================
Domain-semantic interface for decision review storage and retrieval.

Implementations:
  - domain.rule_engine.decision_archive (existing, extends with review storage)

Invariant #11: New cross-module interfaces must have a Protocol first.
Design doc: docs/design/07-decision-guard.md
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class DecisionGuardProtocol(Protocol):
    """Structural interface for decision review persistence.

    Stores decision reviews (post-trade) linked to the decision archive system.
    """

    def save_review(self, user_id: str, review: Dict[str, object]) -> Dict[str, object]:
        """Save a decision review record. Returns the saved record with generated ID."""
        ...

    def get_reviews(self, user_id: str, limit: int = 20) -> List[Dict[str, object]]:
        """Get recent decision reviews for a user (newest first)."""
        ...

    def get_review_by_id(self, user_id: str, review_id: str) -> Optional[Dict[str, object]]:
        """Get a specific review by ID. Returns None if not found."""
        ...

    def get_review_stats(self, user_id: str) -> Dict[str, object]:
        """Get aggregate review statistics (avg score, red flag count, etc.)."""
        ...
