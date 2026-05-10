"""
Checklist Domain Models — 7-Point Pre-Trade Decision Checklist
================================================================
Business value objects for the Mode A pre-trade checklist (07-decision-guard.md §2-3).

Contents:
  - ChecklistItem: a single checklist item with score and pass/fail
  - ChecklistResult: complete checklist evaluation result

All models are frozen dataclasses (immutable facts).
Downstream consumers: use_cases/run_checklist, api/decisions (checklist endpoint),
                      decision_archive (stored with trade records).

Design doc: docs/design/07-decision-guard.md §2 (7-point checklist)
            docs/design/03-rule-engine.md §7 (thresholds)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


# ============================================================
# ChecklistItem — a single checklist evaluation item
# ============================================================

@dataclass(frozen=True)
class ChecklistItem:
    """A single item in the 7-point decision checklist.

    Attributes:
        item_id: unique identifier (e.g., "emergency_fund", "concentration")
        label_zh: Chinese label for UI display
        score: 0-10 (10 = fully passes, 0 = completely fails)
        passed: True if score >= threshold (default 6)
        is_red_light: True if this item is a "red light" (score <= 3)
        detail: human-readable explanation of the evaluation
    """
    item_id: str = ""
    label_zh: str = ""
    score: int = 0              # 0-10
    passed: bool = True
    is_red_light: bool = False
    detail: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "item_id": self.item_id,
            "label_zh": self.label_zh,
            "score": self.score,
            "passed": self.passed,
            "is_red_light": self.is_red_light,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChecklistItem":
        return cls(
            item_id=str(d.get("item_id", "")),
            label_zh=str(d.get("label_zh", "")),
            score=int(d.get("score", 0)),
            passed=bool(d.get("passed", True)),
            is_red_light=bool(d.get("is_red_light", False)),
            detail=str(d.get("detail", "")),
        )


# ============================================================
# ChecklistResult — complete 7-point evaluation
# ============================================================

# Pass threshold: total score must be >= 60% of max to proceed
CHECKLIST_PASS_THRESHOLD_PCT: float = 0.60
CHECKLIST_MAX_SCORE: int = 70  # 7 items × 10 points each
CHECKLIST_PASS_THRESHOLD: int = 42  # 70 × 0.60 = 42


@dataclass(frozen=True)
class ChecklistResult:
    """Complete 7-point checklist evaluation result.

    Attributes:
        items: list of 7 ChecklistItem evaluations
        total_score: sum of all item scores (0-70)
        max_score: maximum possible score (70)
        passed: True if total_score >= 42 (60% threshold)
        red_light_count: number of items with is_red_light=True
        recommendation: action recommendation based on result
        blocked: True if checklist says "do not proceed"
    """
    items: List[ChecklistItem] = field(default_factory=list)
    total_score: int = 0
    max_score: int = CHECKLIST_MAX_SCORE
    passed: bool = True
    red_light_count: int = 0
    recommendation: str = ""
    blocked: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_score": self.total_score,
            "max_score": self.max_score,
            "passed": self.passed,
            "red_light_count": self.red_light_count,
            "recommendation": self.recommendation,
            "blocked": self.blocked,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ChecklistResult":
        items_raw = d.get("items", [])
        items = [ChecklistItem.from_dict(i) for i in items_raw] if items_raw else []
        return cls(
            items=items,
            total_score=int(d.get("total_score", 0)),
            max_score=int(d.get("max_score", CHECKLIST_MAX_SCORE)),
            passed=bool(d.get("passed", True)),
            red_light_count=int(d.get("red_light_count", 0)),
            recommendation=str(d.get("recommendation", "")),
            blocked=bool(d.get("blocked", False)),
        )


__all__ = [
    "ChecklistItem",
    "ChecklistResult",
    "CHECKLIST_PASS_THRESHOLD",
    "CHECKLIST_PASS_THRESHOLD_PCT",
    "CHECKLIST_MAX_SCORE",
]
