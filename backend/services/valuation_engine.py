"""
钱袋子 — V6.5: 估值定价引擎（模块 I）
基于盈利预测的 Forward PE / PEG / 目标价空间 / 估值合理性评估
输入：earnings_forecast 的一致预期 + tushare_data 的当前价和历史 PE
"""

MODULE_META = {
    "name": "valuation_engine",
    "scope": "public",
    "input": ["earnings_forecast", "daily_basic"],
    "output": "valuation_assessment",
    "cost": "compute_light",
    "tags": ["估值", "目标价", "Forward PE", "PEG"],
    "description": "基于一致预期的估值定价引擎（Forward PE+PEG+目标价空间）",
    "layer": "analysis",
    "priority": 3,
}

import time

_val_cache = {}
_VAL_CACHE_TTL = 3600  # 1小时


def assess_valuation(code: str) -> dict:
    """综合估值评估

    Returns: {
        "current_price": float,
        "forward_pe": float,     # 基于预测 EPS 的 PE
        "trailing_pe": float,    # 基于历史 EPS 的 PE
        "peg": float,            # PE / 盈利增速
        "target_price_avg": float,
        "upside_pct": float,     # 潜在上涨空间
        "valuation_signal": str, # 低估/合理/高估
        "confidence": str,       # 高/中/低
        "score": int,            # 0-100 估值评分
    }
    """
    cache_key = f"val_{code}"
    now = time.time()
    if cache_key in _val_cache and now - _val_cache[cache_key]["ts"] < _VAL_CACHE_TTL:
        return _val_cache[cache_key]["data"]

    result = {
        "available": False, "code": code,
        "current_price": 0, "forward_pe": 0, "trailing_pe": 0,
        "peg": 0, "target_price_avg": 0, "upside_pct": 0,
        "valuation_signal": "未知", "confidence": "低", "score": 50,
    }

    try:
        # 1. 获取当前价和历史 PE
        from services.tushare_data import is_configured, get_valuation
        if not is_configured():
            return result

        val = get_valuation(code)
        if not val.get("available"):
            return result

        current_price = 0
        trailing_pe = val.get("pe_ttm", 0) or 0

        # 获取当前价（从实时行情或 daily）
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is not None:
                code_col = next((c for c in df.columns if "代码" in c), None)
                price_col = next((c for c in df.columns if "最新价" in c), None)
                if code_col and price_col:
                    match = df[df[code_col].astype(str) == code]
                    if len(match) > 0:
                        current_price = float(match.iloc[0][price_col])
        except Exception:
            pass

        # 没拿到实时价，用 total_mv 推算
        if current_price <= 0 and val.get("total_mv") and val.get("circ_mv"):
            # total_mv 是亿元，circ_mv 也是亿元
            # 这个不够精确，先跳过
            pass

        result["current_price"] = current_price
        result["trailing_pe"] = round(trailing_pe, 2) if trailing_pe else 0

        # 2. 获取盈利预测
        from services.earnings_forecast import get_consensus_eps
        forecast = get_consensus_eps(code)

        if forecast.get("available") and forecast.get("eps_avg"):
            eps_forecast = forecast["eps_avg"]

            # Forward PE = 当前价 / 预测 EPS
            if current_price > 0 and eps_forecast > 0:
                forward_pe = round(current_price / eps_forecast, 2)
                result["forward_pe"] = forward_pe

                # PEG = Forward PE / EPS 增速
                # 需要历史 EPS 来算增速
                from services.tushare_data import get_financials
                fin = get_financials(code)
                historical_eps = fin.get("eps", 0) or 0
                if historical_eps > 0 and eps_forecast > 0:
                    eps_growth = round((eps_forecast / historical_eps - 1) * 100, 1)
                    if eps_growth > 0:
                        peg = round(forward_pe / eps_growth, 2)
                        result["peg"] = peg
                        result["eps_growth"] = eps_growth

            # 目标价空间
            from services.earnings_forecast import get_stock_forecast
            full_fc = get_stock_forecast(code)
            if full_fc.get("target_price_avg") and current_price > 0:
                target = full_fc["target_price_avg"]
                upside = round((target / current_price - 1) * 100, 1)
                result["target_price_avg"] = target
                result["target_price_high"] = full_fc.get("target_price_high", 0)
                result["target_price_low"] = full_fc.get("target_price_low", 0)
                result["upside_pct"] = upside

            result["org_count"] = forecast.get("org_count", 0)
            result["consensus_rating"] = forecast.get("consensus_rating", "")

        # 3. 估值信号判定
        score = 50  # 默认中性
        signals = []

        # Forward PE 评估
        fpe = result.get("forward_pe", 0)
        if fpe > 0:
            if fpe < 15:
                score += 20
                signals.append("Forward PE 偏低")
            elif fpe < 25:
                score += 5
            elif fpe > 40:
                score -= 20
                signals.append("Forward PE 偏高")
            elif fpe > 30:
                score -= 10

        # PEG 评估
        peg = result.get("peg", 0)
        if peg > 0:
            if peg < 0.8:
                score += 15
                signals.append("PEG<0.8 成长型价值")
            elif peg < 1.0:
                score += 10
            elif peg > 2.0:
                score -= 15
                signals.append("PEG>2 高估")
            elif peg > 1.5:
                score -= 5

        # 目标价空间评估
        upside = result.get("upside_pct", 0)
        if upside > 30:
            score += 15
            signals.append(f"目标价上涨空间{upside}%")
        elif upside > 15:
            score += 10
        elif upside < -10:
            score -= 15
            signals.append(f"目标价下行空间{upside}%")

        # 限制范围
        score = max(0, min(100, score))
        result["score"] = score

        # 信号文字
        if score >= 70:
            result["valuation_signal"] = "低估"
        elif score >= 55:
            result["valuation_signal"] = "合理偏低"
        elif score >= 45:
            result["valuation_signal"] = "合理"
        elif score >= 30:
            result["valuation_signal"] = "合理偏高"
        else:
            result["valuation_signal"] = "高估"

        # 置信度（基于覆盖机构数）
        org_count = result.get("org_count", 0)
        if org_count >= 10:
            result["confidence"] = "高"
        elif org_count >= 5:
            result["confidence"] = "中"
        else:
            result["confidence"] = "低"

        result["available"] = True
        result["signals"] = signals

        print(f"[VALUATION] {code}: score={score}, signal={result['valuation_signal']}, "
              f"fPE={fpe}, PEG={peg}, upside={upside}%")

    except Exception as e:
        print(f"[VALUATION] {code} failed: {e}")

    _val_cache[cache_key] = {"data": result, "ts": now}
    return result


