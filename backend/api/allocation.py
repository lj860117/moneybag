"""
Allocation API — Asset allocation endpoints
=============================================
Endpoints for target allocation computation, deviation analysis, and rebalancing checks.

Routes:
  POST   /api/family/{family_id}/allocation/target           Compute target allocation
  GET    /api/family/{family_id}/allocation/analyze          Analyze deviation from target
  GET    /api/family/{family_id}/allocation/rebalance-check  Check if rebalancing needed
  PUT    /api/family/{family_id}/allocation/target-override  Save user override

Each endpoint <200 lines. Only routing + validation here; business logic in use_cases/.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from domain.models.allocation import AllocationState, AllocationTarget
from infra.store.family_profile_store import FamilyProfileStore
from use_cases.manage_allocation import (
    analyze_allocation_deviation,
    check_rebalance_need,
    compute_allocation_target,
    save_allocation_override,
)

router = APIRouter(tags=["资产配置"])

# Singleton store instance (injected by default; tests can override)
_store = FamilyProfileStore()


# ---- Pydantic request/response schemas ----

class AllocationTargetSchema(BaseModel):
    """Target or current allocation percentages."""
    stock_pct: float = Field(..., ge=0, le=100, description="Stock allocation %")
    bond_pct: float = Field(..., ge=0, le=100, description="Bond allocation %")
    cash_pct: float = Field(..., ge=0, le=100, description="Cash allocation %")
    gold_pct: float = Field(..., ge=0, le=100, description="Gold/other allocation %")
    reason: Optional[str] = Field(None, description="Reason for this allocation (matrix/age_adjusted/user_override)")


class AllocationStateSchema(BaseModel):
    """Current actual allocation."""
    stock_pct: float = Field(..., ge=0, le=100, description="Current stock allocation %")
    bond_pct: float = Field(..., ge=0, le=100, description="Current bond allocation %")
    cash_pct: float = Field(..., ge=0, le=100, description="Current cash allocation %")
    gold_pct: float = Field(..., ge=0, le=100, description="Current gold/other allocation %")
    last_rebalanced: Optional[str] = Field(None, description="ISO 8601 last rebalance date")


class DeviationAnalysisSchema(BaseModel):
    """Allocation deviation analysis."""
    target: AllocationTargetSchema = Field(..., description="Target allocation")
    current: AllocationStateSchema = Field(..., description="Current allocation")
    stock_deviation: float = Field(..., description="Stock deviation from target %")
    bond_deviation: float = Field(..., description="Bond deviation from target %")
    cash_deviation: float = Field(..., description="Cash deviation from target %")
    gold_deviation: float = Field(..., description="Gold deviation from target %")
    max_deviation: float = Field(..., description="Maximum absolute deviation %")
    severity: str = Field(..., description="normal | mild | moderate | high")
    recommendation: str = Field(..., description="Recommendation text")


class AllocationOverrideRequest(BaseModel):
    """Request to override target allocation."""
    stock_pct: float = Field(..., ge=0, le=100, description="Override stock %")
    bond_pct: float = Field(..., ge=0, le=100, description="Override bond %")
    cash_pct: float = Field(..., ge=0, le=100, description="Override cash %")
    gold_pct: float = Field(..., ge=0, le=100, description="Override gold %")


class AllocationResponse(BaseModel):
    """Standard allocation endpoint response."""
    status: str = "ok"
    data: Dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


# ---- Endpoints ----

@router.post("/api/family/{family_id}/allocation/target", response_model=AllocationResponse)
def get_target_allocation(family_id: str) -> Dict[str, Any]:
    """Compute target allocation for a family profile.

    Returns target based on:
      - risk_preference (conservative/balanced/aggressive)
      - family_stage (single/married_mortgage/with_children/near_retirement)
      - age (with adjustment)
      - user override (if any)
    """
    target, errors = compute_allocation_target(family_id, _store)

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    return {
        "status": "ok",
        "data": target.to_dict(),
        "errors": [],
    }


@router.post("/api/family/{family_id}/allocation/analyze", response_model=AllocationResponse)
def analyze_deviation(
    family_id: str,
    current: AllocationStateSchema,
) -> Dict[str, Any]:
    """Analyze allocation deviation from target.

    Takes current allocation and computes:
      - Per-asset-class deviation
      - Overall deviation severity
      - Rebalancing recommendation
    """
    # Get target
    target, errors = compute_allocation_target(family_id, _store)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    # Convert schema to domain model
    current_state = AllocationState(
        stock_pct=current.stock_pct,
        bond_pct=current.bond_pct,
        cash_pct=current.cash_pct,
        gold_pct=current.gold_pct,
        last_rebalanced=current.last_rebalanced or "",
    )

    # Analyze
    analysis = analyze_allocation_deviation(current_state, target)

    return {
        "status": "ok",
        "data": analysis.to_dict(),
        "errors": [],
    }


@router.get("/api/family/{family_id}/allocation/rebalance-check", response_model=AllocationResponse)
def check_rebalance(
    family_id: str,
    stock_pct: float = 0.0,
    bond_pct: float = 0.0,
    cash_pct: float = 0.0,
    gold_pct: float = 0.0,
    last_rebalanced_date: str = "",
) -> Dict[str, Any]:
    """Check if rebalancing is needed.

    Compares current allocation against target and time since last rebalance.
    """
    # Get target
    target, errors = compute_allocation_target(family_id, _store)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    # Build current state
    current = AllocationState(
        stock_pct=stock_pct,
        bond_pct=bond_pct,
        cash_pct=cash_pct,
        gold_pct=gold_pct,
        last_rebalanced=last_rebalanced_date,
    )

    # Analyze deviation
    analysis = analyze_allocation_deviation(current, target)

    # Check rebalance trigger
    should_rebalance, reason = check_rebalance_need(analysis, last_rebalanced_date)

    return {
        "status": "ok",
        "data": {
            "should_rebalance": should_rebalance,
            "reason": reason,
            "analysis": analysis.to_dict(),
        },
        "errors": [],
    }


@router.put("/api/family/{family_id}/allocation/target-override", response_model=AllocationResponse)
def save_override(
    family_id: str,
    req: AllocationOverrideRequest,
) -> Dict[str, Any]:
    """Save user-provided allocation override.

    Note: M2 W4 stores in memory only. M2 W5+ will extend FamilyProfile
    with user_preferences field for persistence.
    """
    override = AllocationTarget(
        stock_pct=req.stock_pct,
        bond_pct=req.bond_pct,
        cash_pct=req.cash_pct,
        gold_pct=req.gold_pct,
        reason="user_override",
    )

    saved_target, errors = save_allocation_override(family_id, override, _store)

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    return {
        "status": "ok",
        "data": saved_target.to_dict(),
        "errors": [],
    }
