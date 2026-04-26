"""
Asset Allocation Defaults — Hard thresholds for rule engine
============================================================
All allocation matrices, deviation thresholds, and risk limits are defined here.
Users can override, but not exceed hard limits.

Reference: docs/design/03-rule-engine.md §2-8
Invariant #12: All hardcoded thresholds live here, not scattered in business code.
"""
from dataclasses import dataclass
from typing import Dict, Tuple, ClassVar


@dataclass(frozen=True)
class AllocationDefaults:
    """Target asset allocation matrix: risk_preference × family_stage → (stock%, bond%, cash%, gold%)"""

    # 12-cell matrix: (risk_preference, family_stage) → (stock, bond, cash, gold)
    # All percentages sum to 100
    MATRIX: ClassVar[Dict[Tuple[str, str], Tuple[int, int, int, int]]] = {
        # Conservative (保守)
        ("conservative", "single"): (30, 50, 15, 5),
        ("conservative", "married_mortgage"): (20, 55, 20, 5),
        ("conservative", "with_children"): (15, 60, 20, 5),
        ("conservative", "near_retirement"): (10, 60, 25, 5),

        # Balanced (平衡 — 默认)
        ("balanced", "single"): (50, 30, 15, 5),
        ("balanced", "married_mortgage"): (40, 35, 20, 5),
        ("balanced", "with_children"): (35, 40, 20, 5),
        ("balanced", "near_retirement"): (25, 50, 20, 5),

        # Aggressive (进取)
        ("aggressive", "single"): (70, 15, 10, 5),
        ("aggressive", "married_mortgage"): (60, 20, 15, 5),
        ("aggressive", "with_children"): (50, 25, 20, 5),
        ("aggressive", "near_retirement"): (35, 40, 20, 5),
    }

    # Age adjustment: stock_pct -= max(0, age - 30) * 0.5
    # After adjustment, enforce minimum from conservative row
    AGE_BASE: ClassVar[int] = 30
    AGE_ADJUSTMENT_RATE: ClassVar[float] = 0.5  # percentage points per year

    # Deviation thresholds (§3.1) — in percentage points
    DEVIATION_MILD: ClassVar[float] = 3.0  # 3 percentage points — gentle reminder
    DEVIATION_MODERATE: ClassVar[float] = 7.0  # 7 percentage points — clear reminder
    DEVIATION_HIGH: ClassVar[float] = 15.0  # 15 percentage points — strong reminder

    # User-adjustable range (§3.2) — hard limits (in percentage points)
    DEVIATION_MILD_MIN: ClassVar[float] = 2.0
    DEVIATION_MILD_MAX: ClassVar[float] = 5.0
    DEVIATION_MODERATE_MIN: ClassVar[float] = 5.0
    DEVIATION_MODERATE_MAX: ClassVar[float] = 10.0
    DEVIATION_HIGH_MIN: ClassVar[float] = 10.0
    DEVIATION_HIGH_MAX: ClassVar[float] = 20.0


@dataclass(frozen=True)
class RiskDefaults:
    """Concentration and risk limits (§5)"""

    # Single-asset concentration (§5)
    SINGLE_STOCK_MAX: ClassVar[float] = 0.25  # 25% — max position in one stock
    SINGLE_INDUSTRY_MAX: ClassVar[float] = 0.40  # 40% — max position in one industry
    TOP3_MAX: ClassVar[float] = 0.60  # 60% — top 3 holdings must not exceed

    # Loss triggers (§5)
    SINGLE_STOCK_LOSS_TRIGGER: ClassVar[float] = -0.25  # -25% — stop-loss review
    DRAWDOWN_1Y_TRIGGER: ClassVar[float] = -0.30  # -30% — portfolio max drawdown

    # Rebalance event triggers (§4)
    SINGLE_ASSET_MOVE: ClassVar[float] = 0.20  # 20% price change → urgent rebalance
    CONCENTRATION_TRIGGER: ClassVar[float] = 0.40  # concentration > 40% → urgent rebalance


@dataclass(frozen=True)
class ScoringDefaults:
    """5-dimensional recommendation scoring (§6)"""

    CUT_OFF: ClassVar[int] = 60  # Score <60 → eliminated from pool
    WATCH_THRESHOLD: ClassVar[int] = 75  # 60-75 → observation pool (tracked, not recommended)
    FOCUS_THRESHOLD: ClassVar[int] = 85  # >75 → candidate pool (recommended)
    VALUATION_PCT_FOR_HIGHLIGHT: ClassVar[float] = 0.30  # Score >85 + valuation percentile <30% → highlight


@dataclass(frozen=True)
class RebalanceDefaults:
    """Rebalancing schedule and thresholds (§4)"""

    CHECK_INTERVAL_DAYS: ClassVar[int] = 90  # Quarterly check
    EXECUTE_INTERVAL_DAYS: ClassVar[int] = 180  # Semi-annual execution
    URGENT_MOVE_PCT: ClassVar[float] = 0.20  # Single-asset move triggers immediate rebalance

    # Deviation triggers rebalance immediately
    URGENT_DEVIATION: ClassVar[float] = 15.0  # >15 percentage points deviation


@dataclass(frozen=True)
class StaleDataDefaults:
    """Data expiry thresholds for graceful fallback (§8 in design doc 06)"""

    INCOME_EXPENSE_MAX_DAYS: ClassVar[int] = 365  # Monthly income/expense max staleness
    INSURANCE_MAX_DAYS: ClassVar[int] = 365  # Insurance coverage max staleness
    REAL_ESTATE_MAX_DAYS: ClassVar[int] = 365  # Real estate valuation max staleness
    BALANCE_SHEET_MAX_DAYS: ClassVar[int] = 30  # Balance sheet items (cash, investments, etc.)


# Convenience exports for single-module imports
__all__ = [
    "AllocationDefaults",
    "RiskDefaults",
    "ScoringDefaults",
    "RebalanceDefaults",
    "StaleDataDefaults",
]
