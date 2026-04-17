"""
钱袋子 — V4.5 新因子数据
北向资金、融资融券、国债收益率、SHIBOR、股息率、LLM情绪
V6 Phase 3: 新增北向持股行业分布推算 + enrich() Pipeline 接入
V6 Phase 3b: 北向资金从 AKShare 切换到 Tushare moneyflow_hsgt（消除 2024-08 数据断层）
             SHIBOR 从 AKShare 切换到 Tushare shibor（提升稳定性）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "factor_data",
    "scope": "public",
    "input": [],
    "output": "factors",
    "cost": "cpu",
    "tags": ["因子", "北向", "融资", "国债", "股息", "行业分布"],
    "description": "因子数据：北向资金(含持股行业分布)+融资融券+国债+SHIBOR+股息率+Pipeline enrich",
    "layer": "data",
    "priority": 2,
}
import os
import time
import json
from datetime import datetime, timedelta
from config import FACTOR_CACHE_TTL, LLM_API_URL, LLM_API_KEY, LLM_MODEL

factor_cache = {}

def get_northbound_flow() -> dict:
    """获取北向资金（沪股通+深股通）净流入数据

    V6 Phase 3b 架构：Tushare moneyflow_hsgt（主） → AKShare（降级）
    - Tushare: 5000积分，moneyflow_hsgt 数据持续更新到最新交易日
    - AKShare: stock_hsgt_hist_em 在 2024-08-16 后全 NaN，仅作为历史回看降级
    """
    cache_key = "northbound"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"net_flow_today": 0, "net_flow_5d": 0, "net_flow_20d": 0, "trend": "中性", "available": False,
              "source": "none"}

    # ── 方案 A（主）：Tushare moneyflow_hsgt ──
    try:
        from services.tushare_data import is_configured, get_northbound_flow as ts_north
        if is_configured():
            ts_data = ts_north(days=30)
            if ts_data.get("available"):
                result.update(ts_data)
                print(f"[NORTH] Tushare OK: today={result['net_flow_today']}亿, 5d={result['net_flow_5d']}亿")
                factor_cache[cache_key] = {"data": result, "ts": now}
                return result
            else:
                print("[NORTH] Tushare 返回但 available=False，降级到 AKShare")
    except Exception as e:
        print(f"[NORTH] Tushare failed, fallback AKShare: {e}")

    # ── 方案 B（降级）：AKShare stock_hsgt_hist_em ──
    # 注意：2024-08-16 后数据全 NaN，只能拿到历史趋势
    result["notice"] = "2024年8月起交易所不再披露每日北向净流入，AKShare 降级数据可能过旧"
    try:
        import akshare as ak
        df = None
        if hasattr(ak, "stock_hsgt_hist_em"):
            try:
                df = ak.stock_hsgt_hist_em(symbol="北向资金")
            except Exception:
                pass

        if df is not None and len(df) >= 20:
            val_col = next((c for c in df.columns if "净流入" in str(c) or "value" in str(c).lower()), None)
            date_col = next((c for c in df.columns if "日期" in str(c) or "date" in str(c).lower()), df.columns[0])
            if val_col:
                df = df.sort_values(date_col, ascending=True)
                valid_df = df.dropna(subset=[val_col])
                if len(valid_df) >= 20:
                    vals = valid_df[val_col].astype(float).values
                    result["net_flow_today"] = round(float(vals[-1]), 2)
                    result["net_flow_5d"] = round(float(sum(vals[-5:])), 2)
                    result["net_flow_20d"] = round(float(sum(vals[-20:])), 2)
                    result["available"] = True
                    result["source"] = "akshare_fallback"
                    # 检查数据是否过旧
                    try:
                        import pandas as pd
                        last_date = pd.to_datetime(valid_df[date_col].iloc[-1])
                        days_old = (datetime.now() - last_date).days
                        if days_old > 7:
                            result["notice"] = f"数据最后更新于{days_old}天前(AKShare降级)"
                            result["stale"] = True
                    except Exception:
                        result["stale"] = True

                    if result["net_flow_5d"] > 50:
                        result["trend"] = "大幅流入"
                    elif result["net_flow_5d"] > 10:
                        result["trend"] = "净流入"
                    elif result["net_flow_5d"] < -50:
                        result["trend"] = "大幅流出"
                    elif result["net_flow_5d"] < -10:
                        result["trend"] = "净流出"
                    print(f"[NORTH] AKShare fallback: today={result['net_flow_today']}亿, stale={result.get('stale')}")
    except Exception as e:
        print(f"[NORTH] AKShare also failed: {e}")

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_margin_trading() -> dict:
    """获取两融（融资融券）余额数据 — 市场杠杆情绪指标

    V6 Phase 4 架构：Tushare margin（主，沪+深+北全市场） → AKShare（降级，仅上交所）
    """
    cache_key = "margin"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"margin_balance": 0, "margin_change_5d": 0, "trend": "中性", "available": False}

    # ── 方案 A（主）：Tushare margin（沪+深+北合计） ──
    try:
        from services.tushare_data import is_configured, get_margin_data as ts_margin
        if is_configured():
            ts_data = ts_margin(days=30)
            if ts_data.get("available"):
                result["margin_balance"] = ts_data.get("margin_balance", 0)
                result["margin_change_5d"] = ts_data.get("margin_change_5d", 0)
                result["rzmre"] = ts_data.get("rzmre", 0)
                result["rqye"] = ts_data.get("rqye", 0)
                result["trend"] = ts_data.get("trend", "中性")
                result["available"] = True
                result["source"] = "tushare"
                result["data_date"] = ts_data.get("data_date", "")
                print(f"[MARGIN] Tushare OK: balance={result['margin_balance']}亿, trend={result['trend']}")
                factor_cache[cache_key] = {"data": result, "ts": now}
                return result
            else:
                print("[MARGIN] Tushare 返回但 available=False，降级到 AKShare")
    except Exception as e:
        print(f"[MARGIN] Tushare failed, fallback AKShare: {e}")

    # ── 方案 B（降级）：AKShare stock_margin_sse（仅上交所 ≈ 60%） ──
    try:
        import akshare as ak
        df = ak.stock_margin_sse()  # 仅上交所
        if df is not None and len(df) >= 20:
            bal_col = next((c for c in df.columns if "融资余额" in str(c)), None)
            if bal_col:
                vals = df[bal_col].astype(float).values
                current = vals[-1]
                prev_5d = vals[-6] if len(vals) >= 6 else vals[0]
                change_pct = (current - prev_5d) / prev_5d * 100 if prev_5d > 0 else 0
                result["margin_balance"] = round(current / 1e8, 2)
                result["margin_change_5d"] = round(change_pct, 2)
                result["available"] = True
                result["source"] = "akshare_fallback"
                result["notice"] = "仅上交所数据（约60%市场），Tushare 不可用时降级"
                if change_pct > 3:
                    result["trend"] = "杠杆快速上升"
                elif change_pct > 1:
                    result["trend"] = "杠杆温和上升"
                elif change_pct < -3:
                    result["trend"] = "杠杆快速下降"
                elif change_pct < -1:
                    result["trend"] = "杠杆温和下降"
                print(f"[MARGIN] AKShare fallback: balance={result['margin_balance']}亿(仅沪), 5d={change_pct:.2f}%")
    except Exception as e:
        print(f"[MARGIN] AKShare also failed: {e}")

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
    """获取 SHIBOR 利率（银行间市场流动性指标）

    V6 Phase 3b 架构：Tushare shibor（主） → AKShare rate_interbank（降级）
    """
    cache_key = "shibor"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < FACTOR_CACHE_TTL:
        return factor_cache[cache_key]["data"]

    result = {"overnight": 0, "one_week": 0, "trend": "中性", "available": False}

    # ── 方案 A（主）：Tushare shibor ──
    try:
        from services.tushare_data import is_configured, get_shibor_rate as ts_shibor
        if is_configured():
            ts_data = ts_shibor(days=30)
            if ts_data.get("available"):
                result["overnight"] = ts_data.get("overnight", 0)
                result["one_week"] = ts_data.get("one_week", 0)
                result["one_month"] = ts_data.get("one_month", 0)
                result["trend"] = ts_data.get("trend", "中性")
                result["available"] = True
                result["source"] = "tushare"
                result["data_date"] = ts_data.get("data_date", "")
                print(f"[SHIBOR] Tushare OK: ON={result['overnight']}%, trend={result['trend']}")
                factor_cache[cache_key] = {"data": result, "ts": now}
                return result
            else:
                print("[SHIBOR] Tushare 返回但 available=False，降级到 AKShare")
    except Exception as e:
        print(f"[SHIBOR] Tushare failed, fallback AKShare: {e}")

    # ── 方案 B（降级）：AKShare rate_interbank ──
    try:
        import akshare as ak
        df = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="隔夜")
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
                    result["source"] = "akshare_fallback"
                    if current > avg_5d * 1.2:
                        result["trend"] = "流动性收紧"
                    elif current < avg_5d * 0.8:
                        result["trend"] = "流动性宽松"
                    else:
                        result["trend"] = "流动性平稳"
                    print(f"[SHIBOR] AKShare fallback: ON={current}%, trend={result['trend']}")
    except Exception as e:
        print(f"[SHIBOR] AKShare also failed: {e}")

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
        # 方案A：用 stock_a_lg_indicator 获取个股股息率（沪深300成分股代表）
        # 方案B（降级）：从 stock_index_pe_lg 的数据推算
        dy_value = None

        # 尝试方案A：获取沪深300成分股加权股息率
        try:
            # 先用 stock_index_pe_lg 获取沪深300 PE，然后推算
            df = ak.stock_index_pe_lg(symbol="沪深300")
            if df is not None and len(df) > 0:
                # 检查是否有股息率列
                dy_col = next((c for c in df.columns if "股息率" in str(c)), None)
                if dy_col:
                    dy_vals = df[dy_col].dropna().astype(float)
                    if len(dy_vals) > 0:
                        dy_value = float(dy_vals.iloc[-1])
                else:
                    # 没有股息率列，尝试用 PE 推算（1/PE × 分红率估算）
                    pe_col = next((c for c in df.columns if "pe" in str(c).lower() or "市盈率" in str(c)), None)
                    if pe_col:
                        pe_vals = df[pe_col].dropna().astype(float)
                        if len(pe_vals) > 0:
                            current_pe = float(pe_vals.iloc[-1])
                            if 5 < current_pe < 100:  # 合理性校验
                                # 沪深300平均分红率约30%，股息率 ≈ 分红率 / PE
                                dy_value = round(30.0 / current_pe, 2)
                                print(f"[DIVIDEND] PE={current_pe}, estimated DY={dy_value}%")
        except Exception as e:
            print(f"[DIVIDEND] stock_index_pe_lg failed: {e}")

        # 方案B：尝试 stock_a_lg_indicator 获取代表性个股
        if dy_value is None:
            try:
                df2 = ak.stock_a_lg_indicator(symbol="000300")  # 沪深300指数
                if df2 is not None and len(df2) > 0:
                    dy_col2 = next((c for c in df2.columns if "股息率" in str(c) or "dy" in str(c).lower()), None)
                    if dy_col2:
                        dy_value = float(df2[dy_col2].dropna().iloc[-1])
            except Exception as e:
                print(f"[DIVIDEND] stock_a_lg_indicator failed: {e}")

        if dy_value is not None and dy_value > 0:
            result["dividend_yield"] = round(dy_value, 2)
            result["available"] = True
            # 简单评级
            if dy_value > 3:
                result["level"] = "高股息(价值凸显)"
            elif dy_value > 2:
                result["level"] = "中等股息"
            else:
                result["level"] = "低股息(成长偏好)"
            print(f"[DIVIDEND] yield={dy_value}%, level={result['level']}")

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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# V6 Phase 3: 北向持股行业分布推算
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_northbound_holdings() -> dict:
    """获取北向资金持股个股列表 → 按行业聚合持仓市值

    数据源: stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")
    返回: 2700+ 只持股个股含「所属板块」字段 → 按板块聚合总市值

    注意: 虽然 2024-08 后不再披露每日净流入，但持股数据仍在更新。
    这是比净流入更好的北向资金指标——直接看"外资重仓哪些行业"。
    """
    cache_key = "northbound_holdings"
    now = time.time()
    if cache_key in factor_cache and now - factor_cache[cache_key]["ts"] < 3600:
        return factor_cache[cache_key]["data"]

    result = {"available": False, "sectors": [], "total_stocks": 0, "total_market_value": 0}
    try:
        import akshare as ak
        df = ak.stock_hsgt_hold_stock_em(market="北向", indicator="今日排行")

        if df is None or len(df) < 100:
            result["error"] = "北向持股数据不足"
            factor_cache[cache_key] = {"data": result, "ts": now}
            return result

        # 找关键列
        sector_col = next((c for c in df.columns if "所属板块" in c or "板块" in c), None)
        mv_col = next((c for c in df.columns if "持股-市值" in c), None)

        if not sector_col:
            result["error"] = f"列名中无板块字段: {list(df.columns)}"
            factor_cache[cache_key] = {"data": result, "ts": now}
            return result

        # 按行业聚合
        import pandas as pd
        if mv_col:
            df[mv_col] = pd.to_numeric(df[mv_col], errors="coerce").fillna(0)
            sector_agg = df.groupby(sector_col).agg(
                stock_count=(sector_col, "size"),
                total_mv=(mv_col, "sum"),
            ).reset_index()
            sector_agg = sector_agg.sort_values("total_mv", ascending=False)
        else:
            # 没有市值列，只按个数聚合
            sector_agg = df.groupby(sector_col).size().reset_index(name="stock_count")
            sector_agg["total_mv"] = 0
            sector_agg = sector_agg.sort_values("stock_count", ascending=False)

        total_mv = sector_agg["total_mv"].sum()
        sectors = []
        for _, row in sector_agg.iterrows():
            name = str(row[sector_col])
            mv = round(float(row.get("total_mv", 0)), 2)
            cnt = int(row.get("stock_count", 0))
            pct = round(mv / max(total_mv, 1) * 100, 2) if total_mv > 0 else 0
            sectors.append({
                "name": name,
                "market_value": mv,
                "stock_count": cnt,
                "pct": pct,
            })

        result = {
            "available": True,
            "total_stocks": len(df),
            "total_sectors": len(sectors),
            "total_market_value": round(total_mv, 2),
            "top_sectors": sectors[:15],
            "sectors": sectors,
            "data_date": str(df.iloc[0].get("日期", "")) if "日期" in df.columns else "",
            "notice": "持股数据仍在更新（虽净流入停披露），反映外资行业偏好",
        }

        print(f"[NORTH] 持股行业分布: {len(sectors)}行业, TOP3={[s['name'] for s in sectors[:3]]}")

    except Exception as e:
        print(f"[NORTH] get_northbound_holdings failed: {e}")
        result["error"] = str(e)

    factor_cache[cache_key] = {"data": result, "ts": now}
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# V6 Phase 3: Pipeline enrich()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def enrich(ctx):
    """Pipeline Layer2 自动调用 — 注入北向资金 + 融资融券 + 国债 + 北向持股行业分布

    做 3 件事:
    1. 拉北向资金净流入（历史趋势，2024-08后可能 stale）
    2. 拉北向持股行业分布（V6 新增，用持股市值推算行业偏好）
    3. 拉融资融券 + 国债收益率 + SHIBOR 作为辅助因子
    """
    try:
        # 1. 北向资金传统数据
        north = get_northbound_flow()

        # 2. V6: 北向持股行业分布
        north_hold = get_northbound_holdings()

        # 3. 融资融券
        margin = get_margin_trading()

        # 4. 国债收益率 + SHIBOR
        treasury = get_treasury_yield()
        shibor = get_shibor()

        # 方向判断
        north_trend = north.get("trend", "中性")
        if north_trend in ("大幅流入", "净流入"):
            direction = "bullish"
            score = 0.65
        elif north_trend in ("大幅流出", "净流出"):
            direction = "bearish"
            score = 0.35
        else:
            direction = "neutral"
            score = 0.5

        # 融资情绪辅助
        margin_trend = margin.get("trend", "均衡")
        if margin_trend == "加杠杆" and direction != "bearish":
            score = min(score + 0.05, 0.7)
        elif margin_trend == "去杠杆" and direction != "bullish":
            score = max(score - 0.05, 0.3)

        # 构造 detail
        detail_parts = [f"北向:{north_trend}"]
        if north.get("available"):
            detail_parts.append(f"5日净流入{north.get('net_flow_5d', 0):.1f}亿")
        if north.get("stale"):
            detail_parts.append("⚠️北向净流入数据过旧")
        if north_hold.get("available"):
            top3 = [s["name"] for s in north_hold.get("top_sectors", [])[:3]]
            detail_parts.append(f"外资重仓:{','.join(top3)}")
        detail_parts.append(f"融资:{margin_trend}")
        if treasury.get("available"):
            detail_parts.append(f"10Y国债{treasury.get('yield_10y', 'N/A')}%")

        ctx.modules_results["factor_data"] = {
            "direction": direction,
            "score": score,
            "confidence": 50 if north.get("stale") else 65,
            "available": True,
            "detail": " | ".join(detail_parts),
            "north_trend": north_trend,
            "north_flow_5d": north.get("net_flow_5d", 0),
            "north_flow_20d": north.get("net_flow_20d", 0),
            "north_stale": north.get("stale", False),
            "north_holdings_available": north_hold.get("available", False),
            "north_top_sectors": north_hold.get("top_sectors", [])[:10],
            "north_total_stocks": north_hold.get("total_stocks", 0),
            "margin_trend": margin_trend,
            "margin_balance": margin.get("balance", 0),
            "treasury_10y": treasury.get("yield_10y"),
            "shibor_on": shibor.get("on"),
        }

        if "factor_data" not in ctx.modules_called:
            ctx.modules_called.append("factor_data")

    except Exception as e:
        print(f"[FACTOR] enrich failed: {e}")
        ctx.modules_results["factor_data"] = {
            "available": False,
            "error": str(e),
            "direction": "neutral",
            "score": 0.5,
        }

    return ctx
