"""
钱袋子 — 全局配置
所有阈值、权重、缓存TTL集中管理，禁止在业务代码里写魔法数字
"""
import os
from pathlib import Path

# ---- 持久化目录 ----
BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BACKEND_DIR.parent / "data"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(exist_ok=True)
RECEIPTS_DIR = DATA_DIR / "receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)
# 推送存档目录（用于质量评估）
PUSH_ARCHIVE_DIR = DATA_DIR / "logs" / "pushes"
PUSH_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# ---- 启动期路径自检（v9.9.x P4）----
# 事故根因：API 由 systemd 注入 DATA_DIR=/opt/moneybag/data，cron 侧没有这个变量，
# 两侧把同名缓存写进互不可见的两棵目录树（预热白做）。启动期把解析结果打出来，
# 便于用一条命令比对两侧是否一致；环境变量缺失时显式告警，不再静默回落。
print(f"[CONFIG] DATA_DIR = {DATA_DIR} "
      f"(source={'env' if os.environ.get('DATA_DIR') else 'default'})", flush=True)
if not os.environ.get("DATA_DIR"):
    print(f"[CONFIG] ⚠️ 环境变量 DATA_DIR 未设置，已回落到默认值 {DATA_DIR}；"
          f"cron 侧请与 API 进程保持一致，否则缓存会写进另一棵目录树", flush=True)

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

# ---- 选股 7 维权重：按市场状态（regime）固化的权重表 ----
#
# 【来源标注 · 必读】
# 下面每个 regime 的 7 个权重都是**经验设定，未经过任何回测验证**。
# 2026-09 本项目刚清理掉三处硬编码假统计（34.6% 准确率 / +3.7% 超额 /
# 85% 盈利概率），这里的数字与它们是同一类东西的反面教材：我们明确写下
# 它**没有**样本区间、没有样本数、没有任何收益证据支撑，只是把
# 「牛市提权动量与舆情、熊市提权价值质量与风险」这类定性共识翻译成了
# 具体数值。要把它当"优化结果"对外讲之前，必须先补回测；在回测结论
# 落地前，任何引用都必须与「经验值，未回测」同时出现。
#
# 为什么从「LLM 每次现编权重」改成固化表（P1-7）：
#   现编 = 同一天跑两次可能得到两套权重 → 同一批股票排名就变了，
#   用户无法复现也无法信任；而且只喂 3 个市场指标就让模型输出 7 个精确
#   数值，本质是在让 LLM 编数字。现在 LLM 只做它擅长的离散分类（判断
#   regime），权重一律由本表查得 —— 同一 regime 永远得到同一份权重。
#
# 约束：每个 regime 的 7 个权重之和必须为 1.0
# （tests/test_stock_screen_weights_regime.py 有容差 1e-6 的断言守着）。
STOCK_FACTOR_WEIGHTS_BY_REGIME = {
    # 牛市：估值偏高+情绪贪婪 → 提权动量/成长/舆情，降权价值/风险
    "牛市": {
        "value": 0.12, "growth": 0.18, "quality": 0.15, "momentum": 0.24,
        "risk": 0.08, "liquidity": 0.09, "sentiment": 0.14,
    },
    # 熊市：估值偏低+情绪恐惧 → 提权价值/质量/风险，降权动量/舆情
    "熊市": {
        "value": 0.26, "growth": 0.10, "quality": 0.24, "momentum": 0.06,
        "risk": 0.20, "liquidity": 0.09, "sentiment": 0.05,
    },
    # 震荡：估值适中 → 均衡，与 STOCK_SCREEN_WEIGHTS 基线一致（即默认权重）
    "震荡": {
        "value": 0.20, "growth": 0.15, "quality": 0.18, "momentum": 0.15,
        "risk": 0.12, "liquidity": 0.10, "sentiment": 0.10,
    },
    # 轮动：资金在行业间流动 → 提权动量/流动性，降权长期基本面
    "轮动": {
        "value": 0.14, "growth": 0.16, "quality": 0.14, "momentum": 0.22,
        "risk": 0.10, "liquidity": 0.14, "sentiment": 0.10,
    },
}

# LLM 允许输出的 regime 枚举：只做离散分类，不含任何权重数字。
# 返回值不在这个枚举里 → 一律视为识别失败，回退默认权重表。
STOCK_FACTOR_REGIME_ENUM = ("牛市", "熊市", "震荡", "轮动")

