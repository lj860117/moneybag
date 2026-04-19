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
STOCK_CACHE_TTL = 7200      # 选股数据 2小时
FACTOR_CACHE_TTL = 3600     # 因子数据 1小时

# ---- 13维信号权重 V5.0（P0.3: Single Source of Truth，signal.py 从这里读）----
SIGNAL_WEIGHTS_V5 = {
    # --- 技术面 25% ---
    "RSI": 0.08,
    "MACD": 0.10,
    "布林带": 0.07,
    # --- 基本面 30% ---
    "估值": 0.18,
    "股息率": 0.05,
    "股债性价比": 0.07,
    # --- 资金面 20% ---
    "北向资金": 0.10,
    "融资融券": 0.05,
    "SHIBOR": 0.05,
    # --- 情绪面 15% ---
    "恐贪指数": 0.08,
    "新闻情绪": 0.07,
    # --- 宏观面 5% ---
    "宏观经济": 0.05,
    # --- 地缘面 5%（V6 Phase 2 新增）---
    "地缘风险": 0.05,
}
# 向后兼容：旧代码可能引用 FACTOR_WEIGHTS
FACTOR_WEIGHTS = {
    "技术面": 0.25, "基本面": 0.30, "资金面": 0.20,
    "情绪面": 0.15, "宏观面": 0.05, "地缘面": 0.05,
}

