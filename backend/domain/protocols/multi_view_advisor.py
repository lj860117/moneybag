"""
MultiViewAdvisor Protocol
=========================
Interface for generating structured multi-perspective reviews.

Invariant #11: all cross-layer dependencies go through Protocol.
"""
from typing import Protocol, runtime_checkable, Dict, Optional

from domain.models.multi_perspective import MultiViewReview, MultiViewRequest


@runtime_checkable
class MultiViewAdvisorProtocol(Protocol):
    """Generate three-perspective reviews of portfolio decisions."""
    
    def generate_review(self, request: MultiViewRequest) -> Optional[MultiViewReview]:
        """Generate a multi-perspective review if triggers are met.
        
        Args:
            request: Decision context (asset, amount, portfolio state)
        
        Returns:
            MultiViewReview if triggers met and all views generated,
            None if triggers not met or generation fails.
        """
        ...
    
    def check_triggers(self, request: MultiViewRequest) -> Dict[str, bool]:
        """Check if this decision meets any trigger conditions.
        
        Args:
            request: Decision context
        
        Returns:
            Dict mapping trigger names to boolean (met or not).
        """
        ...
    
    def get_perspective_titles(self) -> Dict[str, str]:
        """Return display titles for each perspective.
        
        Returns:
            {"conservative": "保守派 (Graham)", ...}
        """
        ...
