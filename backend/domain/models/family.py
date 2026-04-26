"""
Family Profile Domain Models
==============================
Business value objects for the family financial questionnaire (M2 W2).

Contents:
  - Member: a family member (applicant, spouse, child, parent)
  - SubAccount: a purpose-segregated investment account
  - FamilyProfile: the complete family financial profile (questionnaire output)

All models are frozen dataclasses (immutable facts).
Downstream consumers: rule_engine (03), decision_guard (07), scheduling (05), ai_interface (04).

Design doc: docs/design/06-family-profile.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Tuple


# ============================================================
# Member
# ============================================================

@dataclass(frozen=True)
class Member:
    """A family member in the household.

    Fields::

        member_id        -- unique within family ("self", "spouse", "child1", ...)
        role             -- display label
        age              -- current age (0 = not provided)
        income           -- monthly income in CNY (0.0 = not provided)
        is_decision_maker -- True for the primary financial decision-maker
    """

    member_id: str
    role: str = ""
    age: int = 0
    income: float = 0.0
    is_decision_maker: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "member_id": self.member_id,
            "role": self.role,
            "age": self.age,
            "income": self.income,
            "is_decision_maker": self.is_decision_maker,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Member":
        return cls(
            member_id=d.get("member_id", ""),
            role=d.get("role", ""),
            age=d.get("age", 0),
            income=d.get("income", 0.0),
            is_decision_maker=d.get("is_decision_maker", False),
        )


# ============================================================
# SubAccount
# ============================================================

@dataclass(frozen=True)
class SubAccount:
    """A purpose-segregated investment sub-account.

    Different sub-accounts can have independent target allocations and
    investment horizons. E.g., education fund (15y) vs retirement (30y)
    should NOT share the same deviation calculation.

    Fields::

        account_id         -- unique within family ("main", "education", ...)
        purpose            -- free-text description
        target_allocation  -- {"stock_pct": 50, "bond_pct": 30, "cash_pct": 15, "gold_pct": 5}
        horizon_years      -- investment horizon in years (0 = not set)
        is_independent     -- if True, this account has its own target allocation
    """

    account_id: str
    purpose: str = ""
    target_allocation: Tuple[tuple[str, int], ...] = ()  # frozen-safe mapping
    horizon_years: int = 0
    is_independent: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "account_id": self.account_id,
            "purpose": self.purpose,
            "target_allocation": dict(self.target_allocation),
            "horizon_years": self.horizon_years,
            "is_independent": self.is_independent,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SubAccount":
        raw_alloc = d.get("target_allocation", {})
        if isinstance(raw_alloc, dict):
            alloc = tuple(sorted(raw_alloc.items()))
        else:
            alloc = ()
        return cls(
            account_id=d.get("account_id", ""),
            purpose=d.get("purpose", ""),
            target_allocation=alloc,
            horizon_years=d.get("horizon_years", 0),
            is_independent=d.get("is_independent", False),
        )


# ============================================================
# FamilyProfile
# ============================================================

# Valid values for constrained fields
VALID_RISK_PREFERENCES = ("conservative", "balanced", "aggressive")
VALID_FAMILY_STAGES = (
    "single", "married_mortgage", "with_children", "near_retirement",
)
VALID_PRIMARY_GOALS = ("retirement", "house", "education", "inheritance", "")
VALID_DRAWDOWN_TOLERANCES = (-0.10, -0.20, -0.30, -0.50)


@dataclass(frozen=True)
class FamilyProfile:
    """Complete family financial profile -- the output of the questionnaire.

    This is the primary input for the rule engine (03-rule-engine.md).
    All thresholds, allocation matrices, and risk rules consume these fields.

    Questionnaire categories (06-family-profile.md section 2.1):
      - Basic info (age, family structure, job stability)
      - Cash flow (income, expense, investable assets, surplus)
      - Liabilities (mortgage, car loan, consumer loan, credit card)
      - Insurance coverage (critical illness, life, medical, accident)
      - Emergency reserve (months of expenses covered)
      - Investment (horizon, max drawdown tolerance)
      - Goals (primary goal + time window)

    frozen=True: profiles are immutable snapshots. Updates create new instances.
    """

    family_id: str

    # Members and sub-accounts (tuples for frozen compatibility)
    members: Tuple[Member, ...] = ()
    sub_accounts: Tuple[SubAccount, ...] = ()

    # --- Questionnaire: Basic ---
    risk_preference: str = "balanced"     # conservative / balanced / aggressive
    family_stage: str = "single"          # single / married_mortgage / with_children / near_retirement

    # --- Questionnaire: Cash flow ---
    monthly_income: float = 0.0           # CNY
    monthly_expense: float = 0.0
    investable_assets: float = 0.0
    monthly_surplus: float = 0.0

    # --- Questionnaire: Liabilities ---
    mortgage_remaining: float = 0.0
    car_loan: float = 0.0
    consumer_loan: float = 0.0
    credit_card_debt: float = 0.0

    # --- Questionnaire: Insurance (has/coverage pairs) ---
    has_critical_illness: bool = False
    critical_illness_coverage: float = 0.0
    has_life_insurance: bool = False
    life_insurance_coverage: float = 0.0
    has_medical_insurance: bool = False
    medical_insurance_coverage: float = 0.0
    has_accident_insurance: bool = False
    accident_insurance_coverage: float = 0.0

    # --- Questionnaire: Emergency ---
    emergency_months: float = 0.0         # How many months of expenses covered

    # --- Questionnaire: Investment ---
    investment_horizon_years: int = 10
    max_drawdown_tolerance: float = -0.20  # -0.10 / -0.20 / -0.30 / -0.50

    # --- Questionnaire: Goals ---
    primary_goal: str = ""                # retirement / house / education / inheritance
    goal_time_window_years: int = 0

    # --- Metadata ---
    created_at: str = ""
    updated_at: str = ""

    # ---- Serialization ----

    def to_dict(self) -> Dict[str, object]:
        """Serialize to JSON-compatible dict for persistence."""
        return {
            "family_id": self.family_id,
            "members": [m.to_dict() for m in self.members],
            "sub_accounts": [s.to_dict() for s in self.sub_accounts],
            "risk_preference": self.risk_preference,
            "family_stage": self.family_stage,
            "monthly_income": self.monthly_income,
            "monthly_expense": self.monthly_expense,
            "investable_assets": self.investable_assets,
            "monthly_surplus": self.monthly_surplus,
            "mortgage_remaining": self.mortgage_remaining,
            "car_loan": self.car_loan,
            "consumer_loan": self.consumer_loan,
            "credit_card_debt": self.credit_card_debt,
            "has_critical_illness": self.has_critical_illness,
            "critical_illness_coverage": self.critical_illness_coverage,
            "has_life_insurance": self.has_life_insurance,
            "life_insurance_coverage": self.life_insurance_coverage,
            "has_medical_insurance": self.has_medical_insurance,
            "medical_insurance_coverage": self.medical_insurance_coverage,
            "has_accident_insurance": self.has_accident_insurance,
            "accident_insurance_coverage": self.accident_insurance_coverage,
            "emergency_months": self.emergency_months,
            "investment_horizon_years": self.investment_horizon_years,
            "max_drawdown_tolerance": self.max_drawdown_tolerance,
            "primary_goal": self.primary_goal,
            "goal_time_window_years": self.goal_time_window_years,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FamilyProfile":
        """Construct from persisted dict. Ignores unknown keys, fills defaults."""
        members_raw = d.get("members", [])
        members = tuple(Member.from_dict(m) for m in members_raw) if members_raw else ()

        subs_raw = d.get("sub_accounts", [])
        sub_accounts = tuple(SubAccount.from_dict(s) for s in subs_raw) if subs_raw else ()

        return cls(
            family_id=d.get("family_id", ""),
            members=members,
            sub_accounts=sub_accounts,
            risk_preference=d.get("risk_preference", "balanced"),
            family_stage=d.get("family_stage", "single"),
            monthly_income=d.get("monthly_income", 0.0),
            monthly_expense=d.get("monthly_expense", 0.0),
            investable_assets=d.get("investable_assets", 0.0),
            monthly_surplus=d.get("monthly_surplus", 0.0),
            mortgage_remaining=d.get("mortgage_remaining", 0.0),
            car_loan=d.get("car_loan", 0.0),
            consumer_loan=d.get("consumer_loan", 0.0),
            credit_card_debt=d.get("credit_card_debt", 0.0),
            has_critical_illness=d.get("has_critical_illness", False),
            critical_illness_coverage=d.get("critical_illness_coverage", 0.0),
            has_life_insurance=d.get("has_life_insurance", False),
            life_insurance_coverage=d.get("life_insurance_coverage", 0.0),
            has_medical_insurance=d.get("has_medical_insurance", False),
            medical_insurance_coverage=d.get("medical_insurance_coverage", 0.0),
            has_accident_insurance=d.get("has_accident_insurance", False),
            accident_insurance_coverage=d.get("accident_insurance_coverage", 0.0),
            emergency_months=d.get("emergency_months", 0.0),
            investment_horizon_years=d.get("investment_horizon_years", 10),
            max_drawdown_tolerance=d.get("max_drawdown_tolerance", -0.20),
            primary_goal=d.get("primary_goal", ""),
            goal_time_window_years=d.get("goal_time_window_years", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    # ---- Computed properties ----

    @property
    def total_debt(self) -> float:
        """Total outstanding liabilities."""
        return (
            self.mortgage_remaining
            + self.car_loan
            + self.consumer_loan
            + self.credit_card_debt
        )

    @property
    def insurance_count(self) -> int:
        """Number of the 4 major insurance types held (0-4)."""
        return sum([
            self.has_critical_illness,
            self.has_life_insurance,
            self.has_medical_insurance,
            self.has_accident_insurance,
        ])

    @property
    def primary_member(self) -> Member | None:
        """The decision-maker member, or None if not set."""
        for m in self.members:
            if m.is_decision_maker:
                return m
        return self.members[0] if self.members else None
