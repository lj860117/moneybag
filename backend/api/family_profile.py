"""
Family Profile API -- questionnaire submission, profile/member/sub-account CRUD
================================================================================
From design doc: docs/design/06-family-profile.md

Endpoints:
  POST /api/family/questionnaire           Submit or update the family questionnaire
  GET  /api/family/profile/{family_id}     Get full family profile
  GET  /api/family/profile/{family_id}/members       Get members list
  POST /api/family/profile/{family_id}/members       Update members list
  GET  /api/family/profile/{family_id}/sub-accounts  Get sub-accounts list
  POST /api/family/profile/{family_id}/sub-accounts  Update sub-accounts list

Each endpoint <400 lines. Only routing + validation here; business logic in use_cases/.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from infra.store.family_profile_store import FamilyProfileStore
from use_cases.submit_family_questionnaire import (
    submit_questionnaire,
    get_family_profile,
    update_members,
    update_sub_accounts,
)


router = APIRouter(tags=["家庭画像"])

# Singleton store instance (injected by default; tests can override)
_store = FamilyProfileStore()


# ---- Pydantic request/response schemas ----

class MemberSchema(BaseModel):
    member_id: str = Field(..., description="Unique member ID within family")
    role: str = Field("", description="Display role label")
    age: int = Field(0, ge=0, le=150)
    income: float = Field(0.0, ge=0)
    is_decision_maker: bool = False


class SubAccountSchema(BaseModel):
    account_id: str = Field(..., description="Unique account ID within family")
    purpose: str = ""
    target_allocation: Dict[str, int] = Field(
        default_factory=dict,
        description="e.g. {'stock_pct': 50, 'bond_pct': 30, 'cash_pct': 15, 'gold_pct': 5}",
    )
    horizon_years: int = Field(0, ge=0)
    is_independent: bool = False


class QuestionnaireRequest(BaseModel):
    """Request body for questionnaire submission."""
    family_id: str = Field(..., description="Unique family identifier")

    # Members (optional on first submit; auto-creates primary member from answers)
    members: List[MemberSchema] = Field(default_factory=list)
    sub_accounts: List[SubAccountSchema] = Field(default_factory=list)

    # Basic
    age: int = Field(0, ge=0, le=150)
    risk_preference: str = Field("balanced", description="conservative / balanced / aggressive")
    family_stage: str = Field("", description="Auto-derived if empty")

    # Cash flow
    monthly_income: float = Field(0.0, ge=0)
    monthly_expense: float = Field(0.0, ge=0)
    investable_assets: float = Field(0.0, ge=0)
    monthly_surplus: float = Field(0.0, ge=0)

    # Liabilities
    mortgage_remaining: float = Field(0.0, ge=0)
    car_loan: float = Field(0.0, ge=0)
    consumer_loan: float = Field(0.0, ge=0)
    credit_card_debt: float = Field(0.0, ge=0)

    # Insurance
    has_critical_illness: bool = False
    critical_illness_coverage: float = Field(0.0, ge=0)
    has_life_insurance: bool = False
    life_insurance_coverage: float = Field(0.0, ge=0)
    has_medical_insurance: bool = False
    medical_insurance_coverage: float = Field(0.0, ge=0)
    has_accident_insurance: bool = False
    accident_insurance_coverage: float = Field(0.0, ge=0)

    # Emergency
    emergency_months: float = Field(0.0, ge=0)

    # Investment
    investment_horizon_years: int = Field(10, ge=0)
    max_drawdown_tolerance: float = Field(-0.20, le=0)
    years_to_retire: int = Field(99, ge=0, description="Used to derive family_stage")

    # Goals
    primary_goal: str = Field("", description="retirement / house / education / inheritance")
    goal_time_window_years: int = Field(0, ge=0)


class ProfileResponse(BaseModel):
    """Response containing the full family profile."""
    status: str = "ok"
    profile: Dict[str, Any]
    errors: List[str] = Field(default_factory=list)


class MembersUpdateRequest(BaseModel):
    members: List[MemberSchema]


class SubAccountsUpdateRequest(BaseModel):
    sub_accounts: List[SubAccountSchema]


# ---- Endpoints ----

@router.post("/api/family/questionnaire", response_model=ProfileResponse)
def submit_family_questionnaire(req: QuestionnaireRequest) -> Dict[str, Any]:
    """Submit or update the family financial questionnaire.

    First submission creates the profile; subsequent submissions update it.
    """
    answers = req.model_dump()

    profile, errors = submit_questionnaire(
        family_id=req.family_id,
        answers=answers,
        store=_store,
    )

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors, "profile": profile.to_dict()})

    return {"status": "ok", "profile": profile.to_dict(), "errors": []}


@router.get("/api/family/profile/{family_id}", response_model=ProfileResponse)
def get_profile(family_id: str) -> Dict[str, Any]:
    """Get the full family profile."""
    profile = get_family_profile(family_id, _store)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Family '{family_id}' not found")

    return {"status": "ok", "profile": profile.to_dict(), "errors": []}


@router.get("/api/family/profile/{family_id}/members")
def get_members(family_id: str) -> Dict[str, Any]:
    """Get the members list of a family profile."""
    profile = get_family_profile(family_id, _store)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Family '{family_id}' not found")

    return {
        "status": "ok",
        "family_id": family_id,
        "members": [m.to_dict() for m in profile.members],
    }


@router.post("/api/family/profile/{family_id}/members")
def update_members_endpoint(family_id: str, req: MembersUpdateRequest) -> Dict[str, Any]:
    """Update the members list of an existing family profile."""
    members_data = [m.model_dump() for m in req.members]
    profile, errors = update_members(family_id, members_data, _store)

    if profile is None:
        raise HTTPException(status_code=404, detail=errors[0] if errors else "Not found")

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    return {
        "status": "ok",
        "family_id": family_id,
        "members": [m.to_dict() for m in profile.members],
    }


@router.get("/api/family/profile/{family_id}/sub-accounts")
def get_sub_accounts(family_id: str) -> Dict[str, Any]:
    """Get the sub-accounts list of a family profile."""
    profile = get_family_profile(family_id, _store)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Family '{family_id}' not found")

    return {
        "status": "ok",
        "family_id": family_id,
        "sub_accounts": [s.to_dict() for s in profile.sub_accounts],
    }


@router.post("/api/family/profile/{family_id}/sub-accounts")
def update_sub_accounts_endpoint(
    family_id: str, req: SubAccountsUpdateRequest
) -> Dict[str, Any]:
    """Update the sub-accounts list of an existing family profile."""
    subs_data = [s.model_dump() for s in req.sub_accounts]
    profile, errors = update_sub_accounts(family_id, subs_data, _store)

    if profile is None:
        raise HTTPException(status_code=404, detail=errors[0] if errors else "Not found")

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    return {
        "status": "ok",
        "family_id": family_id,
        "sub_accounts": [s.to_dict() for s in profile.sub_accounts],
    }
