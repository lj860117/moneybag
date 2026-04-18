"""
钱袋子 — V7.1: 推荐引擎
5 维评分（估值30% + 盈利25% + 技术15% + 资金15% + 风险15%）→ 排序 → R1 生成理由

候选池：Tushare report_rc 有研报覆盖的股票（≈200-300只）
评分来源：V6.5 盈利预测 + 已有 signal/factor/risk 模块
输出：Top N 推荐列表 + 5维雷达图数据 + R1推理理由 + 建议仓位
"""

MODULE_META = {
    "name": "recommend_engine",
    "scope": "public",
    "input": ["earnings_forecast", "valuation_engine", "factor_data"],
    "output": "recommendations",
    "cost": "llm_heavy",
    "tags": ["推荐", "评分", "选股", "配置"],
    "description": "V7推荐引擎：5维评分+R1理由+建议仓位",
    "layer": "analysis",
    "priority": 5,
}

import os
import time
import json
from datetime import datetime
from config import DATA_DIR

_rec_cache = {}
_REC_CACHE_TTL = 3600  # 1小时

# 5 维权重（可配置，V8 复盘可调）
RECOMMEND_WEIGHTS = {
    "valuation": 0.30,
    "earnings": 0.25,
    "technical": 0.15,
    "capital": 0.15,
    "risk": 0.15,
}

# V7.5 增强：按持有周期分类的权重
PERIOD_WEIGHTS = {
    "short": {  # 短线 1-2 周
        "valuation": 0.05, "earnings": 0.10, "technical": 0.40,
        "capital": 0.30, "risk": 0.15,
        "label": "短线（1-2周）", "icon": "⚡",
    },
    "medium": {  # 中线 1-3 月
        "valuation": 0.25, "earnings": 0.30, "technical": 0.15,
        "capital": 0.20, "risk": 0.10,
        "label": "中线（1-3月）", "icon": "📊",
    },
    "long": {  # 长线 6 月+
        "valuation": 0.35, "earnings": 0.30, "technical": 0.05,
        "capital": 0.10, "risk": 0.20,
        "label": "长线（6月+）", "icon": "🏦",
    },
}


