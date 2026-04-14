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

_alt_cache = {}
_ALT_CACHE_TTL = 1800  # 30 分钟


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
    """北向资金实时流向 + 近期趋势"""
    cache_key = "nb_flow_detail"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

    result = {"today": {}, "trend": [], "top_stocks": [], "signal": ""}

    try:
        import akshare as ak

        # 今日实时
        try:
            df = ak.stock_hsgt_north_net_flow_in_em()
            if df is not None and len(df) > 0:
                latest = df.tail(20)
                result["trend"] = [
                    {
                        "date": str(row.get("date", row.get("日期", ""))),
                        "net_flow": float(row.get("value", row.get("当日净流入", 0))) if not _is_nan(row.get("value", row.get("当日净流入", 0))) else 0
                    }
                    for _, row in latest.iterrows()
                ]
        except Exception:
            pass

        # 北向持股 top
        try:
            df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
            if df is not None and len(df) > 0:
                for _, row in df.head(15).iterrows():
                    result["top_stocks"].append({
                        "code": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "holding_value": float(row.get("持股市值", 0)) if not _is_nan(row.get("持股市值", 0)) else 0,
                        "change_pct": float(row.get("今日增持估计金额", 0)) if not _is_nan(row.get("今日增持估计金额", 0)) else 0,
                    })
        except Exception:
            pass

        # 信号判断
        if result["trend"] and len(result["trend"]) >= 5:
            recent_5d = sum(d["net_flow"] for d in result["trend"][-5:])
            if recent_5d > 100:
                result["signal"] = "🟢 强势流入（5日 > 100亿），外资看多"
            elif recent_5d > 0:
                result["signal"] = "🟡 温和流入，外资偏乐观"
            elif recent_5d > -50:
                result["signal"] = "🟡 小幅流出，外资观望"
            else:
                result["signal"] = "🔴 大幅流出（5日 < -50亿），外资避险"

    except Exception as e:
        result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 2. 融资融券
# ============================================================

def get_margin_detail() -> dict:
    """融资融券余额趋势 + 信号"""
    cache_key = "margin_detail"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

    result = {"trend": [], "signal": "", "latest": {}}

    try:
        import akshare as ak
        df = ak.stock_margin_sse()
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

    except Exception as e:
        result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 3. 龙虎榜
# ============================================================

def get_dragon_tiger() -> dict:
    """龙虎榜数据 — 游资/机构买卖"""
    cache_key = "dragon_tiger"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

    result = {"records": [], "inst_buy": [], "inst_sell": []}

    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em()
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
    _alt_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 4. 大宗交易
# ============================================================

def get_block_trade() -> dict:
    """大宗交易数据"""
    cache_key = "block_trade"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

    result = {"records": [], "premium_count": 0, "discount_count": 0}

    try:
        import akshare as ak
        df = ak.stock_dzjy_mrtj()
        if df is not None and len(df) > 0:
            for _, row in df.head(30).iterrows():
                trade = {
                    "code": str(row.get("证券代码", "")),
                    "name": str(row.get("证券简称", "")),
                    "amount": float(row.get("成交总额", 0)) / 1e4 if not _is_nan(row.get("成交总额", 0)) else 0,
                    "premium": float(row.get("溢价率", 0)) if not _is_nan(row.get("溢价率", 0)) else 0,
                    "count": int(row.get("成交笔数", 0)) if not _is_nan(row.get("成交笔数", 0)) else 0,
                }
                result["records"].append(trade)
                if trade["premium"] > 0:
                    result["premium_count"] += 1
                else:
                    result["discount_count"] += 1

    except Exception as e:
        result["error"] = str(e)

    result = _clean_nan(result)
    _alt_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 5. 股东增减持
# ============================================================

def get_insider_trading() -> dict:
    """重要股东增减持"""
    cache_key = "insider_trading"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

    result = {"increases": [], "decreases": [], "signal": ""}

    try:
        import akshare as ak
        df = ak.stock_inner_trade_xq()
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
    _alt_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 6. 行业ETF资金流
# ============================================================

def get_sector_flow() -> dict:
    """行业板块资金流向"""
    cache_key = "sector_flow"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

    result = {"inflow": [], "outflow": []}

    try:
        import akshare as ak
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
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
    _alt_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 综合仪表盘
# ============================================================

def get_alt_data_dashboard() -> dict:
    """另类数据综合仪表盘"""
    cache_key = "alt_dashboard"
    now = time.time()
    if cache_key in _alt_cache and now - _alt_cache[cache_key]["ts"] < _ALT_CACHE_TTL:
        return _alt_cache[cache_key]["data"]

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

    _alt_cache[cache_key] = {"data": dashboard, "ts": now}
    return dashboard


def _is_nan(v):
    """检查是否为 NaN"""
    import math
    try:
        return v is None or (isinstance(v, float) and math.isnan(v))
    except Exception:
        return False
