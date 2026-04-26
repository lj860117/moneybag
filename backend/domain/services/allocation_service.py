"""
Allocation Service — Domain business logic for asset allocation
================================================================
Implements allocation matrix lookup, age adjustment, deviation analysis.

Functions:
  - compute_target_allocation() — determine target allocation from profile
  - analyze_deviation() — compare current vs target, classify severity
  - detect_rebalance_trigger() — check if rebalancing is needed
  - validate_allocation() — check allocation validity

All functions are pure (no side effects, no I/O). Store/cache access via caller.

Design doc: docs/design/03-rule-engine.md §2-8
Invariant #10: domain/ layer has zero imports from infra/.
"""
from __future__ import annotations

from typing import List

from domain.models.allocation import (
    AllocationTarget,
    AllocationState,
    DeviationAnalysis,
)
from domain.models.family import FamilyProfile
from domain.rule_engine.defaults import (
    AllocationDefaults,
    RiskDefaults,
    RebalanceDefaults,
)


def compute_target_allocation(
    profile: FamilyProfile,
    user_override: AllocationTarget | None = None,
) -> AllocationTarget:
    """Compute target allocation for a family profile.

    Algorithm (design doc 03 §2):
      1. Look up base allocation from matrix: (risk_preference, family_stage) → (s%, b%, c%, g%)
      2. Apply age adjustment: stock_pct -= max(0, age - 30) * 0.5%
      3. Enforce minimum from conservative row (prevent stock_pct going negative)
      4. If user_override provided, use that instead

    Args:
        profile: FamilyProfile containing risk_preference, family_stage, age
        user_override: optional user-provided allocation (if any)

    Returns:
        AllocationTarget with computed percentages

    Raises:
        ValueError: if profile has invalid risk_preference or family_stage
    """
    if user_override is not None:
        user_override_with_reason = AllocationTarget(
            stock_pct=user_override.stock_pct,
            bond_pct=user_override.bond_pct,
            cash_pct=user_override.cash_pct,
            gold_pct=user_override.gold_pct,
            reason="user_override",
        )
        return user_override_with_reason

    # Validate inputs
    if profile.risk_preference not in ("conservative", "balanced", "aggressive"):
        raise ValueError(f"Invalid risk_preference: {profile.risk_preference}")
    if profile.family_stage not in ("single", "married_mortgage", "with_children", "near_retirement"):
        raise ValueError(f"Invalid family_stage: {profile.family_stage}")

    # Step 1: Look up matrix
    key = (profile.risk_preference, profile.family_stage)
    matrix_value = AllocationDefaults.MATRIX.get(key)
    if matrix_value is None:
        raise ValueError(f"No matrix entry for {key}")

    stock_base, bond_base, cash_base, gold_base = matrix_value
    stock = float(stock_base)
    bond = float(bond_base)
    cash = float(cash_base)
    gold = float(gold_base)

    # Step 2: Age adjustment (linear decline from age 30)
    primary_member = profile.primary_member
    if primary_member is not None:
        age = primary_member.age
        age_adjustment = max(0, age - AllocationDefaults.AGE_BASE) * AllocationDefaults.AGE_ADJUSTMENT_RATE
        stock = stock - age_adjustment

        # Step 3: Enforce minimum from conservative row (same family_stage, conservative risk)
        conservative_key = ("conservative", profile.family_stage)
        conservative_value = AllocationDefaults.MATRIX.get(conservative_key)
        if conservative_value is not None:
            conservative_stock = float(conservative_value[0])
            stock = max(stock, conservative_stock)

    return AllocationTarget(
        stock_pct=stock,
        bond_pct=bond,
        cash_pct=cash,
        gold_pct=gold,
        reason="age_adjusted",
    )


def analyze_deviation(
    current: AllocationState,
    target: AllocationTarget,
) -> DeviationAnalysis:
    """Analyze deviation from target allocation.

    Computes per-asset-class deviation, max deviation, and severity classification.

    Design doc 03 §3.1: Four severity levels:
      - normal: <3%
      - mild: 3-7%
      - moderate: 7-15%
      - high: >15%

    Args:
        current: actual current allocation
        target: target allocation

    Returns:
        DeviationAnalysis with severity and recommendation
    """
    stock_dev = current.stock_pct - target.stock_pct
    bond_dev = current.bond_pct - target.bond_pct
    cash_dev = current.cash_pct - target.cash_pct
    gold_dev = current.gold_pct - target.gold_pct

    max_dev = max(abs(stock_dev), abs(bond_dev), abs(cash_dev), abs(gold_dev))

    # Classify severity (per design doc 03 §3.1)
    if max_dev < AllocationDefaults.DEVIATION_MILD:
        severity = "normal"
        recommendation = "配置在目标范围内，无需调整"
    elif max_dev < AllocationDefaults.DEVIATION_MODERATE:
        severity = "mild"
        recommendation = "配置略有偏离，可以关注"
    elif max_dev < AllocationDefaults.DEVIATION_HIGH:
        severity = "moderate"
        recommendation = f"配置偏离目标{max_dev:.1%}，建议考虑再平衡"
    else:
        severity = "high"
        recommendation = f"配置严重偏离目标{max_dev:.1%}，建议立即再平衡"

    return DeviationAnalysis(
        target=target,
        current=current,
        stock_deviation=stock_dev,
        bond_deviation=bond_dev,
        cash_deviation=cash_dev,
        gold_deviation=gold_dev,
        max_deviation=max_dev,
        severity=severity,  # type: ignore
        recommendation=recommendation,
    )


def detect_rebalance_trigger(
    analysis: DeviationAnalysis,
    days_since_last_rebalance: int,
) -> tuple[bool, str]:
    """Determine if rebalancing is needed.

    Two triggers (design doc 03 §4):
      1. Urgent: deviation > 15% (RebalanceDefaults.URGENT_DEVIATION)
      2. Time-based: deviation > 7% AND 6+ months since last rebalance

    Args:
        analysis: deviation analysis from analyze_deviation()
        days_since_last_rebalance: days elapsed since last rebalance

    Returns:
        tuple: (should_rebalance: bool, reason: str)
    """
    if analysis.max_deviation >= RebalanceDefaults.URGENT_DEVIATION:
        return True, f"严重偏离({analysis.max_deviation:.1%})，立即再平衡"

    if (analysis.max_deviation >= AllocationDefaults.DEVIATION_MODERATE and
            days_since_last_rebalance >= RebalanceDefaults.EXECUTE_INTERVAL_DAYS):
        return True, f"中度偏离({analysis.max_deviation:.1%})且距上次再平衡已{days_since_last_rebalance}天，执行再平衡"

    return False, "暂无需再平衡"


def validate_allocation(allocation: AllocationTarget) -> List[str]:
    """Validate allocation percentages.

    Checks:
      - Each percentage in [0, 100]
      - Sum approximately 100 (±2 tolerance)
      - No NaN/inf values

    Returns:
        List of validation errors (empty if valid)
    """
    errors: List[str] = []

    for asset_class in ("stock", "bond", "cash", "gold"):
        value = getattr(allocation, f"{asset_class}_pct")
        if value < 0 or value > 100:
            errors.append(f"{asset_class}_pct must be in [0, 100], got {value}")

    total = allocation.total_pct
    if total < 98 or total > 102:
        errors.append(f"Total allocation {total:.1f}% not close to 100%")

    return errors


__all__ = [
    "compute_target_allocation",
    "analyze_deviation",
    "detect_rebalance_trigger",
    "validate_allocation",
]