def get_stock_recommendations(user_id: str = "", top_n: int = 10, pool: str = "hot", period: str = "medium") -> dict:
    """股票推荐主函数

    Args:
        user_id: 用户 ID
        top_n: 返回前 N 个推荐
        pool: 候选池 - "hot"(研报热门) / "hs300"(沪深300) / "all"
        period: 持有周期 - "short"(短线) / "medium"(中线) / "long"(长线)
    """
    # 根据周期选择权重
    weights = PERIOD_WEIGHTS.get(period, PERIOD_WEIGHTS["medium"])
    period_label = weights.get("label", "中线")

    # V7.5: 按用户风险偏好调整权重
    if user_id:
        try:
            from services.user_service import get_user_preference
            pref = get_user_preference(user_id)
            risk_type = pref.get("riskType", "balanced")
            if risk_type == "growth":
                # 进攻型：加技术+资金，减风险
                weights = {**weights, "technical": weights.get("technical", 0.15) + 0.05,
                           "capital": weights.get("capital", 0.15) + 0.05,
                           "risk": max(0.05, weights.get("risk", 0.15) - 0.10)}
                period_label += " · 进攻型"
            elif risk_type == "conservative":
                # 保守型：加估值+风险，减技术
                weights = {**weights, "valuation": weights.get("valuation", 0.30) + 0.10,
                           "risk": weights.get("risk", 0.15) + 0.05,
                           "technical": max(0.05, weights.get("technical", 0.15) - 0.10),
                           "capital": max(0.05, weights.get("capital", 0.15) - 0.05)}
                period_label += " · 保守型"
            elif risk_type == "balanced":
                period_label += " · 均衡型"
        except Exception:
            pass

    active_weights = {k: v for k, v in weights.items() if k in RECOMMEND_WEIGHTS}

    cache_key = f"rec_{user_id}_{pool}_{top_n}_{period}"
    now = time.time()
    if cache_key in _rec_cache and now - _rec_cache[cache_key]["ts"] < _REC_CACHE_TTL:
        return _rec_cache[cache_key]["data"]

    print(f"[RECOMMEND] 开始推荐: user={user_id}, pool={pool}, top={top_n}")

    # 1. 获取候选池
    candidates = _get_candidate_pool(pool)
    if not candidates:
        # 降级：尝试从 AKShare 拉热门股票
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                cols = list(df.columns)
                code_col = next((c for c in cols if "代码" in c), None)
                name_col = next((c for c in cols if "名称" in c), None)
                pe_col = next((c for c in cols if "市盈率" in c), None)
                if code_col and name_col:
                    # 取成交额 TOP 30（热门）
                    vol_col = next((c for c in cols if "成交额" in c), None)
                    if vol_col:
                        df = df.sort_values(vol_col, ascending=False)
                    for _, row in df.head(30).iterrows():
                        c = str(row[code_col])
                        if c.startswith(("0", "3", "6")):
                            candidates.append({
                                "code": c, "ts_code": c,
                                "name": str(row.get(name_col, "")),
                                "forecast_pe": float(row[pe_col]) if pe_col and row.get(pe_col) else None,
                                "rating": "", "source": "akshare_hot",
                            })
                    print(f"[RECOMMEND] 降级候选池(AKShare热门): {len(candidates)} 只")
        except Exception as e:
            print(f"[RECOMMEND] 降级候选池也失败: {e}")

    if not candidates:
        return {"recommendations": [], "pool_size": 0, "error": "候选池为空"}

    # 2. 逐个评分
    scored = []
    for stock in candidates[:50]:  # 最多评 50 只，控制 API 调用量
        try:
            s = _calc_composite_score(stock)
            if s:
                scored.append(s)
        except Exception as e:
            print(f"[RECOMMEND] 评分失败 {stock.get('code', '')}: {e}")

    # 3. 排序取 Top N
    scored.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    top = scored[:top_n]

    # 4. R1 生成推荐理由（批量）
    if top:
        _generate_reasons(top)

    # 5. 计算建议仓位
    for item in top:
        item["suggested_position"] = _calc_position(item)

    result = {
        "recommendations": top,
        "pool_size": len(candidates),
        "scored_count": len(scored),
        "generated_at": datetime.now().isoformat(),
        "weights": active_weights,
        "period": period,
        "period_label": period_label,
    }

    print(f"[RECOMMEND] 完成: 候选{len(candidates)} → 评分{len(scored)} → 推荐{len(top)}")

    _rec_cache[cache_key] = {"data": result, "ts": now}
    return result


def _get_candidate_pool(pool: str) -> list:
    """获取候选股票池"""
    candidates = []

    if pool == "hot":
        # 从研报中提取最近被覆盖的股票
        try:
            from services.tushare_data import _call_tushare
            rows = _call_tushare(
                "report_rc",
                {"limit": 200},
                "ts_code,name,report_date,eps,pe,roe,rating,max_price,min_price",
            )
            # 按 ts_code 去重，取最新
            seen = {}
            for r in rows:
                code = r.get("ts_code", "")
                if code and code not in seen:
                    seen[code] = {
                        "code": code.split(".")[0],
                        "ts_code": code,
                        "name": r.get("name", ""),
                        "forecast_eps": r.get("eps"),
                        "forecast_pe": r.get("pe"),
                        "forecast_roe": r.get("roe"),
                        "rating": r.get("rating", ""),
                        "target_high": r.get("max_price"),
                        "target_low": r.get("min_price"),
                    }
            candidates = list(seen.values())
            print(f"[RECOMMEND] 候选池(hot): {len(candidates)} 只")
        except Exception as e:
            print(f"[RECOMMEND] 获取候选池失败: {e}")

    return candidates


