"""
钱袋子 — 信号侦察兵 (Signal Scout)
v3.0 新增模块

职责:
  1. collect() — 从多源收集原始信号（新闻/公告/增减持/解禁/资金异动）
  2. match(user_id) — 将信号与用户持仓匹配（公共→私有）
  3. deliver(user_id) — 推送匹配的信号（企微/前端）
  4. enrich(ctx) — Pipeline 适配层，写入 DecisionContext

数据流:
  collect()=公共(全市场) → match(uid)=私有(用户相关) → deliver(uid)=推送

存储:
  - 原始信号缓存: 内存 30min（公共）
  - 匹配结果: data/{uid}/signals/YYYY-MM-DD.json（私有）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "signal_scout",
    "scope": "private",
    "input": ["user_id", "stock_holdings", "fund_holdings"],
    "output": "signals",
    "cost": "cpu",
    "tags": ["信号", "新闻", "公告", "增减持", "解禁"],
    "description": "信号侦察兵：多源信号收集(新闻/公告/增减持/解禁/资金)→持仓匹配→推送",
    "layer": "data",
    "priority": 1,
}

import os
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from config import DATA_DIR
from infra.cache import MemoryCache

# ---- 信号类型定义 ----
SIGNAL_TYPES = {
    "news_policy": "📜 政策信号",
    "news_market": "📰 市场新闻",
    "holder_change": "👔 增减持",
    "pledge_risk": "⚠️ 质押风险",
    "unlock": "🔓 解禁预警",
    "dividend": "💰 分红送转",
    "announcement": "📋 公告",
    "fund_flow": "💹 资金异动",
    "technical": "📊 技术信号",
    "st_warning": "🔴 ST预警",
}

# ---- 个股事件类信号类型（stock-event types）----
#
# 收录标准：信号的语义是"**某只特定股票**发生了某事"，即它一定带一个
# **股票代码**标的。登记进来后有两个效果（两处都用它，语义一致）：
#
#   1. 推送门槛（deliver/_should_push）：
#      即便 level == "danger"，也不得绕过 match() 的持仓相关性校验，
#      必须 relevance >= 50（= 命中持仓代码，或命中关键标签）才允许推送。
#      因为用户没持有这只票时这类推送是纯噪音 —— 既不能据此操作，还会
#      让用户怀疑数据串号（事故：用户实际只持有 8 只基金、股票持仓为空，
#      却收到 "301563.SZ 解禁7.6847%" / "920222.BJ 解禁7.4623%"）。
#
#   2. 代码命名空间（match）：
#      这类信号的 codes 里装的**只会是股票代码**，因此只用【股票持仓】
#      匹配。股票代码与基金代码都是 6 位数字且会重叠（002163 既是深市
#      股票「海南发展」也是基金「东方惠新灵活配置混合C」），拿基金持仓
#      去匹配会串号 —— 把个股事件挂到基金名下，比推无关股票更糟，因为
#      它"看起来相关"，用户会当真。
#
# 逐个类型的判定依据（codes 来源）：
#   unlock        ← Tushare share_float.ts_code（股票）
#   holder_change ← Tushare stk_holdertrade.ts_code（股票）
#   fund_flow     ← 北向十大活跃成交股的 6 位**股票**代码（不是基金！）
#   pledge_risk / st_warning / dividend / announcement
#                 ← 当前无采集器，但语义同为个股事件，先登记防漏
#
# 反例（**不要**登记）：news_policy / news_market / technical —— 没有具体
#   标的，对所有用户都成立，保留"danger 级直接放行"的原逻辑。
#   注：news_* 的 codes 是从标题正则抽的 6 位数字，理论上可能抽到基金代码，
#   所以它们也必须走"股票+基金"合并视图，否则会漏匹配。
#
# 维护约定：新增"个股事件类"信号类型时，务必同步登记到本集合，
#   否则会重新引入"无关标的推送"的骚扰问题 + 代码串号问题。
_HOLDING_REQUIRED_TYPES = {
    "unlock",
    "holder_change",
    "fund_flow",
    "pledge_risk",
    "st_warning",
    "dividend",
    "announcement",
}

# ---- 基金信号专用类型（C 方案）----
# 这四类信号由 fund_signal 包产出，render._signal() 已自带 relevance：
#   100 = 推送级，40 = 仅前端（被 budget.gate 砍掉预算后降级）。
# 它们的 relevance 已在预算守门阶段定稿，match() 不得再走「codes 命中 →
# 重算 100」的公共逻辑，否则超预算被砍到 40 的信号会因 codes 命中基金代码
# 被覆盖回 100 照推，预算守门失效。契约见 docs/design/signal-scout-fund-account.md §3.1/§3.4。
_FUND_SIGNAL_TYPES = {
    "fund_xray_concentration",
    "fund_manager_change",
    "fund_drawdown_rung",
    "dca_preflight",
}

# ---- 单位换算 ----
#
# Tushare 的**股数类**字段（share_float.float_share、stk_holdertrade.change_vol）
# 原始单位一律是【股】；中文语境的展示口径是【万股】。展示前必须除这个常数。
# 历史坑：曾经直接把股数当成万股拼进文案（"合计解禁 34,782,667.40 万股"），
# 数字被放大 10000 倍。凡是要展示股数的地方，都要显式走这个换算。
_SHARES_PER_WAN = 10000.0

# 解禁占【总股本】比例的物理上限：超过 100% 意味着解禁股数比公司总股本还多，
# 只可能是上游数据脏了（历史实例：同一笔解禁的两次公告被重复累加，
# 301507.SZ 算出 138.00%，去重后 69.00%）。
#
# 命中时**保留真实值 + 加标注 + 打告警，不做钳制**（用法见
# _collect_unlock_signals()）。
#
# ⚠️ 判定是严格大于（float_ratio > 100.0），**没有容差**：
# 100% 本身是合法的（清仓式解禁），且 float_ratio 来自上游的百分比数值，
# 不存在"累加小数位导致 100.0000001"的浮点场景，加容差只会让真正的异常
# 100.5% 漏报。真出现 100.0000001 这种值时，标个"数据存疑"也是对的。
_FLOAT_RATIO_SANITY_MAX = 100.0

# ---- 缓存 ----
_SIGNAL_CACHE_TTL = 1800  # 30 分钟
_signal_cache = MemoryCache(default_ttl=_SIGNAL_CACHE_TTL)

# 代码→名称本地缓存。底层复用 tushare_data._get_stock_names() 的 24h 批量映射，
# 这里只做进程内兜底：查不到的代码也缓存空串，避免重复查询。
_name_cache: dict = {}
_name_map_attempt_ts: float = 0.0
_NAME_MAP_RETRY_TTL = 600.0  # 名称映射拉取失败后的冷却秒数

# ---- 休市日历 ----
_MARKET_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02",  # 元旦
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",  # 春节
    "2026-02-02", "2026-02-03",
    "2026-04-06",  # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",  # 劳动节
    "2026-06-19",  # 端午
    "2026-09-28", "2026-09-29",  # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆
}


def is_trading_day(dt: datetime = None) -> bool:
    """判断是否为交易日（排除周末+法定假日）"""
    if dt is None:
        dt = datetime.now()
    if dt.weekday() >= 5:  # 周六日
        return False
    return dt.strftime("%Y-%m-%d") not in _MARKET_HOLIDAYS_2026


# ============================================================
# 1. collect() — 公共信号收集（全市场，不涉及用户）
# ============================================================

def collect() -> list:
    """
    从多源并行收集原始信号，返回统一格式 list[dict]
    每条信号: {type, title, content, codes[], source, time, level, tags[]}
    """
    cache_key = "all_signals"
    now = time.time()
    cached = _signal_cache.get(cache_key)
    if cached is not None:
        return cached

    signals = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_collect_news_signals): "news",
            pool.submit(_collect_holder_changes): "holder",
            pool.submit(_collect_unlock_signals): "unlock",
            pool.submit(_collect_fund_flow_signals): "fund_flow",
            pool.submit(_collect_technical_signals): "technical",
        }
        for fut in futures:
            try:
                result = fut.result(timeout=15)
                if result:
                    signals.extend(result)
            except Exception as e:
                print(f"[SIGNAL_SCOUT] {futures[fut]} failed: {e}")

    # 按时间倒序 + 去重
    seen = set()
    unique = []
    for s in sorted(signals, key=lambda x: x.get("time", ""), reverse=True):
        key = f"{s['type']}_{s['title'][:30]}"
        if key not in seen:
            seen.add(key)
            unique.append(s)

    _signal_cache.set(cache_key, unique)
    return unique


def _collect_news_signals() -> list:
    """从新闻/政策数据中提取信号"""
    signals = []
    try:
        from services.news_data import get_market_news, get_policy_news

        # 政策新闻
        for n in get_policy_news(10):
            title = n.get("title", "")
            if "加载中" in title:
                continue
            level = _classify_news_level(title)
            signals.append({
                "type": "news_policy",
                "title": title,
                "content": n.get("summary", title),
                "codes": _extract_codes_from_text(title),
                "source": n.get("source", "政策"),
                "time": n.get("time", datetime.now().strftime("%H:%M")),
                "level": level,
                "tags": _extract_tags(title),
                "url": n.get("url", ""),
            })

        # 市场新闻
        for n in get_market_news(8):
            title = n.get("title", "")
            level = _classify_news_level(title)
            signals.append({
                "type": "news_market",
                "title": title,
                "content": n.get("summary", title),
                "codes": _extract_codes_from_text(title),
                "source": n.get("source", "市场"),
                "time": n.get("time", ""),
                "level": level,
                "tags": _extract_tags(title),
                "url": n.get("url", ""),
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] news failed: {e}")
    return signals


def _collect_holder_changes() -> list:
    """从 Tushare 收集大股东增减持信号

    ⚠️ 方向字段是 `in_de`，不是 `change_type`（2026-09-04 修正，服务器实测
    stk_holdertrade 1333 行）：
      * `in_de == 'IN'` → 增持；`in_de == 'DE'` → 减持。
      * **接口不返回 `change_type`**（请求它会被静默丢弃，见 tushare_data.
        get_holder_trades 的取证）。原代码写成
        `"增持" if t.get("change_type") == "增持" else "减持"`，
        而 `change_type` 恒为 None ⇒ **277 条真实增持（占 20.8%）全部被报成
        减持**，并且 level 跟着 action 走，这批信号的 level 也一起错了。
      * 未知方向（空 / None / 未枚举到的新取值）**不猜**：如实写"方向未披露"、
        level 降级为 info、且不把它当成增持/减持塞进 tags。
        写成 `if in_de == 'IN': 增持 else: 减持` 只是把同一个 bug 换个写法。

    ⚠️ 单位（同上实测）：
      * `change_vol` 单位是【股】，不是万股。实测值如 4016100.0（= 401.61 万股），
        原代码直接拼 "万股" 把它放大了 10000 倍（401 亿股）。
      * `change_amount` 单位是【元】，不是万元。而且**接口不返回这个字段**
        （1333/1333 行缺失，请求它会被静默丢弃）—— 注意这不等于"金额是 0"：
        下面 `_safe_float(..., 0.0)` 拿到的 0 是**兜底值，不是真实值**，
        别理解成"这笔变动金额真的是 0 万元"。处理沿用
        _collect_unlock_signals() 里 holder_count 的同一套降级风格：拿不到
        就不显示该字段，而不是显示一个假的 0。
    """
    signals = []
    try:
        from services.tushare_data import get_holder_trades
        trades = get_holder_trades()
        for t in trades[:20]:
            # 方向：只看 in_de。
            # ⚠️ 绝不能写成 `if in_de == "IN": 增持 else: 减持` —— 这正是当前
            # bug 的形状：任何拿不到的值都会默认成减持，并把 level 连带判成
            # warning。拿不到就如实说"方向未披露"，不编方向。
            raw_direction = str(t.get("in_de") or "").strip().upper()
            if raw_direction == "IN":
                action = "增持"
                level = "info"
            elif raw_direction == "DE":
                action = "减持"
                level = "warning"
            else:
                action = "方向未披露"
                level = "info"

            # 股 → 万股（源数据单位是【股】）
            change_vol_shares = _safe_float(t.get("change_vol"), 0.0)
            vol_wan = change_vol_shares / _SHARES_PER_WAN
            # 元 → 万元（源数据单位是【元】，且接口不返回该字段 → 恒为兜底 0）
            change_amount_yuan = _safe_float(t.get("change_amount"), 0.0)
            amount_wan = change_amount_yuan / _SHARES_PER_WAN

            parts = [f"变动股数: {_fmt_number(vol_wan)} 万股"]
            if amount_wan > 0:
                parts.append(f"变动金额: {_fmt_number(amount_wan)} 万元")

            # 方向未知时不把它塞进 tags 当成增持/减持 —— 下游按 tag 过滤会误伤
            tags = [action, "股东"] if action in ("增持", "减持") else ["股东变动", "股东"]

            signals.append({
                "type": "holder_change",
                "title": f"{t.get('holder_name', '股东')} {action} {t.get('ann_date', '')}",
                "content": ", ".join(parts),
                "codes": [t.get("ts_code", "").split(".")[0]],
                "source": "Tushare",
                "time": t.get("ann_date", ""),
                "level": level,
                "tags": tags,
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] holder_change failed: {e}")
    return signals


def _collect_unlock_signals() -> list:
    """收集限售股解禁信号

    ⚠️ 口径修正 1（聚合）：Tushare share_float 按【股东逐行】返回，单行
    float_ratio 只是"某一个股东这一笔"的占比，不是这只票当天的解禁总量。
    此前直接消费原始行，导致数字严重失真。现在消费 get_upcoming_unlocks()
    已按 (ts_code, float_date) 聚合后的结果，float_share / float_ratio 均为
    当日合计。实例（服务器实测 2026-09-30，301563.SZ 云汉芯城）：
      * 单行最大值        = 7.68%
      * 当日 32 行合计    = 41.09%（= 占总股本，与 Tushare float_ratio 同口径）

    ⚠️ 口径修正 2（float_ratio 的分母）：Tushare 的 float_ratio 分母是
    【总股本】，不是【流通股本】。两个比例都真实，但含义不同，不能混用：
      * 占总股本 = float_share / daily_basic.total_share
                  = 34,782,667.4 / 84,650,928 = 41.09%
      * 占流通盘 = float_share / daily_basic.float_share
                  = 34,782,667.4 / 19,135,696 = 181.77%
    本信号展示的是**占总股本**（即 Tushare 原值），与 get_upcoming_unlocks()
    的 docstring 口径一致。若将来要展示"占流通盘"，必须自己除
    daily_basic.float_share，不能直接把 181.77% 当成 float_ratio 的默认值。

    ⚠️ 口径修正 3（单位）：float_share 的单位是【股】，不是万股。
    34,782,667.4 股 = 3,478.27 万股。此前直接拼 "万股" 把数字放大 10000 倍，
    推送正文变成"合计解禁 34,782,667.40 万股"。

    ⚠️ 口径修正 4（去重）：上游 get_upcoming_unlocks() 已按"解禁事件"去重
    （同一笔解禁的原始公告 + 提示性公告只算一次，见 tushare_data.
    _dedupe_share_float_rows）。不去重时合计值会放大约 2 倍，出现过
    "解禁 138.00%" 这种超过总股本的荒谬数字。
    """
    signals = []
    try:
        from services.tushare_data import get_upcoming_unlocks
        for u in get_upcoming_unlocks()[:10]:
            ts_code = str(u.get("ts_code") or "").strip()
            if not ts_code:
                continue

            code6 = ts_code.split(".")[0]
            float_ratio = _safe_float(u.get("float_ratio"), 0.0)
            float_share = _safe_float(u.get("float_share"), 0.0)
            # holder_count = 去重后的股东名数量（与 holder_names 同口径，见
            # tushare_data.get_upcoming_unlocks）。全部行都没有股东名时为 0
            # → 不显示该字段，避免出现"涉及 0 个股东"这种自相矛盾的文案。
            holder_count = int(_safe_float(u.get("holder_count"), 0.0))
            float_date = _fmt_signal_date(u.get("float_date"))

            # 标题带公司名称，代码退居括号内（"看不懂推送"的直接原因之一）
            name = _lookup_stock_name(ts_code)
            label = f"{name}({code6})" if name else code6

            level = "danger" if float_ratio > 5 else ("warning" if float_ratio > 2 else "info")
            # float_share 单位是【股】，展示口径是【万股】 → 必须除 10000
            # （实测：未除时正文为"合计解禁 34,782,667.40 万股"，正确值是 3,478.27 万股）
            content = (
                f"解禁日 {float_date}，"
                f"合计解禁 {_fmt_number(float_share / _SHARES_PER_WAN)} 万股"
            )
            if holder_count > 0:
                content += f"，涉及 {holder_count} 个股东"

            # 防线：解禁比例不可能超过 100% 总股本，超过就说明上游数据脏了
            # （历史实例：301507.SZ 曾算出 138.00%，真因是同一笔解禁的
            #  原始公告 + 提示性公告被重复累加；去重后是 69.00%）。
            # 处理方式：**不钳制**——把 138 改成 100 是凭空造数，与本项目
            # "宁可显示得难看也不编数字"的取向冲突（见 _collect_fund_flow_signals
            #  里对"买入 0万"的处理）。这里保留真实值但加标注 + 打告警，
            #  让异常既可见、又不被伪装成正常值。
            if float_ratio > _FLOAT_RATIO_SANITY_MAX:
                print(
                    f"[SIGNAL_SCOUT] 解禁比例异常：{ts_code} {float_date} "
                    f"float_ratio={float_ratio:.2f}% > {_FLOAT_RATIO_SANITY_MAX}%"
                    f"（超过总股本，上游数据存疑，已标注后照常产出）"
                )
                content += "（数据存疑：占比超 100%）"

            signals.append({
                "type": "unlock",
                "title": f"解禁预警: {label} 解禁{float_ratio:.2f}%",
                "content": content,
                "codes": [code6],
                "source": "Tushare",
                "time": str(u.get("float_date") or ""),
                "level": level,
                "tags": ["解禁"],
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] unlock failed: {e}")
    return signals


def _collect_fund_flow_signals() -> list:
    """收集北向活跃个股信号

    ⚠️ 口径（2026-08 修正）：
    1. 北向【净买入】自 2024-08-19 起交易所改为按季度披露，日频不可得，
       因此本函数**不再声称"买入"**，只报"活跃个股"，方向留白。
    2. 原代码读的 `net_amount` / `hold_change` 两个 key 在 alt_data 的
       top_stocks 里**从来不存在**（实际 key 是 code/name/holding_value/change_pct），
       `.get(..., 0)` 恒返回 0，标题恒为"买入 0万"、正文恒为"持股变化: 0万股"
       —— 是凭空捏造的数字。已改为只展示真实存在的字段。
    3. 数据时点用 data_date 显式标注，让陈旧数据暴露出来而不是假装实时。
    """
    signals = []
    try:
        from services.alt_data import get_northbound_flow_detail
        nb = get_northbound_flow_detail()
        top = nb.get("top_stocks") or []
        if not nb.get("available") or not top:
            return signals

        # 口径标注：区分数据来源，避免把两种不同含义的榜单混为一谈
        src = nb.get("top_stocks_source", "")
        if src == "tushare_hsgt_top10":
            src_label = "沪深股通十大成交股"
        elif src == "akshare":
            src_label = "北向持股排行"
        else:
            src_label = "北向榜单"

        data_date = str(nb.get("data_date", "") or "")
        if len(data_date) == 8:
            date_label = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:8]}"
        else:
            date_label = data_date or "时点未知"

        for s in top[:5]:
            name = str(s.get("name", "") or s.get("code", "") or "").strip()
            if not name:
                continue

            # 只展示真实存在且为数值的字段，不做方向性表述
            detail_parts = [f"来源: {src_label}（数据时点 {date_label}）"]
            holding_value = s.get("holding_value")
            if isinstance(holding_value, (int, float)) and holding_value > 0:
                detail_parts.append(f"持股市值 {holding_value:.0f}")
            detail_parts.append("净买入方向数据不可得：交易所自2024-08-19起改为按季度披露")

            signals.append({
                "type": "fund_flow",
                "title": f"北向活跃个股: {name}",
                "content": "；".join(detail_parts),
                "codes": [s.get("code", "")],
                "source": "北向",
                "time": datetime.now().strftime("%H:%M"),
                "level": "info",
                "tags": ["北向", "资金"],
            })
    except Exception as e:
        print(f"[SIGNAL_SCOUT] fund_flow failed: {e}")
    return signals


def _collect_technical_signals() -> list:
    """收集技术面信号（从已有的盯盘数据中提取）"""
    signals = []
    try:
        # 涨停跌停池
        from infra.data_source.alt.flows import get_zt_pool
        try:
            df = get_zt_pool(date=datetime.now().strftime("%Y%m%d"))
            if df is not None and len(df) > 0:
                zt_count = len(df)
                signals.append({
                    "type": "technical",
                    "title": f"今日涨停 {zt_count} 只",
                    "content": f"涨停家数: {zt_count}",
                    "codes": [],
                    "source": "东财",
                    "time": datetime.now().strftime("%H:%M"),
                    "level": "info" if zt_count < 50 else "warning",
                    "tags": ["涨停", "情绪"],
                })
        except Exception:
            pass
    except Exception as e:
        print(f"[SIGNAL_SCOUT] technical failed: {e}")
    return signals


# ============================================================
# 2. match(user_id) — 私有匹配（信号→用户持仓）
# ============================================================

def match(user_id: str) -> list:
    """
    将公共信号与用户持仓匹配
    返回: 按相关性排序的信号列表，每条附加 relevance 和 holding_name
    """
    # 获取用户持仓代码
    #
    # ⚠️ 股票代码与基金代码**都是 6 位数字且会重叠**（实例：002163 既是深市
    # 股票「海南发展」，也是基金「东方惠新灵活配置混合C」）。原实现把两者
    # 合并进同一个 user_codes 集合，导致个股事件信号被误判成命中基金持仓、
    # related_holding 串成基金名 —— 比推无关股票更糟，因为它"看起来相关"，
    # 用户会当真。因此必须拆成两个映射，按信号类型分别取用。
    user_stock_codes = {}  # {股票代码: 持仓名称}
    user_fund_codes = {}   # {基金代码: 持仓名称}
    try:
        from services.stock_monitor import load_stock_holdings
        for h in load_stock_holdings(user_id):
            code = h.get("code", "")
            if code:
                user_stock_codes[code] = h.get("name", code)
    except Exception:
        pass
    try:
        from services.fund_monitor import load_fund_holdings
        for h in load_fund_holdings(user_id):
            code = h.get("code", "")
            if code:
                user_fund_codes[code] = h.get("name", code)
    except Exception:
        pass

    # 非个股事件类沿用原「股票+基金全量」匹配，保持原有行为不受本次改动影响。
    # 基金后写入 → 代码冲突时基金名覆盖股票名，与原实现（先股票后基金）一致。
    user_all_codes = {**user_stock_codes, **user_fund_codes}

    # ---- 基金账户专用通道（C 方案唯一接缝）----
    # 纯基金账户：跳过 unlock/holder_change/fund_flow，追加 P0-1/P0-2/P0-3/P1-1 基金信号；
    # 持股/混合账户：原样 collect()，行为零变更。契约见 docs/design/signal-scout-fund-account.md §3.2。
    from services.fund_signal import build_signal_pool
    all_signals = build_signal_pool(user_id, user_stock_codes, user_fund_codes)
    if not all_signals:
        return []

    matched = []
    for sig in all_signals:
        sig_type = sig.get("type", "")

        # 基金信号专用通道（C 方案）：relevance 已在 budget.gate 定稿
        # （100=推送 / 40=仅前端），此处直接沿用，不走下方「codes 命中 →
        # 重算 100」的公共逻辑；否则被砍到 40 的信号会因 codes 命中基金代码
        # 被覆盖回 100 照推，预算守门失效。详见 _FUND_SIGNAL_TYPES 注释。
        if sig_type in _FUND_SIGNAL_TYPES:
            relevance = _safe_float(sig.get("relevance"), 0.0)
            if relevance > 0:
                matched.append({
                    **sig,
                    "relevance": relevance,
                    "related_holding": sig.get("related_holding", ""),
                })
            continue

        relevance = 0
        related_holding = ""

        # 直接代码匹配（最高相关性）
        # 个股事件类信号的 codes 里装的都是股票代码，只拿股票持仓来匹配；
        # 用基金代码去匹配它属于类型错误，会串号（见上方 002163 事故说明）。
        holding_map = (
            user_stock_codes if sig_type in _HOLDING_REQUIRED_TYPES else user_all_codes
        )
        for code in sig.get("codes", []):
            if code in holding_map:
                relevance = 100
                related_holding = holding_map[code]
                break

        # 标签匹配（中等相关性）
        if relevance == 0:
            for tag in sig.get("tags", []):
                if tag in ["降息", "降准", "利好", "利空", "关税", "贸易战"]:
                    relevance = 50
                    break

        # 全市场信号（低相关性但仍有价值）
        if relevance == 0 and sig.get("level") in ("danger", "warning"):
            relevance = 30

        if relevance > 0:
            matched.append({
                **sig,
                "relevance": relevance,
                "related_holding": related_holding,
            })

    # 按相关性+级别排序
    level_order = {"danger": 0, "warning": 1, "info": 2}
    matched.sort(key=lambda x: (-x["relevance"], level_order.get(x.get("level", "info"), 2)))

    # 存储匹配结果
    _save_matched(user_id, matched)

    return matched


def _save_matched(user_id: str, signals: list):
    """保存匹配结果到用户目录"""
    try:
        d = DATA_DIR / user_id / "signals"
        d.mkdir(parents=True, exist_ok=True)
        fp = d / f"{datetime.now().strftime('%Y-%m-%d')}.json"
        fp.write_text(json.dumps(signals[:50], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[SIGNAL_SCOUT] save failed: {e}")


# ============================================================
# 3. deliver(user_id) — 推送
# ============================================================

def _should_push(sig: dict) -> bool:
    """判断单条信号是否值得推送

    规则：
      1. relevance >= 50（命中持仓代码，或命中关键标签）→ 推送
      2. level == "danger" 且**不是**个股事件类 → 推送
         （宏观/市场类信号无具体标的，全市场用户都受影响，保留放行）
      3. 其余 → 不推送

    ⚠️ 原实现是 `relevance >= 50 or level == "danger"`，右半支让**所有**
    全市场 danger 级信号绕过持仓匹配直接推送，与文件头"公共信号 → 按持仓
    私有化 → 再推送"的定位冲突，是用户收到无关标的推送的根本原因。
    """
    if _safe_float(sig.get("relevance"), 0.0) >= 50:
        return True
    if (
        sig.get("level") == "danger"
        and sig.get("type") not in _HOLDING_REQUIRED_TYPES
        and sig.get("type") not in _FUND_SIGNAL_TYPES
    ):
        return True
    return False


def deliver(user_id: str, signals: list = None) -> dict:
    """
    推送信号到企微
    只推高相关性(≥50)或危险级别的信号，避免骚扰

    ⚠️ danger 级不再无条件放行：个股事件类信号（unlock/holder_change/
    pledge_risk/st_warning/dividend/announcement）必须命中持仓才推，
    详见 _HOLDING_REQUIRED_TYPES 的注释与 _should_push()。
    """
    if signals is None:
        signals = match(user_id)

    important = [s for s in signals if _should_push(s)]
    if not important:
        return {"pushed": 0, "reason": "无重要信号"}

    # 构建推送文本（纯文本，不用 Markdown — 铁律 #20）
    lines = [f"📡 信号侦察 ({len(important)}条)"]
    for s in important[:8]:
        icon = SIGNAL_TYPES.get(s.get("type", ""), "📌")
        title = str(s.get("title", "") or "")
        holding = f" → {s['related_holding']}" if s.get("related_holding") else ""
        # 带上 content：解禁日、解禁数量等关键信息此前被整段丢弃，
        # 用户只看到"某代码 解禁X%"，无法判断"什么时候、多少"。
        content = str(s.get("content", "") or "").strip()
        line = f"{icon} {title}{holding}"
        if content and content != title:
            line += f"\n   {content}"
        lines.append(line)

    text = "\n".join(lines)

    try:
        from services.wxwork_push import send_text, is_configured  # FIX: 函数名是 send_text 不是 send_text_message
        if is_configured():
            result = send_text(text, user_id=user_id)  # FIX: 使用正确的参数名 user_id
            pushed = result.get("ok", False)
            if pushed:
                # 推送成功后才记账：日/月额度的口径必须是「实际发出去的条数」，
                # 不是「match() 被调用的次数」。budget.gate() 在 match() 里只
                # 判额度不写账；这里才是唯一该记账的地方。
                # 失败不记账 —— 没发出去就不该占额度。
                try:
                    from services.fund_signal.budget import commit as _budget_commit
                    _budget_commit(user_id, important)
                except Exception as ce:
                    # 记账失败绝不能让已成功的推送变成失败返回。
                    print(f"[SIGNAL_SCOUT][WARNING] 推送预算记账失败（{ce}）")
            return {"pushed": len(important) if pushed else 0, "text": text}
    except Exception as e:
        print(f"[SIGNAL_SCOUT] push failed: {e}")

    return {"pushed": 0, "text": text, "reason": "企微未配置或推送失败"}


# ============================================================
# 4. enrich(ctx) — Pipeline 适配层
# ============================================================

_ENRICH_CACHE_TTL = 900  # 15分钟
_enrich_cache = MemoryCache(default_ttl=_ENRICH_CACHE_TTL)  # {user_id: {"data": matched, "ts": time}}

def enrich(ctx):
    """
    Pipeline 适配: 收集信号 → 匹配用户持仓 → 写入 ctx
    """
    import time as _time
    user_id = ctx.user_id
    if not user_id:
        return ctx

    now = _time.time()
    cached = _enrich_cache.get(user_id)
    if cached is not None:
        matched = cached
        print("[SIGNAL_SCOUT] enrich using cache")
    else:
        matched = match(user_id)
        _enrich_cache.set(user_id, matched, ttl=_ENRICH_CACHE_TTL)
    ctx.modules_results["signal_scout"] = {
        "available": True,
        "total_collected": len(collect()),
        "matched_count": len(matched),
        "high_relevance": len([s for s in matched if s.get("relevance", 0) >= 50]),
        "danger_count": len([s for s in matched if s.get("level") == "danger"]),
        "top_signals": matched[:5],
        "confidence": min(0.8, len(matched) / 20),
        "direction": _infer_direction(matched),
    }

    # 如果用户问的是具体个股/基金，拉新闻（关键增量信息）
    stock_code = getattr(ctx, "question_stock_code", "")
    stock_name = getattr(ctx, "question_stock_name", "")
    is_fund = getattr(ctx, "question_is_fund", False)

    if stock_code or stock_name:
        try:
            if is_fund:
                # 基金新闻
                fund_news = _fetch_fund_news(stock_code, stock_name)
                if fund_news:
                    ctx.modules_results["signal_scout"]["fund_news"] = fund_news[:5]
                    ctx.modules_results["signal_scout"]["fund_news_count"] = len(fund_news)
                    print(f"[SIGNAL_SCOUT] 基金新闻: {stock_name or stock_code} → {len(fund_news)}条")
            else:
                # 个股新闻
                stock_news = _fetch_stock_news(stock_code, stock_name)
                if stock_news:
                    ctx.modules_results["signal_scout"]["stock_news"] = stock_news[:5]
                    ctx.modules_results["signal_scout"]["stock_news_count"] = len(stock_news)
                    news_direction = _infer_news_direction(stock_news)
                    if news_direction != "neutral":
                        ctx.modules_results["signal_scout"]["stock_news_direction"] = news_direction
                    print(f"[SIGNAL_SCOUT] 个股新闻: {stock_name or stock_code} → {len(stock_news)}条, 方向={news_direction}")
        except Exception as e:
            print(f"[SIGNAL_SCOUT] 新闻获取失败: {e}")

    return ctx


def _infer_direction(signals: list) -> str:
    """从信号推断整体方向"""
    bull = sum(1 for s in signals if any(t in s.get("tags", []) for t in ["利好", "增持", "降息", "买入"]))
    bear = sum(1 for s in signals if any(t in s.get("tags", []) for t in ["利空", "减持", "加息", "卖出", "ST"]))
    if bull > bear + 2:
        return "bullish"
    if bear > bull + 2:
        return "bearish"
    return "neutral"


def _fetch_stock_news(code: str, name: str) -> list:
    """拉取个股新闻（AKShare + 已有新闻接口）"""
    news = []
    try:
        # 方式1：用已有的 news_data 接口按代码搜
        from services.news_data import get_market_news
        all_news = get_market_news(30)
        # 过滤：标题中包含股票名或代码
        search_terms = [t for t in [name, code] if t]
        for n in all_news:
            title = n.get("title", "")
            if any(term in title for term in search_terms):
                news.append(n)
    except Exception as e:
        print(f"[STOCK_NEWS] market_news filter failed: {e}")

    try:
        # 方式2：AKShare 个股新闻（东方财富）
        from infra.data_source.macro.indicators import get_stock_news
        df = get_stock_news(symbol=code)  # 直接传6位代码
        if df is not None and len(df) > 0:
            for _, row in df.head(10).iterrows():
                title = str(row.get("新闻标题", ""))
                pub_time = str(row.get("发布时间", ""))
                url = str(row.get("新闻链接", ""))
                source = str(row.get("文章来源", "东财"))
                if title and title not in [n.get("title") for n in news]:
                    news.append({"title": title, "time": pub_time, "url": url, "source": source})
    except Exception as e:
        print(f"[STOCK_NEWS] akshare failed: {e}")

    return news[:10]


def _fetch_fund_news(code: str, name: str) -> list:
    """拉取基金相关新闻"""
    news = []
    try:
        from services.data_layer import get_fund_news
        fund_news = get_fund_news(code, 8)
        for n in fund_news:
            title = n.get("title", "")
            if title and "加载中" not in title:
                news.append(n)
    except Exception as e:
        print(f"[FUND_NEWS] fund_news failed: {e}")

    # 补充：从大盘新闻里筛选和基金相关的
    try:
        from services.news_data import get_market_news
        all_news = get_market_news(30)
        search_terms = [t for t in [name, code] if t]
        for n in all_news:
            title = n.get("title", "")
            if any(term in title for term in search_terms):
                if title not in [x.get("title") for x in news]:
                    news.append(n)
    except Exception:
        pass

    return news[:10]


def _infer_news_direction(news: list) -> str:
    """从个股新闻推断方向"""
    BULL_KW = ["利好", "增持", "回购", "业绩超预期", "大单", "涨停", "突破", "新高"]
    BEAR_KW = ["利空", "减持", "质押", "业绩下滑", "亏损", "跌停", "暴跌", "ST", "处罚", "退市"]
    bull = 0
    bear = 0
    for n in news:
        title = n.get("title", "")
        if any(k in title for k in BULL_KW):
            bull += 1
        if any(k in title for k in BEAR_KW):
            bear += 1
    if bull > bear + 1:
        return "bullish"
    if bear > bull + 1:
        return "bearish"
    return "neutral"


# ============================================================
# 5. API 辅助函数
# ============================================================

def get_latest(user_id: str) -> dict:
    """获取最新匹配信号（供 API 调用）"""
    matched = match(user_id)
    return {
        "signals": matched[:20],
        "total": len(matched),
        "high_relevance": len([s for s in matched if s.get("relevance", 0) >= 50]),
        "scanned_at": datetime.now().isoformat(),
        "is_trading_day": is_trading_day(),
    }


def get_history(user_id: str, days: int = 7) -> list:
    """获取历史信号"""
    results = []
    d = DATA_DIR / user_id / "signals"
    if not d.exists():
        return []

    for i in range(days):
        dt = datetime.now() - timedelta(days=i)
        fp = d / f"{dt.strftime('%Y-%m-%d')}.json"
        if fp.exists():
            try:
                signals = json.loads(fp.read_text(encoding="utf-8"))
                results.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "count": len(signals),
                    "signals": signals[:10],
                })
            except Exception:
                pass
    return results


# ============================================================
# 工具函数
# ============================================================

# 利好/利空关键词
_BULL_KW = ["降息", "降准", "宽松", "利好", "上涨", "增持", "反弹", "刺激", "补贴", "减税"]
_BEAR_KW = ["加息", "收紧", "利空", "下跌", "减持", "暴跌", "制裁", "关税", "处罚", "退市"]


def _classify_news_level(title: str) -> str:
    """根据标题关键词判断信号级别"""
    if any(k in title for k in ["暴跌", "崩盘", "退市", "爆仓", "处罚"]):
        return "danger"
    if any(k in title for k in _BEAR_KW):
        return "warning"
    if any(k in title for k in _BULL_KW):
        return "info"
    return "info"


def _extract_tags(title: str) -> list:
    """从标题提取标签"""
    tags = []
    tag_map = {
        "降息": "降息", "降准": "降准", "关税": "关税", "贸易": "贸易战",
        "半导体": "科技", "芯片": "科技", "AI": "科技", "利好": "利好",
        "利空": "利空", "增持": "增持", "减持": "减持", "解禁": "解禁",
        "房地产": "地产", "央行": "央行", "美联储": "美联储",
    }
    for kw, tag in tag_map.items():
        if kw in title:
            tags.append(tag)
    return tags[:5]


def _extract_codes_from_text(text: str) -> list:
    """从文本提取股票代码（6位数字）"""
    import re
    codes = re.findall(r'\b(\d{6})\b', text)
    return list(set(codes))[:5]


def _safe_float(value, default: float = 0.0) -> float:
    """安全转 float，非有限值（inf / nan）也按 default 处理

    Tushare 返回值类型不稳定（None / "" / "1.23" / 123 / "1,234" 都出现过），
    脏值一律按 default 返回，不抛异常。

    ⚠️ 为什么要把 inf / nan 也当脏值：它们能穿过 float() 不报错，但会让
    后续算术和格式化全线崩坏（x + inf = inf、int(inf) → OverflowError、
    int(nan) → ValueError），而本模块的调用点大多被 try/except 包着只 print
    —— 结果是整批信号**静默丢弃**，日志里只有一行，线上根本看不出来。
    """
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _lookup_stock_name(ts_code: str) -> str:
    """查询股票名称；拿不到时返回空串，由调用方降级显示代码

    复用 services.tushare_data._get_stock_names() —— 它内部用 stock_basic
    一次性拉全量并缓存 24 小时，因此**不会**为补充名称而新增每次推送的
    网络请求（硬约束）。

    不用 services.stock_monitor._get_stock_name()：那个走 akshare 全量列表
    且无缓存，每查一个代码就是一次全量网络拉取。

    Args:
        ts_code: Tushare 格式代码，如 "301563.SZ"。

    Returns:
        股票名称；查不到或数据源故障时返回 ""。
    """
    global _name_map_attempt_ts

    ts_code = str(ts_code or "").strip()
    if not ts_code:
        return ""
    if ts_code in _name_cache:
        return _name_cache[ts_code]

    name = ""
    now = time.time()
    # 名称映射拉不到时做冷却，避免每个代码都重复打一次数据源
    if now - _name_map_attempt_ts >= _NAME_MAP_RETRY_TTL:
        try:
            from services.tushare_data import _get_stock_names
            mapping = _get_stock_names() or {}
            if mapping:
                _name_cache.update({k: str(v or "") for k, v in mapping.items()})
                name = _name_cache.get(ts_code, "")
            else:
                _name_map_attempt_ts = now
        except Exception as e:
            _name_map_attempt_ts = now
            print(f"[SIGNAL_SCOUT] stock name lookup failed: {e}")

    _name_cache.setdefault(ts_code, name)
    return name


def _fmt_signal_date(value) -> str:
    """把 Tushare 日期（20260930 / 2026-09-30）规范成 YYYY-MM-DD

    拿不到或格式异常时返回 "待定"，保证信号不会因为日期脏而丢失。
    """
    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(raw) == 10 and raw[4] == "-":
        return raw
    return "待定"


def _fmt_number(value: float) -> str:
    """数字千分位格式化（纯格式化，单位由调用方负责），整数不带小数点

    ⚠️ 单位是调用方的责任，本函数**不做任何换算**：传进来是什么单位，
    显示的就是什么单位。调用方必须自己先把源数据换算到目标单位再传进来
    （例：解禁信号传的是 float_share / _SHARES_PER_WAN，源数据单位是【股】，
    展示单位是【万股】）。历史坑：这里曾既不做换算、又在 docstring 里写死
    "（万股）"，让调用方误以为函数内部会换算，结果正文把股数放大 10000 倍。

    ⚠️ 必须吞掉一切异常、绝不外抛：本函数在 _collect_unlock_signals() 的
    循环里被调用，而那个循环外层 try/except 只 print —— 一旦这里抛
    （int(inf) → OverflowError、int(nan) → ValueError），**该条及其后所有**
    解禁信号都会被静默丢弃，产出从 3 条变 1 条且无人报警。
    非有限值降级为 str(value)（"inf" / "nan"），宁可显示得难看，
    也不能让脏数据吃掉同批次的其他信号。
    """
    try:
        if not math.isfinite(value):
            return str(value)
        if value == int(value):
            return f"{int(value):,}"
        return f"{value:,.2f}"
    except (TypeError, ValueError, OverflowError):
        return str(value)
