"""
钱袋子 — 信号引擎
13维多因子综合信号 + 大师策略 + 智能定投 + 止盈策略

V6 Phase 2: 12维→13维，新增"地缘面"因子
  宏观面 10% → 宏观面 5% + 地缘面 5%
"""
import math
from datetime import datetime

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "signal",
    "scope": "public",
    "input": [],
    "output": "daily_signal",
    "cost": "cpu",
    "tags": ["信号", "13维", "技术指标", "定投", "止盈", "地缘面"],
    "description": "13维多因子综合信号+大师策略+智能定投+止盈策略(V6:含地缘面)",
    "layer": "analysis",
    "priority": 1,
}

from config import (
    SIGNAL_WEIGHTS_V5, FACTOR_WEIGHTS, VALUATION_EXTREME, VALUATION_HIGH, VALUATION_LOW,
    DCA_MULTIPLIERS,
)
from services.data_layer import (
    get_fear_greed_index, get_valuation_percentile, get_technical_indicators,
    get_northbound_flow, get_margin_trading, get_treasury_yield,
    get_shibor, get_dividend_yield, get_news_sentiment_score,
    get_macro_calendar, get_market_news,
)

def calc_smart_dca(base_amount: float, valuation_pct: float) -> dict:
    """智能定投：根据估值百分位动态调整定投金额
    低估多买，高估少买，极度高估暂停
    """
    if valuation_pct < 20:
        multiplier = 1.5
        advice = "极度低估，建议定投 1.5 倍"
    elif valuation_pct < 30:
        multiplier = 1.3
        advice = "低估区间，建议定投 1.3 倍"
    elif valuation_pct < 50:
        multiplier = 1.1
        advice = "偏低估，建议定投 1.1 倍"
    elif valuation_pct < 70:
        multiplier = 1.0
        advice = "估值适中，正常定投"
    elif valuation_pct < 85:
        multiplier = 0.7
        advice = "偏高估，建议定投 0.7 倍"
    else:
        multiplier = 0.3
        advice = "极度高估，建议大幅减少或暂停定投"

    return {
        "baseAmount": round(base_amount, 2),
        "multiplier": multiplier,
        "smartAmount": round(base_amount * multiplier, 2),
        "advice": advice,
        "valuationPct": valuation_pct,
    }


def calc_take_profit_strategy(cost: float, market_value: float, profile: str) -> dict:
    """止盈止损策略：根据风险类型给目标收益率和止损线"""
    # 不同风险类型的止盈止损参数
    params = {
        "保守型": {"target_pct": 15, "stop_loss_pct": -8, "partial_pct": 10},
        "稳健型": {"target_pct": 20, "stop_loss_pct": -10, "partial_pct": 15},
        "平衡型": {"target_pct": 30, "stop_loss_pct": -15, "partial_pct": 20},
        "进取型": {"target_pct": 50, "stop_loss_pct": -20, "partial_pct": 30},
        "激进型": {"target_pct": 80, "stop_loss_pct": -25, "partial_pct": 40},
    }
    p = params.get(profile, params["平衡型"])

    current_pnl_pct = ((market_value - cost) / cost * 100) if cost > 0 else 0
    target_value = cost * (1 + p["target_pct"] / 100)
    stop_loss_value = cost * (1 + p["stop_loss_pct"] / 100)

    # 判断当前状态
    if current_pnl_pct >= p["target_pct"]:
        status = "reached_target"
        action = f"🎯 已达止盈目标！建议卖出 {p['partial_pct']}% 锁定利润，剩余继续持有。"
    elif current_pnl_pct >= p["partial_pct"]:
        status = "partial_profit"
        action = f"📈 收益不错（+{current_pnl_pct:.1f}%），可考虑止盈一小部分（20-30%），剩余继续持有。"
    elif current_pnl_pct <= p["stop_loss_pct"]:
        status = "stop_loss"
        action = f"⚠️ 亏损已达 {current_pnl_pct:.1f}%，接近止损线。检查基金基本面是否变化，若无问题可继续持有甚至加仓。"
    elif current_pnl_pct < 0:
        status = "in_loss"
        action = f"📉 当前浮亏 {current_pnl_pct:.1f}%，离止损线还有空间。保持耐心，继续定投摊低成本。"
    else:
        status = "holding"
        action = f"✅ 当前盈利 +{current_pnl_pct:.1f}%，距止盈目标还有 {p['target_pct'] - current_pnl_pct:.1f}%，继续持有。"

    return {
        "currentPnlPct": round(current_pnl_pct, 2),
        "targetPct": p["target_pct"],
        "stopLossPct": p["stop_loss_pct"],
        "targetValue": round(target_value, 2),
        "stopLossValue": round(stop_loss_value, 2),
        "status": status,
        "action": action,
        "profile": profile,
    }


