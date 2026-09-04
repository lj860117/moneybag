"""
fund_signal 配置 — 全部阈值常量集中于此，禁止在业务代码里写魔法数字。

设计依据：docs/design/signal-scout-fund-account.md §3.4 / §5 / §7
口径约定（铁律）：
  * 百分比字段一律 `_pct` 结尾，值为百分数本体（33.5 表示 33.5%，不是 0.335）。
  * 回撤触发基准 = 成本净值：dd_cost = unit_nav / cost_nav - 1。adj_nav 只用于
    定位 60 日高点【日期】，取到日期后再用 unit_nav 计价，禁止 adj_nav / cost_nav。
"""

# ---- P0-3 回撤档位状态机 ----
# 档位阶梯（百分数本体，负值）。档位索引：rung ∈ {-1, 0, 1, 2}，
#   -1 = 未破任何档；0 = 破 -20% 档；1 = 破 -30% 档；2 = 破 -40% 档。
# 暂不设 -50% 档（设计 §5.3.2：当前最深 -26.95%，该档完全休眠，留作扩展点）。
DRAWDOWN_RUNGS = [-20.0, -30.0, -40.0]

# 重新武装缓冲：当前档位阈值 + 5pct。即 dd 回升到「档位线 + 5pct」上方才降一档。
# 例：档 3（-40%）→ dd > -35% 时降为档 2。
DRAWDOWN_REARM_BUFFER_PCT = 5.0

# 「相对近 60 日高点」参考口径的净值回看窗口（净值日个数）。
# 取 61 是为了包含「今天 + 前 60 个净值日」的高点。
DRAWDOWN_ROLL_LOOKBACK_DAYS = 61

# ---- P0-1 组合穿透体检触发规则 ----
XRAY_INDUSTRY_MAX_PCT = 25.0   # R1：单一申万二级行业穿透暴露 > 25% 总净值
XRAY_STOCK_MAX_PCT = 3.0       # R2：单一个股穿透暴露 > 3% 总净值
XRAY_STOCK_FUND_COUNT_MIN = 3  # R3：任一个股被 ≥3 只持仓基金同时列入前十大

# ---- P0-2 基金经理变更 ----
MANAGER_COOLDOWN_DAYS = 30     # 只推 ann_date 在最近 30 天内的记录
MANAGER_PAIRING_GAP_DAYS = 7   # 离任 end_date 与接任 begin_date 相差 ≤7 天 → 配对

# ---- P1-1 定投前瞻（P0-4）----
DCA_TRIGGER_DAY = 24           # 每月 24 日触发（25 号扣款前一天）；非交易日向前顺延
DCA_SKIP_LAUNCH_GRACE_DAYS = 3  # 上线日距当月 24 日不足 3 天 → 跳过本月（设计 P0-4 冷启动）

# ---- 推送预算守门（H2 硬约束 ≤4 条/月）----
BUDGET_DAILY_MAX = 2           # 同日最多 2 条推送
BUDGET_MONTHLY_MAX = 4         # 同月最多 4 条推送
# 优先级（值越小越优先；超预算时按此倒序砍，即排在最后的先被砍）。
# 设计 §8 Q4：dca > manager > drawdown > xray。
BUDGET_PRIORITY = [
    "dca_preflight",
    "fund_manager_change",
    "fund_drawdown_rung",
    "fund_xray_concentration",
]

# ---- 推送开关（写死，不另发明机制）----
RELEVANCE_PUSH = 100           # 企微推送（_should_push 校验 ≥50 通过）
RELEVANCE_FRONTEND_ONLY = 40   # 只写 _save_matched 供前端读，不推送

# ---- QDII 判定（纯展示字段；覆盖率主判定靠运行时 portfolios[code].ok）----
QDII_NAME_KEYWORD = "QDII"

# ---- 申万二级反查缓存 ----
SW_CACHE_TTL_SECONDS = 7 * 86400       # 7 天（行业分类变动极慢）
SW_INDEX_MEMBER_PAGE_SIZE = 3000       # index_member_all 单页硬上限（设计 §8.1 实测）
SW_INDEX_MEMBER_OFFSETS = (0, 3000)    # 全量约 5902 行，两页取完

# ---- 穿透持仓版本缓存（按 end_date，不用时间 TTL）----
PORTFOLIO_CACHE_DIR_NAME = "fund_portfolio"

# ---- 状态 schema 版本 ----
STATE_SCHEMA_VERSION = 1
