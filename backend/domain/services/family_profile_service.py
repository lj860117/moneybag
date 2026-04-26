"""
Family Profile Service -- questionnaire processing and validation
=================================================================
Pure domain logic for the family financial questionnaire (M2 W2).

Responsibilities:
  - Build a FamilyProfile from raw questionnaire answers
  - Validate profile completeness and field ranges
  - Derive family_stage from demographics
  - Find the primary decision-maker member

Invariant #9: No cross-imports between domain services.
             This module does NOT import user_preference_service or decision_archive.
Invariant #10: domain/ does not import from infra/. Persistence is handled
              by use_cases/ via FamilyProfileProtocol injection.

Design doc: docs/design/06-family-profile.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from domain.models.family import (
    FamilyProfile,
    Member,
    SubAccount,
    VALID_RISK_PREFERENCES,
    VALID_FAMILY_STAGES,
    VALID_PRIMARY_GOALS,
    VALID_DRAWDOWN_TOLERANCES,
)


# ============================================================
# Build profile from questionnaire answers
# ============================================================

def build_profile_from_questionnaire(
    family_id: str,
    answers: Dict[str, Any],
    existing_profile: FamilyProfile | None = None,
) -> FamilyProfile:
    """Construct a FamilyProfile from raw questionnaire answers.

    Args:
        family_id: unique family identifier
        answers: flat dict from the questionnaire form (keys match FamilyProfile fields)
        existing_profile: if updating, merge with existing (keeps created_at, members)

    Returns:
        A new frozen FamilyProfile instance.
    """
    now = datetime.now().isoformat()

    # Parse members from answers (list of dicts)
    members_raw: list[dict[str, Any]] = answers.get("members", [])
    members = tuple(Member.from_dict(m) for m in members_raw) if members_raw else ()

    # If no members provided but we have existing, keep them
    if not members and existing_profile and existing_profile.members:
        members = existing_profile.members

    # If still no members, create a default primary member from answers
    if not members:
        age = int(answers.get("age", 0))
        income = float(answers.get("monthly_income", 0.0))
        members = (
            Member(
                member_id="self",
                role="主申请人",
                age=age,
                income=income,
                is_decision_maker=True,
            ),
        )

    # Parse sub_accounts
    subs_raw: list[dict[str, Any]] = answers.get("sub_accounts", [])
    sub_accounts = tuple(SubAccount.from_dict(s) for s in subs_raw) if subs_raw else ()

    if not sub_accounts and existing_profile and existing_profile.sub_accounts:
        sub_accounts = existing_profile.sub_accounts

    # If no sub_accounts, create a default "main" account
    if not sub_accounts:
        sub_accounts = (
            SubAccount(account_id="main", purpose="家庭主账户"),
        )

    # Derive family_stage if not explicitly provided
    family_stage = answers.get("family_stage", "")
    if not family_stage:
        age = int(answers.get("age", 0))
        has_mortgage = float(answers.get("mortgage_remaining", 0.0)) > 0
        has_children = any(m.role == "子女" for m in members)
        years_to_retire = int(answers.get("years_to_retire", 99))
        family_stage = derive_family_stage(age, has_mortgage, has_children, years_to_retire)

    # Clamp risk_preference to valid values
    risk_pref = answers.get("risk_preference", "balanced")
    if risk_pref not in VALID_RISK_PREFERENCES:
        risk_pref = "balanced"

    # Clamp drawdown tolerance to valid values
    drawdown = float(answers.get("max_drawdown_tolerance", -0.20))
    if drawdown not in VALID_DRAWDOWN_TOLERANCES:
        # Snap to nearest valid value
        drawdown = min(VALID_DRAWDOWN_TOLERANCES, key=lambda x: abs(x - drawdown))

    return FamilyProfile(
        family_id=family_id,
        members=members,
        sub_accounts=sub_accounts,
        risk_preference=risk_pref,
        family_stage=family_stage,
        monthly_income=float(answers.get("monthly_income", 0.0)),
        monthly_expense=float(answers.get("monthly_expense", 0.0)),
        investable_assets=float(answers.get("investable_assets", 0.0)),
        monthly_surplus=float(answers.get("monthly_surplus", 0.0)),
        mortgage_remaining=float(answers.get("mortgage_remaining", 0.0)),
        car_loan=float(answers.get("car_loan", 0.0)),
        consumer_loan=float(answers.get("consumer_loan", 0.0)),
        credit_card_debt=float(answers.get("credit_card_debt", 0.0)),
        has_critical_illness=bool(answers.get("has_critical_illness", False)),
        critical_illness_coverage=float(answers.get("critical_illness_coverage", 0.0)),
        has_life_insurance=bool(answers.get("has_life_insurance", False)),
        life_insurance_coverage=float(answers.get("life_insurance_coverage", 0.0)),
        has_medical_insurance=bool(answers.get("has_medical_insurance", False)),
        medical_insurance_coverage=float(answers.get("medical_insurance_coverage", 0.0)),
        has_accident_insurance=bool(answers.get("has_accident_insurance", False)),
        accident_insurance_coverage=float(answers.get("accident_insurance_coverage", 0.0)),
        emergency_months=float(answers.get("emergency_months", 0.0)),
        investment_horizon_years=int(answers.get("investment_horizon_years", 10)),
        max_drawdown_tolerance=drawdown,
        primary_goal=str(answers.get("primary_goal", "")),
        goal_time_window_years=int(answers.get("goal_time_window_years", 0)),
        created_at=existing_profile.created_at if existing_profile else now,
        updated_at=now,
    )


# ============================================================
# Validation
# ============================================================

def validate_profile(profile: FamilyProfile) -> List[str]:
    """Validate a FamilyProfile for completeness and field ranges.

    Returns:
        List of error messages. Empty list = valid.
    """
    errors: List[str] = []

    # Required: family_id
    if not profile.family_id:
        errors.append("family_id is required")

    # Required: at least one member
    if not profile.members:
        errors.append("At least one family member is required")

    # Required: at least one member must be decision_maker
    if profile.members and not any(m.is_decision_maker for m in profile.members):
        errors.append("At least one member must be the decision maker")

    # Enum validation
    if profile.risk_preference not in VALID_RISK_PREFERENCES:
        errors.append(
            f"risk_preference must be one of {VALID_RISK_PREFERENCES}, "
            f"got '{profile.risk_preference}'"
        )

    if profile.family_stage not in VALID_FAMILY_STAGES:
        errors.append(
            f"family_stage must be one of {VALID_FAMILY_STAGES}, "
            f"got '{profile.family_stage}'"
        )

    if profile.primary_goal and profile.primary_goal not in VALID_PRIMARY_GOALS:
        errors.append(
            f"primary_goal must be one of {VALID_PRIMARY_GOALS}, "
            f"got '{profile.primary_goal}'"
        )

    # Range checks (non-negative financials)
    for field_name in (
        "monthly_income", "monthly_expense", "investable_assets",
        "monthly_surplus", "mortgage_remaining", "car_loan",
        "consumer_loan", "credit_card_debt", "emergency_months",
    ):
        val = getattr(profile, field_name)
        if val < 0:
            errors.append(f"{field_name} must be >= 0, got {val}")

    # Coverage amounts should be non-negative
    for field_name in (
        "critical_illness_coverage", "life_insurance_coverage",
        "medical_insurance_coverage", "accident_insurance_coverage",
    ):
        val = getattr(profile, field_name)
        if val < 0:
            errors.append(f"{field_name} must be >= 0, got {val}")

    # Investment horizon should be positive if set
    if profile.investment_horizon_years < 0:
        errors.append(
            f"investment_horizon_years must be >= 0, got {profile.investment_horizon_years}"
        )

    # Drawdown tolerance should be negative (it's a loss threshold)
    if profile.max_drawdown_tolerance > 0:
        errors.append(
            f"max_drawdown_tolerance must be <= 0, got {profile.max_drawdown_tolerance}"
        )

    # Sub-account validation
    account_ids = [s.account_id for s in profile.sub_accounts]
    if len(account_ids) != len(set(account_ids)):
        errors.append("Duplicate sub_account account_id found")

    # Member validation
    member_ids = [m.member_id for m in profile.members]
    if len(member_ids) != len(set(member_ids)):
        errors.append("Duplicate member member_id found")

    return errors


# ============================================================
# Family stage derivation
# ============================================================

def derive_family_stage(
    age: int,
    has_mortgage: bool,
    has_children: bool,
    years_to_retire: int,
) -> str:
    """Auto-derive family_stage from demographics.

    Logic (from 03-rule-engine.md section 2.1):
      - near_retirement if < 5 years to retire
      - with_children if has children
      - married_mortgage if has mortgage (and married implied)
      - single otherwise

    Args:
        age: primary member's age
        has_mortgage: True if mortgage_remaining > 0
        has_children: True if any member has role "子女"
        years_to_retire: years until retirement (default: 65 - age)
    """
    if years_to_retire <= 5:
        return "near_retirement"
    if has_children:
        return "with_children"
    if has_mortgage:
        return "married_mortgage"
    return "single"


# ============================================================
# Member helpers
# ============================================================

def compute_primary_member(members: Tuple[Member, ...]) -> Member | None:
    """Find the primary decision-maker member.

    Returns the first member with is_decision_maker=True,
    or the first member if none is flagged, or None if empty.
    """
    for m in members:
        if m.is_decision_maker:
            return m
    return members[0] if members else None
