"""
Decision Domain Models — Decision Review + Buy Reasons + Quality Score
=======================================================================
Business value objects for the M3 W1 decision guard system (mode B: post-trade review).

Contents:
  - BuyReasonCategory: enum-like categories (基本面/技术面/情绪面/跟风/其他)
  - BuyReason: a single buy reason with signal level
  - DecisionQualityScore: computed quality score = f(clarity, source, risk, horizon)
  - DecisionReview: final review record (reasons + score + checklist)

All models are frozen dataclasses (immutable facts).
Downstream consumers: decision_archive (storage), use_cases/review_decision,
                      api/decisions (review endpoint), M5 attribution_analysis.

Design doc: docs/design/07-decision-guard.md sections 四-五
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal


# ============================================================
# BuyReasonCategory — the 5 categories from design doc 07 §5.2
# ============================================================

class BuyReasonCategory(str, Enum):
    """Buy reason categories.

    Mapping:
      fundamental = 基本面 (valuation low, sector logic, earnings)
      technical   = 技术面 (chart signals, moving averages)
      emotional   = 情绪面 (FOMO, momentum chase, hot news)
      follow      = 跟风 (friend recommended, social media)
      other       = 其他 (DCA plan, rebalance need, etc.)
    """
    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    EMOTIONAL = "emotional"
    FOLLOW = "follow"
    OTHER = "other"


# ============================================================
# Signal levels for each predefined reason
# ============================================================

class SignalLevel(str, Enum):
    """Signal level indicating healthiness of a buy reason.

    green  = healthy reason (planned, data-driven)
    yellow = caution (e.g., averaging down after loss)
    red    = danger signal (FOMO, hot news chase, friend tip)
    """
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


# ============================================================
# Predefined reasons (from 07-decision-guard.md §5.2)
# ============================================================

@dataclass(frozen=True)
class PredefinedReason:
    """A predefined buy reason option shown in the multi-select UI.

    Attributes:
        id: unique slug for this reason
        label_zh: Chinese label shown to user
        category: which category this belongs to
        signal: signal level (green/yellow/red)
    """
    id: str
    label_zh: str
    category: BuyReasonCategory
    signal: SignalLevel


# Complete list of predefined reasons (from design doc 07 §5.2)
PREDEFINED_REASONS: List[PredefinedReason] = [
    # Fundamental — GREEN
    PredefinedReason(
        id="valuation_low",
        label_zh="估值已到历史低位（<30% 分位）",
        category=BuyReasonCategory.FUNDAMENTAL,
        signal=SignalLevel.GREEN,
    ),
    PredefinedReason(
        id="sector_logic",
        label_zh="基本面 / 行业长期逻辑改善",
        category=BuyReasonCategory.FUNDAMENTAL,
        signal=SignalLevel.GREEN,
    ),
    # Technical — GREEN
    PredefinedReason(
        id="allocation_gap",
        label_zh="我的目标配比缺这部分，补齐即可",
        category=BuyReasonCategory.TECHNICAL,
        signal=SignalLevel.GREEN,
    ),
    PredefinedReason(
        id="dca_plan",
        label_zh="我做了定投计划，这是例行操作",
        category=BuyReasonCategory.OTHER,
        signal=SignalLevel.GREEN,
    ),
    # Emotional — RED
    PredefinedReason(
        id="momentum_chase",
        label_zh="最近涨得好 / 势头猛",
        category=BuyReasonCategory.EMOTIONAL,
        signal=SignalLevel.RED,
    ),
    PredefinedReason(
        id="hot_news",
        label_zh="热搜 / 新闻 / 朋友推荐",
        category=BuyReasonCategory.FOLLOW,
        signal=SignalLevel.RED,
    ),
    # Emotional — YELLOW
    PredefinedReason(
        id="averaging_down",
        label_zh="前期亏损，想摊平成本（补仓）",
        category=BuyReasonCategory.EMOTIONAL,
        signal=SignalLevel.YELLOW,
    ),
    # Follow — RED
    PredefinedReason(
        id="fomo",
        label_zh="其他人都在买，怕错过",
        category=BuyReasonCategory.FOLLOW,
        signal=SignalLevel.RED,
    ),
]

# Quick lookup by ID
REASON_BY_ID: Dict[str, PredefinedReason] = {r.id: r for r in PREDEFINED_REASONS}


# ============================================================
# BuyReason — a user's selected reason (or custom text)
# ============================================================

@dataclass(frozen=True)
class BuyReason:
    """A single buy reason selected by the user.

    Either a predefined reason (reason_id set) or custom text (custom_text set).
    """
    reason_id: str = ""          # matches PredefinedReason.id (empty for custom)
    custom_text: str = ""        # free-text for "其他补充"
    category: str = ""           # BuyReasonCategory value
    signal: str = "green"        # SignalLevel value (default green for custom)

    def to_dict(self) -> Dict[str, object]:
        return {
            "reason_id": self.reason_id,
            "custom_text": self.custom_text,
            "category": self.category,
            "signal": self.signal,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BuyReason":
        return cls(
            reason_id=str(d.get("reason_id", "")),
            custom_text=str(d.get("custom_text", "")),
            category=str(d.get("category", "")),
            signal=str(d.get("signal", "green")),
        )

    @classmethod
    def from_predefined(cls, reason_id: str) -> "BuyReason":
        """Create from a predefined reason ID."""
        pr = REASON_BY_ID.get(reason_id)
        if pr is None:
            return cls(reason_id=reason_id, category="other", signal="green")
        return cls(
            reason_id=pr.id,
            category=pr.category.value,
            signal=pr.signal.value,
        )

    @classmethod
    def from_custom(cls, text: str, category: str = "other") -> "BuyReason":
        """Create from custom user text."""
        return cls(
            custom_text=text[:200],  # cap length
            category=category,
            signal="green",  # custom reasons default to green (user is explicit)
        )


# ============================================================
# DecisionQualityScore — computed quality assessment
# ============================================================

@dataclass(frozen=True)
class DecisionQualityScore:
    """Decision quality score = f(reason_clarity, info_source, risk_awareness, time_horizon).

    Total score: 0-100 (higher = better quality decision).
    Sub-scores: each 0-25 (sum = total).

    Interpretation:
      >= 80: excellent (thoughtful, data-driven)
      60-79: good (mostly rational)
      40-59: mediocre (some emotional signals)
      < 40:  poor (mostly impulsive/emotional)

    Design doc: 07-decision-guard.md §4.1 — quality score stored in decision_log.
    """
    reason_clarity: int = 0     # 0-25: how clear/specific the reasons are
    info_source: int = 0        # 0-25: quality of information sources (data vs gossip)
    risk_awareness: int = 0     # 0-25: awareness of risks (emergency fund, concentration)
    time_horizon: int = 0       # 0-25: appropriate time horizon (not short-term speculation)

    total: int = 0              # 0-100 sum
    red_flags: int = 0          # count of red-signal reasons selected
    yellow_flags: int = 0       # count of yellow-signal reasons selected
    grade: str = ""             # "excellent" / "good" / "mediocre" / "poor"

    def to_dict(self) -> Dict[str, object]:
        return {
            "reason_clarity": self.reason_clarity,
            "info_source": self.info_source,
            "risk_awareness": self.risk_awareness,
            "time_horizon": self.time_horizon,
            "total": self.total,
            "red_flags": self.red_flags,
            "yellow_flags": self.yellow_flags,
            "grade": self.grade,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionQualityScore":
        return cls(
            reason_clarity=int(d.get("reason_clarity", 0)),
            info_source=int(d.get("info_source", 0)),
            risk_awareness=int(d.get("risk_awareness", 0)),
            time_horizon=int(d.get("time_horizon", 0)),
            total=int(d.get("total", 0)),
            red_flags=int(d.get("red_flags", 0)),
            yellow_flags=int(d.get("yellow_flags", 0)),
            grade=str(d.get("grade", "")),
        )


# ============================================================
# DecisionReview — full review record (stored in archive)
# ============================================================

@dataclass(frozen=True)
class DecisionReview:
    """A complete post-trade decision review record.

    Created by use_cases/review_decision.py, stored via decision_archive.
    Used by M5 attribution analysis for pattern detection.

    Fields:
      id: unique review identifier
      user_id: who made this decision
      trade_time: when the trade was executed (ISO 8601)
      review_time: when the review was submitted (ISO 8601)
      asset_code: stock/fund code (e.g., "600519")
      asset_name: display name (e.g., "贵州茅台")
      action: "buy" | "sell" | "add" | "reduce"
      amount: trade amount in CNY
      reasons: list of buy reasons (multi-select + custom)
      quality_score: computed quality assessment
      notes: optional user notes
    """
    id: str = ""
    user_id: str = ""
    trade_time: str = ""
    review_time: str = ""
    asset_code: str = ""
    asset_name: str = ""
    action: str = "buy"         # buy/sell/add/reduce
    amount: float = 0.0
    reasons: List[BuyReason] = field(default_factory=list)
    quality_score: DecisionQualityScore = field(default_factory=DecisionQualityScore)
    notes: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "trade_time": self.trade_time,
            "review_time": self.review_time,
            "asset_code": self.asset_code,
            "asset_name": self.asset_name,
            "action": self.action,
            "amount": self.amount,
            "reasons": [r.to_dict() for r in self.reasons],
            "quality_score": self.quality_score.to_dict(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionReview":
        reasons_raw = d.get("reasons", [])
        reasons = [BuyReason.from_dict(r) for r in reasons_raw] if reasons_raw else []
        qs_raw = d.get("quality_score", {})
        quality_score = DecisionQualityScore.from_dict(qs_raw) if qs_raw else DecisionQualityScore()
        return cls(
            id=str(d.get("id", "")),
            user_id=str(d.get("user_id", "")),
            trade_time=str(d.get("trade_time", "")),
            review_time=str(d.get("review_time", "")),
            asset_code=str(d.get("asset_code", "")),
            asset_name=str(d.get("asset_name", "")),
            action=str(d.get("action", "buy")),
            amount=float(d.get("amount", 0.0)),
            reasons=reasons,
            quality_score=quality_score,
            notes=str(d.get("notes", "")),
        )


__all__ = [
    "BuyReasonCategory",
    "SignalLevel",
    "PredefinedReason",
    "PREDEFINED_REASONS",
    "REASON_BY_ID",
    "BuyReason",
    "DecisionQualityScore",
    "DecisionReview",
]
