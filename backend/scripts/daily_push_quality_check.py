#!/usr/bin/env python3
"""
每日推送质量评估脚本
- 检查今日所有推送内容（存档在 /opt/moneybag/data/logs/pushes/）
- 评估：截断、幻觉、数据源、AI分析质量、推送格式
- 有问题发企微告警
"""
import os
import sys
import json
import re
import math
import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PUSH_ARCHIVE_DIR, DATA_DIR
from services.persistence import atomic_write_json  # 铁律：JSON 落盘禁止裸 open().write()
from services.wxwork_push import (
    send_markdown,
    byte_len,
    effective_channel,
    WECOM_MARKDOWN_LIMIT,
    MARKDOWN_CHUNK_BUDGET,
    LENGTH_ALERT_BYTES,
    PUSH_ENVELOPE_OVERHEAD_BYTES,
)


# ===========================================================================
# 质检判定常量（2026-09-14 误报修复）
# ===========================================================================

# ---------- 检查2：「基金估算净值」语境 ----------
# 只有命中这些写法，才要求正文标注估值时间。
#
# 为什么用**白名单（正向匹配）**而不是「黑名单剔除估值百分位之类」：
# 黑名单要跟着 AI 的措辞不断补漏，而 AI 研判是自由文本，措辞几乎不受控
# （实测 106 份存档里出现过 估值百分位 / 估值分位 / 估值偏高 / 估值偏贵 /
# 估值高位 / 估值合理 / 估值修复 / 估值比过去89%的时间都贵 ……）。黑名单
# 每漏一个就是一次新误报 —— 2026-09-14 那次告警正是这么来的。白名单反过来
# 只对「确实在报一个净值/涨跌数字」的写法敏感，对市场估值类描述天然免疫。
#
# 实证（服务器 data/logs/pushes/*_briefing_*.txt 106 份）：
#   「估值/估算」共出现 61 处，**全部**是市场估值语境，0 处基金估算净值
#   （估算净值 / 实时估值 / 盘中估值 这些词在语料里一次都没出现过）。
#   即：旧规则 100% 误报，从未真正抓到过一次真问题。
_FUND_EST_NAV_RE = re.compile(
    # 明确的「估算净值」类措辞
    r"(?:估算净值|净值估算|实时估值|盘中估值|场内估值|基金估值"
    r"|估算涨幅|估算涨跌|估算收益率)"
    # 或「估算/估值」后紧跟一个 4 位小数的净值数字（1.2345 / 0.9876）
    # —— 用 3~4 位小数是为了避开「估值83%以上」「估值分位88.5%」这类
    #    市场估值描述（它们是整数或 1 位小数，且中间还隔着"百分位/分位"）。
    r"|(?:估算|估值)\s*[0-9]+\.[0-9]{3,4}"
)

# ---------- 检查3：分段空行阈值 ----------
# 实测数据（2026-09-14 取自服务器 /opt/moneybag/data/logs/pushes/
# *_briefing_*.txt，共 106 份，覆盖 2026-06-29 ~ 2026-09-14）：
#
#   空行数分布： 10 → 63 份 | 11 → 5 | 12 → 30 | 13 → 6 | 26 → 2
#   正常带 10~13（104/106 份），min 10 / 中位数 10 / 均值 11.08 / p95 = 13
#   —— 换句话说「正常晨报的空行上界就是 13」。
#
#   ⚠️ 「空行数正常」和「字节数没超上限」是**两件独立的事**，不能互相论证。
#   生产实际走 text 通道（上限 2048，不是 markdown 的 4096）：这 104 份
#   里 body 最大 2258B（含信封 2310B），其中 **32 份本来就超过 2048**、
#   会被 send_markdown 无损分片。也就是说，空行数落在正常带的晨报完全可能
#   同时在触发长度类告警 —— 两者互不蕴含。
#
#   （2026-09-17 复核：分布 10→63 / 11→5 / 12→30 / 13→6 / 26→2 与 104 份
#   这两个数已重新实测、与原注释一致；但「最大 2172B、仍远低于 4096B 上限」
#   两处都不成立 —— 实测 max 是 2258B，且 4096 不是生产通道。已按 text
#   通道 2048 更正。）
#
#   仅有的 2 份 26 空行是 2026-07-03 的 LeiJiang / BuLuoGeLi 两份，
#   **是真阳性**：正文把 AI 的 prompt 原文泄漏了进去（开头即
#   "好的，用户让我基于提供的市场数据写一段小结…"），67 行 / 4.7KB，
#   是正常晨报的 2 倍多。这个必须继续报出来。
#
# 旧阈值 10 的问题：会误伤 43/106 = 40.6% 的正常晨报（11 空行即触发），
# 2026-09-14 那份 12 空行就炸了 —— 而 09-11 的 10 空行刚好躲过，所以
# 看起来像"偶发"，实则是阈值压在正常带的下沿上。
#
# 取 20 的依据：比实测正常带上界 13 高 7（留足后续加板块的余量），
# 又比真阳性 26 低 6（不会放过 prompt 泄漏）。取值区间 14~25 都成立，
# 20 取中间偏保守，兼顾"不误报"与"不放过"。
#
# 曾评估过改用「空行数 / 非空行数」密度比以适配长报告，实测分离度太差故弃用：
# 正常样本最高 0.444（2026-07-16），而真阳性只有 0.634（2026-07-03），
# 可用区间窄到 0.45~0.63，任何取值都离某一侧太近。
MAX_BLANK_LINE_RUNS = 20


