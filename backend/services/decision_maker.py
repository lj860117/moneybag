"""
钱袋子 — V7.3: 买卖决策引擎
收集所有模块数据 → R1 综合决策 → 输出具体操作建议（买/卖/持/加/减 + 仓位 + 理由）

输入：推荐引擎(V7.1) + 估值(V6.5) + 信号(13维) + 持仓 + 地缘 + 行业
输出：结构化决策 JSON + 三情景分析 + 决策日志（V8 复盘依赖）
"""

import os
import json
import time
import httpx
from datetime import datetime, date
from pathlib import Path
from config import DATA_DIR, LLM_API_URL, LLM_API_KEY

_decision_cache = {}
_DECISION_CACHE_TTL = 1800  # 30 分钟


def generate_decisions(user_id: str) -> dict:
    """买卖决策主函数

    Returns: {
        "decisions": [{symbol, name, action, position_pct, reason, confidence, risk_warning}],
        "scenarios": {optimistic, neutral, pessimistic},
        "overall_strategy": str,
        "market_regime": str,
        "generated_at": str,
    }
    """
    cache_key = f"decision_{user_id}"
    now = time.time()
    if cache_key in _decision_cache and now - _decision_cache[cache_key]["ts"] < _DECISION_CACHE_TTL:
        return _decision_cache[cache_key]["data"]

    print(f"[DECISION] 开始为 {user_id} 生成买卖决策...")

    # 1. 收集全量上下文
    context = _collect_decision_context(user_id)

    # 2. R1 综合决策
    decisions = _llm_decision(context, user_id)

    # 3. 如果 LLM 失败，降级规则引擎
    if decisions.get("error"):
        print(f"[DECISION] LLM 失败，降级规则引擎: {decisions['error']}")
        decisions = _rule_based_decision(context, user_id)

    # 4. 保存决策日志（V8 复盘依赖）
    _save_decision_log(user_id, decisions, context)

    decisions["generated_at"] = datetime.now().isoformat()
    _decision_cache[cache_key] = {"data": decisions, "ts": now}

    print(f"[DECISION] 完成: {len(decisions.get('decisions', []))} 条操作建议")
    return decisions


