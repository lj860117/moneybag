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
#
# v9.9.34：同样是**纯后端**改动 —— ① 主动巡检（llm_balance_monitor）推送失败
# 不再消费当日去重额度，与 llm_quota_alert 的 W2 同源修复对齐；② Tushare 净值
# 拉取加 offset 翻页（修复仅取到 43% 数据的静默截断）+ 指数基金分类改用
# invest_type 识别（fund_type 里根本没有"指数"字样）；③ 测试侧 conftest 加
# config 模块基线恢复守卫。前端未动，?v= 与 CACHE_NAME 继续保持不变。
#
# v9.9.35：仍是**纯后端** —— 修 qdii 分类恒为 0（与 index 同病：fund_type 里
# 根本没有 "QDII" 这个取值，filter_type(["QDII"]) 永远匹配不到）。
# QDII 没有可用的结构化字段（invest_type 全量 36 个取值实测均无 QDII），
# 唯一可靠信号是法定名称后缀 "(QDII)"（精度接近 100%，是资格标记不是描述词）。
# 实测三种口径：名称含 QDII 481 只（采用）/ fund_screen 的 _QDII_KW 1180 只
# （其中 699 只误判：港股通 ETF 561 + 恒生 208 等）/ 非 QDII 关键词 24 只全误判。
# 前端未动，?v= 与 CACHE_NAME 继续保持不变。
#
# v9.9.36：仍是**纯后端** —— 修晨报质检（scripts/daily_push_quality_check.py）
# 的两条误报规则，不动生成层：
#   ① 「估值数据未标注时间」：旧规则见到"估值"二字就要求时间戳，但正文命中的
#      全是「估值百分位」（市场估值水平），不是基金估算净值。扫服务器 106 份
#      存档共 61 处"估值/估算"，**0 处**是真估算净值 —— 旧规则 100% 误报，
#      从未抓到过一次真问题。改白名单正向匹配（估算净值/实时估值/盘中估值/
#      基金估值 + 3~4 位小数形态）。语料实测告警 48 → 0。
#   ② 「分段可能不合理」：旧阈值 10 处空行，实测 106 份分布 min 10 / 中位 10 /
#      均值 11.08 / p95 13，误伤 43/106 = 40.6%。阈值提到 20（正常带上界 13
#      以上留 7 的余量，真阳性 26 以下留 6 的余量）。语料实测告警 43 → 2。
# 保留的这 2 条是**真阳性**：2026-07-03 两份晨报把 LLM prompt 原文泄漏进正文
# （「好的，用户让我基于提供的市场数据写一段小结…」），26 空行 / 67 行 / 4.7KB，
# 是正常晨报的两倍多 —— 分段规则虽然误伤 40%，但它唯一抓到的恰恰是最严重的
# 那类事故，所以只能调阈值，不能删规则。
# 前端未动，?v= 与 CACHE_NAME 继续保持不变。
#
# v9.9.37：仍是**纯后端** —— QDII 判据统一到唯一真源（commit 2b051dd 的代码）。
#   ① 新建 services/fund_taxonomy.py 放共享判据，fund_rank_build.py（Tushare 榜单）
#      与 fund_screen.py（AKShare 选基）同时改为引用，消除两套互相矛盾的口径。
#   ② 判据从「名称含 QDII」改为**并集**：名称含 QDII OR 命中 14 个高精关键词
#      {纳斯达克,海外,亚太,新兴市场,日经,日本,越南,印度,德国,法国,欧洲,东南亚,道琼,标普}
#      （标普另带否定词 {港股通,中国A股,香港上市中国}）。
#      删 9 词：港股（440 命中真值精度≈0，全是港股通）/ 恒生（230 命中里 17 只是
#      「恒生前海」**基金公司名**）/ 国际（7 命中全是 MSCI中国A股国际通）/ 全球
#      （top-N 唯一误判源）/ 纳指·美股·英国·韩国·S&P（全市场 0 命中）。
#      真值改用雪球「基金类型」，不再用"名称含 QDII"当代理（代理会低估精度）。
#   ③ 实测（7945 只候选池）：score top30 与 1y top50 **双双 0 误判 0 漏判**；
#      对照旧 24 词 6/30、7/50 误判，纯名称版 0 误判但漏 3~4 只。
# 影响面：选基页「🌐 海外」tab 的**名单会变**（去掉港股通/恒生前海/A股等误判项）。
# 前端未动，?v= 与 CACHE_NAME 继续保持不变。
# ---- v9.9.38: QDII 口径再统一（第 4/6/8 套）+ 晨报 T+2 标注 + 两处静默失败修复 ----
# 1) 晨报「QDII 未标注延迟」是 2026-09-14 质检 3 条告警里**唯一为真**的一条。
#    生成层 scripts/night_worker.py 在持仓速览块追加**写死的**标注行 —— 不靠
#    LLM：`diag` 是 `_call_v3` 的自由文本，把"记得标注"写进 prompt 会说时不说，
#    告警正是这么间歇性复发的。告警文案本身也错：QDII 实际 **T+2** 披露，不是
#    T+1，一并修正（判据只放宽不收紧，历史存档标 "T+1" 的仍判合规）。
# 2) QDII 口径全仓库普查：实际有 10+ 处（早前以为 5 套）。本轮统一其中 3 处：
#    api/signals.py 的 _STYLE_MAP（QDII 桶提前到首位，否则「国泰纳斯达克100指数」
#    「华夏野村日经225ETF(QDII)」先被「指数」桶抢走 → 选基页漏挂 QDII badge）、
#    _check_qdii_purchase_status（旧词含"全球"/"港股"→ 拉境内基金来查限购，
#    挤占 checked>=8 的配额；又缺"越南/德国/法国/欧洲"等 → 真 QDII 漏查）、
#    STYLE_KW 的 "海外/QDII" 归因桶。全部改走 fund_taxonomy.is_qdii_fund。
#    **语义本就不同、刻意不统一**的：portfolio.py:_detect_qdii（仅文案）、
#    longterm_screen.py（排除词）、portfolio_doctor.py（资产类别映射）、
#    signals.py:1078 us_keywords（美股敞口 ≠ QDII；它含"港股"是另一个问题）。
# 3) 前端 K 线币种误判：pages/insight-fund.js 的正则含 "标普"/"港股" 而无否定词，
#    「华宝标普港股通低波红利A」这类**境内人民币**基金被判 USD。改为后端
#    fund_nav_history 下发 is_qdii（唯一真源判定，不进缓存），前端据此撤掉误判
#    出的切换条。
#    ⚠️ 措辞更正（2026-09-16 生产实测）：下面这个说法**在当前线上不可达**，别再
#    照抄 —— 「用户一点切换就把整条 K 线 ÷7.2，是数值错误」。实测依据：后端
#    is_qdii 判定准确（探针 501310→false、006555→true），撤除逻辑会执行；且
#    fetch 失败路径 `_klineRawData` 为 null、无数据可除。当前**可观察**后果是
#    非正常路径下切换条残留、点了没反应 —— 错误可操作性，属 UX 缺陷。仅当后端
#    漂移/未回传 is_qdii 时才退化为数值错误。v9.9.44 已改为「确认 is_qdii===true
#    才插入」的失效安全写法，从结构上免疫该类退化。
# 4) 修两处被静默 except 吞掉的失败：api/fund_detail.py::_enrich_holding 的
#    industry_tag 三重叠错（传 2 参 → TypeError；传 code 不当 name；拿 dict 当
#    字符串）导致该字段**恒为空**。except 改为打印日志 —— 裸吞异常是根因。
# 前端仅动 pages/insight-fund.js，故只 bump 它的 ?v= → 9.9.38 与 sw.js 的
# CACHE_NAME → moneybag-v9938-cache。
#
# ---- v9.9.39: 三处"看着像 QDII 其实不是"的实修 + 一处死码 ----
# 1) api/signals.py 再平衡"欠配方向"判据 _is_gap_match：「美股/QDII 欠配-22%」
#    的 us_keywords 混进了 "港股"。港股/港股通基金法律上**既非美股也非 QDII**
#    （走互联互通额度），全市场实测 "港股" 440 命中、真值精度≈0 → 一只港股通
#    基金会被打上「补仓方向（美股/QDII欠配-22%）」绿色徽章，诱导用户买港股去
#    补**美股桶**。删掉 "港股"；同时把判据提到模块级便于回归测试直调生产代码。
# 2) services/weekly_report.py 把展示桶 key "QDII" 改名 "海外/全球"（该桶装的是
#    海外/全球配置而非仅法律 QDII，同 dict 已有独立"港股"桶）→ 纯展示改名。
# 3) scripts/night_worker.py 组合温度计**死码**：晨报用 replace("📋 【{name} 持仓
#    诊断】") 插温度计，但块真实标题是「持仓速览」（2026-08-09 同一 commit 分叉）
#    → replace 恒不命中 → 温度计从未进过任何晨报。改为 _render_holdings_block
#    直接拼进块首，新增 _render_user_briefing 组装正文，从根上消除脆弱字符串匹配。
# 4) api/fund_detail.py industry_tag：v9.9.38 修好三重叠错但生产仍为 None ——
#    根因在其**之前**的第二道门（真实用户 portfolio.holdings 恒为 []，早退）。
#    industry_tag 只依赖基金名、与持仓无关 → 挪到共享结果，所有读缓存路径都拿到。
# 本轮仍**不**统一（语义不同）：portfolio.py:_detect_qdii（文案）、
# longterm_screen.py（排除词）、portfolio_doctor.py（类别映射）、
# fund_risk_adjusted.py:67（"沪深300基准不适用"比 QDII 宽）、
# industry_templates.py:456（展示兜底）、ds_enhance.py:168（纯文案）。
# 本轮**未动前端**，故 index.html/sw.js 的 ?v= 与 CACHE_NAME 保持 9.9.38 不变。
#
# ---- v9.9.40: 详情弹窗 121 行死区 + 两处缓存/取源真 bug（独立 QA 挖出）----
# 1) pages/_components.js 决策辅助面板**永假闸门**（`:457-577`）：
#    闸门 `if (d.holding_relation && d.advices)` 对任何人都不成立 ——
#    `advices` 只由 `GET /api/fund-holdings/detail/{code}` 产出，而弹窗只调
#    `/api/fund/detail/{code}`（两接口间无 merge，fund_detail 历史上从未产出该字段）；
#    `holding_relation` 那边另有下面第 2 条 bug。后果：**后端一直在算**的
#    「走势预估 8 维面板」「智能定投建议」「止盈止损纪律线设定」「持仓摘要」全部从不渲染。
#    同时修 `:801-806` 的第二处脆弱接线：原本用字面标题串
#    `body.innerHTML.includes('持仓决策辅助')` 决定「追加 or 覆盖」，标题行条件化后
#    会失效并把刚渲染的面板整个覆盖 → 改为 `_panelRendered` 标志位。
# 2) api/fund_detail.py `_enrich_detail_with_holding` **取错持仓源**：
#    读 `load_user(uid).portfolio.holdings`，而该字段对真实用户**恒为空**
#    （实测 LeiJiang/BuLuoGeLi 长度均 0；真实持仓在 `data/fund_holdings_{uid}.json`）。
#    全仓 15+ 处都用 `services.fund_monitor.load_fund_holdings(uid)`，只有此处用错源
#    → 对真实用户永远在 `if not holding: return detail` 早退。且字段名也对不上真实
#    schema（真实是 `costNav` / `addedAt`，代码读 `cost_nav` / `buyDate` / `amount`）。
#    盈亏基准 = 成本净值 costNav（全仓口径）。
# 3) api/fund_detail.py 缓存加**载荷形状版本门** `_DETAIL_PAYLOAD_VER`：
#    v9.9.39 把 industry_tag 加进共享结果后，682 个旧缓存（无该键）因
#    `allow_stale` 早退继续被返回，且 `cache_warmer._warm_fund_details` 只 GET
#    同一接口、**从不强制刷新** → 修复最长 72h 不可见（实测 industry_tag=None）。
#    改法：`_get_cached(require_pv=)` 按调用点 opt-in，fresh 与 stale 两条路径都校验，
#    信封 `pv` 不符即视为 miss（**不删文件**，让重算覆盖）；`_set_cached(pv=)` 写入。
#    以后改动共享载荷的顶层键形状，**必须把 `_DETAIL_PAYLOAD_VER` +1**。
# 前端本次有改动（pages/_components.js），故 index.html 全部 27 处 ?v= 与
# sw.js 的 CACHE_NAME 一并 bump 到 9.9.40。
#
# ---- v9.9.41: 决策辅助面板**数据源接错接口**（v9.9.40 只修了一半）----
# 真正的根因：面板要的 `advices` / `dca` / `action_direction` **只由**
# `GET /api/fund-holdings/detail/{code}`（backend/api/holdings.py：dca@:666、
# action_direction@:741/743/745/748、advices@:735）产出；而弹窗只调
# `GET /api/fund/detail/{code}`，实测该接口 40 个顶层键里**没有**这三个字段。
# 故 v9.9.40 放宽闸门后**只救出三块**：持仓摘要 / 走势预估 8 维 / 纪律线；
# 「💡 智能定投建议」面板与「建议列表」**仍永不渲染**，且 :483 的
# `${d.action_direction||'持有观察'}` 会**无中生有**一个后端从未给出的判断
# （后端给的是 None）。v9.9.40 的 commit message 写「救出走势预估/定投面板」有误：
# 走势预估确实救出了，定投面板没有。
# 修法（pages/_components.js）：
#  1) 新增 `_fetchFundDecisionPayload`，照抄 fund/detail 的 prefetch/inflight 双缓存，
#     URL 打 `/fund-holdings/detail/{code}`，默认 timeout 20000ms；失败 reject，由调用方兜住。
#  2) 在 showFundDetailModal 里**并行**取数（`Promise.all`）：决断面 + fund/detail
#     → 总延迟 = max(两者)。**必须并行**：fund/detail 冷态本就 60s+，串行会翻倍。
#  3) 新增 `_mergeDecisionPayload(base, dec)`：只取 advices/dca/action_direction，
#     以及 my_holding/holding_relation **仅在决断面非 null 时**才覆盖
#     （决断面未持仓基金的 my_holding 是 null，不能抹掉 fund/detail 已算好的值）；
#     其余字段保留 fund/detail（带 v9.9.38 的 industry_tag 修复与载荷版本门）。
#  4) 删掉 :483 伪造兜底 `${d.action_direction||'持有观察'}` —— 仅当后端真给出
#     action_direction 才输出徽章，为假整段不输出，不用任何字面串顶替。
# 前端本次有改动，故 index.html 全部 27 处 ?v= 与 sw.js 的 CACHE_NAME 一并 bump 到 9.9.41。
#
# ---- v9.9.42: 3 条假绿测试根治 + 补合并 3 个诊断标签字段（独立 QA 用 Node+vm 真渲染证伪）----
# 1) **测试自身的假绿**（本仓 test_scorecard_caliber_honesty.py 早有教训：只有行为级断言
#    才能挡住「逻辑被改坏但字符串还在」）。backend/tests/test_fund_detail_modal_panel_wiring.py
#    里 3 条只断言字面串/窗口，可被绕过：
#    - ① 只断言 '/fund-holdings/detail/' in src —— 该字面串在三处**注释**里也有，把真 URL
#         改回 /fund/detail/（= 数据源修复整体回退）pytest 仍全绿（QA 注入 I5）。
#    - ② 只断言 if(_panelRendered){ / _panelRendered = true; 字面存在 —— 把
#         `body.innerHTML += html` 削弱回 `=`（「面板刚渲染就被覆盖」缺陷复活）仍全绿（I7）。
#    - ③ 只查 Promise.all 后 400 字符窗口出现两个函数名 —— 在 Promise.all **之前**插一行
#         await 详情（伪并行/串行）不碰被断言字符串，仍全绿（I4b；Node 测出 605ms > 400ms）。
#    修法：新增去注释源码 _components_src_no_comments()（剥 // 与 /* */，保留字符串字面量，
#    不误伤 URL 里的 //）；① 改用去注释源码断言 URL；② 钉住 if 分支 `+=` 与 else 分支 `=`
#    的确切语句；③ 断言弹窗内两个取数都不被单独 await（仅查 showFundDetailModal 内，
#    _prefetchFundDetail:471 那处合法 await 不误伤）。QA 的 4 条注入现全部真红。
# 2) **真实漏合并**：pages/_components.js `_mergeDecisionPayload` 未合并 nav_pct_label /
#    nav_percentile / timing_label。这三个字段只有 GET /api/fund-holdings/detail/{code} 产出
#    （fund/detail 实测为 None），而决策面板 tags 确实消费 d.nav_pct_label / d.timing_label
#    （nav_percentile 决定标签配色）→「净值百分位」「择时」标签永不渲染（后端早算好、数据在手）。
#    已按同样的非 null 守卫补上三键，并加测试。
# 前端本次有改动，故 index.html 全部 27 处 ?v= 与 sw.js 的 CACHE_NAME 一并 bump 到 9.9.42。
#
# ---- v9.9.43: 把启发式源码断言升级为**行为级**断言 + 修去注释实现缺陷（QA 真渲染复验凿出）----
# 1) 2 条新的测试绕路（本仓 test_scorecard_caliber_honesty.py 的范式：node+vm 真跑）：
#    - I9：URL 断言 `assert '/fund-holdings/detail/' in src_nc` 只问「字面串出现过吗」——
#      把 decisionUrl 改回 /fund/detail/ 同时在**别处**加一句多余字面串 `/fund-holdings/detail/`
#      → pytest 仍全绿（只有 Node 真渲染红）。
#    - I8：并行断言只认 `await _fetch*` 形态——改成 `const _pre=_fetchFundDetailPayload(...);
#      await _pre;`（真串行）→ 正则抓不到 `await _pre`，pytest 仍全绿（Node 测出 603ms）。
#    修法：新增 node+vm 行为级测试（无 node 则 pytest.skip，不静默变绿）：
#      真调 _fetchFundDecisionPayload 抓 fetch 实参 URL（I9 的「别处加字面串」无效）；
#      真调 showFundDetailModal 注入 600/200ms 延迟、断言总耗时 <750ms（I8 的正解）；
#      记录 body.innerHTML 每次写入、断言第 2 次以第 1 次为前缀且更长；纯函数合并守卫四方向矩阵。
#    源码级断言保留但**精确化**：URL 锚定到 `decisionUrl = API_BASE + '/fund-holdings/detail/'`；
#    「弹窗内除 `await Promise.all([` 外无其它 await」并把作用域界定到 showFundDetailModal 函数体。
# 2) **_components_src_no_comments() 的真 bug**：不识别正则字面量——`/https?:\/\//` 里的双斜线
#    被当行注释，吃掉后续代码（方向=假红）。修法：状态机在「`/` 处于表达式起点（前一非空白字符
#    或关键字）」时按正则字面量扫描到收尾 `/`+flags。另修：块注释展开为等量换行、行注释保留换行，
#    使去注释前后行数一致（行号对齐）。自带对抗性自测（正则/字符串含 //、字符串含 /*、模板 `${}`
#    内注释、跨行块注释、真实文件行数对齐）。
# 注：本轮**未改前端 JS**（仅改测试基础设施 + 版本号）；按团队要求把版本号统一 bump 到 9.9.43。
APP_VERSION = "9.9.45"

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