def check_truncation(content: str) -> list:
    """
    检查推送内容是否截断
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    # 检查1：末尾是否不完整（以 "..." 结尾）
    if content.rstrip().endswith("..."):
        issues.append("⚠️ 内容可能截断：末尾有 '...'")
    
    # 检查2：括号是否匹配
    open_parens = content.count("（") + content.count("(")
    close_parens = content.count("）") + content.count(")")
    if open_parens != close_parens:
        issues.append(f"⚠️ 括号不匹配：开放 {open_parens}，闭合 {close_parens}")
    
    # 检查3：引号是否匹配
    quotes = content.count("\"") + content.count("'")
    if quotes % 2 != 0:
        issues.append("⚠️ 引号不匹配：奇数个引号")
    
    # 检查4：是否以不完整的中文字符结尾（如 "+0."）
    if re.search(r'[0-9]\.$', content.rstrip()):
        issues.append("⚠️ 内容可能截断：末尾有不完整数字（如 '+0.'）")
    
    return issues


# ===========================================================================
# 净值核对 / 内部一致性检查（v9.9.54：原「幻觉检查」空转根治）
# ===========================================================================
#
# 背景（2026-09-18 深挖）：`check_hallucination()` 从上线起**每天空转**。
# 调用处 `evaluate_push_quality` 里 `actual_data = {}` 恒空，于是
# `actual_data.get("funds", ...).get(code, {}).get("change_pct")` 恒为 None、
# `actual_pct is not None` 永不成立 —— 所有分支都进不去，只被如实记进
# `checks_skipped`。这个「看起来在监工、其实没在监工」的环节本次根治。
#
# 原「检查1（基金涨跌幅）」的语义本身也是错的：它用正则抓晨报「持仓明细」行
# 的百分比，而那一列是**持仓浮盈亏率** ((现净值-加权成本)/加权成本)，**不是
# 当日涨跌幅**。`actual_data["funds"][code]["change_pct"]` 的契约是当日涨跌幅
# （±3% 量级），拿 55.4% 去比 0.5% 必然每条都报错。所以本轮把「检查1」整体
# 换成**净值核对**。
#
# 净值口径实测（生产 get_fund_nav_history，累计净值口径，与晨报同源）：
#   铁律：晨报日期 D 的「现净值」= **严格早于 D 的最后一个交易日**的净值。
#   晨报 08:30 生成，那时能拿到的最新净值就是前一交易日的。
#   002163: 9-15=4.0314  9-16=4.1639  9-17=4.1558
#   ⇒ 9-16 晨报「现4.031」= 9-15 净值；9-17「现4.164」= 9-16 净值；…
#
# 原「检查2（板块涨跌幅）」的正则（匹配「某某板块 … 数字%」的写法）
# 实测在 5 天真实晨报里**全部 0 命中**：晨报里「板块」只出现在 AI 研判的自由
# 文本（"AI/芯片板块强势"、"…等板块有热点"）后面都没有「数字%」；真正带涨跌幅
# 的行业行是「🏭 【行业热点】(前日)」段的另一种格式（且是「前日」数据，本轮
# 无当日数据源可核对）。留着一个永远匹配不上的正则在代码里充当「检查」，会让
# 下一个人误以为它在工作 —— 本轮**直接删除**，替换为下面这套**纯内部一致性**
# 检查（不需要任何外部数据源，不会因数据源抖动而误报）。
#
# ⚠️ 本脚本被 cron 每天 22:00 跑（`--date today --user LeiJiang --alert`），
#    误报会真发企微告警打扰用户 → 容差宁可放宽，也不要造出天天误报的检查。

# 持仓明细行（净值正常）：
#   • 东方惠新灵活配置混合C(002163)  买入2.594 → 现4.156  ▲60.2%  ¥160.2
# 名称用惰性匹配 `[^\n]*?`（不跨行）+ 6 位代码锚点，兼容历史上出现过的
# 「浦银安盛全球智能科技(Q(006555)」这种**名称被括号吐到一半**的旧存档
# （名称里带未闭合 `(` 时，仍能正确捕获代码 006555）。
_POSITION_ROW_RE = re.compile(
    r"•\s*(?P<name>[^\n]*?)\((?P<code>\d{6})\)\s*"
    r"买入\s*(?P<buy>\d+(?:\.\d+)?)\s*→\s*"
    r"现\s*(?P<cur>\d+(?:\.\d+)?)\s+"
    r"(?P<arrow>[▲▼])\s*(?P<pct>\d+(?:\.\d+)?)\s*%\s*"
    r"¥\s*(?P<val>\d+(?:\.\d+)?)"
)

# 持仓明细行（净值缺失）：
#   • 华夏全球科技先锋混合(005698)  买入3.530 → 现净值缺失 ⚠️  ¥75.0（按成本计）
# 这类行**没有「现Y」可核对**，但它的 ¥V（按成本计）仍要计入块级市值合计 ——
# 漏掉它会让「当前市值 ≈ Σ¥V」的块级检查误报。
_POSITION_ROW_MISSING_RE = re.compile(
    r"•\s*(?P<name>[^\n]*?)\((?P<code>\d{6})\)\s*"
    r"买入\s*(?P<buy>\d+(?:\.\d+)?)\s*→\s*现净值缺失"
    r"[^\n]*?¥\s*(?P<val>\d+(?:\.\d+)?)"
)

# 组合温度计汇总行：  总投入 ¥709  当前市值 ¥743  整体浮盈 📈 +4.8%
_SUMMARY_RE = re.compile(
    r"总投入\s*¥\s*(?P<cost>\d+(?:\.\d+)?)\s+"
    r"当前市值\s*¥\s*(?P<val>\d+(?:\.\d+)?)\s+"
    r"整体浮盈\s*(?:📈|📉)?\s*(?P<pct>[+-]?\d+(?:\.\d+)?)\s*%"
)

# 容差常量（集中放这里，便于复核与故障注入）
NAV_TOLERANCE = 0.001          # 净值：晨报用 .3f 显示（四舍五入）
ROW_PCT_TOLERANCE = 0.15       # 行浮盈率：晨报用 .1f；加权成本另有 .3f 显示损失
SUMMARY_VALUE_TOLERANCE = 1.0  # 当前市值 ≈ Σ¥V 的**下限**（见下动态容差）
SUMMARY_PCT_TOLERANCE = 1.0    # 整体浮盈%（由 .0f 的投入/市值反推，round 损失大）

# 块级「当前市值 ≈ Σ¥V」**动态**容差（2026-09-18 QA 复核后改）：
#   误差上界 = 0.5（当前市值 .0f 的舍入）+ 0.05×行数（每行 ¥V 是 .1f 的舍入）。
#   8 行即 0.90 —— 用固定 1.0 只剩 10% 余量，行数 ≥11 必破 → 天天误报。
#   取 `0.5 + 0.05×行数 + 0.5(余量)` 与下限 SUMMARY_VALUE_TOLERANCE 的较大者。
SUMMARY_VALUE_TOLERANCE_MARGIN = 0.5

# 「严格早于 D 的最后 K 个交易日」窗口。
# 用途：识别**真实但时点不同**的净值 —— 典型是 QDII（T+2）：晨报 08:30 生成时
# 最新可见的是 D-2 的净值，而质检在 22:00 跑、能拿到 D-1 的，拿 D-1 去比必然
# 差一档（2026-09-18 QA 实测 9-16/9-17 两只 QDII 各差 0.008~0.068）。
# 窗口内命中 → 判「时点差」→ 记 skipped（非 issue）：既不误报，也不静默通过。
RECENT_NAV_WINDOW = 5

_DATE_ANY_RE = re.compile(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})")


def _norm_date(raw) -> str:
    """把各种净值日期写法归一到 YYYY-MM-DD（供字符串比较即日期比较）。

    兼容 akshare 可能返回的 "2026-09-17" / "2026-09-17 00:00:00" /
    "20260917" / "2026/09/17"。归一后字典序比较即日期序比较。
    """
    if raw is None:
        return ""
    m = _DATE_ANY_RE.search(str(raw).strip())
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _nav_strictly_before(history: list, push_date: str):
    """从净值序列里取**严格早于 push_date 的最后一个交易日**的净值。

    Args:
        history: ``get_fund_nav_history`` 的返回，元素形如
            ``{"date": "YYYY-MM-DD", "nav": float, "rate": None}``。
        push_date: 晨报日期（YYYY-MM-DD）。

    Returns:
        float 净值；取不到返回 None（**绝不编 0、绝不拿成本顶替**）。
    """
    if not history or not push_date:
        return None
    best_date = ""
    best_nav = None
    for row in history:
        d = _norm_date(row.get("date"))
        if not d or d >= push_date:      # 必须**严格早于**，等于/晚于都排除
            continue
        try:
            nav = float(row.get("nav"))
        except (TypeError, ValueError):
            continue
        if nav <= 0:                     # 0 / 负值一律视为无效
            continue
        if d > best_date:
            best_date = d
            best_nav = nav
    return best_nav


def _recent_navs(history: list, push_date: str, k: int = RECENT_NAV_WINDOW) -> list:
    """取**严格早于 push_date 的最后 k 个交易日**的有效净值（升序）。

    与 ``_nav_strictly_before`` 同一套清洗规则（坏日期 / 0 / 负值一律排除），
    只是返回一段窗口而非单个值，用于识别「真实但时点不同」的净值（QDII T+2）。
    """
    rows: list = []
    if not history or not push_date:
        return rows
    for row in history:
        d = _norm_date(row.get("date"))
        if not d or d >= push_date:
            continue
        try:
            nav = float(row.get("nav"))
        except (TypeError, ValueError):
            continue
        if nav <= 0:
            continue
        rows.append((d, nav))
    rows.sort(key=lambda x: x[0])       # 按日期升序
    return [nav for _d, nav in rows[-k:]]


def _unit_nav_history(code: str, days: int = 30) -> list:
    """取**单位净值**走势，返回 ``[{"date": "YYYY-MM-DD", "nav": float}]``。

    ⚠️ 为什么**不能**用 ``services.fund_monitor.get_fund_nav_history``
    ---------------------------------------------------------------
    那个函数走 ``indicator="累计净值走势"``（含分红再投资）。累计口径对
    「回撤 / 波动率 / 回测」是正确的，但晨报「持仓明细」渲染的是
    **单位净值**（``scripts/night_worker._fetch_unit_nav`` →
    ``services.market_data.get_fund_nav`` 的 ``official_nav``）。

    拿累计净值去核对单位净值，对**有分红历史**的基金会整行误报 —— 生产实测
    （2026-09-19，容差 0.001）：

        002163  累计 4.2824 vs 单位 3.0385  差 1.2439
        100038  累计 2.4820 vs 单位 1.8960  差 0.5860
        163406  累计 8.7192 vs 单位 2.2927  差 6.4265

    （无分红历史的基金两者相等，所以这个错**只**打有分红的，更隐蔽。）

    ⚠️ 不改 ``fund_monitor`` 而是本脚本自带取数的原因：``fund_monitor`` 是
    回撤/波动率/回测的共享取数，且其缓存键是 ``f"{code}_{days}"`` **不含口径**
    —— 给它加 indicator 参数会让两种口径互相串味。本函数保持 QC 自洽即可。

    Returns:
        list: ``[{"date", "nav"}]``，按日期升序。取不到 / 异常 → ``[]``
            （**绝不**回落成累计净值，那正是本次要修的错）。
    """
    try:
        from infra.data_source.market.stocks import get_fund_nav_history as _raw
        df = _raw(code=code, indicator="单位净值走势")
    except Exception as e:                # 数据源异常 → 当作取不到，不编数
        print(f"[QUALITY] 取 {code} 单位净值历史失败：{e}")
        return []

    if df is None or getattr(df, "empty", True):
        return []

    rows: list = []
    for _, r in df.tail(days).iterrows():
        try:
            nav = float(r.get("单位净值"))
        except (TypeError, ValueError):
            continue
        rows.append({"date": str(r.get("净值日期", "")), "nav": nav})
    return rows


def _build_actual_data(push_date: str, codes: list) -> dict:
    """构建「真实净值」字典供净值核对使用。

    Args:
        push_date: 晨报日期（YYYY-MM-DD）。
        codes: 需要核对的基金代码列表。

    Returns:
        dict: ``{code: {"expect": <严格早于 D 的最后交易日净值>,
                        "recent": [严格早于 D 的最后 K 个交易日净值]}}``。
            ``expect`` 是 A 股/境内口径下的正确参照；QDII 因 T+2 会与它差一档，
            但会命中 ``recent``（→ 记 skipped，不误报）。
            **取不到的 code 不会出现在字典里**（绝不编 0 或拿成本顶替），
            由调用方如实记入 skipped / 告警 —— 本项目铁律：不允许静默失效。
    """
    actual: dict = {}
    for code in codes:
        try:
            history = _unit_nav_history(code, days=30)
        except Exception as e:           # 数据源异常 → 当作取不到，不编数
            print(f"[QUALITY] 取 {code} 净值历史失败：{e}")
            history = None
        expect = _nav_strictly_before(history, push_date)
        recent = _recent_navs(history, push_date, RECENT_NAV_WINDOW)
        if expect is None and not recent:
            print(f"[QUALITY] {code} 无严格早于 {push_date} 的净值，跳过核对")
            continue
        actual[code] = {"expect": expect, "recent": recent}
    return actual


def _parse_position_rows(content: str) -> list:
    """解析晨报「持仓明细」行为结构化数据。

    Returns:
        list[dict]: 每行含 name/code/buy/cur/pct/value/navMissing。
            净值缺失行的 cur/pct 为 None，value 为「按成本计」的市值。
    """
    rows: list = []
    for m in _POSITION_ROW_RE.finditer(content):
        rows.append({
            "name": m.group("name").strip(),
            "code": m.group("code"),
            "buy": float(m.group("buy")),
            "cur": float(m.group("cur")),
            "pct": float(m.group("pct")),   # 显示的**绝对值**；方向看 arrow
            "arrow": m.group("arrow"),      # ▲ / ▼
            "value": float(m.group("val")),
            "navMissing": False,
        })
    for m in _POSITION_ROW_MISSING_RE.finditer(content):
        rows.append({
            "name": m.group("name").strip(),
            "code": m.group("code"),
            "buy": float(m.group("buy")),
            "cur": None,
            "pct": None,
            "arrow": "",
            "value": float(m.group("val")),
            "navMissing": True,
        })
    return rows


# ===========================================================================
# 硬断言：晨报渲染的持仓明细行数 == 该用户真实持仓只数（v9.9.59）
# ===========================================================================
#
# 事故（2026-09-16 ~ 09-18，连续三天无人发现）：
#   BuLuoGeLi 晨报「📊 组合温度计」的持仓明细里，占比约 75% 的最大持仓
#   163406 兴全合润混合A 被一段「幻觉删句」逻辑**整行删除**，而汇总行的
#   「当前市值 ¥1333」仍含它的市值 —— 用户看到的是「总市值 1333 / 明细
#   合计 328」的残缺组合。删句缺陷已在 v9.9.58 修掉（程序渲染的结构化行
#   改为「只标注不删」），但**没有任何一条检查能直接抓住"行数少了"**：
#   现有质检是逐行核对数值，行数少一行它只会少核一行，不会报"少了一行"。
#
# 本断言补的就是这一格。❗ 它不是「数值核对」的替代品，而是**另一类**缺陷
# （整行消失）的唯一守门人。
#
# ---------------------------------------------------------------------------
# 误报评估（先评估后落地，2026-09-19 生产只读实测）
# ---------------------------------------------------------------------------
# 1) 真实持仓只数：BuLuoGeLi 3 只（009708/100038/163406）、LeiJiang 8 只。
#    基准取 **V4 transactions**（data/users/{sha256(uid)[:16]}.json →
#    portfolio.transactions），与生成层 scripts/night_worker.py 的
#    `_build_portfolio_thermometer` **同一口径**（剩余份额 > 1e-6 才算活跃）。
#    ⚠️ 故意**不用** data/fund_holdings_{uid}.json：那是 v4_sync 的另一份
#    快照，与晨报生成路径不同源，拿它当基准等于引入第二套口径。
#
# 2) 生成层对**每一只**活跃持仓都渲染且只渲染一行：净值取不到时渲染的是
#    「现净值缺失 ⚠️」（见 `_POSITION_ROW_MISSING_RE`），**不会整行不渲染**。
#    ⇒ 不存在「净值取不到就不渲染」这类合理少渲染场景，无需为此豁免。
#
# 3) 真实会误报的场景只有两个，都已豁免并**如实记 skipped**：
#
#    ① **持仓明细段整体不存在** → skipped（position_count:no_block）。
#       实测 221 份生产归档：该段是 2026-09-16 才引入的，**09-15 及更早的
#       晨报 + 全部 closing_review 都没有这一段**。不豁免的话，历史归档会
#       一夜之间集体变红（那正是本断言最不能犯的错）。
#       ⚠️ 豁免的是「段不存在」，不是「段存在但 0 行」—— 后者是真异常，照报。
#
#    ② **持仓集合在晨报生成当天发生变动** → skipped（...:holdings_changed_
#       same_day）。晨报 01:00 生成、质检 22:00 跑，中间 21 小时里用户补录
#       一笔买入/清仓，基准就会与晨报对不上。宁可这一条漏报也不误报：
#       实测近 4 个月 **0 笔**新交易（全部 txn 落在 2026-05），风险极低但
#       机制上必须关掉这个口子。
#
#    ③ 基准算不出来（用户档案缺失 / 无 portfolio / 无 transactions）→
#       skipped。**绝不退化成 0 只、绝不静默通过**。
#
# 4) 生产真实归档 dry-run 结论（221 份 / 2 用户，只读）：
#       skipped（无持仓明细段）: 218 份
#       真阳性（163406 被删）  : 3 份（09-16 / 09-17 / 09-18 BuLuoGeLi）
#       误报                  : 0
#    ⇒ 真实数据下不会天天红。
#
# ---------------------------------------------------------------------------
# 故障注入契约
# ---------------------------------------------------------------------------
# 从晨报里删掉一行持仓明细 → 必须红；行数齐全 → 必须不红。恒绿的守卫等于
# 没守卫，见 backend/tests/test_push_quality_position_count.py。
#
# 「持仓明细：」段头。生成层 scripts/night_worker.py 写的是全角冒号，
# 这里半角一并认，避免改版时整条断言静默失效。
_POSITION_BLOCK_RE = re.compile(r"持仓明细\s*[：:]")

# 判「已清仓」的份额阈值，与 night_worker 的 1e-6 一致。
_ZERO_SHARE_EPS = 1e-6


def _user_portfolio_path(user_id: str):
    """用户 V4 档案路径 —— 与 scripts/night_worker.py 的取法保持一致。

    night_worker 用 `sha256(uid)[:16]` 做文件名（不是裸 userId），这里必须
    照抄：路径算错会静默落到「基准算不出来」→ 断言永远 skipped，等于没加。
    """
    import hashlib
    from pathlib import Path as _P

    users_dir = _P(os.environ.get("USERS_DIR") or str(_P(DATA_DIR) / "users"))
    safe = hashlib.sha256((user_id or "").encode()).hexdigest()[:16]
    return users_dir / f"{safe}.json"


def load_active_holding_codes(user_id: str, asof_date: str = "") -> tuple:
    """算「该用户真实持仓只数」，口径与生成层 night_worker 完全一致。

    Args:
        user_id: 用户 ID。
        asof_date: 晨报日期 YYYY-MM-DD。``""`` 表示不过滤（按当前全部交易算）。
            非空时只计入 ``date[:10] <= asof_date`` 的交易，并额外报出
            **当天（== asof_date）有过交易**的代码，供调用方豁免基准漂移。

    Returns:
        tuple: ``(codes, diag)``。
            * ``codes``: ``list[str]`` 活跃持仓代码（剩余份额 > 1e-6）；
              **算不出来时为 ``None``**（``diag["reason"]`` 说明原因）。
            * ``diag``: ``{"source": 路径, "reason": "", "same_day_codes": []}``。

    ⚠️ 算不出来必须返回 ``None`` 而不是 ``[]``：``[]`` 会被下游当成
    「该用户 0 只持仓」从而静默通过，那是本断言最危险的失效模式。
    """
    diag = {"source": "", "reason": "", "same_day_codes": []}
    path = _user_portfolio_path(user_id)
    diag["source"] = str(path)
    if not path.exists():
        diag["reason"] = "no_user_file"
        return None, diag

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                       # 档案损坏 → 不算，不猜
        print(f"[QUALITY] 读取用户档案失败 {path}：{e}")
        diag["reason"] = "unreadable_user_file"
        return None, diag

    portfolio = raw.get("portfolio") or {}
    txns = portfolio.get("transactions") or []
    if not txns:
        diag["reason"] = "no_transactions"
        return None, diag

    # 聚合口径照抄 night_worker（BUY 加份额 / SELL 减份额；SELL 缺份额时用
    # 到账金额 ÷ 确认净值 反推）。这里**故意复制**而不是 import night_worker：
    # 那个模块有重型依赖且正在被别的 worker 改动；口径若漂移，测试会红。
    agg: dict = {}
    same_day: set = set()
    for t in txns:
        typ = str(t.get("type") or "BUY").strip().upper()
        if typ not in ("BUY", "SELL"):
            continue
        code = t.get("code") or ""
        if not code:
            continue
        tday = str(t.get("date") or "")[:10]
        if asof_date:
            if not tday or tday > asof_date:     # 晨报生成之后的交易不算基准
                continue
            if tday == asof_date:
                same_day.add(code)
        shares = float(t.get("shares", 0) or 0)
        if typ == "SELL":
            amount = float(t.get("amount", 0) or 0)
            nav = float(t.get("nav", 0) or 0)
            if shares <= 0 and amount > 0 and nav > 0:
                shares = amount / nav
        bucket = agg.setdefault(code, 0.0)
        agg[code] = bucket + (shares if typ == "BUY" else -shares)

    diag["same_day_codes"] = sorted(same_day)
    return sorted(c for c, v in agg.items() if v > _ZERO_SHARE_EPS), diag


def check_position_count(content: str, expected_codes, diag=None,
                         user_id: str = "") -> tuple:
    """硬断言：晨报渲染的持仓明细行数 == 该用户真实持仓只数。

    Args:
        content: 晨报正文。
        expected_codes: 真实持仓代码列表；``None`` 表示基准算不出来。
        diag: ``load_active_holding_codes`` 的诊断信息（用于豁免当天的基准漂移）。
        user_id: 仅用于告警文案。

    Returns:
        tuple: ``(issues, skipped)``。豁免一律走 ``skipped``，**绝不静默通过**。
    """
    issues: list = []
    skipped: list = []
    diag = diag or {}

    # 豁免①：持仓明细段整体不存在（历史归档 / closing_review / 未来改版）。
    # 不豁免的话 09-15 及更早的 200+ 份归档会集体变红。
    if not _POSITION_BLOCK_RE.search(content):
        skipped.append("position_count:no_block")
        return issues, skipped

    # 豁免③：基准算不出来 → 如实记 skipped，绝不当成「0 只」放过。
    if expected_codes is None:
        skipped.append(f"position_count:{diag.get('reason') or 'no_source'}")
        return issues, skipped

    rows = _parse_position_rows(content)
    rendered = [r["code"] for r in rows]

    dupes = sorted({c for c in rendered if rendered.count(c) > 1})
    if dupes:
        issues.append(
            f"❌ 持仓明细重复渲染：{'/'.join(dupes)} 在晨报里出现多行"
        )

    if not expected_codes:
        # 用户 0 只持仓却渲染出持仓明细段 —— 不该发生，但基准为空时无从判缺失
        skipped.append("position_count:no_active_holdings")
        return issues, skipped

    if not rendered:
        # 段头在、一行都没有：要么全被删光（正是本次事故的最坏形态），
        # 要么渲染格式变了。两者都该被看见，不许因为「解析不出」就放行。
        issues.append(
            f"❌ 持仓明细段存在但 0 行：晨报渲染 0 行，"
            f"{user_id} 真实持仓 {len(expected_codes)} 只"
            f"（{'/'.join(expected_codes)}）"
        )
        return issues, skipped

    missing = sorted(set(expected_codes) - set(rendered))
    extra = sorted(set(rendered) - set(expected_codes))
    if not missing and not extra:
        return issues, skipped          # 行数齐全 → 不报

    # 豁免②：差异代码在晨报当天有交易 → 基准可能漂移，宁漏报不误报。
    same_day = set(diag.get("same_day_codes") or [])
    drift = sorted((set(missing) | set(extra)) & same_day)
    if drift:
        skipped.append(
            f"position_count:holdings_changed_same_day:{','.join(drift)}"
        )
    hard_missing = sorted(set(missing) - same_day)
    hard_extra = sorted(set(extra) - same_day)
    if not hard_missing and not hard_extra:
        return issues, skipped

    issues.append(
        f"❌ 持仓明细行数与真实持仓只数不符：晨报渲染 {len(rendered)} 行，"
        f"{user_id} 真实持仓 {len(expected_codes)} 只"
        + (f"；缺失 {'/'.join(hard_missing)}" if hard_missing else "")
        + (f"；多出 {'/'.join(hard_extra)}" if hard_extra else "")
        + "（行数少 = 有持仓被整行删掉，数值核对看不见这类缺陷）"
    )
    return issues, skipped


# ===========================================================================
# 晨报净值质检 v3：侧车 + 独立第三方源交叉核对（v9.9.59）
# ===========================================================================
#
# 一、它要解决的根问题：**同源监督**
# ---------------------------------------------------------------------------
# v9.9.57 的净值核对用的是 ``infra.data_source.market.stocks.get_fund_nav_history``
# —— 和晨报生成层 ``night_worker._fetch_unit_nav`` → ``services.market_data
# .get_fund_nav`` **同属项目内部取数链路**。链路一旦整体拿错口径（例如某只基金
# 返回的是累计净值），两边**错得一模一样**，核对必然全绿。这就是「看起来在监工、
# 其实没在监工」的第二种形态。
#
# ⇒ v3 引入两件东西：
#   ① **侧车**：生成层把自己当时实际用到的每一只净值 + 净值日期 + 口径声明
#      落盘（``…_briefing_<uid>.navs.json``），质检不再猜"它当时用了什么"；
#   ② **独立第三方源**：天天基金 ``pingzhongdata``，与项目内部链路无任何
#      共享缓存/共享接口。拿它按侧车声明的 ``nav_date`` 钉日期去核。
#
# 二、v3 的四层（(0) 是 v9.9.57 的既有逻辑，抽出复用）
# ---------------------------------------------------------------------------
#   (0) 内部一致性（``_check_internal_consistency``）：口径无关，永远能跑。
#   (i) 渲染一致性：侧车 vs 正文（**同源**）—— 抓丢行/截断/四舍五入漂移，
#       抓不了"数值对错"。
#   (ii) 独立源口径核对：独立源 vs 侧车，按 ``nav_date`` 钉日期，
#        **不设"最近 K 个交易日"窗口** —— 窗口是万能免罪符，任何滞后一档都会
#        被判成"时点差 → skipped → 永远绿"（v2 实测：整体滞后一档 0 issue）。
#   (iii) 时效性：``max(nav_date)`` 不得早于"最近应已披露的交易日"，抓整条链路
#        悄悄滞后一档。纯 QDII 组合无法用境内日历断言 → 如实记 DEGRADED。
#
# 三、三态 → **本脚本的既有告警语义**（关键，别照抄旧 patch）
# ---------------------------------------------------------------------------
#   旧 patch 设计的是「DEGRADED 也发告警、退出码 0」。本脚本的
#   ``send_alert_if_needed`` 只认 ``status == FAIL``，而 cron 每天 22:00 带
#   ``--alert`` 跑，**告警会真的推到真人用户的企微**。所以 v3 落地时把三态映射到
#   既有语义上：
#
#       FAIL     核到了但不一致（真错）        → blocking issue → 告警 ✅
#       DEGRADED 没核到（无侧车/独立源不可达/时效不达标/口径声明不符）
#                                             → **checks_skipped** → 不告警
#       PASS     核到且一致                    → 静默
#
#   ⚠️ DEGRADED 走 skipped 而不是静默返回：`main()` 会把
#   ``checks_skipped`` 全部打印出来，运维看得见 —— 符合本项目「不允许静默失效」
#   的铁律，同时满足「22:00 不因取不到数打扰用户」。
#
# 四、误报评估（2026-09-19，生产只读 + 真实归档 dry-run）
# ---------------------------------------------------------------------------
#   • 独立源实测：**11/11 只全部可取**，`Data_netWorthTrend` 的 ``y`` 即单位净值，
#     精度与项目自有单位净值口径在 8/11 只上**逐位一致**（容差 0.001）。
#   • 时区坑已用真实锚点钉死：``x=1789488000000`` → naive 算成 2026-09-15、
#     **+8h 才是 2026-09-16**（见 ``test_push_quality_v3_sidecar.py``）。
#   • (i)/(iii) 与 DEGRADED 全部是非阻塞的，天然零误报。
#   • (ii) 会红 —— 但红的是**真 P0**，不是误报：生产当前对 **002163 / 100038 /
#     163406** 三只渲染的是**累计净值**（买入净值仍是单位净值 ⇒ 混口径）。详见
#     ``_check_independent_caliber`` 的 ``caliber_accumulated`` 分类注释。
#
# ⚠️ 铁律：独立源**只用单位净值**核对；取不到 → DEGRADED，**绝不回落**到
#    ``get_fund_nav_history``（累计口径）—— 拿另一套口径冒充核对比不查更糟。
#
CALIBER_UNIT_NAV = "unit_nav"      # 侧车必须声明的口径（**承重字段**）
SIDECAR_SCHEMA = 1

# (i) 渲染一致性容差（侧车精度 vs 正文显示精度）
RENDER_NAV_TOLERANCE = 0.0015      # 正文「现Y」为 .3f
RENDER_WTNAV_TOLERANCE = 0.0015    # 正文「买入X」为 .3f
RENDER_VALUE_TOLERANCE = 0.055     # 正文 ¥V 为 .1f（0.05 + 余量）
RENDER_PCT_TOLERANCE = 0.05        # 正文 ▲Z% 为 .1f

# (ii) 独立源容差
INDEPENDENT_NAV_TOLERANCE = 0.001
INDEPENDENT_TIMEOUT_S = 8.0        # 逐只超时；失败即 DEGRADED，不回落
INDEPENDENT_RETRIES = 2            # 实测该源偶发 ReadTimeout，重试一次显著提成功率

# 独立源：天天基金 pingzhongdata。**绝不用** get_fund_nav_history。
_EASTMONEY_PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_NETWORTH_TREND_RE = re.compile(r"Data_netWorthTrend\s*=\s*(\[.*?\])\s*;")
_ACWORTH_TREND_RE = re.compile(r"Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;")
_BJ_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _sidecar_path(push_file: str) -> Path:
    """正文存档 → **同目录同前缀**的 ``*.navs.json`` 侧车路径。

    ``2026-09-18_briefing_LeiJiang.txt`` → ``…_briefing_LeiJiang.navs.json``，
    与正文一一对应，不会串日期 / 串用户。
    """
    p = Path(push_file)
    return Path(str(p.with_suffix("")) + ".navs.json")


def _load_sidecar(push_file: str) -> tuple:
    """读侧车并校验 schema / caliber。

    Returns:
        ``(sidecar, error)``。成功则 ``error == ""``；失败则 ``sidecar is None``
        且 ``error`` 是**原因分类**（进 ``checks_skipped`` + 供冷却键用）。
    """
    path = _sidecar_path(push_file)
    if not path.exists():
        return None, "sidecar_missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                      # 侧车损坏 → 如实记，不猜
        return None, f"sidecar_unreadable:{type(e).__name__}"
    if not isinstance(data, dict):
        return None, "sidecar_invalid:not_object"
    if data.get("schema") != SIDECAR_SCHEMA:
        return None, f"sidecar_schema:{data.get('schema')!r}"
    # caliber 是**承重字段**：不是 unit_nav 一律拒用（否则会拿累计口径去核单位）
    caliber = data.get("caliber")
    if caliber != CALIBER_UNIT_NAV:
        return None, f"caliber_mismatch:{caliber!r}"
    if not isinstance(data.get("rows"), list):
        return None, "sidecar_invalid:rows"
    return data, ""


def _parse_networth_trend(js_text: str) -> dict:
    """``Data_netWorthTrend`` → ``{净值日期: 单位净值}``。

    ⚠️ **时区坑**（已用真实锚点钉死）：``x`` 是**北京零点**的 epoch(ms)，
    naive ``utcfromtimestamp`` 会得到**前一天**。必须按 +8h 取日期。
    真实锚点：``x=1789488000000`` → 北京 **2026-09-16**（naive 会算成 09-15）。

    纯函数（不打网络），便于单测直接喂文本。
    """
    return _parse_trend(js_text, _NETWORTH_TREND_RE, dict_form=True)


def _parse_acworth_trend(js_text: str) -> dict:
    """``Data_ACWorthTrend`` → ``{净值日期: 累计净值}``。

    只用于**归因**：当侧车值对得上累计、对不上单位时，把 issue 分类成
    ``caliber_accumulated``（"疑似渲染了累计净值"），让告警可直接行动，
    而不是丢一句"数值不符"让人自己猜。
    """
    return _parse_trend(js_text, _ACWORTH_TREND_RE, dict_form=False)


def _parse_trend(js_text: str, rx, dict_form: bool) -> dict:
    """解析 pingzhongdata 的走势数组。``dict_form``：元素形如 ``{"x","y"}``；
    否则形如 ``[ts, val]``。坏元素一律跳过（绝不编 0）。"""
    m = rx.search(js_text or "")
    if not m:
        return {}
    try:
        arr = json.loads(m.group(1))
    except (TypeError, ValueError):
        return {}
    if not isinstance(arr, list):
        return {}
    out: dict = {}
    for e in arr:
        try:
            if dict_form:
                ts, val = float(e["x"]), float(e["y"])
            else:
                ts, val = float(e[0]), float(e[1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if val <= 0:
            continue
        out[datetime.datetime.fromtimestamp(ts / 1000.0, _BJ_TZ).strftime("%Y-%m-%d")] = val
    return out


def _fetch_independent_series(code: str, timeout: float = INDEPENDENT_TIMEOUT_S):
    """独立第三方源（天天基金 pingzhongdata）取**单位净值 + 累计净值**序列。

    Returns:
        ``{"unit": {date: nav}, "accum": {date: nav}}``；
        任何失败（超时 / 非 200 / 解析不出）→ ``None``。
        失败**不回落**到 ``get_fund_nav_history`` —— 由调用方判 DEGRADED。
    """
    url = _EASTMONEY_PINGZHONG_URL.format(code=code)
    text = ""
    for attempt in range(INDEPENDENT_RETRIES):
        try:
            import httpx
            resp = httpx.get(url, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200 and resp.text:
                text = resp.text
                break
            print(f"[QUALITY] 独立源 {code} HTTP {resp.status_code}（第 {attempt + 1} 次）")
        except Exception as e:                  # 网络抖动 → 重试，仍失败则 DEGRADED
            print(f"[QUALITY] 独立源 {code} 取数失败（第 {attempt + 1} 次）："
                  f"{type(e).__name__}: {e}")
    if not text:
        return None
    unit = _parse_networth_trend(text)
    if not unit:
        return None
    return {"unit": unit, "accum": _parse_acworth_trend(text)}


def _independent_nav_at(series, nav_date: str):
    """按**侧车声明的日期**取独立源单位净值；该日期不在序列里 → ``None``。

    ⚠️ **没有窗口**：日期对不上就是"没核到"（DEGRADED），不是"差不多算过"。
    """
    if not series or not nav_date:
        return None
    nd = _norm_date(nav_date)
    if not nd:
        return None
    unit = series.get("unit") if isinstance(series, dict) else series
    if not isinstance(unit, dict):
        return None
    val = unit.get(nd)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _last_trading_day_strictly_before(day: str) -> str:
    """最近一个**严格早于 day 的交易日**（A 股日历 = 工作日 − 法定假日）。

    Returns:
        ``YYYY-MM-DD``；日历不可用 / 15 天内找不到 → 空串（调用方判 DEGRADED，
        **绝不**当成"通过"）。
    """
    m = _DATE_ANY_RE.search(day or "")
    if not m:
        return ""
    try:
        cur = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""
    try:
        from services.signal_scout import is_trading_day
    except Exception as e:                      # 日历不可用 → 空串（判 DEGRADED）
        print(f"[QUALITY] ⚠️ 交易日历不可用：{type(e).__name__}: {e}")
        return ""
    cur -= datetime.timedelta(days=1)
    for _ in range(15):
        try:
            if is_trading_day(datetime.datetime(cur.year, cur.month, cur.day)):
                return cur.strftime("%Y-%m-%d")
        except Exception:                       # 日历抛错 → 空串，不猜
            return ""
        cur -= datetime.timedelta(days=1)
    return ""


def _default_qdii_detector(name: str) -> bool:
    """默认 QDII 判据：复用**单源** ``services.fund_taxonomy.is_qdii_fund``。"""
    try:
        from services.fund_taxonomy import is_qdii_fund
        return bool(is_qdii_fund({"name": name or ""}))
    except Exception as e:
        print(f"[QUALITY] ⚠️ QDII 判据不可用：{type(e).__name__}: {e}")
        return False


def _check_render_consistency(rows: list, sidecar: dict) -> tuple:
    """**(i) 渲染一致性**：侧车 vs 正文（同源）。

    抓的是"生成层算出来的数"与"真正发出去的字"是否一致（丢行 / 截断 / 四舍五入
    漂移）。**抓不了数值对错** —— 数值对错在 (ii)。

    Returns:
        ``(issues, unverified_codes)``。
    """
    issues: list = []
    unverified: list = []
    by_code = {str(r.get("code")): r for r in sidecar.get("rows", [])
               if isinstance(r, dict)}
    seen = set()
    for r in rows:
        code = r["code"]
        seen.add(code)
        sc = by_code.get(code)
        if sc is None:
            issues.append(
                f"⚠️ 渲染不一致：正文行 {r['name']}({code}) 在净值侧车里不存在"
                f"（生成层没记这一行？）"
            )
            continue
        label = f"{r['name']}({code})"
        if not sc.get("navMissing"):
            sc_nav = _to_float(sc.get("nav"))
            if r["cur"] is not None and sc_nav is not None and \
                    abs(r["cur"] - sc_nav) > RENDER_NAV_TOLERANCE:
                issues.append(
                    f"⚠️ 渲染不一致：{label} 正文现{r['cur']:.3f} vs 侧车 "
                    f"{sc_nav:.6f}（差 {abs(r['cur'] - sc_nav):.4f}）"
                )
            sc_pct = _to_float(sc.get("float_pct"))
            if r["pct"] is not None and sc_pct is not None:
                shown = r["pct"] if r["arrow"] == "▲" else -r["pct"]
                if abs(round(sc_pct, 1) - shown) > RENDER_PCT_TOLERANCE:
                    issues.append(
                        f"⚠️ 渲染不一致：{label} 正文显示 {shown:+.1f}% vs 侧车 "
                        f"{sc_pct:+.1f}%"
                    )
        sc_buy = _to_float(sc.get("wt_nav"))
        if sc_buy is not None and abs(r["buy"] - sc_buy) > RENDER_WTNAV_TOLERANCE:
            issues.append(
                f"⚠️ 渲染不一致：{label} 正文买入{r['buy']:.3f} vs 侧车 "
                f"{sc_buy:.6f}"
            )
        sc_val = _to_float(sc.get("cur_val"))
        if sc_val is not None and abs(r["value"] - sc_val) > RENDER_VALUE_TOLERANCE:
            issues.append(
                f"⚠️ 渲染不一致：{label} 正文 ¥{r['value']:.1f} vs 侧车 "
                f"¥{sc_val:.3f}"
            )
    for code in by_code:
        if code not in seen:
            unverified.append(f"render_truncated:{code}")
    return issues, unverified


def _to_float(raw):
    """``float()`` 的安全包装：坏值/None → ``None``（绝不编 0）。"""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _check_independent_caliber(sidecar: dict, provider) -> tuple:
    """**(ii) 口径/数值正确性**：独立第三方源 vs 侧车，按 ``nav_date`` 钉日期。

    **没有窗口**：只在侧车声明的那个日期取独立源的值，日期对不上就判"没核到"
    （DEGRADED），而不是"差不多就算过"。这是对口径（单位净值）的承重断言。

    ⚠️ **归因分类**（让告警可直接行动）：不一致时先看侧车值是否对得上**累计
    净值**。对得上 → ``caliber_accumulated``："疑似渲染了累计净值"。
    2026-09-19 生产实测，这正是当前真实存在的 P0：

        002163  侧车(渲染) 4.156  单位 2.9119  累计 4.1558  ← 累计
        100038  侧车(渲染) 2.461  单位 1.8750  累计 2.4610  ← 累计
        163406  侧车(渲染) 8.519  单位 2.2401  累计 8.5191  ← 累计

    三只都是"买入净值用单位、现净值用累计"的**混口径**：市值与浮盈被虚增
    1.4~3.8 倍（163406：报 ¥1005，真实 ¥264）。这种错**同源核对抓不到**
    （两边同链路、错得一样），只有独立源能抓。

    Returns:
        ``(issues, unverified_codes, checked_codes)``。
    """
    issues: list = []
    unverified: list = []
    checked: list = []
    for row in sidecar.get("rows", []):
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        if not code:
            continue
        if row.get("navMissing"):
            unverified.append(f"nav_missing:{code}")
            continue
        nav_date = str(row.get("nav_date") or "")
        nav = _to_float(row.get("nav"))
        if nav is None:
            unverified.append(f"sidecar_bad_nav:{code}")
            continue
        if not nav_date:
            unverified.append(f"no_nav_date:{code}")
            continue
        series = provider(code)
        if not series:
            unverified.append(f"independent_unreachable:{code}")
            continue
        ind = _independent_nav_at(series, nav_date)
        if ind is None:
            unverified.append(f"independent_no_such_date:{code}@{nav_date}")
            continue
        if abs(ind - nav) <= INDEPENDENT_NAV_TOLERANCE:
            checked.append(code)
            continue
        # 不一致 → **先归因**：是不是把累计净值当单位净值渲染了？
        accum = series.get("accum") if isinstance(series, dict) else None
        ac = accum.get(_norm_date(nav_date)) if isinstance(accum, dict) else None
        if ac is not None and abs(float(ac) - nav) <= INDEPENDENT_NAV_TOLERANCE:
            issues.append(
                f"❌ 口径不符（独立源）：{row.get('name')}({code}) {nav_date} "
                f"渲染 {nav:.4f} = 该日**累计净值**，单位净值应为 {ind:.4f}"
                f"（买入净值用的是单位口径 ⇒ 混口径，市值/浮盈被虚增约 "
                f"{nav / ind:.2f}×）"
            )
        else:
            issues.append(
                f"❌ 净值不符（独立源）：{row.get('name')}({code}) 侧车 "
                f"{nav_date}={nav:.4f}，独立源同日期={ind:.4f}"
                f"（差 {abs(ind - nav):.4f}）"
            )
    return issues, unverified, checked


def _check_freshness(sidecar: dict, push_date: str, qdii_detector) -> tuple:
    """**(iii) 时效性**：``max(nav_date)`` 不得早于"最近应已披露的交易日"。

    抓的是"整条链路悄悄滞后一档"（旧窗口口径下这类会被判免罪）。纯 QDII 组合
    无法用境内日历断言 → DEGRADED（如实说"我核不了时效"）。

    Returns:
        ``(degraded_reasons, unverified_codes)``。
    """
    dated: list = []
    for r in sidecar.get("rows", []):
        if not isinstance(r, dict) or r.get("navMissing"):
            continue
        nd = _norm_date(r.get("nav_date"))
        if nd:
            dated.append((str(r.get("code") or ""),
                          str(r.get("name") or ""), nd))
    if not dated:
        return ["freshness_no_dated_rows"], []
    expected = _last_trading_day_strictly_before(push_date)
    if not expected:
        return ["freshness_calendar_unavailable"], []
    domestic = [t for t in dated if not qdii_detector(t[1])]
    if not domestic:
        # 纯 QDII：净值本来就 T+2 滞后，境内日历断言不适用 → 如实 DEGRADED
        return ["freshness_pure_qdii"], []
    max_domestic = max(t[2] for t in domestic)
    if max_domestic < expected:
        stale = "/".join(t[0] for t in domestic if t[2] < expected)
        return [f"freshness_stale:max={max_domestic}<expected={expected}:{stale}"], []
    return [], []


def _reason_class(verdict: dict) -> str:
    """给告警文案/冷却键用的**原因分类**（单值，取最严重的一类）。"""
    if verdict.get("issues"):
        return "nav_mismatch"
    deg = verdict.get("degraded") or []
    if deg:
        return str(deg[0]).split(":", 1)[0]
    return ""


def check_hallucination_v3(push_file: str, *, push_date: str = "",
                           sidecar=None, sidecar_error: str = "",
                           independent_provider=None,
                           qdii_detector=None) -> dict:
    """晨报净值质检 v3：四层（内部 / 渲染 / 独立口径 / 时效）+ 三态。

    **本函数只在被注入 provider 时才打网络** —— 独立源由
    ``independent_provider`` 注入，侧车由调用方读好传入。职责分离，便于无网络单测。

    Returns:
        dict: ``{issues, degraded, unverified, checked, total_rows,
        reason_class}``。issues → FAIL（blocking，会告警）；
        degraded/unverified → 由调用方记进 ``checks_skipped``（不告警）；
        两者皆空 → PASS。
    """
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    rows = _parse_position_rows(content)
    push_date = push_date or _extract_push_date(push_file, content)
    provider = independent_provider or _fetch_independent_series
    detector = qdii_detector or _default_qdii_detector

    verdict = {
        "issues": [], "degraded": [], "unverified": [],
        "checked": 0, "total_rows": len(rows), "reason_class": "",
    }
    if not rows:
        # 没有持仓明细行 → 无物可核（空仓用户 / closing_review）→ 不判 DEGRADED
        return verdict

    # (0) 口径无关的内部一致性（永远可跑，取不到净值也有底线自检）
    verdict["issues"].extend(_check_internal_consistency(rows, content))

    if sidecar is None:
        # 侧车缺失 / 口径声明不符 → DEGRADED（**绝不**静默通过）
        verdict["degraded"].append(sidecar_error or "sidecar_missing")
        verdict["unverified"].extend(
            f"row_unverified:{r['code']}" for r in rows if not r["navMissing"])
        verdict["reason_class"] = _reason_class(verdict)
        return verdict

    # (i) 渲染一致性（同源：侧车 vs 正文）
    r_issues, r_unver = _check_render_consistency(rows, sidecar)
    verdict["issues"].extend(r_issues)
    verdict["unverified"].extend(r_unver)

    # (ii) 口径/数值正确性（独立源 vs 侧车，按 nav_date 钉日期）
    c_issues, c_unver, c_checked = _check_independent_caliber(sidecar, provider)
    verdict["issues"].extend(c_issues)
    verdict["unverified"].extend(c_unver)
    verdict["checked"] = len(set(c_checked))

    # (iii) 时效性
    f_degraded, f_unver = _check_freshness(sidecar, push_date, detector)
    verdict["degraded"].extend(f_degraded)
    verdict["unverified"].extend(f_unver)

    verdict["unverified"] = sorted(set(verdict["unverified"]))
    verdict["reason_class"] = _reason_class(verdict)
    return verdict


def v3_verdict_to_report(verdict: dict) -> tuple:
    """把 v3 三态映射到本脚本既有语义：``(issues, skipped)``。

    - ``issues``   （FAIL）      → blocking，进 FAIL 判定 / 告警
    - ``degraded`` / ``unverified`` → **checks_skipped**（会被 ``main()`` 打印，
      但**不**参与 FAIL 判定，22:00 不打扰用户）
    """
    issues = list(verdict.get("issues") or [])
    skipped: list = []
    for d in verdict.get("degraded") or []:
        skipped.append(f"v3:{d}")
    for u in verdict.get("unverified") or []:
        skipped.append(f"v3:{u}")
    return issues, skipped


def _extract_push_date(push_file: str, content: str = "") -> str:
    """从存档文件名（优先）或正文头取晨报日期 YYYY-MM-DD。取不到返回空串。"""
    m = _DATE_ANY_RE.search(os.path.basename(push_file))
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_ANY_RE.search(content or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _nav_entry(entry) -> tuple:
    """把 ``actual_data`` 的一项规整成 ``(expect, recent)``。

    兼容两种形状：
      * 新形状 ``{"expect": float|None, "recent": [float, ...]}``
      * 旧形状 ``float``（早期版本直接给单个净值）
    取不到（entry 为 None）→ ``(None, [])``。
    """
    if isinstance(entry, dict):
        return entry.get("expect"), list(entry.get("recent") or [])
    if entry is None:
        return None, []
    return float(entry), []


def _summary_value_tolerance(n_rows: int) -> float:
    """块级「当前市值 ≈ Σ¥V」的**动态**容差。

    误差上界 = 0.5（当前市值 .0f 的舍入）+ 0.05×行数（每行 ¥V 是 .1f 的舍入），
    再加固定余量；与下限 ``SUMMARY_VALUE_TOLERANCE`` 取大者。
    固定 1.0 在 8 行时只剩 10% 余量、≥11 行必破 —— 那会天天误报。
    """
    upper = 0.5 + 0.05 * max(0, int(n_rows)) + SUMMARY_VALUE_TOLERANCE_MARGIN
    return max(SUMMARY_VALUE_TOLERANCE, upper)


def _check_internal_consistency(rows: list, content: str) -> list:
    """逐行 + 逐块**内部一致性**（口径无关：只查正文自己算得对不对）。

    v9.9.59：从 ``check_hallucination_classified`` 里**原样抽出**（extract
    method，判定与容差一字未改），供 v3 的 (0) 层复用 —— 同一段逻辑只留一份，
    避免以后改了一处忘另一处。

    - 逐行：``round((现Y-买入X)/买入X*100, 1)`` 应等于正文显示的 Z
      （▲/▼ 只表达方向、数字是绝对值，须先还原符号再比）
    - 逐块：``当前市值 ≈ Σ¥V``（动态容差）；``整体浮盈% ≈ (市值-投入)/投入*100``

    这一层**不需要任何外部数据源**，永远能跑，是"取不到净值也要有的底线自检"。
    """
    issues: list = []
    for r in rows:
        code = r["code"]
        label = f"{r['name']}({code})"

        # ⚠️ 晨报把涨跌**方向**放在 ▲/▼ 里、数字本身是**绝对值**
        #    （night_worker 渲染 `{arrow}{abs(float_pct):.1f}%`）。所以要先把
        #    ▼ 还原成负号再比，否则每一行都"差 2×|浮盈率|"、必然全量误报。
        if r["buy"] > 0 and r["cur"] is not None:
            calc_pct = round((r["cur"] - r["buy"]) / r["buy"] * 100, 1)
            shown_pct = r["pct"] if r["arrow"] == "▲" else -r["pct"]
            if abs(calc_pct - shown_pct) > ROW_PCT_TOLERANCE:
                issues.append(
                    f"⚠️ 内部不一致：{label} 买入{r['buy']:.3f} → 现{r['cur']:.3f} "
                    f"应显示 {calc_pct:+.1f}%，晨报显示 {shown_pct:+.1f}%（差 "
                    f"{abs(calc_pct - shown_pct):.1f}%）"
                )

    m = _SUMMARY_RE.search(content)
    if m:
        cost = float(m.group("cost"))
        total_val = float(m.group("val"))
        overall_pct = float(m.group("pct"))

        values = [r["value"] for r in rows if r["value"] is not None]
        if values:
            row_sum = sum(values)
            tol = _summary_value_tolerance(len(values))
            if abs(row_sum - total_val) > tol:
                issues.append(
                    f"⚠️ 内部不一致：持仓各行市值合计 ¥{row_sum:.1f} 与"
                    f"「当前市值 ¥{total_val:.0f}」不符"
                    f"（差 {abs(row_sum - total_val):.1f}）"
                )
        if cost > 0:
            calc_overall = (total_val - cost) / cost * 100
            if abs(calc_overall - overall_pct) > SUMMARY_PCT_TOLERANCE:
                issues.append(
                    f"⚠️ 内部不一致：整体浮盈应为 {calc_overall:+.1f}%，"
                    f"晨报显示 {overall_pct:+.1f}%"
                )
    return issues


def check_hallucination_classified(push_file: str, actual_data: dict) -> tuple:
    """核对晨报里的数字，返回 ``(issues, skipped)``。

    三块检查：

      1) **净值核对**：晨报「现Y」vs 真实净值（严格早于 D 的最后交易日），
         容差 ``NAV_TOLERANCE``=0.001。code 取不到净值 → 进 ``skipped``。
         ⚠️ 值若不等于「严格早于 D 的最后一条」但命中**最近 K 个交易日**
         （``RECENT_NAV_WINDOW``）→ 判「时点差」（典型 QDII T+2）→ 记 skipped
         （**不是** issue：口径不同源，拿 A 股口径硬套 QDII 会天天误报）。
      2) **逐行内部一致性**：``round((Y-X)/X*100, 1)`` 应等于显示的 Z，
         容差 ``ROW_PCT_TOLERANCE``=0.15（▲/▼ 还原符号后再比）。
      3) **逐块内部一致性**：当前市值 ≈ Σ各行 ¥V（动态容差，见
         ``_summary_value_tolerance``）；整体浮盈% ≈ ``(市值-投入)/投入*100``
         （容差 ``SUMMARY_PCT_TOLERANCE``=1.0，因投入/市值是 .0f）。

    2)/3) 已抽出为 ``_check_internal_consistency``（v9.9.59，判定未改），
    本函数只负责 1) 净值核对 + 组合返回。

    ⚠️ 本函数**不做任何外部取数** —— 真实净值由 ``actual_data`` 注入，取数在
    ``_build_actual_data`` 里，职责分离，便于测试。

    Returns:
        tuple: ``(issues, skipped)``。issues 是真问题；skipped 是「未能核对」的
            如实记录（进 ``results["checks_skipped"]``，绝不静默通过）。
    """
    issues: list = []
    skipped: list = []

    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()

    rows = _parse_position_rows(content)

    issues.extend(_check_internal_consistency(rows, content))

    for r in rows:
        code = r["code"]
        label = f"{r['name']}({code})"

        # --- 1) 净值核对 ---
        if r["navMissing"]:
            # 晨报自己已标注「现净值缺失」，无从核对 → 如实记 skipped
            skipped.append(f"hallucination_nav_missing:{code}")
            continue
        expect, recent = _nav_entry(actual_data.get(code))
        if expect is None:
            # 取不到「严格早于 D」的净值 → 如实记 skipped（不得当作通过）
            skipped.append(f"hallucination_nav_missing:{code}")
            continue
        if abs(r["cur"] - expect) <= NAV_TOLERANCE:
            continue  # 与 A 股/境内口径完全一致 → 核对通过
        if any(abs(r["cur"] - v) <= NAV_TOLERANCE for v in recent):
            # 值是**真实净值**，但不是「严格早于 D 的最后一条」——典型是 QDII
            # T+2（晨报 08:30 只能拿到 D-2 的）。这不是幻觉，也不是「通过」：
            # 如实记 skipped，既不误报（22:00 不打扰用户），也不静默失效。
            skipped.append(f"hallucination_nav_timegap:{code}")
            continue
        issues.append(
            f"⚠️ 净值不符：{label} 晨报 {r['cur']:.3f}，实际 "
            f"{expect:.4f}（差 {abs(r['cur'] - expect):.4f}）"
        )

    return issues, skipped


def check_hallucination(push_file: str, actual_data: dict) -> list:
    """核对晨报数字（向后兼容壳，只返回 issues）。

    真正的判定在 ``check_hallucination_classified``（它多返回一个 skipped
    列表）。保持本函数签名与返回类型不变，既有调用方 / 测试不受影响。

    Args:
        push_file: 推送存档文件路径。
        actual_data: ``{code: nav}``，由 ``_build_actual_data`` 构建。

    Returns:
        list: 检测到的问题列表。
    """
    issues, _skipped = check_hallucination_classified(push_file, actual_data)
    return issues


def check_data_source(push_file: str) -> list:
    """
    检查数据源是否准确
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：QDII基金是否标注净值披露延迟
    #
    # 2026-09-14 修正一（文案错误）：原来写的是 "T+1"，但 QDII 投的是境外
    # 市场 —— 境外收盘晚 + 时差 + 汇率折算，净值普遍 **T+2** 才披露。告警
    # 文案本身是错的，会把修的人往错误方向带（往正文里塞 "T+1" 反而把事实
    # 说错）。此处改为 T+2。
    #
    # 2026-09-14 修正二（判据漏认）：判据原来只认 "T+1" / "延迟"。生成层
    # 现成的一句文案是「QDII 净值**滞后** 2 天」（services/fund_signal/
    # render.py）——「滞后」两个字都挂不上，照抄过去质检照样报。所以判据
    # 追加认 "T+2"。
    #
    # ⚠️ 判据只**放宽**不收紧：历史存档里按旧文案标注过 "T+1" 的一律仍判
    # 合规。收紧会让上百份历史存档一夜之间集体变 FAIL，那是新的一轮误报。
    qdii_mentions = re.findall(r'([^\n\s]+)\(QDII\)', content)
    if qdii_mentions:
        if not any(k in content for k in ("T+2", "T+1", "延迟")):
            issues.append("⚠️ QDII 基金未标注 T+2 披露延迟")
    
    # 检查2：基金估算净值是否标注了时间戳
    # 2026-09-14 误报修复：旧规则是
    #     if "估算" in content or "估值" in content:
    # 把「基金估算净值」和「市场估值水平」两个概念混为一谈。晨报里唯一命中
    # 「估值」的是 AI 研判的「估值百分位67.5%适中」——那是**市场估值分位
    # 指标**（见 services/glossary.py 的「估值百分位」词条、
    # services/portfolio.py:357 的 `估值百分位: {val_pct}%`），
    # 描述的是"当前估值在历史里贵不贵"，根本不是一个需要标注时间戳的
    # 净值数字。实测 106 份存档里「估值/估算」61 处全是这一类，
    # 旧规则 100% 误报。改为只在 _FUND_EST_NAV_RE 命中时才要求时间戳。
    if _FUND_EST_NAV_RE.search(content):
        # 检查是否有时间戳
        if "估值时间" not in content and "数据时间" not in content:
            issues.append("⚠️ 估值数据未标注时间")
    
    return issues


