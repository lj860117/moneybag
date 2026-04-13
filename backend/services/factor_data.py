"""
钱袋子 — V4.5 新因子数据
北向资金、融资融券、国债收益率、SHIBOR、股息率、LLM情绪
"""
import os
import time
from datetime import datetime, timedelta
from config import FACTOR_CACHE_TTL, LLM_API_URL, LLM_API_KEY, LLM_MODEL

factor_cache = {}

def get_northbound_flow() -> dict:
    """获取北向资金（沪股通+深股通）净流入数据"""
    cache_key = "northbound"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"net_flow_today": 0, "net_flow_5d": 0, "net_flow_20d": 0, "trend": "中性", "available": False}
    try:
        import akshare as ak
        # 沪股通+深股通历史数据
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        if df is not None and len(df) >= 20:
            # 列名可能是 "净流入" / "当日净流入" / "value"
            val_col = next((c for c in df.columns if "净流入" in str(c) or "value" in str(c).lower()), None)
            date_col = next((c for c in df.columns if "日期" in str(c) or "date" in str(c).lower()), df.columns[0])
            if val_col:
                df = df.sort_values(date_col, ascending=True)
                vals = df[val_col].astype(float).values
                result["net_flow_today"] = round(float(vals[-1]), 2)  # 亿元
                result["net_flow_5d"] = round(float(sum(vals[-5:])), 2)
                result["net_flow_20d"] = round(float(sum(vals[-20:])), 2)
                result["available"] = True
                if result["net_flow_5d"] > 50:
                    result["trend"] = "大幅流入"
                elif result["net_flow_5d"] > 10:
                    result["trend"] = "净流入"
                elif result["net_flow_5d"] < -50:
                    result["trend"] = "大幅流出"
                elif result["net_flow_5d"] < -10:
                    result["trend"] = "净流出"
                print(f"[NORTH] today={result['net_flow_today']}亿, 5d={result['net_flow_5d']}亿, 20d={result['net_flow_20d']}亿")
    except Exception as e:
        print(f"[NORTH] Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_margin_trading() -> dict:
    """获取两融（融资融券）余额数据 — 市场杠杆情绪指标"""
    cache_key = "margin"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"margin_balance": 0, "margin_change_5d": 0, "trend": "中性", "available": False}
    try:
        import akshare as ak
        df = ak.stock_margin_sse()  # 上交所融资融券
        if df is not None and len(df) >= 20:
            bal_col = next((c for c in df.columns if "融资余额" in str(c)), None)
            if bal_col:
                vals = df[bal_col].astype(float).values
                current = vals[-1]
                prev_5d = vals[-6] if len(vals) >= 6 else vals[0]
                change_pct = (current - prev_5d) / prev_5d * 100 if prev_5d > 0 else 0
                result["margin_balance"] = round(current / 1e8, 2)  # 转亿元
                result["margin_change_5d"] = round(change_pct, 2)
                result["available"] = True
                if change_pct > 3:
                    result["trend"] = "杠杆快速上升"
                elif change_pct > 1:
                    result["trend"] = "杠杆温和上升"
                elif change_pct < -3:
                    result["trend"] = "杠杆快速下降"
                elif change_pct < -1:
                    result["trend"] = "杠杆温和下降"
                print(f"[MARGIN] balance={result['margin_balance']}亿, 5d_change={change_pct:.2f}%")
    except Exception as e:
        print(f"[MARGIN] Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_treasury_yield() -> dict:
    """获取国债收益率（10年期）— 无风险利率 / 股债性价比"""
    cache_key = "treasury"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"yield_10y": 0, "yield_change": 0, "equity_premium": "", "available": False}
    try:
        import akshare as ak
        df = ak.bond_zh_us_rate(start_date="20240101")
        if df is not None and len(df) >= 5:
            cn_col = next((c for c in df.columns if "中国" in str(c) and "10" in str(c)), None)
            if cn_col:
                vals = df[cn_col].dropna().astype(float).values
                if len(vals) >= 2:
                    current = vals[-1]
                    prev = vals[-5] if len(vals) >= 5 else vals[0]
                    result["yield_10y"] = round(current, 3)
                    result["yield_change"] = round(current - prev, 3)
                    result["available"] = True
                    # 股债性价比：PE倒数 vs 国债收益率
                    val = get_valuation_percentile()
                    pe = val.get("current_pe", 12)
                    if pe > 0:
                        equity_yield = round(1 / pe * 100, 2)  # 盈利收益率
                        spread = round(equity_yield - current, 2)
                        if spread > 4:
                            result["equity_premium"] = f"股债价差{spread}%，股市极有吸引力"
                        elif spread > 2:
                            result["equity_premium"] = f"股债价差{spread}%，股市有吸引力"
                        elif spread > 0:
                            result["equity_premium"] = f"股债价差{spread}%，股债相当"
                        else:
                            result["equity_premium"] = f"股债价差{spread}%，债券更有吸引力"
                    print(f"[TREASURY] 10Y={current}%, change={result['yield_change']}%, premium={result['equity_premium']}")
    except Exception as e:
        print(f"[TREASURY] Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_shibor() -> dict:
    """获取 SHIBOR 利率（银行间市场流动性指标）"""
    cache_key = "shibor"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"overnight": 0, "one_week": 0, "trend": "中性", "available": False}
    try:
        import akshare as ak
        df = ak.rate_interbank(market="上海银行同业拆放利率(Shibor)", symbol="隔夜", indicator="利率")
        if df is not None and len(df) >= 10:
            val_col = next((c for c in df.columns if "利率" in str(c) or "报价" in str(c)), None)
            if not val_col and len(df.columns) > 1:
                val_col = df.columns[1]
            if val_col:
                vals = df[val_col].dropna().astype(float).values
                if len(vals) >= 5:
                    current = vals[-1]
                    avg_5d = sum(vals[-5:]) / 5
                    result["overnight"] = round(current, 4)
                    result["available"] = True
                    if current > avg_5d * 1.2:
                        result["trend"] = "流动性收紧"
                    elif current < avg_5d * 0.8:
                        result["trend"] = "流动性宽松"
                    else:
                        result["trend"] = "流动性平稳"
                    print(f"[SHIBOR] overnight={current}%, trend={result['trend']}")
    except Exception as e:
        print(f"[SHIBOR] Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_dividend_yield() -> dict:
    """获取沪深300股息率 — 价值因子"""
    cache_key = "dividend"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"dividend_yield": 0, "level": "中性", "available": False}
    try:
        import akshare as ak
        # 尝试从估值数据中获取股息率
        df = ak.stock_index_pe_lg(symbol="沪深300")
        if df is not None and len(df) > 0:
            dy_col = next((c for c in df.columns if "股息率" in str(c)), None)
            if dy_col:
                dy_vals = df[dy_col].dropna().astype(float)
                if len(dy_vals) > 0:
                    current = float(dy_vals.iloc[-1])
                    result["dividend_yield"] = round(current, 2)
                    result["available"] = True
                    # 历史百分位
                    window = min(1250, len(dy_vals))
                    recent = dy_vals.tail(window).values
                    pct = round(sum(1 for d in recent if d <= current) / len(recent) * 100, 1)
                    result["percentile"] = pct
                    if pct > 70:
                        result["level"] = "高股息(价值凸显)"
                    elif pct > 40:
                        result["level"] = "中等股息"
                    else:
                        result["level"] = "低股息(成长偏好)"
                    print(f"[DIVIDEND] yield={current}%, pct={pct}%")
    except Exception as e:
        print(f"[DIVIDEND] Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result

def get_news_sentiment_score() -> dict:
    """LLM 新闻情绪量化 — 用 DeepSeek/OpenAI 给新闻打分（-100~+100）
    借鉴幻方量化的新闻情绪因子，用LLM替代BERT实现零训练成本
    """
    cache_key = "sentiment"
    now = time.time()
    # 情绪分数缓存 30 分钟（新闻更新频率）
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < 1800:
        return factor_cache[cache_key]["data"]

    result = {"score": 0, "level": "中性", "headlines": [], "available": False}

    try:
        # 获取最新新闻
        news = get_market_news(8)
        policy = get_policy_news(5)
        all_news = news + policy
        valid = [n for n in all_news if "加载中" not in n.get("title", "")]

        if not valid:
            factor_cache[cache_key] = {"data": result, "ts": now}
            return result

        headlines = [n["title"] for n in valid[:10]]
        result["headlines"] = headlines

        # 尝试用 LLM 打分
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

        if api_key:
            try:
                import httpx
                headlines_text = "\n".join([f"{i+1}. {h}" for i, h in enumerate(headlines)])
                prompt = f"""你是A股市场情绪分析师。请对以下新闻标题进行情绪打分。

新闻标题：
{headlines_text}

请返回一个JSON，格式：
{{"score": 数字(-100到+100, 负数看空 正数看多), "level": "极度悲观/悲观/偏空/中性/偏多/乐观/极度乐观", "reason": "一句话总结"}}

打分标准：
- 降息降准/财政刺激/经济复苏/业绩超预期 → 正分(+20~+80)
- 加息缩表/贸易摩擦/地缘冲突/经济衰退 → 负分(-20~-80)
- 日常资讯/无明确方向 → 0附近(-10~+10)
只返回JSON，不要其他内容。"""

                # 同步调用（在后台线程中）
                import httpx
                with httpx.Client(timeout=15) as client:
                    resp = client.post(
                        f"{api_base}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 200,
                            "temperature": 0.3,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data["choices"][0]["message"]["content"]
                        import re
                        json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
                        if json_match:
                            parsed = json.loads(json_match.group())
                            result["score"] = max(-100, min(100, int(parsed.get("score", 0))))
                            result["level"] = parsed.get("level", "中性")
                            result["reason"] = parsed.get("reason", "")
                            result["available"] = True
                            result["source"] = "llm"
                            print(f"[SENTIMENT] LLM score={result['score']}, level={result['level']}")
            except Exception as e:
                print(f"[SENTIMENT] LLM failed: {e}")

        # 降级：关键词规则打分
        if not result["available"]:
            pos_words = ["利好", "上涨", "反弹", "新高", "突破", "降准", "降息", "刺激", "增长", "超预期", "回暖", "复苏"]
            neg_words = ["利空", "下跌", "暴跌", "制裁", "关税", "战争", "衰退", "收紧", "加息", "缩表", "违约", "暴雷"]
            pos_count = sum(1 for h in headlines for w in pos_words if w in h)
            neg_count = sum(1 for h in headlines for w in neg_words if w in h)
            raw = (pos_count - neg_count) * 15
            result["score"] = max(-100, min(100, raw))
            result["available"] = True
            result["source"] = "keywords"
            if result["score"] > 30:
                result["level"] = "乐观"
            elif result["score"] > 10:
                result["level"] = "偏多"
            elif result["score"] > -10:
                result["level"] = "中性"
            elif result["score"] > -30:
                result["level"] = "偏空"
            else:
                result["level"] = "悲观"
            print(f"[SENTIMENT] Keywords score={result['score']}, pos={pos_count}, neg={neg_count}")

    except Exception as e:
        print(f"[SENTIMENT] Fatal: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# V4.5 风控体系（借鉴幻方 CVaR 模型简化版）
# ============================================================


# ============================================================
# V5.5 数据缺口补齐（审计报告 P0 缺口）
# ============================================================

def get_main_money_flow() -> dict:
    """获取主力资金流向（沪深300成分股的主力净流入）"""
    cache_key = "main_flow"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"net_flow": 0, "top_inflow": [], "top_outflow": [], "available": False}
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow_rank(indicator="今日")
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            name_col = next((c for c in cols if "名称" in c), None)
            flow_col = next((c for c in cols if "主力净流入" in c and "净占比" not in c), None)
            if name_col and flow_col:
                df[flow_col] = df[flow_col].astype(float)
                top_in = df.nlargest(5, flow_col)
                top_out = df.nsmallest(5, flow_col)
                result["top_inflow"] = [{"name": str(r[name_col]), "flow": round(float(r[flow_col]) / 1e4, 2)} for _, r in top_in.iterrows()]
                result["top_outflow"] = [{"name": str(r[name_col]), "flow": round(float(r[flow_col]) / 1e4, 2)} for _, r in top_out.iterrows()]
                result["net_flow"] = round(float(df[flow_col].sum()) / 1e8, 2)
                result["available"] = True
                print(f"[MAIN_FLOW] net={result['net_flow']}亿, top_in={result['top_inflow'][0]['name']}")
    except Exception as e:
        print(f"[MAIN_FLOW] Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_stock_financials(code: str) -> dict:
    """获取个股核心财务数据 — 优先 Tushare Pro → 降级 AKShare"""
    cache_key = f"fin_{code}"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < 86400:  # 24h 缓存
        return factor_cache[cache_key]["data"]

    result = {
        "code": code, "roe": None, "eps": None, "revenue_growth": None,
        "gross_margin": None, "net_margin": None, "debt_ratio": None,
        "cash_flow_per_share": None, "available": False, "source": "none",
    }

    # 方案 A：Tushare Pro（稳定+快速）
    try:
        from services.tushare_data import is_configured, get_financials as ts_fin
        if is_configured():
            ts = ts_fin(code)
            if ts.get("available"):
                result["roe"] = ts.get("roe")
                result["eps"] = ts.get("eps")
                result["revenue_growth"] = ts.get("revenue_yoy")
                result["gross_margin"] = ts.get("gross_margin")
                result["net_margin"] = ts.get("net_margin")
                result["debt_ratio"] = ts.get("debt_ratio")
                result["cash_flow_per_share"] = ts.get("cash_flow_per_share")
                result["netprofit_yoy"] = ts.get("netprofit_yoy")
                result["current_ratio"] = ts.get("current_ratio")
                result["available"] = True
                result["source"] = "tushare"
                print(f"[FIN] {code} Tushare OK: ROE={result['roe']}, GM={result['gross_margin']}")
                factor_cache[cache_key] = {"data": result, "ts": now}
                return result
    except Exception as e:
        print(f"[FIN] {code} Tushare failed, fallback AKShare: {e}")

    # 方案 B：AKShare 降级
    try:
        import akshare as ak
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year="2024")
        if df is not None and len(df) > 0:
            cols = list(df.columns)

            def _extract(keywords):
                col = next((c for c in cols if all(k in str(c) for k in keywords)), None)
                if col:
                    try:
                        return round(float(df[col].iloc[0]), 2)
                    except (ValueError, TypeError):
                        pass
                return None

            result["roe"] = _extract(["净资产收益率"])
            result["eps"] = _extract(["每股收益"])
            result["revenue_growth"] = _extract(["营业收入", "增长"])
            result["gross_margin"] = _extract(["毛利率"]) or _extract(["销售毛利率"])
            result["net_margin"] = _extract(["净利率"]) or _extract(["销售净利率"])
            result["debt_ratio"] = _extract(["资产负债率"])
            result["cash_flow_per_share"] = _extract(["每股经营现金"])

            result["available"] = any(v is not None for k, v in result.items() if k not in ("code", "available", "source"))
            result["source"] = "akshare"
            print(f"[FIN] {code} AKShare OK: ROE={result['roe']}, GM={result['gross_margin']}")
    except Exception as e:
        print(f"[FIN] {code} AKShare also failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_fund_holding_detail(code: str) -> dict:
    """获取基金持仓明细（前10大重仓股）"""
    cache_key = f"holding_{code}"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < 86400:
        return factor_cache[cache_key]["data"]

    result = {"code": code, "holdings": [], "update_date": "", "available": False}
    try:
        import akshare as ak
        df = ak.fund_portfolio_hold_em(symbol=code, date="2025")
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            name_col = next((c for c in cols if "股票名称" in c), None)
            pct_col = next((c for c in cols if "占净值比" in c), None)
            code_col = next((c for c in cols if "股票代码" in c), None)
            if name_col:
                for _, row in df.head(10).iterrows():
                    h = {"name": str(row.get(name_col, ""))}
                    if code_col:
                        h["code"] = str(row.get(code_col, ""))
                    if pct_col:
                        try:
                            h["pct"] = round(float(row.get(pct_col, 0)), 2)
                        except (ValueError, TypeError):
                            h["pct"] = 0
                    result["holdings"].append(h)
                result["available"] = True
                print(f"[HOLDING] {code} top={result['holdings'][0]['name'] if result['holdings'] else 'N/A'}")
    except Exception as e:
        print(f"[HOLDING] {code} Failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


