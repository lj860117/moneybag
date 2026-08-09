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


# ============================================================
# 1. 北向资金
# ============================================================

def get_northbound_flow_detail() -> dict:
    """北向资金实时流向 + 近期趋势

    策略：Tushare 主（moneyflow_hsgt 日级别明细） + AKShare 降级（top 持股）
    """
    cache_key = "nb_flow_detail"
    now = time.time()
    cached = _alt_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"today": {}, "trend": [], "top_stocks": [], "signal": "", "source": "unknown"}

    # 策略：Tushare 主（使用 tushare_data.py 的 get_northbound_flow）
    try:
        from services.tushare_data import is_configured, get_northbound_flow
        if is_configured():
            nb_data = get_northbound_flow(days=30)
            if nb_data and nb_data.get("available"):
                # Tushare 返回日级别明细（daily_flows）
                if nb_data.get("daily_flows"):
                    result["trend"] = nb_data["daily_flows"]
                    result["source"] = "tushare"
                    result["data_date"] = nb_data.get("data_date", "")
                    result["flow_5d_range"] = nb_data.get("flow_5d_range", "")
                    print(f"[ALT] 北向资金 from Tushare: {len(result['trend'])}天明细, "
                          f"today={nb_data['net_flow_today']}亿, 5d={nb_data['net_flow_5d']}亿")
                else:
                    print(f"[ALT] 北向资金 Tushare 无 daily_flows，降级")
    except Exception as e:
        print(f"[ALT] 北向资金 Tushare failed: {e}")

    # 补充 top_stocks（Tushare hsgt_top10 或 AKShare 降级）
    if len(result["top_stocks"]) == 0:
        try:
            # 尝试 Tushare hsgt_top10（沪股通+深股通 Top10）
            from services.tushare_data import is_configured, _call_tushare
            if is_configured():
                # 沪股通 Top10
                rows_h = _call_tushare(
                    "hsgt_top10",
                    {"trade_date": result.get("data_date", ""), "market_type": "1"},
                    "ts_code,name,net_amount,rank"
                )
                # 深股通 Top10
                rows_s = _call_tushare(
                    "hsgt_top10",
                    {"trade_date": result.get("data_date", ""), "market_type": "3"},
                    "ts_code,name,net_amount,rank"
                )
                all_rows = (rows_h or []) + (rows_s or [])
                if all_rows:
                    # 按 net_amount 降序
                    all_rows.sort(key=lambda x: float(x.get("net_amount", 0) or 0), reverse=True)
                    for r in all_rows[:15]:
                        code = r.get("ts_code", "")
                        result["top_stocks"].append({
                            "code": code.split(".")[0] if "." in code else code,
                            "name": str(r.get("name", "")),
                            "holding_value": 0,  # Tushare 不提供持股市值，用 0 占位
                            "change_pct": round(float(r.get("net_amount", 0) or 0) / 10000, 2),  # 万元→亿元（粗略）
                        })
                    result["top_stocks_source"] = "tushare_hsgt_top10"
                    print(f"[ALT] 北向 Top10 from Tushare: {len(result['top_stocks'])} 只")
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
                        result["top_stocks"].append({
                            "code": str(row.get("代码", "")),
                            "name": str(row.get("名称", "")),
                            "holding_value": float(row.get("持股市值", 0)) if not _is_nan(row.get("持股市值", 0)) else 0,
                            "change_pct": float(row.get("今日增持估计金额", 0)) if not _is_nan(row.get("今日增持估计金额", 0)) else 0,
                        })
                    result["top_stocks_source"] = "akshare"
                    print(f"[ALT] 北向 Top 持股 from AKShare (Tushare hsgt_top10 unavailable)")
            except Exception:
                pass
        except Exception as e:
            result["error"] = str(e)

    # 信号判断（基于趋势数据）
    if result["trend"] and len(result["trend"]) >= 5:
        recent_5d = sum(d["net_flow"] for d in result["trend"][-5:])
        # 单位：亿元
        if recent_5d > 50:
            result["signal"] = "🟢 强势流入（5日 > 50亿），外资看多"
        elif recent_5d > 10:
            result["signal"] = "🟡 温和流入，外资偏乐观"
        elif recent_5d > -30:
            result["signal"] = "🟡 小幅流出，外资观望"
        else:
            result["signal"] = "🔴 大幅流出（5日 < -30亿），外资避险"

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
