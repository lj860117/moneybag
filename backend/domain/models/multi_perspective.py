"""
Multi-Perspective Advisor Models
==================================
Represents structured reviews of major portfolio adjustments from three angles:
conservative (Graham), long-term (Bogle), behavioral (Kahneman).

Invariant #10: domain.models never import from infra
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any


class PerspectiveType(str, Enum):
    """Three distinct advisory perspectives."""
    CONSERVATIVE = "conservative"     # Graham: safety margin, downside protection
    LONGTERM = "longterm"            # Bogle: 30-year horizon, time advantage
    BEHAVIORAL = "behavioral"        # Kahneman: cognitive biases, emotion check


@dataclass(frozen=True)
class PerspectiveView:
    """A single perspective's viewpoint on a decision."""
    perspective: PerspectiveType
    title: str                       # e.g., "保守派视角"
    text: str                        # 50-80 char advice (filled from template)
    template_id: str                 # Which template was used
    confidence: float                # 0.0-1.0, how applicable is this view
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PerspectiveView:
        data = data.copy()
        data["perspective"] = PerspectiveType(data["perspective"])
        return cls(**data)


@dataclass(frozen=True)
class MultiViewReview:
    """Complete multi-perspective review of a portfolio decision."""
    decision_id: str                 # Foreign key to decision_log
    user_id: str
    asset_name: str
    asset_class: str                 # "stock", "fund", "real_estate", etc.
    decision_context: Dict[str, Any] # {amount, current_pct, new_pct, ...}
    
    # Three perspectives
    conservative_view: PerspectiveView
    longterm_view: PerspectiveView
    behavioral_view: PerspectiveView
    
    # Metadata
    created_at: float                # Unix timestamp
    triggers_met: List[str]          # Which trigger conditions activated this review
    
    @property
    def all_views(self) -> List[PerspectiveView]:
        """Return all three perspective views."""
        return [
            self.conservative_view,
            self.longterm_view,
            self.behavioral_view,
        ]
    
    @property
    def average_confidence(self) -> float:
        """Average confidence across all three views."""
        if not self.all_views:
            return 0.0
        return sum(v.confidence for v in self.all_views) / len(self.all_views)
    
    @property
    def summary_text(self) -> str:
        """Concatenate all three views for display."""
        lines = [
            f"【保守派】{self.conservative_view.text}",
            f"【长期派】{self.longterm_view.text}",
            f"【行为派】{self.behavioral_view.text}",
        ]
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "user_id": self.user_id,
            "asset_name": self.asset_name,
            "asset_class": self.asset_class,
            "decision_context": self.decision_context,
            "conservative_view": self.conservative_view.to_dict(),
            "longterm_view": self.longterm_view.to_dict(),
            "behavioral_view": self.behavioral_view.to_dict(),
            "created_at": self.created_at,
            "triggers_met": self.triggers_met,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MultiViewReview:
        data = data.copy()
        data["conservative_view"] = PerspectiveView.from_dict(data["conservative_view"])
        data["longterm_view"] = PerspectiveView.from_dict(data["longterm_view"])
        data["behavioral_view"] = PerspectiveView.from_dict(data["behavioral_view"])
        return cls(**data)


@dataclass(frozen=True)
class TriggerCondition:
    """Evaluation result for a trigger condition."""
    name: str                    # "amount_major", "asset_class_change", etc.
    met: bool
    metadata: Dict[str, Any]    # Context data (amount, pct, etc.)


@dataclass(frozen=True)
class MultiViewRequest:
    """Request to generate multi-perspective review."""
    user_id: str
    asset_name: str
    asset_class: str            # "stock", "fund", "bond", "real_estate", "crypto"
    
    # Context
    transaction_amount: float     # Amount being added/changed
    current_position_value: float # Current position value (for pct calc)
    total_portfolio_value: float  # Total portfolio size (for concentration)
    
    # Optional context for better template rendering
    recent_return_pct: Optional[float] = None      # Perf last 3 months
    historical_return_pct: Optional[float] = None  # Long-term avg return
    loss_pct: Optional[float] = None              # Current drawdown if any
    days_since_rebalance: Optional[int] = None    # Last adjustment
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