# ============================================================
# V4.5 多因子智能信号引擎（12维：技术面+资金面+基本面+情绪面+宏观面）
# 借鉴幻方量化多因子体系，散户成本实现专业级分析
# ============================================================

def generate_daily_signal() -> dict:
    """生成每日综合交易信号 — 13维多因子融合 + 大师策略"""
    signal = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "overall": "HOLD",
        "confidence": 0,
        "summary": "",
        "details": [],
        "masterStrategies": [],
        "smartDca": None,
        "sentiment": None,
        "riskMetrics": None,
        "version": "5.0",
    }

    scores = []  # (score, weight, name, detail, category)

    # P0.3: 权重从 config.SIGNAL_WEIGHTS_V5 读取（Single Source of Truth）
    _w = SIGNAL_WEIGHTS_V5

    # ===== 技术面因子 (权重合计 25%) =====

    # --- 1. RSI 信号 ---
    tech = get_technical_indicators()
    rsi = tech.get("rsi", 50)
    if rsi < 25:
        rsi_score, rsi_detail = 80, f"RSI={rsi}，极度超卖，强烈买入信号"
    elif rsi < 30:
        rsi_score, rsi_detail = 60, f"RSI={rsi}，超卖区，偏向买入"
    elif rsi < 45:
        rsi_score, rsi_detail = 20, f"RSI={rsi}，偏低，轻度看多"
    elif rsi <= 55:
        rsi_score, rsi_detail = 0, f"RSI={rsi}，中性区间"
    elif rsi <= 70:
        rsi_score, rsi_detail = -20, f"RSI={rsi}，偏高，注意风险"
    elif rsi <= 80:
        rsi_score, rsi_detail = -60, f"RSI={rsi}，超买区，偏向卖出"
    else:
        rsi_score, rsi_detail = -80, f"RSI={rsi}，极度超买，强烈卖出信号"
    scores.append((rsi_score, _w["RSI"], "RSI", rsi_detail, "技术面"))

    # --- 2. MACD 信号 ---
    macd = tech.get("macd", {})
    trend = macd.get("trend", "")
    if "金叉" in trend:
        macd_score, macd_detail = 70, f"MACD金叉（{trend}），趋势转多"
    elif "多头" in trend:
        macd_score, macd_detail = 30, f"MACD多头排列，上升趋势持续"
    elif "死叉" in trend:
        macd_score, macd_detail = -70, f"MACD死叉（{trend}），趋势转空"
    elif "空头" in trend:
        macd_score, macd_detail = -30, f"MACD空头排列，下降趋势持续"
    else:
        macd_score, macd_detail = 0, "MACD数据不足"
    scores.append((macd_score, _w["MACD"], "MACD", macd_detail, "技术面"))

    # --- 3. 布林带信号 ---
    boll = tech.get("bollinger", {})
    pos = boll.get("position", "")
    if "超卖" in pos:
        boll_score, boll_detail = 60, "价格低于布林下轨，超卖反弹机会"
    elif "下方" in pos:
        boll_score, boll_detail = 15, "价格在中轨下方，偏弱但未到极端"
    elif "上方" in pos:
        boll_score, boll_detail = -15, "价格在中轨上方，偏强但注意回调"
    elif "超买" in pos:
        boll_score, boll_detail = -60, "价格高于布林上轨，超买回调风险"
    else:
        boll_score, boll_detail = 0, "布林带数据不足"
    scores.append((boll_score, _w["布林带"], "布林带", boll_detail, "技术面"))

    # ===== 基本面因子 (权重合计 30%) =====

    # --- 4. 估值百分位 (18%) --- 最重要
    val = get_valuation_percentile()
    vp = val.get("percentile", 50)
    if vp < 15:
        val_score, val_detail = 90, f"估值百分位{vp}%，极度低估（历史最佳买入区）"
    elif vp < 30:
        val_score, val_detail = 60, f"估值百分位{vp}%，低估区间（适合加仓）"
    elif vp < 50:
        val_score, val_detail = 20, f"估值百分位{vp}%，偏低估（正常定投）"
    elif vp < 70:
        val_score, val_detail = -10, f"估值百分位{vp}%，适中偏高（谨慎加仓）"
    elif vp < 85:
        val_score, val_detail = -50, f"估值百分位{vp}%，偏高估（减少定投）"
    else:
        val_score, val_detail = -80, f"估值百分位{vp}%，极度高估（建议暂停或减仓）"
    scores.append((val_score, _w["估值"], "估值", val_detail, "基本面"))

    # --- 5. 股息率因子 (5%) --- NEW
    dy = get_dividend_yield()
    if dy.get("available"):
        dy_pct = dy.get("percentile", 50)
        dy_val = dy.get("dividend_yield", 0)
        if dy_pct > 70:
            dy_score, dy_detail = 50, f"股息率{dy_val}%（百分位{dy_pct}%），价值凸显"
        elif dy_pct > 40:
            dy_score, dy_detail = 10, f"股息率{dy_val}%（百分位{dy_pct}%），中性"
        else:
            dy_score, dy_detail = -20, f"股息率{dy_val}%（百分位{dy_pct}%），成长偏好期"
    else:
        dy_score, dy_detail = 0, "股息率数据暂不可用"
    scores.append((dy_score, _w["股息率"], "股息率", dy_detail, "基本面"))

    # --- 6. 国债收益率/股债性价比 (7%) --- NEW
    treasury = get_treasury_yield()
    if treasury.get("available"):
        y10 = treasury.get("yield_10y", 2.5)
        premium = treasury.get("equity_premium", "")
        pe = val.get("current_pe", 12)
        if pe > 0:
            eq_yield = 1 / pe * 100
            spread = eq_yield - y10
            if spread > 4:
                tr_score, tr_detail = 60, f"10Y国债{y10}%，股债价差{spread:.1f}%，股市极有吸引力"
            elif spread > 2:
                tr_score, tr_detail = 30, f"10Y国债{y10}%，股债价差{spread:.1f}%，股市有吸引力"
            elif spread > 0:
                tr_score, tr_detail = 0, f"10Y国债{y10}%，股债价差{spread:.1f}%，股债相当"
            else:
                tr_score, tr_detail = -40, f"10Y国债{y10}%，股债价差{spread:.1f}%，债券更有吸引力"
        else:
            tr_score, tr_detail = 0, f"10Y国债{y10}%，估值数据不足"
    else:
        tr_score, tr_detail = 0, "国债收益率数据暂不可用"
    scores.append((tr_score, _w["股债性价比"], "股债性价比", tr_detail, "基本面"))

    # ===== 资金面因子 (权重合计 20%) =====

    # --- 7. 北向资金 (10%) --- NEW 聪明钱风向标
    north = get_northbound_flow()
    if north.get("available"):
        flow_5d = north.get("net_flow_5d", 0)
        flow_today = north.get("net_flow_today", 0)
        if flow_5d > 100:
            north_score, north_detail = 70, f"北向资金5日净流入{flow_5d:.0f}亿，今日{flow_today:.0f}亿，外资大举买入"
        elif flow_5d > 30:
            north_score, north_detail = 40, f"北向资金5日净流入{flow_5d:.0f}亿，外资持续流入"
        elif flow_5d > 0:
            north_score, north_detail = 15, f"北向资金5日净流入{flow_5d:.0f}亿，小幅流入"
        elif flow_5d > -30:
            north_score, north_detail = -15, f"北向资金5日净流出{abs(flow_5d):.0f}亿，小幅流出"
        elif flow_5d > -100:
            north_score, north_detail = -40, f"北向资金5日净流出{abs(flow_5d):.0f}亿，外资持续撤退"
        else:
            north_score, north_detail = -70, f"北向资金5日净流出{abs(flow_5d):.0f}亿，外资大幅撤退"
    else:
        north_score, north_detail = 0, "北向资金数据暂不可用"
    scores.append((north_score, _w["北向资金"], "北向资金", north_detail, "资金面"))

    # --- 8. 融资融券 (5%) --- NEW 市场杠杆情绪
    margin = get_margin_trading()
    if margin.get("available"):
        m_change = margin.get("margin_change_5d", 0)
        m_bal = margin.get("margin_balance", 0)
        if m_change > 3:
            margin_score, margin_detail = -30, f"融资余额{m_bal:.0f}亿，5日增{m_change:.1f}%，杠杆快速上升（过热风险）"
        elif m_change > 1:
            margin_score, margin_detail = 15, f"融资余额{m_bal:.0f}亿，5日增{m_change:.1f}%，温和加杠杆"
        elif m_change < -3:
            margin_score, margin_detail = 30, f"融资余额{m_bal:.0f}亿，5日降{abs(m_change):.1f}%，去杠杆（恐慌中可能见底）"
        elif m_change < -1:
            margin_score, margin_detail = -15, f"融资余额{m_bal:.0f}亿，5日降{abs(m_change):.1f}%，温和去杠杆"
        else:
            margin_score, margin_detail = 0, f"融资余额{m_bal:.0f}亿，杠杆水平稳定"
    else:
        margin_score, margin_detail = 0, "融资融券数据暂不可用"
    scores.append((margin_score, _w["融资融券"], "融资融券", margin_detail, "资金面"))

    # --- 9. SHIBOR 流动性 (5%) --- NEW
    shibor = get_shibor()
    if shibor.get("available"):
        overnight = shibor.get("overnight", 1.5)
        shibor_trend = shibor.get("trend", "中性")
        if "宽松" in shibor_trend:
            shibor_score, shibor_detail = 30, f"SHIBOR隔夜{overnight}%，{shibor_trend}，利好权益市场"
        elif "收紧" in shibor_trend:
            shibor_score, shibor_detail = -30, f"SHIBOR隔夜{overnight}%，{shibor_trend}，流动性承压"
        else:
            shibor_score, shibor_detail = 0, f"SHIBOR隔夜{overnight}%，{shibor_trend}"
    else:
        shibor_score, shibor_detail = 0, "SHIBOR数据暂不可用"
    scores.append((shibor_score, _w["SHIBOR"], "SHIBOR", shibor_detail, "资金面"))

    # ===== 情绪面因子 (权重合计 15%) =====

    # --- 10. 恐惧贪婪指数 (8%) ---
    fgi_data = get_fear_greed_index()
    fgi = fgi_data.get("score", 50)
    if fgi >= 80:
        fgi_score, fgi_detail = 80, f"恐惧指数{fgi:.0f}（极度恐惧），别人恐惧时贪婪"
    elif fgi >= 65:
        fgi_score, fgi_detail = 40, f"恐惧指数{fgi:.0f}（恐惧），市场偏悲观"
    elif fgi >= 40:
        fgi_score, fgi_detail = 0, f"恐惧指数{fgi:.0f}（中性）"
    elif fgi >= 25:
        fgi_score, fgi_detail = -40, f"恐惧指数{fgi:.0f}（贪婪），市场偏乐观"
    else:
        fgi_score, fgi_detail = -80, f"恐惧指数{fgi:.0f}（极度贪婪），别人贪婪时恐惧"
    scores.append((fgi_score, _w["恐贪指数"], "恐贪指数", fgi_detail, "情绪面"))

    # --- 11. LLM新闻情绪 (7%) --- NEW 核心创新
    sentiment = get_news_sentiment_score()
    if sentiment.get("available"):
        sent_score_raw = sentiment.get("score", 0)
        sent_level = sentiment.get("level", "中性")
        sent_source = sentiment.get("source", "unknown")
        # 情绪分数直接映射（-100~+100 → -80~+80）
        sent_score = max(-80, min(80, int(sent_score_raw * 0.8)))
        sent_detail = f"新闻情绪{sent_score_raw:+d}分（{sent_level}），来源:{sent_source}"
        if sentiment.get("reason"):
            sent_detail += f"，{sentiment['reason']}"
    else:
        sent_score, sent_detail = 0, "新闻情绪数据暂不可用"
    scores.append((sent_score, _w["新闻情绪"], "新闻情绪", sent_detail, "情绪面"))
    signal["sentiment"] = sentiment

    # ===== 宏观面因子 (权重 5%，V6: 从10%拆出5%给地缘面) =====

    # --- 12. 宏观经济信号 (5%) ---
    macro = get_macro_calendar()
    macro_score = 0
    macro_parts = []
    for e in macro:
        v = e.get("value", "")
        name = e.get("name", "")
        try:
            num = float(str(v).replace("%", ""))
            if "PMI" in name:
                if num > 50:
                    macro_score += 15
                    macro_parts.append(f"PMI={num}(扩张)")
                else:
                    macro_score -= 15
                    macro_parts.append(f"PMI={num}(收缩)")
            elif "M2" in name:
                if num > 8:
                    macro_score += 10
                    macro_parts.append(f"M2增速{num}%(宽松)")
                elif num < 6:
                    macro_score -= 10
                    macro_parts.append(f"M2增速{num}%(偏紧)")
        except (ValueError, TypeError):
            pass
    macro_detail = "宏观环境：" + ("、".join(macro_parts) if macro_parts else "暂无可量化数据")
    scores.append((max(-50, min(50, macro_score)), _w["宏观经济"], "宏观经济", macro_detail, "宏观面"))

    # ===== 地缘面因子 (权重 5%，V6 Phase 2 新增) =====

    # --- 13. 地缘政治风险 (5%) ---
    try:
        from services.geopolitical import get_geopolitical_risk_score
        geo_risk = get_geopolitical_risk_score()
        if geo_risk.get("available"):
            geo_score_raw = geo_risk.get("score", 0)  # 0-100, 越高越危险
            geo_level = geo_risk.get("level", "low")
            geo_top = geo_risk.get("top_events", [])

            # 风险分 → 信号分：风险0→信号+30(安全利好), 风险100→信号-80(极端bearish)
            if geo_score_raw >= 80:
                geo_signal = -80
                geo_detail = f"🔴 地缘极端风险(score={geo_score_raw},{geo_level})"
            elif geo_score_raw >= 60:
                geo_signal = -50
                geo_detail = f"🟠 地缘高风险(score={geo_score_raw},{geo_level})"
            elif geo_score_raw >= 30:
                geo_signal = -20
                geo_detail = f"🟡 地缘中等风险(score={geo_score_raw},{geo_level})"
            elif geo_score_raw > 0:
                geo_signal = 0
                geo_detail = f"地缘低风险(score={geo_score_raw},{geo_level})"
            else:
                geo_signal = 30
                geo_detail = f"✅ 无地缘风险，市场环境稳定"

            # 追加 top 事件描述
            if geo_top:
                top_titles = [e.get("title", "")[:30] for e in geo_top[:2]]
                geo_detail += "，" + "；".join(top_titles)
        else:
            geo_signal, geo_detail = 0, "地缘风险数据暂不可用"
    except Exception as e:
        print(f"[SIGNAL] 地缘面因子获取失败: {e}")
        geo_signal, geo_detail = 0, f"地缘风险数据异常({e})"
    scores.append((geo_signal, _w["地缘风险"], "地缘风险", geo_detail, "地缘面"))

    # ===== 加权综合 =====
    total_score = sum(s * w for s, w, _, _, _ in scores)
    total_weight = sum(w for _, w, _, _, _ in scores)
    final_score = total_score / total_weight if total_weight > 0 else 0

    # --- 信号判定 ---
    if final_score >= 40:
        signal["overall"] = "STRONG_BUY"
        signal["summary"] = "🟢 强烈买入信号 — 13维多因子共振看多，是较好的加仓时机"
    elif final_score >= 20:
        signal["overall"] = "BUY"
        signal["summary"] = "🟢 买入信号 — 整体偏向看多，适合按计划定投或小额加仓"
    elif final_score >= -20:
        signal["overall"] = "HOLD"
        signal["summary"] = "🟡 持有观望 — 信号中性，维持当前仓位，不急着操作"
    elif final_score >= -40:
        signal["overall"] = "SELL"
        signal["summary"] = "🟠 减仓信号 — 整体偏空，建议减少定投金额或部分止盈"
    else:
        signal["overall"] = "STRONG_SELL"
        signal["summary"] = "🔴 强烈减仓 — 多个指标共振看空，建议止盈或暂停买入"

    signal["confidence"] = min(abs(final_score), 100)
    signal["score"] = round(final_score, 1)
    signal["details"] = [
        {"name": name, "score": round(s, 1), "weight": f"{w*100:.0f}%", "detail": detail, "category": cat}
        for s, w, name, detail, cat in scores
    ]

    # 按类别分组
    signal["factorGroups"] = {}
    for s, w, name, detail, cat in scores:
        if cat not in signal["factorGroups"]:
            signal["factorGroups"][cat] = {"factors": [], "totalWeight": 0, "weightedScore": 0}
        signal["factorGroups"][cat]["factors"].append({"name": name, "score": round(s, 1), "weight": f"{w*100:.0f}%"})
        signal["factorGroups"][cat]["totalWeight"] += w
        signal["factorGroups"][cat]["weightedScore"] += s * w

    # --- 大师策略 ---
    signal["masterStrategies"] = _apply_master_strategies(val, fgi_data, tech)

    # --- 智能定投建议 ---
    signal["smartDca"] = calc_smart_dca(1000, vp)

    return signal