def _collect_decision_context(user_id: str) -> dict:
    """收集所有模块数据作为决策上下文"""
    ctx = {"user_id": user_id}

    # 持仓
    try:
        from services.stock_monitor import load_stock_holdings
        from services.fund_monitor import load_fund_holdings
        ctx["stock_holdings"] = load_stock_holdings(user_id) or []
        ctx["fund_holdings"] = load_fund_holdings(user_id) or []
    except Exception:
        ctx["stock_holdings"] = []
        ctx["fund_holdings"] = []

    # 推荐引擎 Top 5
    try:
        from services.recommend_engine import get_stock_recommendations
        rec = get_stock_recommendations(user_id, top_n=5, pool="hot")
        ctx["recommendations"] = rec.get("recommendations", [])[:5]
    except Exception as e:
        print(f"[DECISION] 推荐引擎失败: {e}")
        ctx["recommendations"] = []

    # 13维信号
    try:
        from services.signal import calculate_daily_signal
        signal = calculate_daily_signal()
        ctx["signal"] = {
            "overall": signal.get("overall"),
            "score": signal.get("score"),
            "confidence": signal.get("confidence"),
        }
    except Exception:
        ctx["signal"] = {"overall": "HOLD", "score": 0}

    # 地缘
    try:
        from services.geopolitical import get_geopolitical_risk_score
        geo = get_geopolitical_risk_score()
        ctx["geopolitical"] = {
            "severity": geo.get("severity", 0),
            "level": geo.get("level", "低"),
        }
    except Exception:
        ctx["geopolitical"] = {"severity": 0}

    # Regime
    try:
        from services.regime_engine import classify
        regime = classify()
        ctx["regime"] = regime.get("regime", "unknown")
    except Exception:
        ctx["regime"] = "unknown"

    # 行业热点
    try:
        from services.sector_rotation import get_sector_rotation
        sr = get_sector_rotation()
        if sr.get("available"):
            ctx["hot_sectors"] = [s.get("name", "") for s in sr.get("top_gainers", [])[:5]]
    except Exception:
        pass

    # ===== P0.1: 补采完整数据（解决信息饥荒） =====

    # 北向资金
    try:
        from services.factor_data import get_northbound_flow
        north = get_northbound_flow()
        if north.get("available"):
            ctx["northbound"] = {
                "net_flow_today": north.get("net_flow_today", 0),
                "net_flow_5d": north.get("net_flow_5d", 0),
                "net_flow_20d": north.get("net_flow_20d", 0),
                "trend": north.get("trend", ""),
            }
    except Exception as e:
        print(f"[DECISION] 北向资金采集失败: {e}")

    # 融资融券
    try:
        from services.factor_data import get_margin_trading
        margin = get_margin_trading()
        if margin.get("available"):
            ctx["margin"] = {
                "margin_balance": margin.get("margin_balance", 0),
                "margin_change_5d": margin.get("margin_change_5d", 0),
            }
    except Exception as e:
        print(f"[DECISION] 融资融券采集失败: {e}")

    # SHIBOR
    try:
        from services.factor_data import get_shibor
        shibor = get_shibor()
        if shibor.get("available"):
            ctx["shibor"] = {
                "overnight": shibor.get("overnight", 0),
                "trend": shibor.get("trend", ""),
            }
    except Exception as e:
        print(f"[DECISION] SHIBOR采集失败: {e}")

    # 新闻情绪
    try:
        from services.data_layer import get_news_sentiment_score
        sentiment = get_news_sentiment_score()
        if sentiment.get("available"):
            ctx["news_sentiment"] = {
                "score": sentiment.get("score", 0),
                "level": sentiment.get("level", "中性"),
                "reason": sentiment.get("reason", ""),
            }
    except Exception as e:
        print(f"[DECISION] 新闻情绪采集失败: {e}")

    # 研报共识
    try:
        from services.broker_research import get_broker_consensus
        br = get_broker_consensus()
        if br.get("available"):
            ctx["broker_consensus"] = {
                "total": br.get("total_reports", 0),
                "consensus": br.get("consensus", ""),
                "hot_industries": br.get("hot_industries", [])[:3],
                "risk_keywords": br.get("risk_keywords", [])[:3],
            }
    except Exception as e:
        print(f"[DECISION] 研报共识采集失败: {e}")

    # 大宗商品
    try:
        from services.market_factors import get_commodity_impact_assessment
        commodities = get_commodity_impact_assessment()
        if commodities:
            ctx["commodities"] = {
                "oil_price": commodities.get("brent_estimate", {}).get("price", 0),
                "oil_alert": commodities.get("brent_estimate", {}).get("alert_level", "normal"),
                "gold_price": commodities.get("gold", {}).get("price", 0),
            }
    except Exception as e:
        print(f"[DECISION] 大宗商品采集失败: {e}")

    # 恐贪指数 + 估值百分位
    try:
        from services.data_layer import get_fear_greed_index, get_valuation_percentile
        fgi = get_fear_greed_index()
        ctx["fear_greed"] = {"score": fgi.get("score", 50), "level": fgi.get("level", "中性")}
        val = get_valuation_percentile()
        ctx["valuation"] = {"percentile": val.get("percentile", 50), "pe": val.get("current_pe", 0)}
    except Exception as e:
        print(f"[DECISION] 恐贪/估值采集失败: {e}")

    # 13维信号分项详情（补充到已有的 signal 汇总之外）
    try:
        from services.signal import generate_daily_signal
        full_signal = generate_daily_signal()
        ctx["signal_details"] = full_signal.get("details", [])
    except Exception as e:
        print(f"[DECISION] 信号分项采集失败: {e}")

    return ctx