def check_ai_quality(push_file: str) -> list:
    """
    检查 AI 分析质量
    
    Returns:
        list: 检测到的问题列表
    """
    issues = []
    
    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：是否过于模板化（每次都说"科技板块强势"）
    template_phrases = ["科技板块强势", "市场情绪较好", "建议关注"]
    phrase_count = sum(1 for phrase in template_phrases if phrase in content)
    if phrase_count >= 2:
        issues.append(f"⚠️ AI 分析可能模板化：检测到 {phrase_count} 处模板用语")
    
    # 检查2：是否给出具体建议
    if "建议" in content or "推荐" in content:
        # 检查建议是否具体（包含具体基金代码/名称）
        if not re.search(r'\d{6}|[^\n\s]+\([^\n\s]+\)', content):
            issues.append("⚠️ AI 建议不够具体（缺少具体基金/股票）")
    
    # 检查3：盈亏锚点是否准确
    if "浮盈" in content or "浮亏" in content:
        # 检查是否有具体数字
        if not re.search(r'[+-]?\d+\.\d+%', content):
            issues.append("⚠️ 盈亏锚点缺少具体数字")
    
    return issues


def check_push_format(push_file: str) -> list:
    """
    检查推送格式是否正确（**完整** issue 列表，含预期类）。

    保持原有签名与返回类型，既有调用方 / 测试不受影响。需要把预期类单独
    分出来时用 check_push_format_classified()。

    Returns:
        list: 检测到的问题列表
    """
    issues, _expected = check_push_format_classified(push_file)
    return issues


