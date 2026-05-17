"""
钱袋子 — Tushare Pro 数据层
独立 service，提供稳定的 PE/PB/财务/北向资金/SHIBOR 数据

数据源：Tushare Pro API（5000 积分）
接口：daily_basic + fina_indicator + daily + moneyflow_hsgt + shibor 等
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "tushare_data",
    "scope": "public",
    "input": [],
    "output": "tushare_data",
    "cost": "cpu",
    "tags": ['Tushare', 'PE', 'PB', '财务', '北向资金', 'SHIBOR'],
    "description": "Tushare Pro数据层(5000积分)：PE/PB/财务/北向资金/SHIBOR",
    "layer": "data",
    "priority": 1,
}
import os
import time
import json
import urllib.request
from infra.cache import MemoryCache

def _get_token() -> str:
    """实时读取 Token（避免 import 时缓存空值）"""
    return os.getenv("TUSHARE_TOKEN", "")


def is_configured() -> bool:
    return bool(_get_token())


_TUSHARE_URL = "http://api.tushare.pro"

# 缓存
_TS_CACHE_TTL = 3600  # 1 小时
_ts_cache = MemoryCache(default_ttl=_TS_CACHE_TTL)


def _call_tushare(api_name: str, params: dict, fields: str = "") -> list:
    """统一 Tushare API 调用"""
    token = _get_token()
    if not token:
        return []

    cache_key = f"{api_name}_{json.dumps(params, sort_keys=True)}_{fields}"
    now = time.time()
    cached = _ts_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        payload = json.dumps({
            "api_name": api_name,
            "token": token,
            "params": params,
            "fields": fields,
        }).encode("utf-8")
        req = urllib.request.Request(
            _TUSHARE_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())

        if resp.get("data") and resp["data"].get("items"):
            # 转换为 dict 列表
            columns = resp["data"]["fields"]
            items = resp["data"]["items"]
            result = [dict(zip(columns, row)) for row in items]
            _ts_cache.set(cache_key, result, ttl=_TS_CACHE_TTL)
            return result
        return []
    except Exception as e:
        print(f"[TUSHARE] {api_name} failed: {e}")
        return []


def _code_to_ts(code: str) -> str:
    """股票代码转 Tushare 格式（600519 → 600519.SH）"""
    code = code.strip()
    if "." in code:
        return code
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


# ============================================================
# 1. 估值数据（PE/PB/总市值/股息率）
# ============================================================

def get_valuation(code: str) -> dict:
    """获取最新估值数据"""
    ts_code = _code_to_ts(code)
    rows = _call_tushare(
        "daily_basic",
        {"ts_code": ts_code, "limit": 1},
        "ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv,turnover_rate",
    )
    if not rows:
        return {"available": False}

    r = rows[0]
    return {
        "available": True,
        "code": code,
        "pe_ttm": r.get("pe_ttm"),
        "pb": r.get("pb"),
        "ps_ttm": r.get("ps_ttm"),
        "dividend_yield": r.get("dv_ttm"),
        "total_mv": round(r["total_mv"] / 10000, 2) if r.get("total_mv") else None,  # 万元→亿元
        "circ_mv": round(r["circ_mv"] / 10000, 2) if r.get("circ_mv") else None,
        "turnover_rate": r.get("turnover_rate"),
        "trade_date": r.get("trade_date"),
        "source": "tushare",
    }


# ============================================================
# 2. 财务指标（ROE/毛利率/净利率/负债率/现金流/EPS/营收增速）
# ============================================================

def get_financials(code: str) -> dict:
    """获取核心财务指标（最近一期）"""
    ts_code = _code_to_ts(code)
    rows = _call_tushare(
        "fina_indicator",
        {"ts_code": ts_code, "limit": 1},
        "ts_code,ann_date,end_date,roe,roe_waa,grossprofit_margin,netprofit_margin,"
        "debt_to_assets,ocfps,eps,revenue_ps,profit_to_gr,"
        "netprofit_yoy,or_yoy,equity_yoy,currentratio",
    )
    if not rows:
        return {"available": False, "source": "tushare"}

    r = rows[0]
    return {
        "available": True,
        "code": code,
        "roe": r.get("roe") or r.get("roe_waa"),
        "eps": r.get("eps"),
        "gross_margin": r.get("grossprofit_margin"),
        "net_margin": r.get("netprofit_margin"),
        "debt_ratio": r.get("debt_to_assets"),
        "cash_flow_per_share": r.get("ocfps"),
        "netprofit_yoy": r.get("netprofit_yoy"),
        "revenue_yoy": r.get("or_yoy"),
        "current_ratio": r.get("currentratio"),
        "profit_to_revenue": r.get("profit_to_gr"),
        "ann_date": r.get("ann_date"),
        "end_date": r.get("end_date"),
        "source": "tushare",
    }


# ============================================================
# 3. 批量估值（选股用，一次拉多只）
# ============================================================

def get_valuation_batch(trade_date: str = "") -> list:
    """批量获取全市场估值数据（选股用）
    trade_date: YYYYMMDD 格式，空则取最近交易日
    """
    params = {"limit": 5000}
    if trade_date:
        params["trade_date"] = trade_date

    rows = _call_tushare(
        "daily_basic",
        params,
        "ts_code,trade_date,pe_ttm,pb,dv_ttm,total_mv,turnover_rate",
    )
    return rows


# ============================================================
# 4. 历史估值（回测用）
# ============================================================

def get_valuation_history(code: str, start_date: str = "", end_date: str = "") -> list:
    """获取历史估值序列（PE/PB 时序数据）"""
    ts_code = _code_to_ts(code)
    params = {"ts_code": ts_code}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    rows = _call_tushare(
        "daily_basic",
        params,
        "ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate",
    )
    return rows


# ============================================================
# 5. 大股东增减持（signal_scout P0 数据源）
# ============================================================

def get_holder_trades(start_date: str = "", end_date: str = "") -> list:
    """获取近期大股东增减持记录"""
    from datetime import datetime, timedelta
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    rows = _call_tushare(
        "stk_holdertrade",
        {"start_date": start_date, "end_date": end_date},
        "ts_code,ann_date,holder_name,holder_type,change_type,change_vol,change_amount,after_share,after_ratio",
    )
    return rows[:50]


# ============================================================
# 6. 股权质押统计（风控因子）
# ============================================================

def get_pledge_stat(code: str = "") -> list:
    """获取股权质押统计
    code: 空=全市场最新, 有值=单只个股
    """
    params = {}
    if code:
        params["ts_code"] = _code_to_ts(code)

    rows = _call_tushare(
        "pledge_stat",
        params,
        "ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio",
    )
    return rows[:100]


# ============================================================
# 7. 限售股解禁（signal_scout P0 数据源）
# ============================================================

def get_upcoming_unlocks(days: int = 30) -> list:
    """获取未来N天的限售股解禁计划"""
    from datetime import datetime, timedelta
    start = datetime.now().strftime("%Y%m%d")
    end = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")

    rows = _call_tushare(
        "share_float",
        {"start_date": start, "end_date": end},
        "ts_code,float_date,float_share,float_ratio,holder_name,share_type",
    )
    # 按解禁比例降序
    return sorted(rows, key=lambda x: x.get("float_ratio", 0) or 0, reverse=True)[:30]


# ============================================================
# 8. 分红送转（价值因子增强）
# ============================================================

def get_dividend(code: str) -> list:
    """获取个股分红送转记录"""
    ts_code = _code_to_ts(code)
    rows = _call_tushare(
        "dividend",
        {"ts_code": ts_code},
        "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,pay_date",
    )
    return rows[:10]


# ============================================================
# 9. ST/*ST 标记（风控排除 + 选股过滤）
# ============================================================

def get_st_stocks() -> list:
    """获取当前所有 ST/*ST 股票列表"""
    cache_key = "st_stocks"
    cached = _ts_cache.get(cache_key)
    if cached is not None:
        return cached

    rows = _call_tushare(
        "namechange",
        {"limit": 200},
        "ts_code,name,start_date,end_date,change_reason",
    )
    # 筛选当前仍为 ST 的
    st_list = [r for r in rows if r.get("name", "").startswith(("ST", "*ST")) and not r.get("end_date")]
    _ts_cache.set(cache_key, st_list, ttl=86400)
    return st_list


def is_st(code: str) -> bool:
    """判断个股是否为 ST"""
    ts_code = _code_to_ts(code)
    return any(s.get("ts_code") == ts_code for s in get_st_stocks())


# ============================================================
# 10. 公告全文摘要（signal_scout 信号源）
# ============================================================

def get_announcements(code: str = "", start_date: str = "", end_date: str = "", limit: int = 20) -> list:
    """获取公告列表"""
    from datetime import datetime, timedelta
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    params = {"start_date": start_date, "end_date": end_date}
    if code:
        params["ts_code"] = _code_to_ts(code)

    rows = _call_tushare(
        "anns",
        params,
        "ts_code,ann_date,title,content,url",
    )
    return rows[:limit]


# ============================================================
# 11. 研报摘要（signal_scout 信号源）
# ============================================================

def get_research_reports(code: str = "", limit: int = 10) -> list:
    """获取最新研报"""
    params = {"limit": limit}
    if code:
        params["ts_code"] = _code_to_ts(code)

    rows = _call_tushare(
        "report_rc",
        params,
        "ts_code,report_date,report_title,author,org_name,rating,abstract",
    )
    return rows


# ============================================================
# 12. 北向资金流向（moneyflow_hsgt — 替代 AKShare 断层数据）
# ============================================================

def get_northbound_flow(days: int = 30) -> dict:
    """获取北向资金净流入（Tushare moneyflow_hsgt）

    原理：moneyflow_hsgt 返回每日沪股通/深股通的「买入成交额」和「卖出成交额」，
    净流入 = 买入 - 卖出（单位：万元）。

    相比 AKShare stock_hsgt_hist_em（2024-08 后全 NaN），Tushare 数据持续更新到最新交易日。

    Returns:
        dict: {
            "net_flow_today": float,  # 今日净流入（亿元）
            "net_flow_5d": float,     # 近5日累计净流入
            "net_flow_20d": float,    # 近20日累计净流入
            "trend": str,             # 大幅流入/净流入/中性/净流出/大幅流出
            "available": bool,
            "source": "tushare",
            "data_date": str,         # 最新数据日期
        }
    """
    from datetime import datetime, timedelta

    result = {
        "net_flow_today": 0, "net_flow_5d": 0, "net_flow_20d": 0,
        "trend": "中性", "available": False, "source": "tushare",
    }

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")  # 多取几天防节假日

    rows = _call_tushare(
        "moneyflow_hsgt",
        {"start_date": start_date, "end_date": end_date},
        "trade_date,ggt_ss,ggt_sz,hgt,sgt,north_money,south_money",
    )

    if not rows:
        print("[TUSHARE-NORTH] moneyflow_hsgt 返回空")
        return result

    # 按日期升序排列
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""))

    # 关键：moneyflow_hsgt 的 north_money/hgt/sgt 都是【历史累计值】（百万元）
    # 当日净流入 = 当天累计 - 前一天累计
    daily_flows = []
    for i in range(1, len(rows)):
        trade_date = rows[i].get("trade_date", "")
        # north_money 是累计北向净买入（百万元）
        nm_today = float(rows[i].get("north_money") or 0)
        nm_prev = float(rows[i-1].get("north_money") or 0)

        if nm_today > 0 and nm_prev > 0:
            # 当日净流入 = 今天累计 - 昨天累计（百万元）
            net_flow_million = nm_today - nm_prev
        else:
            # 降级：hgt + sgt 差值
            hgt_today = float(rows[i].get("hgt") or 0)
            hgt_prev = float(rows[i-1].get("hgt") or 0)
            sgt_today = float(rows[i].get("sgt") or 0)
            sgt_prev = float(rows[i-1].get("sgt") or 0)
            net_flow_million = (hgt_today - hgt_prev) + (sgt_today - sgt_prev)

        daily_flows.append({"date": trade_date, "net_flow_million": net_flow_million})

    if len(daily_flows) < 5:
        print(f"[TUSHARE-NORTH] 数据不足: {len(daily_flows)}天")
        return result

    # 单位转换：百万元 → 亿元（除以 100）
    def million_to_yi(v):
        return round(v / 100, 2)

    result["net_flow_today"] = million_to_yi(daily_flows[-1]["net_flow_million"])
    result["net_flow_5d"] = million_to_yi(sum(d["net_flow_million"] for d in daily_flows[-5:]))
    result["net_flow_20d"] = million_to_yi(sum(d["net_flow_million"] for d in daily_flows[-20:]))
    result["data_date"] = daily_flows[-1]["date"]
    result["available"] = True

    # 趋势判断（基于5日累计）
    flow_5d = result["net_flow_5d"]
    if flow_5d > 50:
        result["trend"] = "大幅流入"
    elif flow_5d > 10:
        result["trend"] = "净流入"
    elif flow_5d < -50:
        result["trend"] = "大幅流出"
    elif flow_5d < -10:
        result["trend"] = "净流出"
    else:
        result["trend"] = "中性"

    print(f"[TUSHARE-NORTH] date={result['data_date']}, "
          f"today={result['net_flow_today']}亿, 5d={result['net_flow_5d']}亿, "
          f"20d={result['net_flow_20d']}亿, trend={result['trend']}")

    # 口径警告：2024年8月后交易所取消每日北向实时披露，各平台口径有分歧
    result["note"] = "口径:Tushare累计差值法，与东方财富/同花顺可能有差异"

    return result


# ============================================================
# 13. SHIBOR 利率（替代 AKShare rate_interbank）
# ============================================================

def get_shibor_rate(days: int = 30) -> dict:
    """获取 SHIBOR 利率（Tushare shibor 接口）

    相比 AKShare rate_interbank（东财接口不稳定），Tushare 的 shibor 数据更稳定。

    Returns:
        dict: {
            "overnight": float,   # 隔夜利率 (%)
            "one_week": float,    # 1周利率 (%)
            "one_month": float,   # 1月利率 (%)
            "trend": str,         # 流动性收紧/平稳/宽松
            "available": bool,
            "source": "tushare",
            "data_date": str,
        }
    """
    from datetime import datetime, timedelta

    result = {
        "overnight": 0, "one_week": 0, "one_month": 0,
        "trend": "中性", "available": False, "source": "tushare",
    }

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

    rows = _call_tushare(
        "shibor",
        {"start_date": start_date, "end_date": end_date},
        "date,on,1w,2w,1m,3m,6m,9m,1y",
    )

    if not rows:
        print("[TUSHARE-SHIBOR] shibor 返回空")
        return result

    # 按日期升序
    rows = sorted(rows, key=lambda x: x.get("date", ""))

    latest = rows[-1]
    result["overnight"] = round(float(latest.get("on", 0) or 0), 4)
    result["one_week"] = round(float(latest.get("1w", 0) or 0), 4)
    result["one_month"] = round(float(latest.get("1m", 0) or 0), 4)
    result["data_date"] = latest.get("date", "")
    result["available"] = True

    # 趋势判断：对比近5日均值
    if len(rows) >= 5:
        recent_on = [float(r.get("on", 0) or 0) for r in rows[-5:]]
        avg_5d = sum(recent_on) / 5
        current = result["overnight"]
        if current > avg_5d * 1.2:
            result["trend"] = "流动性收紧"
        elif current < avg_5d * 0.8:
            result["trend"] = "流动性宽松"
        else:
            result["trend"] = "流动性平稳"

    print(f"[TUSHARE-SHIBOR] date={result['data_date']}, "
          f"ON={result['overnight']}%, 1W={result['one_week']}%, "
          f"trend={result['trend']}")

    return result


# ============================================================
# 14. 融资融券（margin — 替代 AKShare 只有上交所的问题）
# ============================================================

def get_margin_data(days: int = 30) -> dict:
    """获取融资融券数据（Tushare margin 接口）

    相比 AKShare stock_margin_sse（只有上交所 ≈ 60%），Tushare 有沪+深+北全部数据。

    Returns:
        dict: {
            "margin_balance": float,    # 融资余额（亿元）
            "margin_change_5d": float,  # 5日变化百分比
            "rzmre": float,             # 融资买入额（亿元/日）
            "rqye": float,              # 融券余额（亿元）
            "trend": str,               # 杠杆快速上升/温和上升/温和下降/快速下降
            "available": bool,
            "source": "tushare",
            "data_date": str,
        }
    """
    from datetime import datetime, timedelta

    result = {
        "margin_balance": 0, "margin_change_5d": 0, "rzmre": 0, "rqye": 0,
        "trend": "中性", "available": False, "source": "tushare",
    }

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

    rows = _call_tushare(
        "margin",
        {"start_date": start_date, "end_date": end_date},
        "trade_date,exchange_id,rzye,rzmre,rzche,rqye,rzrqye",
    )

    if not rows:
        print("[TUSHARE-MARGIN] margin 返回空")
        return result

    # 按日期聚合（沪+深+北 合计）
    from collections import defaultdict
    daily_totals = defaultdict(lambda: {"rzye": 0, "rzmre": 0, "rqye": 0, "rzrqye": 0})
    for row in rows:
        d = row.get("trade_date", "")
        if not d:
            continue
        daily_totals[d]["rzye"] += float(row.get("rzye", 0) or 0)
        daily_totals[d]["rzmre"] += float(row.get("rzmre", 0) or 0)
        daily_totals[d]["rqye"] += float(row.get("rqye", 0) or 0)
        daily_totals[d]["rzrqye"] += float(row.get("rzrqye", 0) or 0)

    if not daily_totals:
        return result

    # 按日期排序
    sorted_dates = sorted(daily_totals.keys())
    if len(sorted_dates) < 6:
        return result

    latest = daily_totals[sorted_dates[-1]]
    prev_5d = daily_totals[sorted_dates[-6]] if len(sorted_dates) >= 6 else daily_totals[sorted_dates[0]]

    # 数据完整性检查：如果最新一天余额比前一天骤降 >30%，说明部分交易所数据未到
    # 此时用前一天数据代替，避免误判
    if len(sorted_dates) >= 2:
        prev_day = daily_totals[sorted_dates[-2]]
        if prev_day["rzye"] > 0 and latest["rzye"] / prev_day["rzye"] < 0.7:
            print(f"[TUSHARE-MARGIN] ⚠️ {sorted_dates[-1]} 数据不完整"
                  f"（{latest['rzye']/1e8:.0f}亿 vs 前日{prev_day['rzye']/1e8:.0f}亿），用前日数据")
            latest = prev_day
            sorted_dates[-1] = sorted_dates[-2]

    # 万元 → 亿元
    def to_yi(v):
        return round(v / 1e8, 2)

    current_balance = latest["rzye"]
    prev_balance = prev_5d["rzye"]
    change_pct = round((current_balance - prev_balance) / max(prev_balance, 1) * 100, 2)

    result["margin_balance"] = to_yi(current_balance)
    result["margin_change_5d"] = change_pct
    result["rzmre"] = to_yi(latest["rzmre"])
    result["rqye"] = to_yi(latest["rqye"])
    result["data_date"] = sorted_dates[-1]
    result["available"] = True

    # 趋势判断
    if change_pct > 3:
        result["trend"] = "杠杆快速上升"
    elif change_pct > 1:
        result["trend"] = "杠杆温和上升"
    elif change_pct < -3:
        result["trend"] = "杠杆快速下降"
    elif change_pct < -1:
        result["trend"] = "杠杆温和下降"
    else:
        result["trend"] = "杠杆平稳"

    print(f"[TUSHARE-MARGIN] date={result['data_date']}, "
          f"balance={result['margin_balance']}亿, 5d_change={change_pct:+.2f}%, "
          f"trend={result['trend']}")

    return result


# ============================================================
# 15. 基金份额数据（fund_share — 2000积分门槛）
# ============================================================

def get_fund_share(ts_code: str, days: int = 30) -> dict:
    """获取基金/ETF 每日份额变化"""
    from datetime import datetime, timedelta
    result = {"available": False, "source": "tushare"}
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
    rows = _call_tushare("fund_share", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
                         "ts_code,trade_date,fd_share,total_share,float_share")
    if not rows:
        return result
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""))
    if len(rows) < 2:
        return result
    latest = rows[-1]
    prev_5d = rows[-6] if len(rows) >= 6 else rows[0]
    current_share = float(latest.get("fd_share", 0) or latest.get("total_share", 0) or 0)
    prev_share = float(prev_5d.get("fd_share", 0) or prev_5d.get("total_share", 0) or 0)
    if current_share <= 0:
        return result
    change_pct = round((current_share - prev_share) / max(prev_share, 1) * 100, 2)
    result.update({"shares_latest": round(current_share / 1e8, 2), "shares_change_5d": round((current_share - prev_share) / 1e8, 2),
                   "shares_change_pct": change_pct, "data_date": latest.get("trade_date", ""), "available": True})
    result["trend"] = "份额大增" if change_pct > 5 else "温和增长" if change_pct > 1 else "大减" if change_pct < -5 else "温和减少" if change_pct < -1 else "稳定"
    print(f"[TUSHARE-SHARE] {ts_code}: {result['shares_latest']}亿份, 5d{change_pct:+.2f}%")
    return result


# =====================================================================
# 2026-04-19 A 阶段新增：股票日线 + 估值批量 + 基金全套
# =====================================================================

def get_daily_price(code: str, days: int = 120, adj: str = "qfq") -> list:
    """
    股票日线数据（Tushare pro_bar，自带前复权）
    code: 可以是 000001 也可以是 000001.SZ
    返回: [{trade_date, open, high, low, close, vol, pct_chg}]，按日期升序
    """
    from datetime import datetime, timedelta
    ts_code = _code_to_ts(code)
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 60)).strftime("%Y%m%d")
    rows = _call_tushare(
        "daily",
        {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
    )
    if not rows:
        return []
    # 升序
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""))
    return rows[-days:] if len(rows) > days else rows


def get_valuation_batch_map(trade_date: str = "") -> dict:
    """
    按日期拉全市场 PE/PB/市值/换手率（一次返回几千条）
    返回 {code(纯数字): {pe, pb, total_mv_亿, turnover, trade_date}}
    trade_date: YYYYMMDD，空则自动找最近交易日
    """
    from datetime import datetime, timedelta
    if not trade_date:
        # 往前找最多 7 天，确保拿到交易日
        for i in range(7):
            td = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
            rows = _call_tushare(
                "daily_basic",
                {"trade_date": td},
                "ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate_f",
            )
            if rows and len(rows) > 100:
                trade_date = td
                break
        else:
            return {}
    else:
        rows = _call_tushare(
            "daily_basic",
            {"trade_date": trade_date},
            "ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate_f",
        )
        if not rows:
            return {}

    result = {}
    for r in rows:
        ts_code = r.get("ts_code", "")
        if not ts_code:
            continue
        code = ts_code.split(".")[0]
        try:
            pe = r.get("pe_ttm")
            pb = r.get("pb")
            mv = r.get("total_mv")
            tr = r.get("turnover_rate_f")
            result[code] = {
                "pe": round(float(pe), 2) if pe is not None and 0 < float(pe) < 10000 else None,
                "pb": round(float(pb), 2) if pb is not None and 0 < float(pb) < 1000 else None,
                "total_mv": round(float(mv) / 10000, 1) if mv is not None else None,  # 转亿
                "turnover": round(float(tr), 2) if tr is not None else None,
                "trade_date": trade_date,
            }
        except (ValueError, TypeError):
            continue
    print(f"[TUSHARE-BATCH] daily_basic {trade_date}: {len(result)} 只股票")
    return result


# -------- 基金全套（5000 积分解锁）--------

def get_fund_nav(code: str, days: int = 60) -> dict:
    """
    基金净值（历史）
    code: 006547 或 006547.OF
    返回: {available, source, navs: [...], latest, unit_nav, accum_nav, change_pct}
    """
    from datetime import datetime, timedelta
    ts_code = code if "." in code else f"{code}.OF"
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
    rows = _call_tushare(
        "fund_nav",
        {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
    )
    if not rows:
        return {"available": False, "source": "tushare", "code": code}
    rows = sorted(rows, key=lambda x: x.get("nav_date", ""))
    latest = rows[-1]
    first = rows[0]
    try:
        chg = round((float(latest["unit_nav"]) - float(first["unit_nav"])) / float(first["unit_nav"]) * 100, 2)
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        chg = None
    return {
        "available": True,
        "source": "tushare",
        "code": code,
        "unit_nav": float(latest["unit_nav"]) if latest.get("unit_nav") else None,
        "accum_nav": float(latest["accum_nav"]) if latest.get("accum_nav") else None,
        "nav_date": latest.get("nav_date", ""),
        "change_pct": chg,
        "navs": rows,
    }


def get_fund_manager(code: str) -> dict:
    """基金经理信息"""
    ts_code = code if "." in code else f"{code}.OF"
    rows = _call_tushare(
        "fund_manager",
        {"ts_code": ts_code},
        "ts_code,ann_date,name,gender,birth_year,edu,nationality,begin_date,end_date,resume",
    )
    if not rows:
        return {"available": False, "source": "tushare"}
    # 取当前在任的（end_date 为空的）
    active = [r for r in rows if not r.get("end_date")]
    if not active:
        active = sorted(rows, key=lambda r: r.get("begin_date", ""))[-1:]
    return {
        "available": True,
        "source": "tushare",
        "managers": active[:5],  # 最多 5 位
    }


def get_fund_portfolio(code: str, period: str = "") -> dict:
    """
    基金持仓明细
    period: YYYYMMDD 格式的报告期；空则取最近一期
    """
    from datetime import datetime
    ts_code = code if "." in code else f"{code}.OF"
    params = {"ts_code": ts_code}
    if period:
        params["period"] = period
    rows = _call_tushare(
        "fund_portfolio",
        params,
        "ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio",
    )
    if not rows:
        return {"available": False, "source": "tushare"}
    # 同一个 end_date 内按 mkv 降序
    latest_date = max((r.get("end_date", "") for r in rows), default="")
    top_holdings = sorted(
        [r for r in rows if r.get("end_date") == latest_date],
        key=lambda r: float(r.get("mkv", 0) or 0),
        reverse=True,
    )[:10]
    return {
        "available": True,
        "source": "tushare",
        "end_date": latest_date,
        "top_holdings": top_holdings,
    }


def get_fund_nav_by_date(nav_date: str) -> list:
    """
    按日期批量拉全市场基金净值（A++ 基金排行榜飞速版核心）
    nav_date: YYYYMMDD
    返回全部基金当天净值（一次调用可返回 1 万+ 条）
    """
    rows = _call_tushare(
        "fund_nav",
        {"nav_date": nav_date},
        "ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav",
    )
    print(f"[TUSHARE-FUND-BATCH] nav_date={nav_date}: {len(rows)} 条基金净值")
    return rows


def get_fund_basic_all() -> list:
    """全量基金名单（场内 E + 场外 O）"""
    rows_e = _call_tushare(
        "fund_basic",
        {"market": "E"},
        "ts_code,name,fund_type,invest_type,status,list_date,due_date,issue_amount",
    )
    rows_o = _call_tushare(
        "fund_basic",
        {"market": "O"},
        "ts_code,name,fund_type,invest_type,status,list_date,due_date,issue_amount",
    )
    all_rows = (rows_e or []) + (rows_o or [])
    print(f"[TUSHARE-FUND-BASIC] 场内 {len(rows_e or [])} + 场外 {len(rows_o or [])} = {len(all_rows)}")
    return all_rows


# ============================================================
# 国债收益率（yc_cb 中债国债收益率曲线）
# ============================================================

def get_treasury_yield(days: int = 30) -> dict:
    """获取中国10年期国债收益率（Tushare yc_cb）

    返回:
        {
            "yield_10y": 1.77,
            "yield_change_5d": -0.01,
            "yield_1y": 1.21,
            "available": True,
            "data_date": "20260515",
        }
    """
    from datetime import datetime, timedelta

    result = {"yield_10y": 0, "yield_change_5d": 0, "available": False}

    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    rows = _call_tushare(
        "yc_cb",
        {"curve_type": "0", "curve_term": "10", "start_date": start, "end_date": end},
        "trade_date,curve_term,yield",
    )

    if not rows or len(rows) < 2:
        return result

    # 按日期排序（最新在前）
    rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)

    current = float(rows[0].get("yield", 0))
    prev_5d = float(rows[min(4, len(rows) - 1)].get("yield", current))

    result["yield_10y"] = round(current, 4)
    result["yield_change_5d"] = round(current - prev_5d, 4)
    result["available"] = True
    result["data_date"] = rows[0].get("trade_date", "")
    result["source"] = "tushare_yc_cb"

    # 额外获取1年期收益率
    rows_1y = _call_tushare(
        "yc_cb",
        {"curve_type": "0", "curve_term": "1", "start_date": end, "end_date": end},
        "trade_date,curve_term,yield",
    )
    if rows_1y:
        result["yield_1y"] = round(float(rows_1y[0].get("yield", 0)), 4)

    print(f"[TUSHARE] 国债收益率 10Y={current}%, 5d变化={result['yield_change_5d']}%")
    return result


# ============================================================
# 指数日线（index_daily）
# ============================================================

def get_index_daily(ts_code: str = "000300.SH", days: int = 120) -> list:
    """获取指数日线数据（用于技术指标计算）

    Args:
        ts_code: 指数代码（000300.SH=沪深300, 000001.SH=上证指数, 399001.SZ=深证成指）
        days: 获取天数

    Returns:
        list of dict: [{"trade_date": ..., "close": ..., "open": ..., "high": ..., "low": ..., "vol": ...}, ...]
        按日期升序排列
    """
    from datetime import datetime, timedelta

    start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")  # 多取30天缓冲
    end = datetime.now().strftime("%Y%m%d")

    rows = _call_tushare(
        "index_daily",
        {"ts_code": ts_code, "start_date": start, "end_date": end},
        "trade_date,close,open,high,low,vol,amount",
    )

    if not rows:
        return []

    # 按日期升序
    rows.sort(key=lambda x: x.get("trade_date", ""))
    print(f"[TUSHARE] index_daily {ts_code}: {len(rows)} 条")
    return rows


# ============================================================
# 主力资金流排名（moneyflow + stock_basic 名称映射）
# ============================================================

_stock_name_cache = MemoryCache(default_ttl=86400)  # 股票名称缓存24小时


def _get_stock_names() -> dict:
    """获取全A股代码→名称映射（缓存24小时）"""
    cached = _stock_name_cache.get("all_names")
    if cached is not None:
        return cached

    rows = _call_tushare(
        "stock_basic",
        {"exchange": "", "list_status": "L"},
        "ts_code,name",
    )
    if not rows:
        return {}

    mapping = {r["ts_code"]: r["name"] for r in rows}
    _stock_name_cache.set("all_names", mapping, ttl=86400)
    print(f"[TUSHARE] stock_basic 名称映射: {len(mapping)} 只")
    return mapping


def validate_stock_code(code: str) -> dict:
    """校验股票代码是否为当前上市的A股

    Returns:
        {"valid": True, "name": "贵州茅台", "ts_code": "600519.SH"}
        {"valid": False, "reason": "该代码不在A股上市列表中"}
        {"valid": None, "reason": "无法连接数据源校验"}  # 降级放行
    """
    ts_code = _code_to_ts(code)
    names = _get_stock_names()
    if not names:
        # 数据源不可用时降级放行（不阻塞用户操作）
        return {"valid": None, "reason": "无法连接数据源校验"}
    if ts_code in names:
        return {"valid": True, "name": names[ts_code], "ts_code": ts_code}
    return {"valid": False, "reason": f"代码 {code} 不在A股上市列表中"}


def validate_fund_code(code: str) -> dict:
    """校验基金代码是否存在（直接查询 Tushare fund_basic）

    不依赖批量缓存（Tushare 分页限制 15000 条会遗漏基金），
    而是直接查询目标基金代码。结果缓存 24 小时。

    Returns:
        {"valid": True, "name": "易方达沪深300ETF联接A"}
        {"valid": False, "reason": "该代码不在公募基金列表中"}
        {"valid": None, "reason": "无法连接数据源校验"}
    """
    cache_key = f"fund_valid_{code}"
    cached = _stock_name_cache.get(cache_key)
    if cached is not None:
        return cached

    # 尝试 .OF（场外）和 .SH/.SZ（场内 ETF）
    ts_candidates = [f"{code}.OF"]
    if code.startswith("5"):
        ts_candidates.append(f"{code}.SH")
    elif code.startswith("1"):
        ts_candidates.append(f"{code}.SZ")

    for ts_code in ts_candidates:
        rows = _call_tushare("fund_basic", {"ts_code": ts_code}, "ts_code,name")
        if rows:
            result = {"valid": True, "name": rows[0].get("name", "")}
            _stock_name_cache.set(cache_key, result, ttl=86400)
            return result

    # 所有候选都查不到
    result = {"valid": False, "reason": f"代码 {code} 不在公募基金列表中"}
    _stock_name_cache.set(cache_key, result, ttl=3600)  # 失败缓存1小时（防误伤新基金）
    return result


def get_main_money_flow(trade_date: str = "") -> dict:
    """获取全市场主力资金流排名

    使用 Tushare moneyflow 接口，按 net_mf_amount 排序。

    Returns:
        {
            "available": True,
            "net_flow_total": -12.5 (亿),
            "top_inflow": [{"code": ..., "name": ..., "net_flow": 万元}, ...],
            "top_outflow": [{"code": ..., "name": ..., "net_flow": 万元}, ...],
            "data_date": "20260515",
        }
    """
    from datetime import datetime, timedelta

    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")

    result = {"available": False, "net_flow_total": 0, "top_inflow": [], "top_outflow": []}

    # 获取全市场资金流（Tushare 每次最多 5000+ 条）
    rows = _call_tushare(
        "moneyflow",
        {"trade_date": trade_date},
        "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount",
    )

    if not rows:
        # 当天可能还没收盘/非交易日，试前一天
        prev_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        rows = _call_tushare(
            "moneyflow",
            {"trade_date": prev_date},
            "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount",
        )
        if rows:
            trade_date = prev_date

    if not rows:
        return result

    # 获取股票名称映射
    name_map = _get_stock_names()

    # 计算主力净流入 = (buy_lg + buy_elg) - (sell_lg + sell_elg)
    # net_mf_amount 已经是净主力资金（万元）
    for r in rows:
        r["_net"] = float(r.get("net_mf_amount", 0) or 0)
        r["_name"] = name_map.get(r.get("ts_code", ""), "")

    # 排序
    rows.sort(key=lambda x: x["_net"], reverse=True)

    # TOP5 流入
    top_in = []
    for r in rows[:10]:
        if r["_net"] > 0:
            top_in.append({
                "code": r["ts_code"].split(".")[0],
                "name": r["_name"],
                "net_flow": round(r["_net"] / 10000, 2),  # 万→亿
            })
        if len(top_in) >= 5:
            break

    # TOP5 流出
    top_out = []
    for r in rows[-10:][::-1]:
        if r["_net"] < 0:
            top_out.append({
                "code": r["ts_code"].split(".")[0],
                "name": r["_name"],
                "net_flow": round(r["_net"] / 10000, 2),  # 万→亿
            })
        if len(top_out) >= 5:
            break

    # 全市场净流入
    total_net = sum(r["_net"] for r in rows) / 10000  # 万→亿

    result["available"] = True
    result["top_inflow"] = top_in
    result["top_outflow"] = top_out
    result["net_flow_total"] = round(total_net, 2)
    result["data_date"] = trade_date
    result["source"] = "tushare_moneyflow"
    result["total_stocks"] = len(rows)

    print(f"[TUSHARE] moneyflow {trade_date}: 全市场净流{total_net:.1f}亿, "
          f"TOP流入={top_in[0]['name'] if top_in else 'N/A'}")
    return result


def get_fund_extra_info_ak(code: str) -> dict:
    """AKShare 兜底：获取基金规模/经理/类型等补充信息

    返回 dict 格式与 AKShare fund_individual_basic_info_xq 的 item→value 映射一致。
    如果 AKShare 不可用或失败，返回空 dict。
    """
    try:
        import akshare as ak
        df = ak.fund_individual_basic_info_xq(symbol=code)
        if df is not None and len(df) > 0:
            return dict(zip(df['item'], df['value']))
    except Exception:
        pass
    return {}