def _apply_master_strategies(val: dict, fgi_data: dict, tech: dict) -> list:
    """应用投资大师策略"""
    strategies = []
    vp = val.get("percentile", 50)
    pe = val.get("current_pe", 0)
    fgi = fgi_data.get("score", 50)
    rsi = tech.get("rsi", 50)

    # 巴菲特价值投资（幻方量化逻辑：估值为核心，情绪为辅助）
    buffett_signal = "HOLD"
    if vp < 20 and fgi >= 65:
        buffett_signal = "STRONG_BUY"
        buffett_msg = f"🔥 极度低估({vp}%) + 市场恐惧({fgi:.0f})！巴菲特的黄金时刻——\"别人恐惧时我贪婪\"。"
    elif vp < 30 and fgi >= 50:
        buffett_signal = "BUY"
        buffett_msg = f"✅ 估值低({vp}%) + 市场偏恐惧({fgi:.0f})，巴菲特会果断买入优质资产。"
    elif vp < 40:
        buffett_signal = "HOLD_BUY"
        buffett_msg = f"估值尚可({vp}%)，巴菲特会耐心等待更好价格，但已可以开始建仓。"
    elif vp >= 85:
        buffett_signal = "SELL"
        buffett_msg = f"⚠️ 极度高估({vp}%)！巴菲特会说\"无论市场情绪如何，这个价格不值得持有\"。建议减仓或暂停买入。"
    elif vp > 70:
        buffett_signal = "SELL" if fgi < 40 else "HOLD"
        buffett_msg = f"⚠️ 估值偏高({vp}%){'+ 市场贪婪(' + str(round(fgi)) + ')' if fgi < 40 else ''}，巴菲特会谨慎——\"别人贪婪时我恐惧\"。" if fgi < 40 else f"估值偏高({vp}%)但市场情绪({fgi:.0f})未极端贪婪，巴菲特会持仓观望但不再加仓。"
    else:
        buffett_msg = f"估值{vp}%处于中间区域，巴菲特会说\"价格合理但不便宜\"，保持耐心等待。"
    strategies.append({
        "master": "巴菲特",
        "philosophy": "价值投资：低估时买入优质资产，长期持有",
        "signal": buffett_signal,
        "message": buffett_msg,
        "icon": "🧓",
    })

    # 格雷厄姆安全边际
    graham_signal = "HOLD"
    if vp < 25:
        graham_signal = "BUY"
        graham_msg = f"✅ 安全边际充足！估值百分位{vp}%，远低于内在价值。格雷厄姆建议果断买入。"
    elif vp < 40:
        graham_signal = "HOLD_BUY"
        graham_msg = f"安全边际尚可({vp}%)。格雷厄姆会建议分批买入，不要一次性重仓。"
    elif vp > 75:
        graham_signal = "SELL"
        graham_msg = f"⚠️ 安全边际不足！估值百分位{vp}%，格雷厄姆会建议减仓或换入防御性资产。"
    else:
        graham_msg = f"估值{vp}%在中间区域。格雷厄姆会说\"保持耐心，等待安全边际出现\"。"
    strategies.append({
        "master": "格雷厄姆",
        "philosophy": "安全边际：只在价格远低于内在价值时买入",
        "signal": graham_signal,
        "message": graham_msg,
        "icon": "📚",
    })

    # 彼得·林奇成长投资
    lynch_signal = "HOLD"
    macro = get_macro_calendar()
    pmi_val = None
    for e in macro:
        if "PMI" in e.get("name", ""):
            try:
                pmi_val = float(str(e.get("value", "")).replace("%", ""))
            except (ValueError, TypeError):
                pass
    if pmi_val and pmi_val > 50 and vp < 50:
        lynch_signal = "BUY"
        lynch_msg = f"✅ 经济扩张(PMI={pmi_val}) + 估值合理({vp}%)。林奇会说\"跟着经济增长投资\"。"
    elif pmi_val and pmi_val < 50 and vp > 60:
        lynch_signal = "SELL"
        lynch_msg = f"⚠️ 经济收缩(PMI={pmi_val}) + 估值偏高({vp}%)。林奇会建议转向防御性持仓。"
    else:
        lynch_msg = f"林奇重视\"用日常观察选股\"。宏观面{'扩张' if (pmi_val and pmi_val > 50) else '收缩' if pmi_val else '未知'}，估值{vp}%，建议关注消费领域基金。"
    strategies.append({
        "master": "彼得·林奇",
        "philosophy": "成长投资：寻找被低估的成长型企业",
        "signal": lynch_signal,
        "message": lynch_msg,
        "icon": "🔍",
    })

    # 约翰·博格 (Vanguard 指数基金之父)
    bogle_msg = "📌 博格指数投资策略永远是：坚持定投，不要择时，降低费用，长期持有。"
    if vp < 30:
        bogle_msg += f"\n当前估值{vp}%偏低，定投的筹码在未来会更有价值。"
    elif vp > 70:
        bogle_msg += f"\n当前估值{vp}%偏高，但博格会说\"不要试图择时，继续你的定投计划\"。"
    strategies.append({
        "master": "约翰·博格",
        "philosophy": "指数投资：低成本指数基金 + 长期持有 + 定期定投",
        "signal": "HOLD",
        "message": bogle_msg,
        "icon": "📊",
    })

    return strategies


