"""
Multi-Perspective Advisor Router
==================================
Endpoints for generating structured three-perspective reviews of portfolio decisions.

Paths:
  POST /api/advisor/multi-view -- Generate review
  GET  /api/advisor/triggers -- Check if triggers are met
  GET  /api/advisor/info -- Get metadata (titles, trigger descriptions)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from domain.models.multi_perspective import (
    MultiViewRequest,
    MultiViewReview,
    PerspectiveView,
)
from domain.models import PerspectiveType
from use_cases.run_multi_view_review import (
    generate_portfolio_review,
    check_decision_triggers,
    get_review_format_hints,
)
from infra.knowledge import get_multi_view_advisor


router = APIRouter(prefix="/api/advisor", tags=["advisor"])


# ============================================================================
# Pydantic Models for API
# ============================================================================

class MultiViewRequestSchema(BaseModel):
    """Request schema for multi-perspective review."""
    user_id: str
    asset_name: str = Field(..., description="Asset name (e.g., '贵州茅台', 'SPY')")
    asset_class: str = Field(
        ...,
        description="Asset class: stock, fund, bond, real_estate, crypto, commodity",
    )
    transaction_amount: float = Field(..., description="Amount being transacted")
    current_position_value: float = Field(0.0, description="Current position value")
    total_portfolio_value: float = Field(..., description="Total portfolio size")
    recent_return_pct: Optional[float] = Field(None, description="Return last 3m (%)")
    historical_return_pct: Optional[float] = Field(None, description="Long-term avg return (%)")
    loss_pct: Optional[float] = Field(None, description="Current drawdown (%)")
    days_since_rebalance: Optional[int] = Field(None, description="Days since last adjustment")


class PerspectiveViewSchema(BaseModel):
    """Single perspective's viewpoint."""
    perspective: str
    title: str
    text: str
    template_id: str
    confidence: float


class MultiViewReviewSchema(BaseModel):
    """Complete multi-perspective review."""
    decision_id: str
    user_id: str
    asset_name: str
    asset_class: str
    conservative_view: PerspectiveViewSchema
    longterm_view: PerspectiveViewSchema
    behavioral_view: PerspectiveViewSchema
    summary_text: str
    created_at: float
    triggers_met: List[str]
    average_confidence: float


class TriggersCheckSchema(BaseModel):
    """Result of trigger condition check."""
    any_met: bool
    triggers: Dict[str, bool]
    metadata: Dict[str, Any]


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/multi-view", response_model=Optional[MultiViewReviewSchema])
async def generate_multi_view_review(request: MultiViewRequestSchema) -> Optional[MultiViewReviewSchema]:
    """Generate multi-perspective review of a portfolio decision.
    
    Triggers on major decisions:
    - Amount > 20% of portfolio
    - New major asset class (gold, real estate, crypto)
    - Single asset would exceed 25% concentration
    
    Returns: Complete review with 3 perspectives (conservative/long-term/behavioral)
             or None if triggers not met.
    """
    try:
        advisor = get_multi_view_advisor()
        
        # Build domain request
        domain_request = MultiViewRequest(
            user_id=request.user_id,
            asset_name=request.asset_name,
            asset_class=request.asset_class,
            transaction_amount=request.transaction_amount,
            current_position_value=request.current_position_value,
            total_portfolio_value=request.total_portfolio_value,
            recent_return_pct=request.recent_return_pct,
            historical_return_pct=request.historical_return_pct,
            loss_pct=request.loss_pct,
            days_since_rebalance=request.days_since_rebalance,
        )
        
        # Generate review
        review = generate_portfolio_review(domain_request, advisor)
        if not review:
            return None
        
        # Convert to schema
        return MultiViewReviewSchema(
            decision_id=review.decision_id,
            user_id=review.user_id,
            asset_name=review.asset_name,
            asset_class=review.asset_class,
            conservative_view=PerspectiveViewSchema(**review.conservative_view.to_dict()),
            longterm_view=PerspectiveViewSchema(**review.longterm_view.to_dict()),
            behavioral_view=PerspectiveViewSchema(**review.behavioral_view.to_dict()),
            summary_text=review.summary_text,
            created_at=review.created_at,
            triggers_met=review.triggers_met,
            average_confidence=review.average_confidence,
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating review: {str(e)}")


@router.post("/check-triggers", response_model=TriggersCheckSchema)
async def check_triggers(request: MultiViewRequestSchema) -> TriggersCheckSchema:
    """Check which trigger conditions are met for a decision."""
    try:
        advisor = get_multi_view_advisor()
        
        domain_request = MultiViewRequest(
            user_id=request.user_id,
            asset_name=request.asset_name,
            asset_class=request.asset_class,
            transaction_amount=request.transaction_amount,
            current_position_value=request.current_position_value,
            total_portfolio_value=request.total_portfolio_value,
            recent_return_pct=request.recent_return_pct,
            historical_return_pct=request.historical_return_pct,
            loss_pct=request.loss_pct,
            days_since_rebalance=request.days_since_rebalance,
        )
        
        triggers = check_decision_triggers(domain_request, advisor)
        any_met = any(triggers.values())
        
        return TriggersCheckSchema(
            any_met=any_met,
            triggers=triggers,
            metadata={
                "transaction_pct": (request.transaction_amount / request.total_portfolio_value * 100),
                "current_pct": (request.current_position_value / request.total_portfolio_value * 100),
                "new_pct": ((request.current_position_value + request.transaction_amount) / request.total_portfolio_value * 100),
            },
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking triggers: {str(e)}")


@router.get("/info")
async def get_advisor_info() -> Dict[str, Any]:
    """Get metadata about multi-perspective review system.
    
    Returns information about perspectives, trigger conditions, and format hints.
    """
    return get_review_format_hints()