def enrich(ctx):
    """Pipeline 注入 — 为持仓股票补充估值评估"""
    try:
        holdings = ctx.modules_results.get("stock_holdings", {}).get("holdings", [])
        if not holdings:
            return ctx

        valuations = {}
        for h in holdings[:5]:
            code = h.get("code", "")
            if code:
                v = assess_valuation(code)
                if v.get("available"):
                    valuations[code] = {
                        "score": v["score"],
                        "signal": v["valuation_signal"],
                        "forward_pe": v.get("forward_pe"),
                        "peg": v.get("peg"),
                        "upside": v.get("upside_pct"),
                    }

        if valuations:
            ctx.modules_results["valuation_engine"] = {
                "available": True,
                "valuations": valuations,
                "detail": f"估值评估覆盖{len(valuations)}只持仓股",
            }

        if "valuation_engine" not in ctx.modules_called:
            ctx.modules_called.append("valuation_engine")

    except Exception as e:
        print(f"[VALUATION] enrich failed: {e}")

    return ctx


# ============================================================
# V7.2: DCF 简化估值（现金流折现）
# ============================================================

# 默认参数（V8 可调）
DCF_DISCOUNT_RATE = 0.10      # 折现率 10%
DCF_TERMINAL_GROWTH = 0.03    # 永续增长率 3%
DCF_PROJECTION_YEARS = 5      # 预测期 5 年
DCF_MARGIN_OF_SAFETY = 0.30   # 安全边际 30%


