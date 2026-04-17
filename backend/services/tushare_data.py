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

def _get_token() -> str:
    """实时读取 Token（避免 import 时缓存空值）"""
    return os.getenv("TUSHARE_TOKEN", "")


def is_configured() -> bool:
    return bool(_get_token())


_TUSHARE_URL = "http://api.tushare.pro"

# 缓存
_ts_cache = {}
_TS_CACHE_TTL = 3600  # 1 小时


def _call_tushare(api_name: str, params: dict, fields: str = "") -> list:
    """统一 Tushare API 调用"""
    token = _get_token()
    if not token:
        return []

    cache_key = f"{api_name}_{json.dumps(params, sort_keys=True)}_{fields}"
    now = time.time()
    if cache_key in _ts_cache and now - _ts_cache[cache_key]["ts"] < _TS_CACHE_TTL:
        return _ts_cache[cache_key]["data"]

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
            _ts_cache[cache_key] = {"data": result, "ts": now}
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
    now = time.time()
    if cache_key in _ts_cache and now - _ts_cache[cache_key]["ts"] < 86400:  # 24h缓存
        return _ts_cache[cache_key]["data"]

    rows = _call_tushare(
        "namechange",
        {"limit": 200},
        "ts_code,name,start_date,end_date,change_reason",
    )
    # 筛选当前仍为 ST 的
    st_list = [r for r in rows if r.get("name", "").startswith(("ST", "*ST")) and not r.get("end_date")]
    _ts_cache[cache_key] = {"data": st_list, "ts": now}
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

    # 计算每日北向净流入（万元）
    # north_money = 沪股通净买额 + 深股通净买额（Tushare 直接提供）
    # 如果 north_money 字段可用就直接用，否则 hgt + sgt
    daily_flows = []
    for row in rows:
        trade_date = row.get("trade_date", "")
        # north_money 是直接的北向净买入（万元）
        nm = row.get("north_money")
        if nm is not None:
            net_flow = float(nm)
        else:
            # 降级：hgt（沪股通）+ sgt（深股通）
            hgt = float(row.get("hgt", 0) or 0)
            sgt = float(row.get("sgt", 0) or 0)
            net_flow = hgt + sgt
        daily_flows.append({"date": trade_date, "net_flow": net_flow})

    if len(daily_flows) < 5:
        print(f"[TUSHARE-NORTH] 数据不足: {len(daily_flows)}天")
        return result

    # 单位转换：万元 → 亿元
    def wan_to_yi(v):
        return round(v / 10000, 2)

    result["net_flow_today"] = wan_to_yi(daily_flows[-1]["net_flow"])
    result["net_flow_5d"] = wan_to_yi(sum(d["net_flow"] for d in daily_flows[-5:]))
    result["net_flow_20d"] = wan_to_yi(sum(d["net_flow"] for d in daily_flows[-20:]))
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