def _llm_decision(context: dict, user_id: str) -> dict:
    """用 R1 生成买卖决策"""
    if not LLM_API_KEY:
        return {"error": "LLM API Key 未配置"}

    # 构建 Prompt（prompt-engineering-expert Skill 指导）
    holdings_text = ""
    if context.get("stock_holdings"):
        holdings_text = "当前股票持仓：" + ", ".join(
            f"{h.get('name', h.get('code', ''))}({h.get('pnlPct', 0):+.1f}%)"
            for h in context["stock_holdings"][:10]
        )
    if context.get("fund_holdings"):
        holdings_text += "\n当前基金持仓：" + ", ".join(
            h.get("name", h.get("code", "")) for h in context["fund_holdings"][:10]
        )
    if not holdings_text:
        holdings_text = "当前空仓"

    rec_text = ""
    if context.get("recommendations"):
        rec_text = "推荐引擎 Top5：" + ", ".join(
            f"{r.get('name', '')}(评分{r.get('total_score', 0)})"
            for r in context["recommendations"]
        )

    signal = context.get("signal", {})
    geo = context.get("geopolitical", {})
    regime = context.get("regime", "unknown")

    # ===== P0.1: 构建完整数据段（解决信息饥荒） =====

    # 资金面
    capital_lines = []
    if context.get("northbound"):
        n = context["northbound"]
        capital_lines.append(f"- 北向资金: 今日{n['net_flow_today']:+.1f}亿，5日{n['net_flow_5d']:+.1f}亿，20日{n['net_flow_20d']:+.1f}亿，趋势={n['trend']}")
    if context.get("margin"):
        m = context["margin"]
        capital_lines.append(f"- 融资融券: 余额{m['margin_balance']:.0f}亿，5日变动{m['margin_change_5d']:+.1f}%")
    if context.get("shibor"):
        s = context["shibor"]
        capital_lines.append(f"- SHIBOR: 隔夜{s['overnight']}%，趋势={s['trend']}")
    capital_text = "\n".join(capital_lines) if capital_lines else "- 资金面数据暂不可用"

    # 情绪面
    sentiment_lines = []
    if context.get("fear_greed"):
        fg = context["fear_greed"]
        sentiment_lines.append(f"- 恐贪指数: {fg['score']:.0f}（{fg['level']}）")
    if context.get("news_sentiment"):
        ns = context["news_sentiment"]
        sentiment_lines.append(f"- 新闻情绪: {ns['score']:+d}分（{ns['level']}）{'，' + ns['reason'] if ns.get('reason') else ''}")
    sentiment_text = "\n".join(sentiment_lines) if sentiment_lines else "- 情绪面数据暂不可用"

    # 基本面
    fundamental_lines = []
    if context.get("valuation"):
        v = context["valuation"]
        fundamental_lines.append(f"- 估值百分位: {v['percentile']}%，当前PE={v['pe']}")
    if context.get("broker_consensus"):
        br = context["broker_consensus"]
        fundamental_lines.append(f"- 研报共识: {br['total']}篇，方向={br['consensus']}，热门行业={'、'.join(br['hot_industries'])}")
    fundamental_text = "\n".join(fundamental_lines) if fundamental_lines else "- 基本面数据暂不可用"

    # 大宗商品
    commodity_text = "- 暂不可用"
    if context.get("commodities"):
        c = context["commodities"]
        commodity_text = f"- 原油≈{c['oil_price']:.0f}美元/桶（{c['oil_alert']}），黄金={c['gold_price']:.0f}元/克"

    # 13维信号分项
    signal_detail_lines = []
    for d in context.get("signal_details", []):
        signal_detail_lines.append(f"  {d.get('category','')}/{d.get('name','')}: {d.get('score',0):+.1f}分（{d.get('weight','?')}）— {d.get('detail','')[:60]}")
    signal_detail_text = "\n".join(signal_detail_lines) if signal_detail_lines else "  数据暂不可用"

    # 地缘详情
    geo_text = f"severity={geo.get('severity', 0)}, level={geo.get('level', '低')}"

    prompt = f"""你是专业 A 股投资顾问，拥有 20 年从业经验。基于以下完整市场数据，为用户生成具体操作建议。

## 13维综合信号
- 信号: {signal.get('overall', 'HOLD')}，得分{signal.get('score', 0)}，置信度{signal.get('confidence', 0)}%
- 市场 Regime: {regime}

### 13维分项详情
{signal_detail_text}

## 资金面（聪明钱动向）
{capital_text}

## 情绪面
{sentiment_text}

## 基本面
{fundamental_text}

## 大宗商品
{commodity_text}

## 地缘风险
- {geo_text}
- 行业热点: {', '.join(context.get('hot_sectors', ['暂无']))}

## 用户持仓
{holdings_text}

## 推荐引擎
{rec_text or '暂无推荐'}

## 输出要求
严格输出以下 JSON 格式，不要有任何其他内容：
```json
{{
  "decisions": [
    {{
      "symbol": "代码",
      "name": "名称",
      "action": "buy/sell/hold/reduce/add",
      "position_pct": 5,
      "reason": "一句话理由（必须引用上述具体数据支撑）",
      "confidence": 75,
      "risk_warning": "主要风险"
    }}
  ],
  "scenarios": {{
    "optimistic": "乐观情景描述(1句话)",
    "neutral": "中性情景描述(1句话)",
    "pessimistic": "悲观情景描述(1句话)"
  }},
  "overall_strategy": "总体策略(1句话)"
}}
```

注意：
1. 空仓时推荐买入1-3只，有持仓时给出逐只操作建议
2. 决策理由必须引用具体数据（如"北向5日净流入145亿"），禁止空泛表述
3. 地缘 severity≥4 时降低仓位，severity=5 时暂停所有买入
4. action 只能是 buy/sell/hold/reduce/add 之一
5. position_pct 是建议仓位百分比(0-20)
6. confidence 是置信度(0-100)"""

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                LLM_API_URL,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-reasoner",  # R1 深度推理
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1500,
                    "temperature": 0.3,
                },
            )
            if resp.status_code == 200:
                import re
                text = resp.json()["choices"][0]["message"]["content"]
                json_match = re.search(r'\{[\s\S]*\}', text)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                        result["model"] = "deepseek-reasoner"
                        result["market_regime"] = regime
                        return result
                    except json.JSONDecodeError:
                        return {"error": "JSON 解析失败", "_raw": text[:300]}
                return {"error": "LLM 返回无 JSON"}
            return {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def _rule_based_decision(context: dict, user_id: str) -> dict:
    """规则引擎降级决策（0 token 成本）"""
    signal = context.get("signal", {})
    overall = signal.get("overall", "HOLD")
    regime = context.get("regime", "unknown")
    geo_severity = context.get("geopolitical", {}).get("severity", 0)

    decisions = []

    # 基于持仓生成操作建议
    for h in context.get("stock_holdings", []):
        pnl = h.get("pnlPct", 0) or 0
        if pnl > 30:
            decisions.append({
                "symbol": h.get("code", ""),
                "name": h.get("name", ""),
                "action": "reduce",
                "position_pct": 3,
                "reason": f"盈利{pnl:.1f}%，可适当止盈",
                "confidence": 60,
                "risk_warning": "注意回调风险",
            })
        elif pnl < -15:
            decisions.append({
                "symbol": h.get("code", ""),
                "name": h.get("name", ""),
                "action": "hold",
                "position_pct": 0,
                "reason": f"亏损{pnl:.1f}%，暂时持有观察",
                "confidence": 50,
                "risk_warning": "设好止损位",
            })
        else:
            decisions.append({
                "symbol": h.get("code", ""),
                "name": h.get("name", ""),
                "action": "hold",
                "position_pct": 0,
                "reason": "持仓正常，继续持有",
                "confidence": 55,
                "risk_warning": "",
            })

    # 基于推荐引擎补充买入建议
    if overall in ("BUY",) and geo_severity < 3:
        for r in context.get("recommendations", [])[:3]:
            if r.get("total_score", 0) >= 70:
                decisions.append({
                    "symbol": r.get("code", ""),
                    "name": r.get("name", ""),
                    "action": "buy",
                    "position_pct": 3,
                    "reason": r.get("reason", "推荐引擎高分"),
                    "confidence": 55,
                    "risk_warning": "规则引擎建议，仅供参考",
                })

    # 地缘风险高时全面降仓
    if geo_severity >= 4:
        for d in decisions:
            if d["action"] == "buy":
                d["action"] = "hold"
                d["reason"] = f"⚠️ 地缘风险 severity={geo_severity}，暂不建议买入"
                d["position_pct"] = 0

    return {
        "decisions": decisions,
        "scenarios": {
            "optimistic": "市场延续上涨，持仓获利",
            "neutral": "市场震荡，持仓不变",
            "pessimistic": "市场回调，注意止损",
        },
        "overall_strategy": f"信号={overall}，Regime={regime}，{'地缘风险高需谨慎' if geo_severity >= 3 else '正常操作'}",
        "model": "rule_engine",
        "market_regime": regime,
    }


def _save_decision_log(user_id: str, decisions: dict, context: dict):
    """保存决策日志（V8 复盘依赖）"""
    try:
        log_dir = DATA_DIR / "decisions" / user_id
        log_dir.mkdir(parents=True, exist_ok=True)
        filepath = log_dir / f"{date.today()}.json"

        log = {
            "date": str(date.today()),
            "decisions": decisions,
            "context_summary": {
                "signal": context.get("signal", {}),
                "signal_details": context.get("signal_details", []),
                "regime": context.get("regime"),
                "geo_severity": context.get("geopolitical", {}).get("severity", 0),
                "stock_count": len(context.get("stock_holdings", [])),
                "fund_count": len(context.get("fund_holdings", [])),
                "rec_count": len(context.get("recommendations", [])),
                # P0.1: 完整数据快照（V8 复盘归因用）
                "northbound": context.get("northbound"),
                "margin": context.get("margin"),
                "shibor": context.get("shibor"),
                "news_sentiment": context.get("news_sentiment"),
                "broker_consensus": context.get("broker_consensus"),
                "commodities": context.get("commodities"),
                "fear_greed": context.get("fear_greed"),
                "valuation": context.get("valuation"),
            },
            "saved_at": datetime.now().isoformat(),
        }

        filepath.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[DECISION] 决策日志已保存: {filepath}")
    except Exception as e:
        print(f"[DECISION] 日志保存失败: {e}")