def check_push_format_classified(push_file: str) -> tuple:
    """
    检查推送格式，并把「预期类」issue 单独分出来。

    预期类 = **长度**引发的、send_markdown 会按字节**无损分片**处理掉、不需要
    任何人处理的那几条：

      ① 超通道上限    → 拆成多条
      ② 仅超分段预算  → 拆成多条
      ③ 过 3600 告警线 → 只是「体量偏大」的提前预警，不产生任何分片动作

    生产定局走 text 通道（上限 2048），晨报 3472 字节必然拆多条 —— 这是**预期
    结果**，不是故障。所以这三条仍然显示、仍然扣分，但**不参与 FAIL 判定**：
    天天为此告警会训练人忽略它（告警疲劳），等真出事反而看不见。

    ⚠️ 只有长度类能进预期类。QDII 未标注 T+2 / AI 模板化 / 分段空行 /
    基金名称为空 这些是真问题，**一个都不许塞进来** —— 那是用漏报换清净。
    （2026-09-15 才修过 QDII 未标注，它是真 bug，必须继续报 FAIL。）

    Returns:
        tuple: (issues, expected_issues)。expected_issues 是 issues 的子集。
    """
    issues = []
    expected = []

    with open(push_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 检查1：基金名称是否显示（不是空的 "🔴 ()"）
    if re.search(r'🔴\s*\(\s*\)', content):
        issues.append("❌ 基金名称显示为空（'🔴 ()'）")
    
    if re.search(r'🟡\s*\(\s*\)', content):
        issues.append("❌ 基金名称显示为空（'🟡 ()'）")
    
    # 检查2：消息是否太长
    # v9.9.20 (B2)：原来写的是 `len(content) > 2048`，两个错误叠在一起：
    #   ① 单位错 —— 用「字符数」比「字节上限」。中文 3 字节/字，实测晨报 2.38~2.43
    #      字节/字符，字符判断会把真实体积系统性低估约 2.4 倍；
    #   ② 漏算信封 —— 档案里只有 archive_push 存的 body，不含 send_daily_report_to
    #      拼上的 title + "\n\n" + "\n\n⏰ 时间戳"（实测 52 字节）。
    #   两者叠加的后果：2026-09-11 BuLuoGeLi 晨报 body=2035B「通过检查」，
    #   实际发送 2087B > 2048B 被截断，监控却每晚 22:00 稳定全绿 —— bug 藏了很久。
    body_bytes = byte_len(content)
    sent_bytes = body_bytes + PUSH_ENVELOPE_OVERHEAD_BYTES

    # 2026-09-17（漏报修复）：上限 / 分段预算必须取**实际生效**的通道。
    #
    # 生产默认走 text 通道（`_force_text()` 默认 True，上限 2048 / 分段预算
    # 1800），只有显式 `WXWORK_FORCE_MARKDOWN=1` 才切 markdown（4096 / 3900）。
    # 前两级判定原来写死的是 markdown 的 4096 / 3900，后果是 **text 通道下
    # sent_bytes 落在 2049~3600 时三个分支全不命中 → 一行都不报、完全静默**。
    #
    # 铁证：2026-09-17 真实晨报存档 3610B（含 28B 的 "=== ... ===" 头行）
    # + 信封 52B = 3662B，text 通道（上限 2048）必然按 1800 预算拆成 3 条，
    # 质检却只报「接近告警线 3600」（3600 < 3662 才勉强报出来），还写成
    # 「距通道上限 4096 还剩 X 字节」—— 那个 4096 根本不是生产用的通道。
    # **漏报 + 错误基准**比「报了警但措辞不准」严重得多，所以这里统一改成
    # effective_channel()。
    #
    # （存档里的字节数每次都在变，别把 3662 / 3 条当定值 —— 这里只是记录
    # 2026-09-17 那一份的实测值，用于说明「text 通道下必然拆多条」。）
    #
    # ⚠️ 注意判定顺序：`LENGTH_ALERT_BYTES = 3600` 这条与通道无关的「体量偏大」
    # 预警线语义保持不变，但 text 通道下 3600 > 2048，所以 2049~3600 会被
    # 上面的「超上限 / 会分段」分支先吃掉，3600 分支在 text 下实际不触发 ——
    # 这是**正确**的，不要为了让 3600 分支活着而扭曲判定顺序。
    channel, channel_limit, chunk_budget = effective_channel()

    # 「会拆成几条」是运维真正要的信息：只说「会分段」，他还得自己拿计算器除。
    # 预算用 effective_channel() 给的 chunk_budget，绝不写死 1800 —— text 是
    # 1800、markdown 是 3900，写死就是下一个「拿错通道当基准」。
    #
    # 2026-09-17：这段原来只挂在最下面那个 `> LENGTH_ALERT_BYTES` 分支里，
    # 而 text 通道下要进那一层需 sent_bytes > 3600，可 text 上限只有 2048 ——
    # 恒不成立，是**100% 死代码**（留着会让下一个人误以为「超上限会提示分片」
    # 是已实现的功能）。现在挪到真正会触发的两级上。
    parts = math.ceil(sent_bytes / chunk_budget)
    split_note = f"将按 {chunk_budget} 字节预算无损拆分为 ≥{parts} 条"

    if sent_bytes > channel_limit:
        # 2026-09-17：这一级**刻意是 ⚠️ 而不是 ❌**，别改回去。
        #
        # send_markdown 会按 chunk_budget 无损分片，每一片都 < channel_limit，
        # 所以「总量超通道上限」**只意味着会拆成多条，内容一个字节都不丢**。
        # 生产已定局走 text 通道（WXWORK_FORCE_MARKDOWN 未设，上限 2048），
        # 09-17 晨报 3472 字节必然拆 2 条 —— 这是**预期结果**，不需要任何人
        # 处理。天天报 ❌ 却无需处理 = 训练人忽略告警（告警疲劳），等真出事
        # （内容被硬截断）时反而看不见。❌ 留给真事故。
        #
        # 真被截断时 `_record_event` 会落 truncation 事件，那是另一条发现
        # 路径，**不靠这条告警兜底** —— 所以下面那句「必须排查」只是提示，
        # 不构成把级别留在 ❌ 的理由。
        #
        # ⚠️ 降级的是**级别**，不是**是否上报**：这一级仍然必须产 issue。
        # 它曾经整段静默过（2049~3600 三阈值全不命中），那是更严重的事故。
        msg = (
            f"⚠️ 消息超长：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> 企微 {channel} 通道上限 "
            f"{channel_limit} 字节 —— send_markdown 会按字节无损分段，"
            f"{split_note}（内容不丢，但用户会收到多条）；"
            f"若真被截断说明有调用方绕过了分段逻辑，必须排查"
        )
        issues.append(msg)
        expected.append(msg)  # 预期类①：无损分片，内容不丢
    elif sent_bytes > chunk_budget:
        msg = (
            f"⚠️ 消息会分段：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> 企微 {channel} 通道分段预算 "
            f"{chunk_budget} 字节，{split_note}（内容无损，但阅读体验受损）"
        )
        issues.append(msg)
        expected.append(msg)  # 预期类②：同样是分片，同样无需处理
    elif sent_bytes > LENGTH_ALERT_BYTES:
        # 预期类③：3600 是「体量偏大」的提前预警，本身不产生任何分片动作，
        # 也没有任何需要人去做的动作，与 ①② 同属长度类，一并放行。
        #
        # 注（不是忘了处理）：text 通道下这一级恒不触发 —— 3600 > 上限 2048，
        # sent_bytes > 3600 必然先被上面的「超上限」分支吃掉。它只在
        # markdown（4096/3900）下才可能命中（3601~3900）。
        # 上限必须取**实际生效**的通道：生产默认 text（2048），写死 markdown 的
        # 4096 会把「早就超上限必须分片」说成「还剩几百字节」，完全误导。
        if sent_bytes <= channel_limit:
            headroom = (f"距 {channel} 通道上限 {channel_limit} 仅剩 "
                        f"{channel_limit - sent_bytes} 字节")
        else:
            headroom = (f"已超 {channel} 通道上限 {channel_limit} 字节 "
                        f"{sent_bytes - channel_limit} 字节")
        msg = (
            f"⚠️ 消息接近告警线：{sent_bytes} 字节（body {body_bytes}B + 信封 "
            f"{PUSH_ENVELOPE_OVERHEAD_BYTES}B）> {LENGTH_ALERT_BYTES} 字节，"
            f"{headroom}"
        )
        issues.append(msg)
        expected.append(msg)  # 预期类③

    # 检查3：分段是否合理
    # 2026-09-14 误报修复：旧阈值写死 `> 10`，而实测 106 份
    # 晨报的正常带就是 10~13（p95 = 13），等于把阈值压在正常带下沿上 ——
    # 误伤率 43/106 = 40.6%。阈值与依据见 MAX_BLANK_LINE_RUNS 的注释。
    # ⚠️ 这条**不是**预期类：空行 26 处是真阳性（AI prompt 泄漏导致正文膨胀），
    # 必须继续参与 FAIL 判定。
    blank_runs = content.count("\n\n")
    if blank_runs > MAX_BLANK_LINE_RUNS:
        issues.append(f"⚠️ 分段可能不合理：{blank_runs} 处空行")

    return issues, expected


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_date_arg(date_arg) -> str:
    """
    把日期参数解析成 YYYY-MM-DD。

    v9.9.24 (P0-1)：cron 一直传的是字面量 `--date today`（见 setup_cron.sh / 
    docs/ops/crontab.production.txt），而旧代码 `date_str = args.date` 原样透传，
    glob 变成 `today_*_LeiJiang.txt` → 永远匹配不到任何存档 → 走进
    "空结果 = 100 分 = 通过" 分支，这个检查从上线起就没真正跑过一次。

    支持：None / "" / "today" / "yesterday" / "YYYY-MM-DD"。
    其它格式直接抛 ValueError（由 main 转成退出码 2，不静默降级）。
    """
    raw = (date_arg or "").strip().lower()
    today = datetime.date.today()
    if raw in ("", "today", "now"):
        return today.strftime("%Y-%m-%d")
    if raw == "yesterday":
        return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if _DATE_RE.match(raw):
        # 校验是真日期（拦住 2026-13-45 这种）
        datetime.datetime.strptime(raw, "%Y-%m-%d")
        return raw
    raise ValueError(
        f"无法解析的日期参数 {date_arg!r}：只支持 today / yesterday / YYYY-MM-DD"
    )


def evaluate_push_quality(date_str: str, user_id: str = "LeiJiang",
                          actual_data_provider=None,
                          holdings_provider=None,
                          independent_provider=None,
                          qdii_detector=None) -> dict:
    """
    评估指定日期的推送质量

    Args:
        date_str: 日期字符串（如 "2026-06-16"）
        user_id: 用户ID
        actual_data_provider: 可选，``provider(push_date, codes) -> {code: nav}``。
            默认 None 时用真实取数 ``_build_actual_data``（生产路径）。测试可注入
            假 provider 以保持**无网络**（本仓铁律：单测不得打真实数据源）。
        holdings_provider: 可选，``provider(user_id, asof_date) -> (codes, diag)``，
            供「持仓明细行数 == 真实持仓只数」断言取基准。默认 None 时用
            ``load_active_holding_codes``（读本地用户档案，无网络）。
        independent_provider: 可选，``provider(code) -> {unit,accum} | None``，
            供 v3 (ii) 独立第三方源核对。默认 None 时用真实取数
            ``_fetch_independent_series``（**有网络**）。测试必须注入假 provider
            以保持无网络（本仓铁律：单测不得打真实数据源）。
        qdii_detector: 可选，``detector(name) -> bool``，供 v3 (iii) 时效断言
            剔除 QDII。默认 None 时用 ``_default_qdii_detector``。

    Returns:
        dict: 评估结果
    """
    results = {
        "date": date_str,
        "user_id": user_id,
        "pushes": [],
        "total_issues": 0,
        "score": 100,
        "status": "PASS",
        "issues": [],
        "checks_skipped": [],
        # v9.9.47：预期类 issue 条数（长度类，会无损分片、无需处理）。
        # 它们照常进 total_issues、照常扣分，但**不参与 FAIL 判定**。
        "expected_issues": 0,
        "blocking_issues": 0,
    }
    
    # 查找今日的推送存档
    push_dir = Path(PUSH_ARCHIVE_DIR)
    push_files = sorted(push_dir.glob(f"{date_str}_*_{user_id}.txt"))
    
    if not push_files:
        # v9.9.24 (P0-1)：这里原来是 `results["error"] = ...; return`，
        # 而 score 仍保持初始值 100、total_issues 保持 0 → 调用方看到的是
        # "0 问题 / 100 分 / ✅ 通过"。**没有数据 ≠ 通过**，一次推送都没有的
        # 一天（推送挂了 / 存档路径不一致 / 日期没解析）必须判 FAIL。
        results["status"] = "FAIL"
        results["score"] = 0
        results["total_issues"] = 1
        results["archive_dir"] = str(push_dir)
        results["error"] = f"未找到 {date_str} 的推送存档"
        results["issues"].append(
            f"❌ 未找到 {date_str} 的推送存档（目录 {push_dir}，"
            f"匹配 {date_str}_*_{user_id}.txt）：无法验证推送质量，"
            f"可能是推送任务根本没执行 / 存档路径不一致 / 日期解析错误"
        )
        return results
    
    results["archive_dir"] = str(push_dir)

    # 「持仓明细行数 == 真实持仓只数」的基准（v9.9.59）。
    # 基准只算一次（同一天同一用户），取不到时 expected_codes 为 None →
    # 逐份归档如实记 skipped，绝不退化成「0 只 = 通过」。
    _hp = holdings_provider or load_active_holding_codes
    try:
        expected_codes, holdings_diag = _hp(user_id, date_str)
    except Exception as e:                        # 基准取数异常 → 如实记，不猜
        print(f"[QUALITY] 取 {user_id} 持仓基准失败：{e}")
        expected_codes, holdings_diag = None, {"reason": "holdings_provider_error"}

    # 评估每个推送
    for push_file in push_files:
        push_type = push_file.stem.split("_")[1]
        
        with open(push_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 运行所有检查
        issues = []
        expected: list = []
        issues.extend(check_truncation(content))

        # 净值核对（v9.9.54）：取「严格早于晨报日期 D 的最后一个交易日」的真实
        # 净值，核对晨报「持仓明细」里的「现净值」，并做逐行/逐块内部一致性检查。
        #
        # 原来这里是 `actual_data = {}` 恒空 → 幻觉检查每天空转，只被记进
        # checks_skipped。现在真正取数。**取不到的 code 如实进 skipped**
        # （本项目铁律：不允许静默失效，也不允许把「没核对」当「通过」）。
        push_date = _extract_push_date(str(push_file), content)
        position_rows = _parse_position_rows(content)
        codes = [r["code"] for r in position_rows if not r["navMissing"]]
        # v9.9.59：净值质检 v3（侧车 + 独立第三方源）。
        #
        # 有侧车 → v3 接管净值核对（(ii) 用独立源，不再用同源数据自证）；此时
        # v9.9.57 的**净值核对分支**会被跳过（照跑 (0) 内部一致性，并如实记
        # skipped `hallucination:superseded_by_v3`），避免同一只基金被两条检查
        # 重复报。
        # 无侧车（尚未部署生成层侧车 / closing_review）→ v9.9.57 的完整逻辑
        # **一字不改地照跑**，行为与加 v3 之前完全一致。
        sidecar, sidecar_error = _load_sidecar(str(push_file))
        if sidecar is not None:
            # 有侧车 → v3 接管净值核对
            verdict = check_hallucination_v3(
                str(push_file), push_date=push_date, sidecar=sidecar,
                sidecar_error=sidecar_error,
                independent_provider=independent_provider,
                qdii_detector=qdii_detector,
            )
            v3_issues, v3_skipped = v3_verdict_to_report(verdict)
            issues.extend(v3_issues)
            results["checks_skipped"].extend(v3_skipped)
            results["checks_skipped"].append("hallucination:superseded_by_v3")
            actual_data = {c: 0.0 for c in codes}   # 占位：v9.9.57 分支已让位
        else:
            # 无侧车 → v9.9.57 的完整逻辑一字不改地照跑
            provider = actual_data_provider or _build_actual_data
            actual_data = provider(push_date, codes) if codes else {}
            hall_issues, hall_skipped = check_hallucination_classified(
                str(push_file), actual_data
            )
            issues.extend(hall_issues)
            results["checks_skipped"].extend(hall_skipped)
        if codes and not actual_data:
            # 有需要核对的持仓、却一条净值都没取到 → 本检查整体不可用，如实记录
            results["checks_skipped"].append("hallucination")
        if push_type == "briefing" and not position_rows:
            # 晨报本该有「持仓明细」段、却一行都没解析出来 → 净值核对无从下手。
            # 不记就是把「没核对」当「通过」（违反上面那条铁律）；closing_review
            # 本来就没有持仓明细段，不记，否则天天 110 份噪音。
            results["checks_skipped"].append("hallucination:no_position_rows")

        # v9.9.59：持仓明细行数 == 真实持仓只数。
        # 这是「整行被删」这类缺陷的唯一守门人（数值核对看不见它）。
        # 豁免（无持仓明细段 / 当天有交易 / 基准取不到）全部进 skipped。
        pos_issues, pos_skipped = check_position_count(
            content, expected_codes, holdings_diag, user_id=user_id
        )
        issues.extend(pos_issues)
        results["checks_skipped"].extend(pos_skipped)

        issues.extend(check_data_source(str(push_file)))
        issues.extend(check_ai_quality(str(push_file)))

        # v9.9.47：只有 check_push_format 会产生预期类（长度 / 无损分片），
        # 用 classified 版本把条数单独记下来。
        fmt_issues, fmt_expected = check_push_format_classified(str(push_file))
        issues.extend(fmt_issues)
        expected.extend(fmt_expected)

        # 记录结果
        push_result = {
            "file": push_file.name,
            "type": push_type,
            "issues": issues,
            "expected_issues": expected,
            "issue_count": len(issues),
        }
        results["pushes"].append(push_result)
        results["total_issues"] += len(issues)
        results["expected_issues"] += len(expected)
        results["score"] -= len(issues) * 5  # 每个问题扣 5 分

    results["score"] = max(0, results["score"])
    results["checks_skipped"] = sorted(set(results["checks_skipped"]))

    # v9.9.47：FAIL 只看「非预期」issue。
    #
    # 长度类（超上限 / 超分段预算 / 过 3600 线）在 text 通道下是**必然结果**
    # —— send_markdown 会无损分片，内容一个字节都不丢，没有任何动作需要人做。
    # 把它们算进 FAIL 会让质检天天 FAIL，最后没人看。
    #
    # ⚠️ 反过来，绝不能写反成「长度超了就整体跳过检查」：QDII 未标注 /
    # AI 模板化 / 基金名称为空 / 空行超标 这些是真问题，必须照常判 FAIL。
    blocking = results["total_issues"] - results["expected_issues"]
    results["blocking_issues"] = blocking
    if blocking > 0:
        results["status"] = "FAIL"

    return results


def send_alert_if_needed(results: dict):
    """
    如果有问题，发企微告警
    """
    # v9.9.24 (P0-1)：原来只判 `total_issues == 0`，而"找不到存档"时
    # total_issues 恒为 0 → 走 ✅ 分支。改为认 status，且 fatal（无存档）
    # 也要告警 —— 「没检查到」本身就是最该被看见的告警。
    #
    # v9.9.47：status == PASS 就**不发告警**，即使 total_issues > 0 ——
    # 多出来的那些是预期类（长度超限 → 无损分片），发了就是告警疲劳。
    if results.get("status") == "PASS":
        expected_n = results.get("expected_issues", 0)
        if expected_n:
            print(
                f"✅ 推送质量检查通过（{expected_n} 条预期类提示："
                f"长度超限会由 send_markdown 无损分片，内容不丢，无需处理）"
            )
        else:
            print("✅ 所有推送质量检查通过")
        return

    # 生成告警消息
    alert_msg = f"📊 {results['date']} 推送质量评估\n\n"
    alert_msg += f"结论：{results.get('status', 'FAIL')}\n"
    alert_msg += f"总分：{results['score']}/100\n"
    alert_msg += (
        f"检测到 {results['total_issues']} 处问题"
        f"（其中 {results.get('expected_issues', 0)} 条为预期类："
        f"长度超限会无损分片，无需处理；"
        f"{results.get('blocking_issues', 0)} 条需要处理）：\n\n"
    )
    
    # 无存档 / 其它致命问题（不属于任何单个 push）
    for fatal in results.get("issues", []):
        alert_msg += f"{fatal}\n"
    if results.get("issues"):
        alert_msg += "\n"
    
    for push in results["pushes"]:
        if push["issue_count"] > 0:
            alert_msg += f"❌ {push['type']}（{push['file']}）\n"
            for issue in push["issues"]:
                alert_msg += f"  {issue}\n"
            alert_msg += "\n"
    
    alert_msg += "⚠️ 请及时修复\n"
    
    # 发送告警
    # v9.9.20 (B2)：修参数顺序写反的 bug —— 原来是 send_markdown("LeiJiang", alert_msg)，
    # 而签名是 send_markdown(content, user_id="")，等于把 "LeiJiang" 当正文、
    # 把整段告警文本当 userId 发出去。这个告警其实从来没正常工作过。
    try:
        # v9.9.24 (P0-1)：send_markdown 返回 {"ok": ...}，原来只看「有没有抛异常」，
        # 发送失败也会打 ✅ 告警已发送 —— 又一处"假成功"。
        ret = send_markdown(alert_msg, user_id="LeiJiang")
        if isinstance(ret, dict) and not ret.get("ok", True):
            print(f"❌ 告警发送失败：{ret.get('error') or ret}")
        else:
            print("✅ 告警已发送")
    except Exception as e:
        print(f"❌ 告警发送失败：{e}")


def main():
    """
    主函数
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="每日推送质量评估")
    parser.add_argument(
        "--date", type=str, default=None,
        help="评估日期：today / yesterday / YYYY-MM-DD（默认 today）",
    )
    parser.add_argument("--user", type=str, default="LeiJiang", help="用户ID")
    parser.add_argument("--alert", action="store_true", help="有问题发企微告警")
    parser.add_argument("--out", type=str, default=None, help="结果 JSON 落盘路径（原子写）")
    
    args = parser.parse_args()
    
    # 确定评估日期（v9.9.24 P0-1：cron 传的是字面量 "today"，必须 resolve）
    try:
        date_str = resolve_date_arg(args.date)
    except ValueError as e:
        parser.error(str(e))  # 退出码 2，不静默降级成"通过"
        return
    
    print(f"📊 开始评估 {date_str} 的推送质量...")
    
    # 评估推送质量
    results = evaluate_push_quality(date_str, args.user)
    
    # 打印结果
    print(json.dumps(results, ensure_ascii=False, indent=2))
    
    if results.get("checks_skipped"):
        print(
            f"⚠️ 以下检查被跳过（未取到真实数据，结果不完整）："
            f"{', '.join(results['checks_skipped'])}"
        )
    
    if args.out:
        atomic_write_json(Path(args.out), results)
        print(f"📝 结果已写入 {args.out}")
    
    # 有问题发告警
    if args.alert:
        send_alert_if_needed(results)
    
    # v9.9.24 (P0-1)：退出码必须能反映结论，否则 cron 永远看不到失败
    #
    # v9.9.47：退出码只认 status。原来这里还有 `or total_issues > 0`，
    # 而预期类也会让 total_issues > 0 —— 不去掉的话，长度超限（PASS）
    # 照样退出 1，等于白改。
    if results.get("status") == "FAIL":
        print(
            f"❌ 推送质量检查未通过：{results['total_issues']} 处问题"
            f"（{results.get('blocking_issues', 0)} 条需处理 / "
            f"{results.get('expected_issues', 0)} 条预期类），"
            f"score={results['score']}/100，date={date_str}"
        )
        sys.exit(1)

    print(
        f"✅ 所有推送质量检查通过（score={results['score']}/100"
        f"，{results.get('expected_issues', 0)} 条预期类提示："
        f"长度超限会无损分片，无需处理）"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