# 权重来源标记（供下游判断这次权重是怎么来的，避免静默降级）：
#   llm_regime  — LLM 成功识别 regime，权重由固化表查得
#   rule_regime — LLM 不可用/返回非法，由估值+恐贪规则推断 regime
#   fallback    — 连规则推断都失败，直接用默认权重表
STOCK_FACTOR_WEIGHT_SOURCE_LLM = "llm_regime"
STOCK_FACTOR_WEIGHT_SOURCE_RULE = "rule_regime"
STOCK_FACTOR_WEIGHT_SOURCE_FALLBACK = "fallback"

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
RISK_SINGLE_FUND_MAX = 0.30      # 单只基金最大占比 30%（持仓≤5只时每只20%是合理的）
RISK_INDUSTRY_MAX = 0.20         # 单行业最大占比 20%
RISK_TAKE_PROFIT = 0.40          # 止盈阈值 → 收益≥40%减半
RISK_MAX_DRAWDOWN_LIMIT = -0.20  # 最大允许回撤 -20%（绝不突破）
RISK_REBALANCE_THRESHOLD = 0.08  # 再平衡触发偏离度 ±8%

# ---- 基金性价比（风险调整收益）指标 ----
# v9.9.x: 5 项风险调整收益指标（Sharpe/Sortino/Calmar/IR/Treynor）统一口径：
#   近 3 年窗口、日频、年化因子 252、无风险利率 2%（可配置）
RISK_FREE_RATE_ANNUAL = 0.02          # 年化无风险利率（近 3 年性价比口径）
RISK_ADJUSTED_WINDOW_DAYS = 1095      # 近 3 年窗口（自然日，约 3 年交易日）
ANNUALIZATION_FACTOR = 252            # 日频年化因子（一年交易日数）
RISK_ADJUSTED_BENCHMARK = "000300.SH"  # 股票/混合型基准：沪深300
RISK_ADJUSTED_MAR_ANNUAL = 0.0        # Sortino 最低可接受收益 MAR（年化）。
                                      # 决策：默认 0，与 empyrical.sortino_ratio(returns, required_return=0) 对齐；
                                      # 如改 0.02，需同步改 pages/_components.js 568-572 行 Sortino 文案的 MAR=0 说明。
RISK_ADJUSTED_CACHE_TTL = 86400       # 共享性价比缓存 TTL（秒），24h（选基列表注入 + 详情回填共用）

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
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

# ---- Tushare Token ----
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# ---- 版本号（Phase 1 更新）----
# v9.9.20：本轮改了 pages/insight.js（汇率行补时点标注），属于前端改动，
# 所以前端 cache-busting 必须跟着 bump —— index.html 的 ?v= 与 sw.js 的
# CACHE_NAME 要与这里保持一致，否则用户浏览器会继续吃旧副本
# （2026-09-11 那次 ?v 漏改就是这个坑）。
#
# v9.9.21：本轮是**纯后端**改动 —— 豆包余额误报修复（错误分类收窄为
# 欠费/额度/未开通/限流/鉴权五类 + 告警优先级门禁 + LLM 余额监控与统一门禁
# 对齐）。没有任何前端资源文件被修改，按上面这条规则，index.html 的 ?v= 与
# sw.js 的 CACHE_NAME 保持 9.9.20 不动 —— 改了只会让所有用户白重下一遍资源，
# 且 Service Worker 缓存被整体作废。下一轮只要动了前端，三处必须一起 bump。
APP_VERSION = "9.9.25"

# ---- v9.5.123: API 鉴权 ----
# 每个用户一个token，格式: userId:token（环境变量或data/auth_tokens.json）
# 简单HMAC方案：token = HMAC-SHA256(AUTH_SECRET, userId)
AUTH_SECRET = os.environ.get("AUTH_SECRET", "moneybag_family_cfo_2026")
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() == "true"

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