def _calc_composite_score(stock: dict) -> dict:
    """计算 5 维综合评分"""
    scores = {
        "valuation": _score_valuation(stock),
        "earnings": _score_earnings(stock),
        "technical": _score_technical(stock),
        "capital": _score_capital(stock),
        "risk": _score_risk(stock),
    }

    total = sum(scores[k] * RECOMMEND_WEIGHTS[k] for k in RECOMMEND_WEIGHTS)

    # 构造 evidence（每维度的打分依据）
    evidence = {}
    for dim, score in scores.items():
        evidence[dim] = {
            "score": score,
            "weight": f"{RECOMMEND_WEIGHTS[dim]*100:.0f}%",
        }

    return {
        **stock,
        "total_score": round(total, 1),
        "dimension_scores": scores,
        "evidence": evidence,
    }


def _score_valuation(stock: dict) -> int:
    """估值维度评分 (0-100)"""
    score = 50
    try:
        from services.valuation_engine import assess_valuation
        code = stock.get("code", "")
        if code:
            v = assess_valuation(code)
            if v.get("available"):
                return v.get("score", 50)
    except Exception:
        pass

    # 降级：直接用研报的 PE 和 ROE
    pe = stock.get("forecast_pe")
    if pe:
        pe = float(pe)
        if pe < 15:
            score = 80
        elif pe < 20:
            score = 65
        elif pe < 30:
            score = 50
        elif pe < 50:
            score = 35
        else:
            score = 20

    return score


def _score_earnings(stock: dict) -> int:
    """盈利维度评分 (0-100)"""
    score = 50
    roe = stock.get("forecast_roe")
    if roe:
        roe = float(roe)
        if roe > 25:
            score = 85
        elif roe > 15:
            score = 70
        elif roe > 10:
            score = 55
        elif roe > 5:
            score = 40
        else:
            score = 25

    # 评级加分
    rating = stock.get("rating", "")
    if rating in ("买入", "强烈推荐", "强推"):
        score = min(100, score + 10)
    elif rating in ("增持", "推荐"):
        score = min(100, score + 5)
    elif rating in ("减持", "卖出"):
        score = max(0, score - 15)

    return score