# ---- V4 底座：enrich() 适配层 ----
import json as _json
from pathlib import Path as _Path

def enrich(ctx):
    """Pipeline 适配：生成每日信号 → 写回 ctx（缓存优先，超时保护）"""
    result = None
    
    # 1. 优先读预缓存（cache_warmer 生成的）
    try:
        cache_fp = _Path(__file__).parent.parent.parent / "data" / "_cache" / "daily_signal.json"
        if cache_fp.exists():
            import time
            cache_data = _json.loads(cache_fp.read_text(encoding="utf-8"))
            if cache_data.get("expires_at", 0) > time.time():
                result = cache_data.get("data", {})
    except Exception:
        pass
    
    # 2. 缓存没有或过期 → 实时计算（加超时保护）
    if not result:
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(generate_daily_signal)
                result = future.result(timeout=30)  # 最多30秒
        except Exception as e:
            print(f"[signal.enrich] 超时或失败: {e}")
            # 降级：给个中性结果
            ctx.modules_results["signal"] = {
                "available": True,
                "direction": "neutral",
                "confidence": 50,
                "data": {"weighted_score": 50, "signal": "neutral", "note": f"数据源超时，降级为中性: {e}"},
                "cost": "cpu",
            }
            ctx.modules_called.append("signal")
            return ctx
    
    # 3. 解析结果
    try:
        score = result.get("weighted_score", 50)
        signal = result.get("signal", "neutral")
        direction = "bullish" if signal == "bullish" or score > 60 else ("bearish" if signal == "bearish" or score < 40 else "neutral")
        ctx.modules_results["signal"] = {
            "available": True,
            "direction": direction,
            "confidence": round(abs(score - 50) + 50, 1),
            "data": {"weighted_score": score, "signal": signal, "factors": result.get("factors", {}), "masters": result.get("master_strategies", [])},
            "cost": "cpu",
        }
        ctx.modules_called.append("signal")
    except Exception as e:
        print(f"[signal.enrich] 解析失败: {e}")
        ctx.errors.append({"module": "signal", "error": str(e)})
        ctx.modules_skipped.append({"name": "signal", "reason": str(e)})
    return ctx