# 多 provider 定价（¥/百万token）
# DeepSeek 官方 V4 价格分 flash/pro 两档，input 按缓存命中/未命中 + 峰谷时段分别计价
# doubao（火山引擎 Seed 2.1，2026-06 官方价，元/百万 token）：
#   Pro 输入 6 / 输出 30 / 缓存命中 1.2；Turbo 输入 3 / 输出 15 / 缓存命中 0.6
#   豆包无峰谷时段差价，故 input_cache_hit_peak == input_cache_hit_valley，output_peak == output_valley
PROVIDER_PRICING = {
    "deepseek-flash": {
        "input_cache_hit_valley": 0.05, "input_cache_hit_peak": 0.10,
        "input_cache_miss_valley": 1.5, "input_cache_miss_peak": 3.0,
        "output_peak": 9.0, "output_valley": 4.5,
    },
    "deepseek-pro": {
        "input_cache_hit_valley": 0.15, "input_cache_hit_peak": 0.30,
        "input_cache_miss_valley": 4.5, "input_cache_miss_peak": 9.0,
        "output_peak": 27.0, "output_valley": 13.5,
    },
    # 豆包按 pro/turbo 两档计价（保守：无法识别具体档位时归入 pro）
    "doubao-pro": {
        "input_cache_hit_valley": 1.2, "input_cache_hit_peak": 1.2,
        "input_cache_miss_valley": 6.0, "input_cache_miss_peak": 6.0,
        "output_peak": 30.0, "output_valley": 30.0,
    },
    "doubao-turbo": {
        "input_cache_hit_valley": 0.6, "input_cache_hit_peak": 0.6,
        "input_cache_miss_valley": 3.0, "input_cache_miss_peak": 3.0,
        "output_peak": 15.0, "output_valley": 15.0,
    },
}
# ⚠️ 兼容旧引用：这个名字有误导性，它**只**是 pro 档价表，不是"DeepSeek 的价表"。
# 2026-09-11 全面 Flash 化后，线上绝大多数调用跑的是 deepseek-flash，
# 用这里的价格估算成本/收益会**高估约 3 倍**。新代码请直接用
# PROVIDER_PRICING["deepseek-flash"]（或按实际模型选表），不要再用这个别名。
DEEPSEEK_PRICING = PROVIDER_PRICING["deepseek-pro"]

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

# ---- 推送建议可执行性闸门（v9.9.26 P1-8）----
# 事故背景：真实推送存档里出现过「用户总市值 ¥754，系统却给出『分批止盈三分之一』
# 的交易建议」。754 元止盈 1/3 ≈ 251 元，扣赎回费后所剩无几，且很可能已经贴近
# 该基金合同约定的最低赎回份额 / 最低持有份额下沿 —— 建议根本不可执行。
#
# ⚠️ 下面两个阈值都是【经验值，未校准】：没有用历史数据回测，也没有统计过
#    "建议被采纳率" 或 "执行后净收益分布"。给出的是**推导依据（可核）**，
#    不是最优解。要校准请以这两项为指标做离线回测后再调，别拍脑袋改。
#
# 推导依据（可核事实）：
#   1) 场外基金赎回费下限（证监会规定，基金合同普遍照抄）：
#        持有 <7 天      → 赎回费 ≥ 1.5%
#        持有 7-30 天    → 赎回费 ≥ 0.5%~1%（费率改革后多为 ≥1%）
#        持有 30 天-6 月 → 赎回费 ≥ 0.5%
#      来源示例：银华招利一年持有期混合(009977)开放赎回公告的 3.2 赎回费率表
#      （Y<7 日 1.50% / 7-30 日 0.75% / 30-180 日 0.50% / ≥180 日 0）。
#      注意：费率本身**不是**本闸门的决定性理由 —— 金额小时赎回费的绝对额也小
#      （251 元 × 0.5% ≈ 1.3 元），单看费率不足以否定建议。
#   2) 真正卡住的是【最低赎回份额 / 最低持有份额】这类硬约束：
#      各基金合同自行约定，无统一监管下限，常见 1 份 / 10 份 / 100 份。
#      可核实例：银华招利一年持有期混合(009977)「每笔赎回申请的最低份额为
#      10 份；赎回后单个交易账户保留份额余额不足 10 份的，余额部分必须一同赎回」。
#      含义：当持有份额贴近下限时，『分批减仓 1/3』可能直接触发
#      「余额不足最低持有份额 → 被强制全部赎回」，与建议意图完全相反。
#      金额越小，持有份额越可能贴近这个下限。
#   3) 【收益 / 打扰比】：场外基金 T 日申赎、T+1 确认、资金 T+1~T+3 到账。
#      千元以下仓位的加减仓对家庭总资产的绝对影响只有几十元量级，却要让用户
#      承担一次决策打扰 + 数日资金在途，性价比为负。
#
# 综合 2)+3) 取 1000 元作为「建议动作的最小可执行金额」量级。
MIN_AMOUNT_FOR_TRADE_ADVICE = 1000.0   # 元：标的金额低于此值，交易类建议降级为观察项

