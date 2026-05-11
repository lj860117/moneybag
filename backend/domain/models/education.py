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
    # --- New articles: 股票深化 ---
    HoldingArticleMapping(
        condition="has_stock",
        article_id="position-sizing",
        intro_template="持有个股时，仓位管理是控制风险最直接的手段。",
        priority=7,
    ),
    HoldingArticleMapping(
        condition="has_stock",
        article_id="overconfidence-bias",
        intro_template="持有个股的你，过度自信是最隐蔽的业绩杀手。",
        priority=6,
    ),
    HoldingArticleMapping(
        condition="has_stock",
        article_id="stop-loss-take-profit",
        intro_template="买了股票却不知道什么时候卖？来建立你的止盈止损规则。",
        priority=7,
    ),
    # --- New articles: 基金深化 ---
    HoldingArticleMapping(
        condition="has_fund",
        article_id="etf-vs-active-fund",
        intro_template="你持有基金产品，ETF 和主动基金的长期差距可能超出你的想象。",
        priority=7,
    ),
    HoldingArticleMapping(
        condition="has_fund",
        article_id="hot-fund-trap",
        intro_template="上涨榜前列的基金，往往是下一期的踩雷区——来看冠军基金魔咒。",
        priority=6,
    ),
    HoldingArticleMapping(
        condition="has_fund",
        article_id="asset-allocation-basics",
        intro_template="持有多只基金却越来越像一只？来学学真正的资产配置。",
        priority=5,
    ),
    # --- New articles: 债券&固收 ---
    HoldingArticleMapping(
        condition="has_bond",
        article_id="bond-basics",
        intro_template="你配置了债券类资产，深入了解它的收益来源和风险很有必要。",
        priority=8,
    ),
    HoldingArticleMapping(
        condition="has_bond",
        article_id="convertible-bond-basics",
        intro_template="可转债既不是纯股也不是纯债，搞清楚它的特殊规则再出手。",
        priority=7,
    ),
    # --- New articles: 衍生品&另类 ---
    HoldingArticleMapping(
        condition="has_real_estate",
        article_id="reit-basics",
        intro_template="除了直接持有房产，公募 REITs 是更灵活的不动产投资方式。",
        priority=6,
    ),
    HoldingArticleMapping(
        condition="has_gold",
        article_id="inflation-real-returns",
        intro_template="黄金是抗通胀的工具，先来理解通胀是如何侵蚀真实收益的。",
        priority=7,
    ),
    # --- New articles: 行为金融&心态 ---
    HoldingArticleMapping(
        condition="drawdown_gt_10",
        article_id="drawdown-psychology",
        intro_template="{asset_name}出现浮亏，这五个锚点帮你在下跌时保持理性。",
        priority=10,
    ),
    HoldingArticleMapping(
        condition="drawdown_gt_10",
        article_id="stop-loss-take-profit",
        intro_template="浮亏时最需要的是事先设好的卖出规则，而不是临时决策。",
        priority=9,
    ),
    HoldingArticleMapping(
        condition="has_stock",
        article_id="herd-mentality",
        intro_template="市场追涨杀跌时，从众心理是你最难察觉的敌人。",
        priority=5,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="sunk-cost-fallacy",
        intro_template="买入价不应该成为你卖出的理由——沉没成本谬误是你的死穴吗？",
        priority=4,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="sell-high-buy-low-trap",
        intro_template="大多数投资者的实际收益比基金本身差很多，来看看高买低卖的真实成本。",
        priority=4,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="inflation-real-returns",
        intro_template="你的投资收益跑赢通胀了吗？先学会计算实际收益率。",
        priority=3,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="rebalancing-math",
        intro_template="不做再平衡的长期代价有多大？数学告诉你答案。",
        priority=3,
    ),
    HoldingArticleMapping(
        condition="any_holding",
        article_id="72-rule",
        intro_template="72 法则：5 秒钟估算你的投资翻倍需要多少年。",
        priority=2,
    ),
    # --- 通用课程：无论是否有持仓都可推送 ---
    HoldingArticleMapping(
        condition="always",
        article_id="compound-interest",
        intro_template="投资第一课：复利是时间送给耐心人的礼物。",
        priority=1,
    ),
    HoldingArticleMapping(
        condition="always",
        article_id="emergency-fund-6-months",
        intro_template="在做任何投资之前，先确保有 6 个月生活费的应急金。",
        priority=1,
    ),
    HoldingArticleMapping(
        condition="always",
        article_id="family-pyramid",
        intro_template="理财从哪开始？家庭金字塔告诉你最优先要解决的事。",
        priority=1,
    ),
    HoldingArticleMapping(
        condition="always",
        article_id="72-rule",
        intro_template="72 法则：5 秒钟估算你的钱翻倍需要多少年。",
        priority=1,
    ),
    HoldingArticleMapping(
        condition="always",
        article_id="4pct-rule",
        intro_template="退休需要多少钱？4% 法则给你一个简单公式。",
        priority=1,
    ),
    HoldingArticleMapping(
        condition="always",
        article_id="inflation-real-returns",
        intro_template="把钱存银行就安全了？通胀每年都在偷走你的购买力。",
        priority=1,
    ),
    HoldingArticleMapping(
        condition="always",
        article_id="asset-allocation-basics",
        intro_template="鸡蛋不要放在一个篮子里——资产配置入门。",
        priority=1,
    ),
]


# Fatigue control constants (09-advisor-features.md §3.4)
MAX_PUSHES_PER_WEEK: int = 2          # Weekly cap (including event-triggered)
ARTICLE_REPEAT_COOLDOWN_DAYS: int = 90  # Same article not repeated within N days
EVENT_TRIGGER_MAX_PER_MONTH: int = 1   # Event-triggered lessons max 1/month
DRAWDOWN_THRESHOLD_PCT: float = 10.0    # Drawdown > this triggers event lesson