def _score_technical(stock: dict) -> int:
    """技术维度评分 (0-100) — 个股级 RSI/MACD/均线
    P0.2: 从空壳（永远50）改为真实评分
    """
    code = stock.get("code", "")
    if not code:
        return 50

    # 检查缓存（1小时 TTL，避免重复拉 K 线）
    cache_key = f"tech_{code}"
    now = time.time()
    if cache_key in _rec_cache and now - _rec_cache[cache_key].get("ts", 0) < 3600:
        return _rec_cache[cache_key].get("score", 50)

    try:
        import akshare as ak
        import numpy as np
        from datetime import datetime as _dt, timedelta as _td

        # 拉 60 日 K 线（前复权）
        end_date = _dt.now().strftime("%Y%m%d")
        start_date = (_dt.now() - _td(days=90)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or len(df) < 30:
            return 50

        close = df["收盘"].values.astype(float)
        score = 50

        # RSI(14)
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 100
        if rsi < 30:
            score += 20  # 超卖
        elif rsi < 40:
            score += 10
        elif rsi > 70:
            score -= 20  # 超买
        elif rsi > 60:
            score -= 10

        # MACD（12, 26, 9）
        def _ema(arr, span):
            result = np.zeros_like(arr)
            result[0] = arr[0]
            alpha = 2 / (span + 1)
            for i in range(1, len(arr)):
                result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
            return result

        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        macd_line = ema12 - ema26
        signal_line = _ema(macd_line, 9)
        if len(macd_line) >= 2 and len(signal_line) >= 2:
            if macd_line[-1] > signal_line[-1] and macd_line[-2] <= signal_line[-2]:
                score += 15  # 金叉
            elif macd_line[-1] < signal_line[-1] and macd_line[-2] >= signal_line[-2]:
                score -= 15  # 死叉
            elif macd_line[-1] > signal_line[-1]:
                score += 5   # 多头
            elif macd_line[-1] < signal_line[-1]:
                score -= 5   # 空头

        # MA20 位置
        if len(close) >= 20:
            ma20 = np.mean(close[-20:])
            if close[-1] > ma20:
                score += 10  # 站上均线
            else:
                score -= 10  # 跌破均线

        final_score = max(0, min(100, score))
        _rec_cache[cache_key] = {"score": final_score, "ts": now}
        return final_score

    except Exception as e:
        print(f"[RECOMMEND] 技术评分失败 {code}: {e}")
        return 50


def _score_capital(stock: dict) -> int:
    """资金维度评分 (0-100)"""
    score = 50
    try:
        from services.factor_data import get_northbound_flow, get_margin_trading
        north = get_northbound_flow()
        if north.get("trend") in ("大幅流入", "净流入"):
            score = 70
        elif north.get("trend") in ("大幅流出", "净流出"):
            score = 30
    except Exception:
        pass
    return score


def _score_risk(stock: dict) -> int:
    """风险维度评分 (0-100, 越高越安全)"""
    score = 60  # 默认中等安全
    try:
        from services.geopolitical import get_geopolitical_risk_score
        geo = get_geopolitical_risk_score()
        severity = geo.get("severity", 0)
        if severity >= 4:
            score = 30  # 高地缘风险
        elif severity >= 2:
            score = 50
        else:
            score = 75  # 无地缘风险
    except Exception:
        pass
    return score


def _generate_reasons(top_items: list):
    """用 R1 批量生成推荐理由"""
    try:
        from config import LLM_API_URL, LLM_API_KEY
        if not LLM_API_KEY:
            for item in top_items:
                item["reason"] = _rule_reason(item)
            return

        import httpx
        stocks_text = "\n".join(
            f"{i+1}. {item['name']}({item['code']}) 综合{item['total_score']}分 "
            f"估值={item['dimension_scores']['valuation']} "
            f"盈利={item['dimension_scores']['earnings']} "
            f"PE={item.get('forecast_pe', '?')} ROE={item.get('forecast_roe', '?')}% "
            f"评级={item.get('rating', '?')}"
            for i, item in enumerate(top_items[:5])
        )

        prompt = f"""你是 A 股投资顾问。以下是推荐引擎筛选出的 Top 股票，请为每只股票写一句话推荐理由（20-40字，说清楚为什么推荐）。

{stocks_text}

输出 JSON 数组，每项一句话：
[{{"code":"600519","reason":"一句话理由"}}, ...]
只输出 JSON，不要其他内容。"""

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                LLM_API_URL,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.5,
                },
            )
            if resp.status_code == 200:
                import re
                text = resp.json()["choices"][0]["message"]["content"]
                json_match = re.search(r'\[[\s\S]*\]', text)
                if json_match:
                    reasons = json.loads(json_match.group())
                    reason_map = {r.get("code", ""): r.get("reason", "") for r in reasons}
                    for item in top_items:
                        item["reason"] = reason_map.get(item.get("code", ""), _rule_reason(item))
                    return

    except Exception as e:
        print(f"[RECOMMEND] R1 理由生成失败: {e}")

    # 降级：规则理由
    for item in top_items:
        item["reason"] = _rule_reason(item)


def _rule_reason(item: dict) -> str:
    """规则引擎降级理由"""
    parts = []
    vs = item.get("dimension_scores", {})
    if vs.get("valuation", 0) >= 70:
        parts.append("估值偏低")
    if vs.get("earnings", 0) >= 70:
        parts.append("盈利能力强")
    rating = item.get("rating", "")
    if rating in ("买入", "推荐", "强推"):
        parts.append(f"机构评级{rating}")
    if not parts:
        parts.append("综合评分较高")
    return f"{item.get('name', '')}：{'，'.join(parts)}"


def _calc_position(item: dict) -> dict:
    """计算建议仓位"""
    score = item.get("total_score", 0)
    if score >= 80:
        return {"action": "建议买入", "position_pct": 5, "emoji": "🟢"}
    elif score >= 70:
        return {"action": "可以关注", "position_pct": 3, "emoji": "🟡"}
    elif score >= 60:
        return {"action": "观望", "position_pct": 0, "emoji": "⚪"}
    else:
        return {"action": "不推荐", "position_pct": 0, "emoji": "🔴"}