# 单只标的占组合比例低于此值（百分点）时，交易类建议同样降级为观察项。
# 依据同为经验值：5% 仓位按「减仓 1/3」操作，对组合净敞口的改变仅 1.67 个
# 百分点，再扣掉赎回费与资金在途成本，边际收益已被摩擦吃掉。
# ⚠️ 经验值，未校准。
MIN_POSITION_PCT_FOR_TRADE_ADVICE = 5.0  # %

# 推送建议来源可信度表（v9.9.26 P1-8 单一裁决出口用）。
# 数值越大越可信；冲突时高者胜，同分则不裁决（见 scripts/night_worker.py
# 的 arbitrate_advice）。分级理由：
#   risk_control  100 — 风控硬阈值（回撤/止损/单票超限），是**确定性规则**且
#                       触发时亏损已经发生，不作为的代价不对称（亏 50% 需涨 100%
#                       才回本），故置顶。
#   steward        70 — 推送管家由 pipeline 门控直出，同样是确定性规则链，
#                       不经过 LLM 自由生成。
#   rule_engine    60 — 估值分位 / 恐贪指数 / 技术面等规则引擎，确定性，但
#                       面向的是市场层面而非用户个仓。
#   llm_diagnosis  20 — LLM 生成的持仓诊断，有编造风险（v9.9.24 已抓到过
#                       「229.7% 涨幅」「487 亿流入」），可信度最低一档。
# ⚠️ 这套分级是**设计决策 + 经验值，未校准**：没有用历史数据验证过
#    "哪一档的胜率更高"。校准方法：用 services.judgment_tracker 的验证结果
#    按来源分组统计准确率，再反推排序。
ADVICE_SOURCE_PRIORITY = {
    "risk_control": 100,
    "steward": 70,
    "rule_engine": 60,
    "llm_diagnosis": 20,
}

# 各来源在推送里显示给人看的名字（裁决理由文案要用）。
ADVICE_SOURCE_LABELS = {
    "risk_control": "风控硬阈值",
    "steward": "推送管家",
    "rule_engine": "规则引擎",
    "llm_diagnosis": "AI诊断",
}

