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


def get_stock_recommendations(user_id: str = "", top_n: int = 10, pool: str = "hot") -> dict:
    """股票推荐主函数

    Args:
        user_id: 用户 ID（用于排除已持仓、匹配风险偏好）
        top_n: 返回前 N 个推荐
        pool: 候选池 - "hot"(研报热门) / "hs300"(沪深300) / "all"

    Returns: {
        "recommendations": [{code, name, total_score, dimension_scores, evidence, reason, suggested_position}],
        "pool_size": int,
        "generated_at": str,
    }
    """
    cache_key = f"rec_{user_id}_{pool}_{top_n}"
    now = time.time()
    if cache_key in _rec_cache and now - _rec_cache[cache_key]["ts"] < _REC_CACHE_TTL:
        return _rec_cache[cache_key]["data"]

    print(f"[RECOMMEND] 开始推荐: user={user_id}, pool={pool}, top={top_n}")

    # 1. 获取候选池
    candidates = _get_candidate_pool(pool)
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
        "weights": RECOMMEND_WEIGHTS,
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
    """技术维度评分 (0-100)"""
    score = 50
    try:
        code = stock.get("code", "")
        if code:
            from services.market_data import get_technical_indicators
            # 注意：get_technical_indicators 是按指数（沪深300）的
            # 个股技术面暂用默认分
            pass
    except Exception:
        pass
    return score


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
                f"{LLM_API_URL}/chat/completions",
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