# ---- 7维选股权重（V5.0）----
# FIX 2026-04-19 F4: 从 0.15 → 0.18，统一为 stock_screen.py 原有逻辑
# quality 提权：区分好坏公司的核心，value 由 0.20 降为 0.20（保持），补偿来自 risk 降 0.15→0.12
STOCK_SCREEN_WEIGHTS = {
    "value": 0.20,
    "growth": 0.15,
    "quality": 0.18,     # 质量（提权：区分好坏公司的核心）
    "momentum": 0.15,
    "risk": 0.12,
    "liquidity": 0.10,
    "sentiment": 0.10,
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

# ---- 股票持仓纪律阈值 ----
STOCK_SINGLE_MAX = 0.20          # 单只股票最大仓位占比 20%
STOCK_MIN_COUNT = 5              # 最低持仓只数（低于此数警告分散不足）
STOCK_INDUSTRY_MAX = 0.30        # 单一行业最大占比 30%
STOCK_STOP_LOSS = -0.08          # 止损线 -8%（触发强制提醒）
STOCK_TAKE_PROFIT = 0.20         # 止盈线 +20%（触发分批卖出提醒）
STOCK_CONCENTRATION_WARN = 0.30  # 集中度预警：单只占总市值 > 30%

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

# ---- 版本号（Phase 1 更新）----
APP_VERSION = "7.1.0"  # P0.1: 决策引擎完整数据注入（信息饥荒修复）

# ---- V6 Phase 1: 油价阈值（布伦特，美元/桶）----
OIL_BRENT_NORMAL = 80      # 正常区间上限
OIL_BRENT_WARNING = 100    # 警戒线
OIL_BRENT_CRISIS = 120     # 危机线

# ---- V6 Phase 1: 地缘事件缓存 TTL（秒）----
GEO_CACHE_TTL = 1800       # 地缘新闻 30 分钟
COMMODITY_CACHE_TTL = 3600  # 大宗商品 1 小时

# ---- Token 预算控制（Phase 0 新增）----
TOKEN_BUDGET = {
    "daily_budget_rmb":    3.0,         # ¥3/天（正常 ¥0.5，6倍余量）
    "monthly_budget_rmb":  30.0,        # ¥30/月（硬上限）
    "alert_threshold":     0.7,         # 70% 时推企微预警
    "critical_threshold":  0.9,         # 90% 时降级为规则引擎
    "on_exceed":           "degrade",   # 超限策略：降级/warn_only/hard_stop
    "max_input_per_call":  50_000,      # 单次最大 5万 input token
    "max_output_per_call": 30_000,      # 单次最大 3万 output token
}

# DeepSeek 定价（2026-04，¥/百万token）
DEEPSEEK_PRICING = {
    "input_cache_hit":    0.20,   # 缓存命中
    "input_cache_miss":   2.03,   # 缓存未命中
    "output":             3.04,   # 输出
}

# ============================================================
# V7.2 硬编码治理（2026-04-19）
# 把散落在业务代码里的魔法数字集中到这里，方便调参
# 业务行为完全不变，只是把引用从 hardcoded 改为 from config import XXX
# ============================================================

# ---- DCF 估值引擎默认参数 ----
DCF_DEFAULTS = {
    "discount_rate":     0.10,   # 折现率（WACC 近似）
    "terminal_growth":   0.03,   # 永续增长率（通胀+GDP 长期均值）
    "projection_years":  5,      # 预测期
    "margin_of_safety":  0.30,   # 安全边际（巴菲特经典 30%）
    "default_growth":    0.08,   # 拿不到一致预期时的默认增速
    "growth_min":        0.02,   # 增速下限
    "growth_max":        0.30,   # 增速上限
    "fair_range_upper":  1.2,    # 合理区间 = 内在价值 × 1.2
}

# ---- 回测 / 组合优化 ----
BACKTEST_DEFAULTS = {
    "risk_free_annual":    0.018,     # 年化无风险利率 1.8%（国债 10Y 近似）
    "risk_free_monthly":   0.0015,    # 月化无风险利率（FIX F1）
    "tracking_error_min":  0.01,      # 跟踪误差最小阈值
    "downside_std_min":    0.01,      # 下行标准差最小阈值（防除零）
}

PORTFOLIO_OPTIMIZER_DEFAULTS = {
    "risk_free":           0.02,      # 组合优化无风险利率
    "max_weight":          0.20,      # 单资产最大权重
    "cvar_alpha":          0.05,      # CVaR 尾部概率 5%
}

# ---- Pipeline 门控 / EV ----
PIPELINE_GATE = {
    "confidence_threshold": 0.7,      # 置信度门槛（>0.7 直出，否则 LLM 仲裁）
    "divergence_threshold": 0.3,      # 分歧度门槛（<0.3 直出）
    "winrate_min":          0.3,      # 胜率映射下限
    "winrate_max":          0.9,      # 胜率映射上限
    "trading_cost":         0.0023,   # 交易成本 0.23%（佣金+印花+滑点）
    "expected_gain_factor": 0.8,      # 预期盈利 = 波动率 × 该系数
    "expected_loss_factor": 0.5,      # 预期亏损 = 波动率 × 该系数（ATR 止损）
}

# ---- 蒙特卡洛模拟 ----
MONTE_CARLO_DEFAULTS = {
    "stop_loss":       -0.08,         # 止损 -8%
    "take_profit":      0.20,         # 止盈 +20%
    "profit_realize":   0.5,          # 止盈时兑现 50% 利润
}

# ---- 回撤 / 相关性阈值 ----
DRAWDOWN_ALERT = {
    "severe_pct":       20.0,         # 严重回撤 >20%
    "moderate_pct":     10.0,         # 中度回撤 >10%
}
CORRELATION_DEFAULTS = {
    "all_equity":       0.75,         # 全股票组合相关系数
    "stock_bond_gold":  0.35,         # 股债金组合相关系数
    "with_hedge":       0.45,         # 含避险资产
    "mixed":            0.50,         # 其他默认
}

# ---- 止盈止损（按风险类型）----
TAKE_PROFIT_STOP_LOSS = {
    "保守型": {"target_pct": 15, "stop_loss_pct":  -8, "partial_pct": 10},
    "稳健型": {"target_pct": 20, "stop_loss_pct": -10, "partial_pct": 15},
    "平衡型": {"target_pct": 30, "stop_loss_pct": -15, "partial_pct": 20},
    "进取型": {"target_pct": 50, "stop_loss_pct": -20, "partial_pct": 30},
    "激进型": {"target_pct": 80, "stop_loss_pct": -25, "partial_pct": 40},
}

# ---- 恐贪指数 3 维权重 ----
FGI_DIM_WEIGHTS = {
    "momentum":    0.4,   # 20 日动量
    "volatility":  0.3,   # 波动率
    "volume":      0.3,   # 量能偏离
}

# ---- 基金筛选时间权重 ----
FUND_SCORE_WEIGHTS = {
    "r1y":  0.30,   # 近 1 年占 30%
    "r3y":  0.20,   # 近 3 年年化占 20%
    "r6m":  0.15,   # 近 6 月占 15%
    "r3m":  0.10,   # 近 3 月占 10%
}

# ---- 配置动态调整步长（基于估值+恐贪）----
ALLOCATION_ADJUST = {
    "valuation_extreme_high": {"s": -0.10, "b":  0.05, "c":  0.05},  # 估值 >85
    "valuation_high":         {"s": -0.05, "b":  0.03, "c":  0.02},  # 估值 >70
    "valuation_extreme_low":  {"s":  0.10, "b": -0.05, "c": -0.05},  # 估值 <15
    "valuation_low":          {"s":  0.05, "b": -0.03, "c": -0.02},  # 估值 <30
    "fgi_extreme_greed":      {"s": -0.05, "c":  0.05},              # 恐贪 >80
    "fgi_extreme_fear":       {"s":  0.05, "c": -0.05},              # 恐贪 <20
    "cash_floor":             0.15,   # 塔勒布铁律：现金永远 >= 15%
    "stock_min":              0.05,   # 股票最低占比
    "stock_max":              0.90,   # 股票最高占比
    "bond_max":               0.80,   # 债券最高占比
}

# ---- RL 仓位分档阈值 ----
RL_POSITION_BUCKETS = {
    "empty":      0.05,   # 空仓
    "light":      0.30,   # 轻仓
    "half":       0.60,   # 半仓
    "heavy":      0.85,   # 重仓
    # >0.85 → 满仓
}

# ---- 现金管理默认参数（缺支出数据时的假设）----
CASH_MGMT_DEFAULTS = {
    "emergency_ratio":    0.3,    # 无支出数据时，应急金占现金 30%
    "bank_rate_current":  0.002,  # 银行活期 0.2%
    "inflation_rate":     0.01,   # 通胀假设 1%
}
