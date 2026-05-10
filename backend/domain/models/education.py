"""
Weekly Education Models
========================
Represents weekly financial lessons personalized to user holdings.

Design doc: docs/design/09-advisor-features.md §三

Invariant #10: domain.models never import from infra
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LessonTrigger(str, Enum):
    """What triggered this lesson selection."""
    WEEKLY_REGULAR = "weekly_regular"       # Regular weekly Sunday push
    HOLDING_EVENT = "holding_event"         # Position drawdown >10%
    NEW_ARTICLE = "new_article"            # New article added to knowledge base


class PushStatus(str, Enum):
    """Status of a push delivery."""
    PENDING = "pending"
    DELIVERED = "delivered"
    SKIPPED_FATIGUE = "skipped_fatigue"     # Skipped due to fatigue control
    SKIPPED_DUPLICATE = "skipped_duplicate"  # Same article already sent


@dataclass(frozen=True)
class HoldingContext:
    """Simplified holding context for lesson matching.

    Derived from user's actual portfolio — not the full holding object,
    just what the education service needs to select relevant articles.
    """
    user_id: str
    asset_classes: List[str]              # ["fund", "stock", "gold", ...]
    has_fund: bool = False
    has_stock: bool = False
    has_gold: bool = False
    has_bond: bool = False
    has_real_estate: bool = False
    has_insurance: bool = False
    max_drawdown_pct: Optional[float] = None   # Worst single-position drawdown
    drawdown_asset_name: Optional[str] = None  # Which asset has max drawdown
    days_since_first_trade: int = 0            # Experience proxy
    total_positions: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "asset_classes": self.asset_classes,
            "has_fund": self.has_fund,
            "has_stock": self.has_stock,
            "has_gold": self.has_gold,
            "has_bond": self.has_bond,
            "has_real_estate": self.has_real_estate,
            "has_insurance": self.has_insurance,
            "max_drawdown_pct": self.max_drawdown_pct,
            "drawdown_asset_name": self.drawdown_asset_name,
            "days_since_first_trade": self.days_since_first_trade,
            "total_positions": self.total_positions,
        }


@dataclass(frozen=True)
class WeeklyLesson:
    """A selected weekly lesson for a user.

    Content comes from the RAG knowledge base (08-knowledge-rag.md).
    AI only selects + personalizes intro sentence — never free-creates content.
    """
    lesson_id: str                    # "user1:2026-W19:gold-hedge"
    user_id: str
    article_id: str                   # Slug of knowledge article
    article_title: str                # Display title
    article_category: str             # ContentCategory value
    intro_sentence: str               # Personalized 1-sentence intro (template-filled)
    trigger: LessonTrigger
    week_iso: str                     # "2026-W19" (ISO week)
    created_at: float                 # Unix timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "user_id": self.user_id,
            "article_id": self.article_id,
            "article_title": self.article_title,
            "article_category": self.article_category,
            "intro_sentence": self.intro_sentence,
            "trigger": self.trigger.value,
            "week_iso": self.week_iso,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WeeklyLesson:
        data = data.copy()
        data["trigger"] = LessonTrigger(data["trigger"])
        return cls(**data)


@dataclass(frozen=True)
class LessonPushRecord:
    """Record of a lesson push (for fatigue control + dedup).

    Stored per user. Used to enforce:
    - Max 2 pushes per week (including event-triggered)
    - Same article not repeated within 90 days
    """
    user_id: str
    article_id: str
    week_iso: str                     # When it was pushed
    trigger: LessonTrigger
    status: PushStatus
    pushed_at: float                  # Unix timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "article_id": self.article_id,
            "week_iso": self.week_iso,
            "trigger": self.trigger.value,
            "status": self.status.value,
            "pushed_at": self.pushed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LessonPushRecord:
        data = data.copy()
        data["trigger"] = LessonTrigger(data["trigger"])
        data["status"] = PushStatus(data["status"])
        return cls(**data)


# ============================================================================
# Holding → Article Matching Rules (template-based, no AI free-creation)
# ============================================================================

@dataclass(frozen=True)
class HoldingArticleMapping:
    """Maps a holding condition to a relevant knowledge article.

    Used by education_service to select articles based on portfolio state.
    """
    condition: str                   # "has_fund", "has_gold", "drawdown_gt_10", etc.
    article_id: str                  # Knowledge base article slug
    intro_template: str              # Template for intro sentence
    priority: int = 5                # Higher = more relevant (1-10)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition": self.condition,
            "article_id": self.article_id,
            "intro_template": self.intro_template,
            "priority": self.priority,
        }


# Default mapping table (from 09-advisor-features.md §3.2)
HOLDING_ARTICLE_MAPPINGS: List[HoldingArticleMapping] = [
    # Fund holdings → ETF/index education
    HoldingArticleMapping(
        condition="has_fund",
        article_id="index-investing",
        intro_template="你持有基金产品，这周来了解一下指数投资的长期优势。",
        priority=7,
    ),
    HoldingArticleMapping(
        condition="has_fund",
        article_id="dca-strategy",
        intro_template="持有基金的你，定投策略可能是最适合的加仓方式。",
        priority=6,
    ),
    # Gold holdings → gold hedge education
    HoldingArticleMapping(
        condition="has_gold",
        article_id="gold-hedge",
        intro_template="你配置了黄金，来看看它在组合里到底起什么作用。",
        priority=8,
    ),
    # Stock holdings → rebalance + behavioral
    HoldingArticleMapping(
        condition="has_stock",
        article_id="stock-bond-rebalance",
        intro_template="持有股票的你，了解再平衡能帮你控制风险。",
        priority=6,
    ),
    HoldingArticleMapping(
        condition="has_stock",
        article_id="loss-aversion",
        intro_template="持有个股时，损失厌恶是最常见的心理陷阱。",
        priority=5,
    ),
    HoldingArticleMapping(
        condition="has_stock",
        article_id="anchoring-effect",
        intro_template="买了个股？锚定效应可能正在影响你的判断。",
        priority=5,
    ),
    # Drawdown event → behavioral finance
    HoldingArticleMapping(
        condition="drawdown_gt_10",
        article_id="loss-aversion",
        intro_template="你的{asset_name}浮亏超过10%，这是最该冷静的时刻。",
        priority=9,
    ),
    HoldingArticleMapping(
        condition="drawdown_gt_10",
        article_id="anchoring-effect",
        intro_template="{asset_name}下跌了，别被'买入价'这个锚点困住。",
        priority=8,
    ),
    # Real estate → lifecycle + 4% rule
    HoldingArticleMapping(
        condition="has_real_estate",
        article_id="lifecycle-investing",
        intro_template="有房产配置的你，生命周期投资法帮你看清全局。",
        priority=6,
    ),
    # Insurance → family pyramid
    HoldingArticleMapping(
        condition="has_insurance",
        article_id="family-pyramid",
        intro_template="保障已就位，来看看家庭金字塔的全貌。",
        priority=5,
    ),
    HoldingArticleMapping(
        condition="has_insurance",
        article_id="insurance-priority",
        intro_template="你已买了保险，确认一下四大险种是否齐全。",
        priority=7,
    ),
    # General (any holdings) → compound interest, emergency fund
    HoldingArticleMapping(
        condition="any_holding",
        article_id="compound-interest",
        intro_template="投资路上，复利是你最强的朋友——前提是别中途下车。",
        priority=4,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="emergency-fund-6-months",
        intro_template="在投资之前，确保你有6个月的应急金垫底。",
        priority=3,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="4pct-rule",
        intro_template="长期投资的终局是什么？4%法则告诉你退休需要多少钱。",
        priority=3,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="family-pyramid",
        intro_template="每个家庭的理财都应该从金字塔底层开始。",
        priority=2,
    ),
]


# Fatigue control constants (09-advisor-features.md §3.4)
MAX_PUSHES_PER_WEEK: int = 2          # Weekly cap (including event-triggered)
ARTICLE_REPEAT_COOLDOWN_DAYS: int = 90  # Same article not repeated within N days
EVENT_TRIGGER_MAX_PER_MONTH: int = 1   # Event-triggered lessons max 1/month
DRAWDOWN_THRESHOLD_PCT: float = 10.0    # Drawdown > this triggers event lesson