def dcf_valuation(code: str) -> dict:
    """DCF 简化估值（现金流折现法）

    步骤：
    1. 获取最新自由现金流（经营现金流 - 资本支出）
    2. 用盈利预测增速作为未来增长率
    3. 5年现金流预测 + 终值折现
    4. 除以总股本 = 每股内在价值
    5. 安全边际 30% = 买入价

    Returns: {
        "intrinsic_value": float,  # 每股内在价值
        "buy_price": float,        # 安全边际买入价
        "current_price": float,    # 当前价
        "upside": float,           # 上涨空间 %
        "verdict": str,            # 低估/合理/高估
        "emoji": str,
    }
    """
    cache_key = f"dcf_{code}"
    now = time.time()
    if cache_key in _val_cache and now - _val_cache[cache_key]["ts"] < _VAL_CACHE_TTL:
        return _val_cache[cache_key]["data"]

    result = {"available": False, "code": code, "method": "DCF"}

    try:
        from services.tushare_data import is_configured, get_financials, _code_to_ts, _call_tushare
        if not is_configured():
            return result

        ts_code = _code_to_ts(code)

        # 1. 获取自由现金流
        # 用 fina_indicator 的 ocfps (每股经营现金流) 作为近似
        fin = get_financials(code)
        ocfps = fin.get("cash_flow_per_share")
        if not ocfps or float(ocfps) <= 0:
            result["error"] = "无有效现金流数据"
            _val_cache[cache_key] = {"data": result, "ts": now}
            return result

        fcf_per_share = float(ocfps)

        # 2. 获取盈利增速（从一致预期）
        growth_rate = 0.08  # 默认 8%
        try:
            from services.earnings_forecast import get_consensus_eps
            fc = get_consensus_eps(code)
            if fc.get("available"):
                eps_now = fin.get("eps")
                eps_forecast = fc.get("eps_avg")
                if eps_now and eps_forecast and float(eps_now) > 0:
                    growth_rate = max(0.02, min(0.30, float(eps_forecast) / float(eps_now) - 1))
        except Exception:
            pass

        # 3. 5年现金流预测
        projected = []
        current_fcf = fcf_per_share
        for _ in range(DCF_PROJECTION_YEARS):
            current_fcf *= (1 + growth_rate)
            projected.append(current_fcf)

        # 4. 折现
        pv_fcf = sum(f / (1 + DCF_DISCOUNT_RATE) ** y for y, f in enumerate(projected, 1))

        # 终值 = 最后一年 FCF × (1+g) / (r-g)
        terminal = projected[-1] * (1 + DCF_TERMINAL_GROWTH) / (DCF_DISCOUNT_RATE - DCF_TERMINAL_GROWTH)
        pv_terminal = terminal / (1 + DCF_DISCOUNT_RATE) ** DCF_PROJECTION_YEARS

        intrinsic = round(pv_fcf + pv_terminal, 2)
        buy_price = round(intrinsic * (1 - DCF_MARGIN_OF_SAFETY), 2)

        # 5. 获取当前价
        current_price = 0
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is not None:
                code_col = next((c for c in df.columns if "代码" in c), None)
                price_col = next((c for c in df.columns if "最新价" in c), None)
                if code_col and price_col:
                    match = df[df[code_col].astype(str) == code]
                    if len(match) > 0:
                        current_price = float(match.iloc[0][price_col])
        except Exception:
            pass

        result["intrinsic_value"] = intrinsic
        result["buy_price"] = buy_price
        result["current_price"] = current_price
        result["fcf_per_share"] = round(fcf_per_share, 2)
        result["growth_rate"] = round(growth_rate * 100, 1)
        result["discount_rate"] = DCF_DISCOUNT_RATE * 100
        result["margin_of_safety"] = DCF_MARGIN_OF_SAFETY * 100
        result["available"] = True

        if current_price > 0:
            upside = round((intrinsic / current_price - 1) * 100, 1)
            result["upside"] = upside

            if current_price <= buy_price:
                result["verdict"] = "低估（有安全边际）"
                result["emoji"] = "🟢"
            elif current_price <= intrinsic:
                result["verdict"] = "合理偏低"
                result["emoji"] = "🟡"
            elif current_price <= intrinsic * 1.2:
                result["verdict"] = "合理"
                result["emoji"] = "🟡"
            else:
                result["verdict"] = "高估"
                result["emoji"] = "🔴"
        else:
            result["upside"] = 0
            result["verdict"] = "无法判断（缺当前价）"
            result["emoji"] = "⚪"

        print(f"[DCF] {code}: intrinsic=¥{intrinsic}, buy=¥{buy_price}, "
              f"current=¥{current_price}, verdict={result['verdict']}")

    except Exception as e:
        print(f"[DCF] {code} failed: {e}")
        result["error"] = str(e)

    _val_cache[cache_key] = {"data": result, "ts": now}
    return result
