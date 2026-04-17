"""
钱袋子 — V6.5: 盈利预测聚合模块（模块 H）
数据源：Tushare report_rc（5000 积分，返回券商预测 EPS/PE/ROE/评级/目标价）
功能：汇总多家券商预测 → 计算一致预期 → Pipeline enrich
"""

MODULE_META = {
    "name": "earnings_forecast",
    "scope": "public",
    "input": ["stock_basic"],
    "output": "earnings_consensus",
    "cost": "api_medium",
    "tags": ["盈利预测", "一致预期", "研报", "EPS"],
    "description": "券商盈利预测聚合+一致预期计算（Tushare report_rc）",
    "layer": "data",
    "priority": 2,
}

import time
import json
from datetime import datetime, timedelta
from collections import Counter

_forecast_cache = {}
_FORECAST_CACHE_TTL = 86400  # 24小时（研报更新不频繁）


# 评级→数值映射（用于计算加权共识）
_RATING_SCORE = {
    "买入": 5, "强烈推荐": 5, "强推": 5,
    "增持": 4, "推荐": 4, "优于大市": 4, "跑赢": 4,
    "中性": 3, "持有": 3, "同步大市": 3,
    "减持": 2, "回避": 2, "弱于大市": 2,
    "卖出": 1,
}


def get_stock_forecast(code: str, limit: int = 30) -> dict:
    """获取单只股票的券商盈利预测

    Returns: {
        "available": bool,
        "forecasts": [原始预测列表],
        "consensus": {一致预期},
        "rating_dist": {评级分布},
        "org_count": int,
    }
    """
    cache_key = f"forecast_{code}"
    now = time.time()
    if cache_key in _forecast_cache and now - _forecast_cache[cache_key]["ts"] < _FORECAST_CACHE_TTL:
        return _forecast_cache[cache_key]["data"]

    result = {"available": False, "code": code, "forecasts": [], "consensus": {}, "org_count": 0}

    try:
        from services.tushare_data import is_configured, _call_tushare, _code_to_ts
        if not is_configured():
            return result

        ts_code = _code_to_ts(code)
        rows = _call_tushare(
            "report_rc",
            {"ts_code": ts_code, "limit": limit},
            "ts_code,name,report_date,report_title,org_name,quarter,op_rt,np,eps,pe,roe,ev_ebitda,rating,max_price,min_price",
        )

        if not rows:
            _forecast_cache[cache_key] = {"data": result, "ts": now}
            return result

        result["forecasts"] = rows
        result["available"] = True
        result["stock_name"] = rows[0].get("name", "")

        # 按季度分组计算一致预期
        consensus = _calc_consensus(rows)
        result["consensus"] = consensus

        # 评级分布
        ratings = Counter(r.get("rating", "") for r in rows if r.get("rating"))
        result["rating_dist"] = dict(ratings)

        # 覆盖机构数
        orgs = set(r.get("org_name", "") for r in rows if r.get("org_name"))
        result["org_count"] = len(orgs)

        # 一致评级
        if ratings:
            # 加权平均评级
            total_score = sum(_RATING_SCORE.get(r, 3) * c for r, c in ratings.items())
            total_count = sum(ratings.values())
            avg_score = total_score / max(total_count, 1)
            if avg_score >= 4.5:
                result["consensus_rating"] = "强烈推荐"
            elif avg_score >= 3.5:
                result["consensus_rating"] = "买入/增持"
            elif avg_score >= 2.5:
                result["consensus_rating"] = "中性"
            else:
                result["consensus_rating"] = "谨慎"
        else:
            result["consensus_rating"] = "无评级"

        # 目标价
        prices = [r.get("max_price") or r.get("min_price") for r in rows
                  if r.get("max_price") or r.get("min_price")]
        if prices:
            valid_prices = [float(p) for p in prices if p]
            if valid_prices:
                result["target_price_avg"] = round(sum(valid_prices) / len(valid_prices), 2)
                result["target_price_high"] = round(max(valid_prices), 2)
                result["target_price_low"] = round(min(valid_prices), 2)

        print(f"[EARNINGS] {code}: {len(rows)}篇研报, {result['org_count']}家机构, "
              f"一致评级={result['consensus_rating']}")

    except Exception as e:
        print(f"[EARNINGS] {code} failed: {e}")

    _forecast_cache[cache_key] = {"data": result, "ts": now}
    return result


def _calc_consensus(rows: list) -> dict:
    """按季度分组计算一致预期"""
    from collections import defaultdict

    by_quarter = defaultdict(list)
    for r in rows:
        q = r.get("quarter", "")
        if q:
            by_quarter[q].append(r)

    consensus = {}
    for quarter, items in sorted(by_quarter.items()):
        eps_list = [float(r["eps"]) for r in items if r.get("eps")]
        pe_list = [float(r["pe"]) for r in items if r.get("pe")]
        np_list = [float(r["np"]) for r in items if r.get("np")]
        roe_list = [float(r["roe"]) for r in items if r.get("roe")]

        consensus[quarter] = {
            "eps_avg": round(sum(eps_list) / max(len(eps_list), 1), 2) if eps_list else None,
            "eps_high": round(max(eps_list), 2) if eps_list else None,
            "eps_low": round(min(eps_list), 2) if eps_list else None,
            "pe_avg": round(sum(pe_list) / max(len(pe_list), 1), 1) if pe_list else None,
            "np_avg": round(sum(np_list) / max(len(np_list), 1), 0) if np_list else None,
            "roe_avg": round(sum(roe_list) / max(len(roe_list), 1), 1) if roe_list else None,
            "report_count": len(items),
            "orgs": list(set(r.get("org_name", "") for r in items if r.get("org_name"))),
        }

    return consensus


def get_consensus_eps(code: str) -> dict:
    """获取一致预期 EPS（最近季度）"""
    data = get_stock_forecast(code)
    if not data.get("available"):
        return {"available": False}

    consensus = data.get("consensus", {})
    if not consensus:
        return {"available": False}

    # 取最近的季度
    latest_q = sorted(consensus.keys())[-1] if consensus else ""
    if not latest_q:
        return {"available": False}

    c = consensus[latest_q]
    return {
        "available": True,
        "quarter": latest_q,
        "eps_avg": c.get("eps_avg"),
        "eps_high": c.get("eps_high"),
        "eps_low": c.get("eps_low"),
        "pe_avg": c.get("pe_avg"),
        "report_count": c.get("report_count", 0),
        "org_count": data.get("org_count", 0),
        "consensus_rating": data.get("consensus_rating", ""),
    }


def enrich(ctx):
    """Pipeline 注入 — 为持仓股票补充盈利预测数据"""
    try:
        # 从持仓中提取股票代码
        holdings = ctx.modules_results.get("stock_holdings", {}).get("holdings", [])
        if not holdings:
            return ctx

        forecasts = {}
        for h in holdings[:5]:  # 最多5只，避免 API 压力
            code = h.get("code", "")
            if code:
                fc = get_consensus_eps(code)
                if fc.get("available"):
                    forecasts[code] = fc

        if forecasts:
            ctx.modules_results["earnings_forecast"] = {
                "available": True,
                "forecasts": forecasts,
                "detail": f"盈利预测覆盖{len(forecasts)}只持仓股",
            }

        if "earnings_forecast" not in ctx.modules_called:
            ctx.modules_called.append("earnings_forecast")

    except Exception as e:
        print(f"[EARNINGS] enrich failed: {e}")

    return ctx
