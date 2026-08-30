"""
钱袋子 — 另类数据引擎 V1
散户也能用的"卫星替代品"数据源

数据源（全部免费，来自 AKShare/Tushare）：
  1. 北向资金实时流向 — 外资行为（最有价值的免费另类数据）
  2. 融资融券余额 — 杠杆情绪
  3. 龙虎榜 — 游资/机构行为
  4. 大宗交易 — 大资金动向
  5. 期权隐含波动率 — 市场恐慌度
  6. 行业ETF资金流 — 板块轮动信号
  7. 股东变动（增减持） — 内部人信号
  8. 解禁日历 — 供给压力

参考：
  - 幻方量化另类数据体系（卫星/GPS/IoT 的免费平替）
  - AQR "Alternative Data" Research
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "alt_data",
    "scope": "public",
    "input": [],
    "output": "alt_dashboard",
    "cost": "cpu",
    "tags": ['另类数据', '北向', '融资', '龙虎榜', '大宗'],
    "description": "另类数据仪表盘：北向资金+融资融券+龙虎榜+大宗交易+行业资金流",
    "layer": "data",
    "priority": 2,
}
import time
import traceback
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from infra.cache import MemoryCache

_ALT_CACHE_TTL = 1800  # 30 分钟
_alt_cache = MemoryCache(default_ttl=_ALT_CACHE_TTL)


def _clean_nan(obj):
    """递归清洗 NaN/Inf"""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0
        return obj
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    return obj


def _num_or_none(val):
    """严格数值解析：拿不到就返回 None，**绝不退化成 0**。

    为什么要单独写这个 —— `float(x.get("k", 0) or 0)` 这种写法有两个隐藏危害：
      1. 字段缺失 / 为 None / 为空串时凭空产出 `0`，而这个 0 会被当作
         "真实测得的 0" 对外展示（例如"净买入额 0.0 亿"），属于编造数据；
      2. 拿它做排序 key 时，若整批数据都缺该字段，所有 key 都是 0，
         排序退化成原始顺序，却仍被当作"按金额降序的 Top 榜"展示 ——
         等于把任意顺序伪装成排名。
    正确做法：让"没有"保持 None，由调用方显式处理缺失。
    """
    try:
        if val is None or val == "":
            return None
        f = float(val)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


# ============================================================
# 1. 北向资金
# ============================================================

def get_northbound_flow_detail() -> dict:
    """北向资金【成交额】明细 + 活跃度信号（净流入维度已不可得）

    策略：Tushare 主（moneyflow_hsgt 日级别成交额） + AKShare 降级（top 持股）

    ⚠️ 口径关键事实（2026-08 修正）：
    自 2024-08-19 起沪深交易所停止披露北向日频净买入（改按季度公布），
    Tushare moneyflow_hsgt 的 north_money/hgt/sgt 现为「当日成交额」。
    因此 net_flow_* 一律为 None，signal 只描述成交额活跃度，不再给流入/流出判断。

    Returns:
        dict: 对齐 tushare_data.get_northbound_flow 的返回契约，并附加
              alt 层专有字段 today / top_stocks / signal / source。
              日级别成交额明细统一在 `daily_turnover`（列表项 key 为 date/turnover）。
              ⚠️ 历史上的 `trend` 列表字段已删除 —— 它与契约里字符串型的 `trend`
              同名不同类型，是二义性地雷；日级别数据请改读 `daily_turnover`。

    Note:
        `available` 与 `net_flow_available` 语义不同：
        - `available=True`  表示数据源整体拿到了数据（成交额可用）；
        - `net_flow_available=False` 单独表示「净流入」这一个维度不可得。
        下游据此决定是跳过净流入因子（正常）还是上报数据源故障（异常）。
    """
    from services.tushare_data import _north_unavailable_result

    cache_key = "nb_flow_detail"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    # 契约骨架 + alt 层专有字段
    # 注意：不再提供历史上的 `trend` 列表字段 —— 它与契约里字符串型的 `trend`
    # 同名不同类型（三个同域函数类型不一致 = 地雷），日级别数据统一走 daily_turnover。
    result = _north_unavailable_result("unknown")
    result.update({"today": {}, "top_stocks": [], "signal": ""})

    # 策略：Tushare 主（使用 tushare_data.py 的 get_northbound_flow）
    try:
        from services.tushare_data import is_configured, get_northbound_flow
        if is_configured():
            nb_data = get_northbound_flow(days=30)
            if nb_data and nb_data.get("available"):
                # Tushare 返回日级别成交额明细（daily_turnover）
                if nb_data.get("daily_turnover"):
                    result.update(nb_data)
                    result["source"] = "tushare"
                    result["today"] = {
                        "date": nb_data.get("data_date", ""),
                        "turnover": nb_data.get("turnover_today"),
                    }
                    print(f"[ALT] 北向成交额 from Tushare: {len(result['daily_turnover'])}天明细, "
                          f"today={nb_data.get('turnover_today')}亿, "
                          f"avg5d={nb_data.get('turnover_avg_5d')}亿（净流入维度不可得）")
                else:
                    print(f"[ALT] 北向资金 Tushare 无 daily_turnover，降级")
    except Exception as e:
        print(f"[ALT] 北向资金 Tushare failed: {e}")

    # 补充 top_stocks（Tushare hsgt_top10 或 AKShare 降级）
    if len(result["top_stocks"]) == 0:
        try:
            # 尝试 Tushare hsgt_top10（沪股通+深股通 Top10）
            from services.tushare_data import is_configured, _call_tushare
            if is_configured():
                # 字段说明：
                #   net_amount 净买入额（万元）—— 口径变更后可能缺失
                #   amount     成交金额（元）  —— 成交额维度，通常仍可得
                #   rank       接口自带排名
                #   close/change 收盘价/涨跌（change 语义与单位未经核实，见下）
                _TOP10_FIELDS = "ts_code,name,close,change,amount,net_amount,rank"
                # 沪股通 Top10
                rows_h = _call_tushare(
                    "hsgt_top10",
                    {"trade_date": result.get("data_date", ""), "market_type": "1"},
                    _TOP10_FIELDS
                )
                # 深股通 Top10
                rows_s = _call_tushare(
                    "hsgt_top10",
                    {"trade_date": result.get("data_date", ""), "market_type": "3"},
                    _TOP10_FIELDS
                )
                all_rows = (rows_h or []) + (rows_s or [])
                if all_rows:
                    # 严格解析，缺失一律 None，绝不退化成 0（详见 _num_or_none）。
                    # net_amount 单位万元；amount 单位元。
                    parsed = []
                    for r in all_rows:
                        parsed.append({
                            "row": r,
                            "net_amount_wan": _num_or_none(r.get("net_amount")),
                            "amount_yuan": _num_or_none(r.get("amount")),
                            "rank": _num_or_none(r.get("rank")),
                        })

                    n_valid_net = sum(1 for p in parsed if p["net_amount_wan"] is not None)
                    n_valid_amt = sum(1 for p in parsed if p["amount_yuan"] is not None)
                    n_valid_rank = sum(1 for p in parsed if p["rank"] is not None)

                    # 排序依据按可信度降级：净买入额 → 成交额 → 接口自带 rank → 不排序。
                    # 每一级都要求该字段**真的有值**，否则继续降级 ——
                    # 绝不用全 None/全 0 的 key 排出一个假榜单。
                    if n_valid_net:
                        parsed.sort(key=lambda p: (p["net_amount_wan"] is None,
                                                   -(p["net_amount_wan"] or 0)))
                        ranked_by = "net_amount"
                    elif n_valid_amt:
                        parsed.sort(key=lambda p: (p["amount_yuan"] is None,
                                                   -(p["amount_yuan"] or 0)))
                        ranked_by = "amount"
                    elif n_valid_rank:
                        parsed.sort(key=lambda p: (p["rank"] is None, p["rank"] or 0))
                        ranked_by = "api_rank"
                    else:
                        ranked_by = "unranked"
                        print("[ALT] hsgt_top10 无任何可排序字段（net_amount/amount/rank 全缺），"
                              "保持原始顺序，不作为排名对外展示")

                    for p in parsed[:15]:
                        r = p["row"]
                        code = r.get("ts_code", "")
                        net_amount_wan = p["net_amount_wan"]
                        amount_yuan = p["amount_yuan"]
                        # 字段名修正（原名 change_pct 完全错误：存的是亿元金额，
                        # 不是百分比）。hsgt_top10 的 net_amount 单位为万元。
                        result["top_stocks"].append({
                            "code": code.split(".")[0] if "." in code else code,
                            "name": str(r.get("name", "")),
                            # Tushare 该接口不提供持股市值 → None（不是 0，0 会被
                            # 当成"持股市值真的为零"展示，同样是编造数据）
                            "holding_value": None,
                            "net_amount_yi": (round(net_amount_wan / 10000, 2)
                                              if net_amount_wan is not None else None),
                            # 个股北向成交额（元 → 亿元）。这是口径变更后仍可得的维度。
                            "turnover_yi": (round(amount_yuan / 1e8, 2)
                                            if amount_yuan is not None else None),
                            "close": _num_or_none(r.get("close")),
                            # ⚠️ Tushare `change` 字段的语义（涨跌额 vs 涨跌幅）与单位
                            #    我没有实盘核实过，因此**不改名成 pct/涨跌幅**，
                            #    原样保留并标注 —— 猜一个名字就是重犯 change_pct 的错。
                            "change_raw": _num_or_none(r.get("change")),
                            "change_semantic": "Tushare hsgt_top10 的 change 字段（语义/单位未核实）",
                            "rank": (int(p["rank"]) if p["rank"] is not None else None),
                            "amount_semantic": "十大成交股净买入额",
                            "amount_unit": "亿元",
                        })
                    result["top_stocks_source"] = "tushare_hsgt_top10"
                    result["top_stocks_ranked_by"] = ranked_by
                    print(f"[ALT] 北向 Top10 from Tushare: {len(result['top_stocks'])} 只"
                          f"（净买入额有效 {n_valid_net}/{len(all_rows)}, "
                          f"成交额有效 {n_valid_amt}/{len(all_rows)}, "
                          f"排序依据={ranked_by}）")
        except Exception as e:
            print(f"[ALT] Tushare hsgt_top10 failed: {e}")

    # 降级：AKShare（如果 Tushare 没拿到 top_stocks）
    if len(result["top_stocks"]) == 0:
        try:
            from infra.data_source.alt.flows import get_hsgt_hold_stock

            # 北向持股 top
            try:
                df = get_hsgt_hold_stock(market="北向", indicator="今日排行")
                if df is not None and len(df) > 0:
                    for _, row in df.head(15).iterrows():
                        # 注意：本分支与 Tushare 分支语义不同 —— 这里是「持股排行 +
                        # 今日增持估计金额」，且 AKShare 该列单位未经核实，
                        # 因此不做亿元换算（避免像原 change_pct 那样造出假语义），
                        # 只保留原始值并显式标注。消费方用 top_stocks_source 区分。
                        # 缺失一律 None，不用 0 兜底（0 会被当成真实测得的零）。
                        result["top_stocks"].append({
                            "code": str(row.get("代码", "")),
                            "name": str(row.get("名称", "")),
                            "holding_value": _num_or_none(row.get("持股市值")),
                            "net_amount_yi": None,  # 单位未核实，不换算
                            "hold_change_est_raw": _num_or_none(row.get("今日增持估计金额")),
                            "amount_semantic": "今日增持估计金额（AKShare 原始值，单位未核实）",
                            "amount_unit": "",
                        })
                    result["top_stocks_source"] = "akshare"
                    # AKShare「今日排行」本身是按持股/增持排的，但我们未核实排序依据，
                    # 标为 unverified，避免下游当成"按净买入额排名"。
                    result["top_stocks_ranked_by"] = "akshare_today_rank_unverified"
                    print(f"[ALT] 北向 Top 持股 from AKShare (Tushare hsgt_top10 unavailable)")
            except Exception:
                pass
        except Exception as e:
            result["error"] = str(e)

    # 信号判断：净流入不可得，只描述成交额活跃度（禁止出现流入/流出措辞）
    if result.get("net_flow_available"):
        # 数据源恢复日频净买入披露后才会走到这里
        nf5 = result.get("net_flow_5d")
        if isinstance(nf5, (int, float)):
            result["signal"] = f"5日净流入 {nf5:+.1f} 亿"
    elif result.get("available") and result.get("turnover_today") is not None:
        t_trend = result.get("turnover_trend", "平稳")
        icon = {"显著放量": "🔵", "温和放量": "🔵", "平稳": "⚪",
                "温和缩量": "⚪", "显著缩量": "⚪"}.get(t_trend, "⚪")
        rng = result.get("turnover_5d_range", "")
        rng_txt = f"，{rng}" if rng else ""
        result["signal"] = (
            f"{icon} 北向成交额 {result['turnover_today']:.0f} 亿（{t_trend}"
            f"，5日均 {result.get('turnover_avg_5d')} 亿 vs 20日均 "
            f"{result.get('turnover_avg_20d')} 亿{rng_txt}）；"
            f"净买入方向数据不可得：2024-08-19 起交易所改为按季度披露"
        )
    else:
        result["signal"] = "⚪ 北向数据暂不可得（成交额与净买入均未取到）"

    result = _clean_nan(result)
    _alt_cache.set(cache_key, result)
    return result


# ============================================================
# 2. 融资融券
# ============================================================

def get_margin_detail() -> dict:
    """融资融券余额趋势 + 信号

    策略：Tushare 主 + AKShare 降级
    """
    cache_key = "margin_detail"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"trend": [], "signal": "", "latest": {}, "source": "unknown"}

    # 策略：Tushare 主
    try:
        from services.tushare_fallback import TusharePrimary
        tp = TusharePrimary.instance()
        margin_data = tp.get_margin_detail()
        if margin_data and len(margin_data) > 0:
            # 取最近 30 条
            for item in margin_data[:30]:
                result["trend"].append({
                    "date": item.get("trade_date", ""),
                    "margin_buy": item.get("margin_buy", 0) / 1e8,  # 转为亿元
                    "margin_balance": item.get("margin_balance", 0) / 1e8,
                    "short_sell": item.get("short_balance", 0),
                })

            if len(result["trend"]) >= 2:
                latest = result["trend"][-1]
                prev = result["trend"][-2]
                result["latest"] = latest
                balance_change = latest["margin_balance"] - prev["margin_balance"]
                if balance_change > 50:
                    result["signal"] = "🟢 融资余额大增（>50亿），杠杆资金看多"
                elif balance_change > 0:
                    result["signal"] = "🟡 融资余额小增，杠杆情绪温和"
                elif balance_change > -50:
                    result["signal"] = "🟡 融资余额小降，杠杆情绪降温"
                else:
                    result["signal"] = "🔴 融资余额骤降（<-50亿），杠杆资金撤退"

            result["source"] = "tushare"
            print(f"[ALT] 融资融券 from Tushare: {len(result['trend'])} 条")
    except Exception as e:
        print(f"[ALT] 融资融券 Tushare failed: {e}")

    # 降级：AKShare
    if len(result["trend"]) == 0:
        try:
            from infra.data_source.alt.flows import get_margin_sse
            df = get_margin_sse()
            if df is not None and len(df) > 0:
                df = df.tail(30)
                for _, row in df.iterrows():
                    result["trend"].append({
                        "date": str(row.get("信用交易日期", "")),
                        "margin_buy": float(row.get("融资买入额(元)", 0)) / 1e8 if not _is_nan(row.get("融资买入额(元)", 0)) else 0,
                        "margin_balance": float(row.get("融资余额(元)", 0)) / 1e8 if not _is_nan(row.get("融资余额(元)", 0)) else 0,
                        "short_sell": float(row.get("融券卖出量(股)", 0)) if not _is_nan(row.get("融券卖出量(股)", 0)) else 0,
                    })

                if len(result["trend"]) >= 2:
                    latest = result["trend"][-1]
                    prev = result["trend"][-2]
                    result["latest"] = latest
                    balance_change = latest["margin_balance"] - prev["margin_balance"]
                    if balance_change > 50:
                        result["signal"] = "🟢 融资余额大增（>50亿），杠杆资金看多"
                    elif balance_change > 0:
                        result["signal"] = "🟡 融资余额小增，杠杆情绪温和"
                    elif balance_change > -50:
                        result["signal"] = "🟡 融资余额小降，杠杆情绪降温"
                    else:
                        result["signal"] = "🔴 融资余额骤降（<-50亿），杠杆资金撤退"

                result["source"] = "akshare"
                print(f"[ALT] 融资融券 from AKShare (Tushare unavailable)")
        except Exception as e:
            result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache.set(cache_key, result, ttl=_ALT_CACHE_TTL)
    return result


# ============================================================
# 3. 龙虎榜
# ============================================================

def get_dragon_tiger() -> dict:
    """龙虎榜数据 — 游资/机构买卖"""
    cache_key = "dragon_tiger"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"records": [], "inst_buy": [], "inst_sell": []}

    try:
        from infra.data_source.macro.indicators import get_lhb_detail
        df = get_lhb_detail()
        if df is not None and len(df) > 0:
            for _, row in df.head(30).iterrows():
                record = {
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "reason": str(row.get("上榜原因", "")),
                    "buy_amount": float(row.get("买入总额", 0)) / 1e4 if not _is_nan(row.get("买入总额", 0)) else 0,
                    "sell_amount": float(row.get("卖出总额", 0)) / 1e4 if not _is_nan(row.get("卖出总额", 0)) else 0,
                    "net": float(row.get("净额", 0)) / 1e4 if not _is_nan(row.get("净额", 0)) else 0,
                }
                result["records"].append(record)

                # 分离机构买入/卖出
                if record["net"] > 0:
                    result["inst_buy"].append(record)
                else:
                    result["inst_sell"].append(record)

    except Exception as e:
        result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache.set(cache_key, result, ttl=_ALT_CACHE_TTL)
    return result


# ============================================================
# 4. 大宗交易
# ============================================================

def get_block_trade() -> dict:
    """大宗交易数据

    策略：Tushare 主 + AKShare 降级
    """
    cache_key = "block_trade"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"records": [], "premium_count": 0, "discount_count": 0, "source": "unknown"}

    # 策略：Tushare 主
    try:
        from services.tushare_fallback import TusharePrimary
        tp = TusharePrimary.instance()
        block_data = tp.get_block_trade()
        if block_data and len(block_data) > 0:
            for item in block_data[:30]:
                trade = {
                    "code": item.get("ts_code", ""),
                    "name": "",  # Tushare 无股票名称，需额外查询
                    "amount": item.get("amount", 0) / 1e4,  # 转为万元
                    "premium": 0,  # Tushare 无溢价率，需计算
                    "count": 1,
                    "source": "tushare",
                }
                result["records"].append(trade)
            result["source"] = "tushare"
            print(f"[ALT] 大宗交易 from Tushare: {len(result['records'])} 条")
    except Exception as e:
        print(f"[ALT] 大宗交易 Tushare failed: {e}")

    # 降级：AKShare
    if len(result["records"]) == 0:
        try:
            from infra.data_source.alt.flows import get_block_trade_daily
            df = get_block_trade_daily()
            if df is not None and len(df) > 0:
                for _, row in df.head(30).iterrows():
                    trade = {
                        "code": str(row.get("证券代码", "")),
                        "name": str(row.get("证券简称", "")),
                        "amount": float(row.get("成交总额", 0)) / 1e4 if not _is_nan(row.get("成交总额", 0)) else 0,
                        "premium": float(row.get("溢价率", 0)) if not _is_nan(row.get("溢价率", 0)) else 0,
                        "count": int(row.get("成交笔数", 0)) if not _is_nan(row.get("成交笔数", 0)) else 0,
                        "source": "akshare",
                    }
                    result["records"].append(trade)
                    if trade["premium"] > 0:
                        result["premium_count"] += 1
                    else:
                        result["discount_count"] += 1
                result["source"] = "akshare"
                print(f"[ALT] 大宗交易 from AKShare (Tushare unavailable)")
        except Exception as e:
            result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache.set(cache_key, result, ttl=_ALT_CACHE_TTL)
    return result


# ============================================================
# 5. 股东增减持
# ============================================================

def get_insider_trading() -> dict:
    """重要股东增减持"""
    cache_key = "insider_trading"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"increases": [], "decreases": [], "signal": ""}

    try:
        from infra.data_source.alt.flows import get_insider_trade_xq
        df = get_insider_trade_xq()
        if df is not None and len(df) > 0:
            for _, row in df.head(30).iterrows():
                record = {
                    "code": str(row.get("股票代码", row.get("symbol", ""))),
                    "name": str(row.get("股票名称", row.get("name", ""))),
                    "holder": str(row.get("变动人", row.get("holder_name", ""))),
                    "change_type": str(row.get("变动方向", row.get("direction", ""))),
                    "shares": str(row.get("变动股数", row.get("volume", ""))),
                }
                if "增" in record["change_type"]:
                    result["increases"].append(record)
                else:
                    result["decreases"].append(record)

            inc = len(result["increases"])
            dec = len(result["decreases"])
            if inc > dec * 2:
                result["signal"] = "🟢 增持远多于减持，内部人看好"
            elif dec > inc * 2:
                result["signal"] = "🔴 减持远多于增持，内部人减仓"
            else:
                result["signal"] = "🟡 增减持基本平衡"

    except Exception as e:
        result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache.set(cache_key, result, ttl=_ALT_CACHE_TTL)
    return result


# ============================================================
# 6. 行业ETF资金流
# ============================================================

def get_sector_flow() -> dict:
    """行业板块资金流向"""
    cache_key = "sector_flow"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"inflow": [], "outflow": []}

    try:
        from infra.data_source.alt.flows import get_sector_fund_flow_rank
        df = get_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                sector = {
                    "name": str(row.get("名称", "")),
                    "net_flow": float(row.get("今日主力净流入-净额", 0)) / 1e8 if not _is_nan(row.get("今日主力净流入-净额", 0)) else 0,
                    "change_pct": float(row.get("今日涨跌幅", 0)) if not _is_nan(row.get("今日涨跌幅", 0)) else 0,
                }
                if sector["net_flow"] > 0:
                    result["inflow"].append(sector)
                else:
                    result["outflow"].append(sector)

            result["inflow"].sort(key=lambda x: -x["net_flow"])
            result["outflow"].sort(key=lambda x: x["net_flow"])
            result["inflow"] = result["inflow"][:10]
            result["outflow"] = result["outflow"][:10]

    except Exception as e:
        result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache.set(cache_key, result, ttl=_ALT_CACHE_TTL)
    return result


# ============================================================
# 综合仪表盘
# ============================================================

def get_alt_data_dashboard() -> dict:
    """另类数据综合仪表盘"""
    cache_key = "alt_dashboard"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    dashboard = {
        "northbound": {},
        "margin": {},
        "dragon_tiger": {},
        "block_trade": {},
        "insider": {},
        "sector_flow": {},
        "overall_signal": "",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 并行获取
    tasks = {
        "northbound": get_northbound_flow_detail,
        "margin": get_margin_detail,
        "dragon_tiger": get_dragon_tiger,
        "block_trade": get_block_trade,
        "insider": get_insider_trading,
        "sector_flow": get_sector_flow,
    }

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fn): key for key, fn in tasks.items()}
        for f in as_completed(futures):
            key = futures[f]
            try:
                dashboard[key] = f.result()
            except Exception as e:
                dashboard[key] = {"error": str(e)}

    # 综合信号
    signals = []
    for key in ["northbound", "margin", "insider"]:
        sig = dashboard.get(key, {}).get("signal", "")
        if sig:
            signals.append(sig)

    bullish = sum(1 for s in signals if "🟢" in s)
    bearish = sum(1 for s in signals if "🔴" in s)
    if bullish > bearish:
        dashboard["overall_signal"] = f"📊 综合偏多（{bullish}项看多 vs {bearish}项看空）"
    elif bearish > bullish:
        dashboard["overall_signal"] = f"📊 综合偏空（{bearish}项看空 vs {bullish}项看多）"
    else:
        dashboard["overall_signal"] = "📊 综合中性，多空信号均衡"

    _alt_cache.set(cache_key, dashboard)
    return dashboard


def _is_nan(v):
    """检查是否为 NaN"""
    import math
    try:
        return v is None or (isinstance(v, float) and math.isnan(v))
    except Exception:
        return False
