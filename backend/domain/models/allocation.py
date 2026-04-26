"""
Asset Allocation Domain Models
===============================
Business value objects for the allocation framework (M2 W4).

Contents:
  - AllocationTarget: target allocation percentages (stock/bond/cash/gold)
  - AllocationState: current actual allocation percentages
  - DeviationAnalysis: deviation from target with severity classification

All models are frozen dataclasses (immutable facts).
Downstream consumers: rule_engine (03), scheduling (05), api (allocation endpoints).

Design doc: docs/design/03-rule-engine.md sections 二-三
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal


# ============================================================
# AllocationTarget
# ============================================================

@dataclass(frozen=True)
class AllocationTarget:
    """Target asset allocation percentages based on profile + risk preference.

    Computed by allocation_service.compute_target_allocation() using:
      1. Base matrix from defaults.py (risk_preference × family_stage)
      2. Age adjustment (linear from age 30)
      3. User override (if any)

    All percentages sum to 100 (or 99/101 due to rounding).
    """

    stock_pct: float           # stock allocation (0-100)
    bond_pct: float            # bond allocation (0-100)
    cash_pct: float            # cash allocation (0-100)
    gold_pct: float            # gold/other allocation (0-100)

    reason: str = ""           # explanation: "matrix", "age_adjusted", "user_override", etc.

    def to_dict(self) -> Dict[str, object]:
        return {
            "stock_pct": self.stock_pct,
            "bond_pct": self.bond_pct,
            "cash_pct": self.cash_pct,
            "gold_pct": self.gold_pct,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AllocationTarget":
        return cls(
            stock_pct=float(d.get("stock_pct", 0.0)),
            bond_pct=float(d.get("bond_pct", 0.0)),
            cash_pct=float(d.get("cash_pct", 0.0)),
            gold_pct=float(d.get("gold_pct", 0.0)),
            reason=str(d.get("reason", "")),
        )

    @property
    def total_pct(self) -> float:
        """Sum of all allocation percentages (should be ~100)."""
        return self.stock_pct + self.bond_pct + self.cash_pct + self.gold_pct


# ============================================================
# AllocationState
# ============================================================

@dataclass(frozen=True)
class AllocationState:
    """Current actual asset allocation in portfolio.

    Computed by portfolio_aggregation service, typically from:
      - Holdings (stocks + funds)
      - Cash positions
      - Real estate or other assets

    Updated whenever holdings change.
    """

    stock_pct: float           # actual stock allocation
    bond_pct: float            # actual bond allocation
    cash_pct: float            # actual cash allocation
    gold_pct: float            # actual gold/other allocation

    last_rebalanced: str = ""  # ISO 8601 timestamp of last rebalance

    def to_dict(self) -> Dict[str, object]:
        return {
            "stock_pct": self.stock_pct,
            "bond_pct": self.bond_pct,
            "cash_pct": self.cash_pct,
            "gold_pct": self.gold_pct,
            "last_rebalanced": self.last_rebalanced,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AllocationState":
        return cls(
            stock_pct=float(d.get("stock_pct", 0.0)),
            bond_pct=float(d.get("bond_pct", 0.0)),
            cash_pct=float(d.get("cash_pct", 0.0)),
            gold_pct=float(d.get("gold_pct", 0.0)),
            last_rebalanced=str(d.get("last_rebalanced", "")),
        )

    @property
    def total_pct(self) -> float:
        """Sum of all allocation percentages."""
        return self.stock_pct + self.bond_pct + self.cash_pct + self.gold_pct


# ============================================================
# DeviationAnalysis
# ============================================================

@dataclass(frozen=True)
class DeviationAnalysis:
    """Analysis of allocation deviation from target.

    Measures how far actual allocation deviates from target and classifies severity.
    Design doc 03-rule-engine.md §3.1: Four severity levels based on deviation %.
    """

    target: AllocationTarget          # target allocation
    current: AllocationState          # current allocation

    # Deviation percentages for each asset class (as % difference)
    stock_deviation: float            # actual_stock - target_stock (can be negative)
    bond_deviation: float             # actual_bond - target_bond
    cash_deviation: float             # actual_cash - target_cash
    gold_deviation: float             # actual_gold - target_gold

    # Overall deviation magnitude (max absolute deviation across all classes)
    max_deviation: float              # max(abs(stock), abs(bond), abs(cash), abs(gold))

    # Severity classification (per design doc 03 §3.1)
    severity: Literal["normal", "mild", "moderate", "high"] = "normal"
    recommendation: str = ""          # e.g., "No action needed", "Monitor", "Rebalance soon", "Rebalance now"

    def to_dict(self) -> Dict[str, object]:
        return {
            "target": self.target.to_dict(),
            "current": self.current.to_dict(),
            "stock_deviation": self.stock_deviation,
            "bond_deviation": self.bond_deviation,
            "cash_deviation": self.cash_deviation,
            "gold_deviation": self.gold_deviation,
            "max_deviation": self.max_deviation,
            "severity": self.severity,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DeviationAnalysis":
        severity = str(d.get("severity", "normal"))
        if severity not in ("normal", "mild", "moderate", "high"):
            severity = "normal"
        return cls(
            target=AllocationTarget.from_dict(d.get("target", {})),
            current=AllocationState.from_dict(d.get("current", {})),
            stock_deviation=float(d.get("stock_deviation", 0.0)),
            bond_deviation=float(d.get("bond_deviation", 0.0)),
            cash_deviation=float(d.get("cash_deviation", 0.0)),
            gold_deviation=float(d.get("gold_deviation", 0.0)),
            max_deviation=float(d.get("max_deviation", 0.0)),
            severity=severity,  # type: ignore
            recommendation=str(d.get("recommendation", "")),
        )
