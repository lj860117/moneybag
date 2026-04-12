"""
钱袋子 — 全局配置
所有阈值、权重、缓存TTL集中管理，禁止在业务代码里写魔法数字
"""
import os
from pathlib import Path

# ---- 持久化目录 ----
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(exist_ok=True)
RECEIPTS_DIR = DATA_DIR / "receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)

# ---- 缓存 TTL（秒）----
NAV_CACHE_TTL = 3600        # 基金净值 1小时
NEWS_CACHE_TTL = 1800       # 新闻 30分钟
MACRO_CACHE_TTL = 7200      # 宏观 2小时
FUND_RANK_CACHE_TTL = 86400 # 基金排行 24小时

# ---- 12维因子权重（V4.5）----
FACTOR_WEIGHTS = {
    "技术面": 0.25,
    "基本面": 0.30,
    "资金面": 0.20,
    "情绪面": 0.15,
    "宏观面": 0.10,
}

# ---- 估值阈值 ----
VALUATION_LOW = 20       # 低估百分位
VALUATION_MID_LOW = 40
VALUATION_MID_HIGH = 60
VALUATION_HIGH = 80      # 高估百分位
VALUATION_EXTREME = 85   # 极度高估，巴菲特无条件减仓

# ---- 风控硬阈值（借鉴幻方量化）----
RISK_DRAWDOWN_WARNING = -0.15    # 回撤预警线 → 降仓至50%
RISK_DRAWDOWN_DANGER = -0.18     # 回撤警戒线 → 降仓至40%+增配债券
RISK_DAILY_DROP_LIMIT = -0.04    # 单日跌幅限制 → 暂停开新仓
RISK_SINGLE_STOCK_MAX = 0.03     # 单票最大占比 3%
RISK_SINGLE_FUND_MAX = 0.15      # 单只基金最大占比 15%
RISK_INDUSTRY_MAX = 0.20         # 单行业最大占比 20%
RISK_TAKE_PROFIT = 0.40          # 止盈阈值 → 收益≥40%减半
RISK_MAX_DRAWDOWN_LIMIT = -0.20  # 最大允许回撤 -20%（绝不突破）
RISK_REBALANCE_THRESHOLD = 0.08  # 再平衡触发偏离度 ±8%

# ---- 资产配置目标比例（根据估值动态调整）----
ALLOCATION_PROFILES = {
    "low": {"stock": 0.75, "bond": 0.15, "cash": 0.10},      # 低估(<20%)
    "mid": {"stock": 0.65, "bond": 0.25, "cash": 0.10},      # 适中(20-80%)
    "high": {"stock": 0.45, "bond": 0.35, "cash": 0.20},     # 高估(>80%)
}

# ---- 智能定投倍率 ----
DCA_MULTIPLIERS = {
    "extreme_low": 2.0,     # <10% 极度低估
    "low": 1.5,             # 10-30% 低估
    "mid_low": 1.2,         # 30-50% 偏低
    "mid": 1.0,             # 50-70% 适中
    "mid_high": 0.5,        # 70-85% 偏高
    "high": 0.3,            # 85-95% 高估
    "extreme_high": 0.0,    # >95% 极度高估，暂停
}

# ---- LLM API 配置 ----
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.deepseek.com/v1/chat/completions")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