# 规范化后的建议方向 → 推送文案
ADVICE_ACTION_LABELS = {
    "add": "加仓",
    "reduce": "减仓",
    "hold": "持有观望",
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

# ---- P1-3: 推荐基金列表（单一数据源）----
RECOMMENDED_FUNDS = [
    {"name": "沪深300", "code": "110020", "fullName": "易方达沪深300ETF联接A", "color": "#3B82F6",
     "returns": {"good": 0.15, "mid": 0.08, "bad": -0.10}, "category": "stock", "assetType": "fund", "etfCode": "510300"},
    {"name": "标普500", "code": "050025", "fullName": "博时标普500ETF联接A", "color": "#10B981",
     "returns": {"good": 0.18, "mid": 0.10, "bad": -0.12}, "category": "stock", "assetType": "fund"},
    {"name": "债券", "code": "217022", "fullName": "招商产业债A", "color": "#F59E0B",
     "returns": {"good": 0.06, "mid": 0.04, "bad": 0.01}, "category": "bond", "assetType": "fund"},
    {"name": "黄金", "code": "000216", "fullName": "华安黄金ETF联接A", "color": "#F97316",
     "returns": {"good": 0.15, "mid": 0.08, "bad": -0.05}, "category": "other", "assetType": "fund", "etfCode": "518880"},
    {"name": "红利低波", "code": "008114", "fullName": "天弘红利低波100联接A", "color": "#EF4444",
     "returns": {"good": 0.12, "mid": 0.07, "bad": -0.05}, "category": "stock", "assetType": "fund", "etfCode": "515100"},
    {"name": "货币(应急)", "code": "余额宝", "fullName": "余额宝", "color": "#E5E7EB",
     "returns": {"good": 0.02, "mid": 0.018, "bad": 0.015}, "category": "cash", "assetType": "fund"},
]

# ---- P1-3: 风险资产配置比例（单一数据源）----
RISK_ALLOC_PCTS = {
    "保守型":  [10, 5, 50, 15, 10, 10],
    "稳健型":  [20, 10, 35, 15, 10, 10],
    "平衡型":  [30, 20, 20, 15, 10, 5],
    "进取型":  [35, 25, 10, 10, 15, 5],
    "激进型":  [40, 30, 5, 5, 15, 5],
}

# ---- 二期 AI 运维巡检日报（ops_analyst.py）----
OPS_DIR_NAME = "ops"                     # DATA_DIR 下子目录名
OPS_REPORT_USER_ID = os.environ.get("OPS_REPORT_USER_ID", "LeiJiang")  # 日报只推 LeiJiang
OPS_BASELINE_FILE = "baseline.json"      # 滚动基线
OPS_CRITICAL_STATE_FILE = "critical_state.json"  # 实时 critical 去重
OPS_WINDOW_7D = 7                        # 7 天异常检测窗口
OPS_WINDOW_30D = 30                      # 30 天趋势窗口（也是基线最多保留天数）
OPS_DISK_CRITICAL_GB = 5.0               # 磁盘 critical 阈值（与 ops_summary.DISK_WARN_GB 对齐）
OPS_DISK_WARN_GB = 10.0                  # 磁盘 warn 阈值
OPS_ERROR_CRITICAL_COUNT = 10            # 24h 错误日志 critical 条数
OPS_ERROR_WARN_COUNT = 3                 # 24h 错误日志 warn 条数
OPS_ROUTED_PROVIDERS = ("deepseek", "doubao")  # 主路由模型（欠费=critical）
OPS_LLM_MODEL_TIER = "llm_heavy"         # 分析用重档（2026-09-11 全面 Flash 化后解析为 DeepSeek V4 Flash，豆包 Seed 2.1 Turbo 兜底）
OPS_LLM_MAX_TOKENS = 3000

# ============================================================
# P1-9 置信度不足闸门（confidence gate）
# ============================================================
# 规则：置信度 < MIN_CONFIDENCE_FOR_DIRECTION 时，**禁止**把结论翻译成方向，
# 统一改成非方向性的「数据不足」；但**原始置信度数值必须原样输出**
# （用户可以自己判断），只是不替他把数字翻译成"看多/看空"。
#
# 这是本项目数据诚实准则的一部分（与 P1-1/P1-2 同源）：
# 「没数据 / 数据不足」必须可见，绝不能长得像「判断为中性」或「判断为看多」。
# 之前置信度 35（多空分歧）照样输出「↗️ 偏多」——把"模型自己承认没看懂"
# 伪装成了"判断出来了"，是比不输出更严重的假信号。
#
# ── 阈值来源：经验值，未校准（empirical, NOT backtested）──
# 选基/选股的 trend_confidence 由 8 维信号一致性枚举得出，取值是**离散的**
# {35, 45, 50, 55, 72, 85}，不是连续分布：
#   85 / 72 = 强共振（>=6 维 / >=5 维同向）
#   55      = 一般
#   50      = 触发了「动量强但估值高位」等冲突，被 min() 压到 50
#   45      = 触发了「涨势中资金流出」冲突
#   35      = 多空分歧（正负维度差 <=1）
# 取 50 作为分界的含义是：**只要有任何一条冲突信号把置信度压到 50 或以下
# （即模型自己已经承认没看懂），就不允许声称方向**。50 是这套离散取值里
# 唯一的"中间分界点"，取 55 会把 55（一般）也误伤，取 45 会放过 50。
#
# ⚠️ 该阈值未经历史回测校准，不是统计最优解。若要校准：应取
# trend_direction 与实际 20 日后的收益做命中率 ROC 扫描，选约登指数最大点，
# 然后同步修改本注释与 tests/test_confidence_gate.py 的边界断言（49/50/51）。
MIN_CONFIDENCE_FOR_DIRECTION = 50

# 置信度不足时替代方向性字段的统一取值。
# INSUFFICIENT_DATA_DIRECTION 用 "unknown" 而不是 "flat"：
# "flat"/"震荡" 是一个**判断结论**（判断为横盘），而 "unknown" 是**没有判断**。
# 这两者在本项目里必须严格区分，混用就又回到"没数据伪装成中性"的老坑。
INSUFFICIENT_DATA_DIRECTION = "unknown"
# 文案里刻意不带 ↗/↘/🟢/🔴 等方向性图标，也不含买/卖/多/空字样
INSUFFICIENT_DATA_LABEL = "❓ 数据不足"
