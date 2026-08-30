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
    """智能定投（旧版，纯估值）：保留向后兼容"""
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


# ═══ v9.5.123: 双因子智能定投（走势×估值） ═══

# 定投倍率矩阵: DCA_MATRIX[trend_direction][valuation_tier]
_DCA_MATRIX = {
    # trend_direction: {valuation_tier: (multiplier, label)}
    "up": {
        "极低": (2.0, "最佳击球区"),
        "低":   (1.5, "偏多+低位"),
        "中":   (1.3, "偏多+中位"),
        "高":   (0.8, "追高风险"),
        "极高": (0.5, "高位追涨危险"),
    },
    "flat": {
        "极低": (1.5, "低位待确认"),
        "低":   (1.2, "低位震荡"),
        "中":   (1.0, "标准定投"),
        "高":   (0.7, "高位观望"),
        "极高": (0.3, "高位少投"),
    },
    "down": {
        "极低": (0.8, "低位但偏空"),
        "低":   (0.7, "左侧控制节奏"),
        "中":   (0.5, "偏空减半"),
        "高":   (0.2, "偏空+高位极少"),
        "极高": (0.0, "暂停定投"),
    },
}


def _valuation_tier(nav_pct) -> str:
    """将估值百分位映射为5档。None时返回"中"（保守默认，不做极端判断）"""
    if nav_pct is None:
        return "中"  # 无数据时保守处理，不给极端建议
    # 确保是数值类型
    try:
        nav_pct = float(nav_pct)
    except (TypeError, ValueError):
        return "中"
    if nav_pct < 15:
        return "极低"
    if nav_pct < 30:
        return "低"
    if nav_pct < 70:
        return "中"
    if nav_pct < 85:
        return "高"
    return "极高"


def calc_smart_dca_v2(
    trend_direction: str,
    trend_score: int,
    trend_confidence: int,
    nav_percentile,
    trend_conflict: str = "",
    base_amount: float = 1000.0,
) -> dict:
    """v9.5.123: 双因子智能定投引擎（走势×估值）
    
    核心逻辑:
    1. 走势方向(up/flat/down) × 估值档位(极低/低/中/高/极高) → 基准倍率
    2. 置信度修正: confidence<50% → 倍率向1.0回归50%
    3. 信号冲突保护: 有冲突 → 强制1.0x
    
    返回: {multiplier, advice, label, base_amount, smart_amount, factors}
    """
    # 1. 查矩阵获取基准倍率
    val_tier = _valuation_tier(nav_percentile)
    direction = trend_direction if trend_direction in ("up", "flat", "down") else "flat"
    
    base_mult, base_label = _DCA_MATRIX[direction].get(val_tier, (1.0, "标准定投"))
    
    # 2. 信号冲突保护：冲突时强制回归1.0
    if trend_conflict:
        final_mult = 1.0
        advice = f"信号冲突({trend_conflict})，维持标准定投"
        label = "⚖️ 1.0x 标准"
    else:
        # 3. 置信度修正
        # 核心原则：不确定时应该更保守（减少偏离），而不是盲目回归1.0
        # - 看多信号+低置信度 → 倍率从基准向下修正（别冲太猛）
        # - 看空信号+低置信度 → 倍率从基准向上修正（别太悲观）
        # 统一逻辑：向1.0方向回归，但保持方向不翻转
        if trend_confidence < 50:
            regression = (50 - trend_confidence) / 50.0  # 0~1
            # 向1.0方向回归，回归幅度=偏离×regression×0.5
            final_mult = base_mult + (1.0 - base_mult) * regression * 0.5
            # 关键保护：偏空时回归后不能超过1.0（即不能变成加仓建议）
            if base_mult < 1.0:
                final_mult = min(final_mult, 1.0)
            # 偏多时回归后不能低于1.0（即不能变成减仓建议）
            if base_mult > 1.0:
                final_mult = max(final_mult, 1.0)
        elif trend_confidence >= 80:
            # 高置信度允许更极端（放大偏离1.0的幅度10%）
            deviation = base_mult - 1.0
            final_mult = base_mult + deviation * 0.1
        else:
            final_mult = base_mult
        
        # 防御: NaN 检测（trend_confidence 异常时）
        import math
        if math.isnan(final_mult) or math.isinf(final_mult):
            final_mult = 1.0
        
        # 确保范围 [0, 2.5]
        final_mult = max(0.0, min(2.5, round(final_mult, 2)))
        
        # 生成建议文案
        if final_mult >= 1.8:
            advice = f"强势+低位共振，建议加大定投至 {final_mult}x"
            label = f"🔥 {final_mult}x 加码"
        elif final_mult >= 1.3:
            advice = f"{base_label}，建议定投 {final_mult}x"
            label = f"💪 {final_mult}x 加仓"
        elif final_mult >= 0.9:
            advice = "信号中性，维持标准节奏"
            label = f"✋ {final_mult}x 标准"
        elif final_mult >= 0.5:
            advice = f"{base_label}，建议缩减至 {final_mult}x"
            label = f"📉 {final_mult}x 减量"
        elif final_mult > 0:
            advice = f"高风险环境，建议极少量定投 {final_mult}x"
            label = f"⚠️ {final_mult}x 极少"
        else:
            advice = "多维指标全面偏空+高位，建议暂停定投"
            label = "🛑 暂停定投"
    
    return {
        "multiplier": final_mult,
        "advice": advice,
        "label": label,
        "base_amount": round(base_amount, 2),
        "smart_amount": round(base_amount * final_mult, 2),
        "factors": {
            "trend_direction": direction,
            "trend_score": trend_score,
            "trend_confidence": trend_confidence,
            "valuation_tier": val_tier if nav_percentile is not None else "无数据(默认中)",
            "nav_percentile": nav_percentile,
            "base_multiplier": base_mult,
            "conflict": trend_conflict or None,
            "has_valuation": nav_percentile is not None,
        },
    }


def calc_take_profit_strategy(cost: float, market_value: float, profile: str) -> dict:
    """止盈止损策略：根据风险类型给目标收益率和止损线"""
    # FIX 2026-04-19 V7.2: 参数表从 config 读取
    from config import TAKE_PROFIT_STOP_LOSS
    params = TAKE_PROFIT_STOP_LOSS
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
    # 被跳过、不参与加权的因子（数据不可得）。不进 scores 意味着其权重自动从
    # 分母中剔除；但仍在返回里列出来，避免因子"静默消失"让用户以为没这回事。
    skipped_factors = []  # [{"name","category","reason","note"}]

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
    if "金叉" in trend and "0轴上方" in trend:
        macd_score, macd_detail = 70, f"MACD金叉且在0轴上方，强势买入信号"
    elif "金叉" in trend:
        macd_score, macd_detail = 30, f"MACD金叉但仍在0轴下方，反弹信号（非趋势反转）"
    elif "多头排列" in trend:
        macd_score, macd_detail = 50, f"MACD多头排列（DIF/DEA均在0轴上方），上升趋势"
    elif "弱势反弹" in trend:
        macd_score, macd_detail = 20, f"MACD弱势反弹（0轴下方金叉），谨慎乐观"
    elif "死叉" in trend and "0轴下方" in trend:
        macd_score, macd_detail = -70, f"MACD死叉且在0轴下方，强势卖出信号"
    elif "死叉" in trend:
        macd_score, macd_detail = -30, f"MACD死叉但在0轴上方，回调信号（趋势未破）"
    elif "空头排列" in trend:
        macd_score, macd_detail = -50, f"MACD空头排列，下降趋势"
    elif "高位回调" in trend:
        macd_score, macd_detail = -20, f"MACD高位回调，短期调整"
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
    # ⚠️ 口径关键事实（2026-08 修正）：北向【净买入】自 2024-08-19 起沪深交易所
    #    停止日频披露、改为按季度公布，Tushare moneyflow_hsgt 的 north_money
    #    现为「当日成交额」（恒正，2500~3300亿量级），**不含方向信息**。
    #    因此净流入不可得时该因子【不参与加权】—— 不 append 进 scores，
    #    其权重就自动从 total_weight 分母中剔除。
    #    注意不能给 0 分：0 分会以满权重参与加权平均，把其他因子的信号稀释掉，
    #    与"没有这个因子"语义完全不同。
    #    也不能用成交额去推多空 —— 成交额恒正，放量既可能是买也可能是卖。
    north = get_northbound_flow()
    if north.get("net_flow_available"):
        # 数据源恢复日频净买入披露后自动走回这里
        flow_5d = north.get("net_flow_5d")
        flow_today = north.get("net_flow_today")
        if isinstance(flow_5d, (int, float)):
            today_txt = (f"，今日{flow_today:.0f}亿"
                         if isinstance(flow_today, (int, float)) else "")
            if flow_5d > 100:
                north_score, north_detail = 70, f"北向资金5日净流入{flow_5d:.0f}亿{today_txt}，外资大举买入"
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
            scores.append((north_score, _w["北向资金"], "北向资金", north_detail, "资金面"))
        else:
            skipped_factors.append({
                "name": "北向资金",
                "category": "资金面",
                "weight": f"{_w['北向资金'] * 100:.0f}%",
                "reason": f"net_flow_available=True 但 net_flow_5d={flow_5d!r} 非数值",
                "note": "该因子未参与打分（权重已从分母剔除）",
            })
    else:
        # 净流入不可得 → 跳过打分，但把成交额活跃度作为纯展示信息带出去
        turnover_note = ""
        t_today = north.get("turnover_today")
        if north.get("available") and isinstance(t_today, (int, float)):
            turnover_note = (f"北向成交额今日{t_today:.0f}亿"
                             f"（活跃度={north.get('turnover_trend', '平稳')}）；"
                             f"成交额=买入额+卖出额，恒为正，不含方向信息，不用于打分")
        else:
            turnover_note = "北向成交额亦未取到"
        skipped_factors.append({
            "name": "北向资金",
            "category": "资金面",
            "weight": f"{_w['北向资金'] * 100:.0f}%",
            "reason": north.get("unavailable_reason") or "北向净流入数据不可得",
            "note": f"该因子未参与打分（权重已从分母剔除）。{turnover_note}",
        })

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
    # score 现在是「贪婪分」: 0=极度恐惧, 100=极度贪婪
    fgi_data = get_fear_greed_index()
    fgi = fgi_data.get("score", 50)
    if fgi <= 20:
        fgi_score, fgi_detail = 80, f"恐贪指数{fgi:.0f}（极度恐惧），别人恐惧时贪婪"
    elif fgi <= 35:
        fgi_score, fgi_detail = 40, f"恐贪指数{fgi:.0f}（恐惧），市场偏悲观"
    elif fgi <= 60:
        fgi_score, fgi_detail = 0, f"恐贪指数{fgi:.0f}（中性）"
    elif fgi <= 75:
        fgi_score, fgi_detail = -40, f"恐贪指数{fgi:.0f}（贪婪），市场偏乐观"
    else:
        fgi_score, fgi_detail = -80, f"恐贪指数{fgi:.0f}（极度贪婪），别人贪婪时恐惧"
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

    # FIX 2026-06-14: 置信度计算修复
    # 旧逻辑：confidence = min(abs(final_score), 100)
    # 问题：HOLD 时 final_score ≈ 0 → 置信度 ≈ 0%，完全不对
    # 置信度应该衡量"这个判断有多可靠"，而不是"信号有多强"
    # 新逻辑：基于各因子方向一致性 + 信号强度 双因子计算
    #   1. 方向一致性：各因子加权后，有多少权重的因子方向与最终信号一致
    #   2. 信号强度：|final_score| / 80（满分80分，对应极端信号）
    #   最终置信度 = 一致性×0.6 + 强度×0.4，映射到 0~100

    # 计算方向一致性
    if final_score >= 0:
        # 看多/HOLD偏多：统计权重中得分>=0的比例
        consistent_weight = sum(w for s, w, _, _, _ in scores if s >= 0)
    else:
        # 看空：统计权重中得分<0的比例
        consistent_weight = sum(w for s, w, _, _, _ in scores if s < 0)
    consistency = consistent_weight / total_weight if total_weight > 0 else 0.5

    # 信号强度
    strength = min(abs(final_score) / 80.0, 1.0)

    # 综合置信度
    confidence = round(consistency * 60 + strength * 40, 1)
    # 最低置信度保护：即使信号完全中性，一致性50%也至少给30分
    confidence = max(confidence, 30.0)
    # 异常保护：数据源全部为0时置信度不超过50
    nonzero_count = sum(1 for s, w, _, _, _ in scores if s != 0)
    if nonzero_count < 4:
        confidence = min(confidence, 50.0)
        signal["_confidence_degraded"] = f"仅{nonzero_count}个因子有有效数据"

    # FIX 2026-08-09: 低置信度归因说明 —— 排查周度自检"信号可靠性偏低易误导
    # 用户决策"的问题后发现：置信度公式本身没有算错，长期39%左右恰恰反映的是
    # "13个维度里存在真实的多空分歧"（例如估值看空80分vs 股债性价比看多60分
    # 同时出现），而不是数据缺失或计算bug。但产品端只展示一个孤零零的百分比，
    # 用户看到"39%"只会觉得"这信号不靠谱"，却不知道背后到底是"分歧大"还是
    # "数据差"。这里补充人话归因，供前端/审计/推送消费，明确区分两种低置信度：
    #   - 分歧型（consistency低）：多空因子打架，市场处于胶着期，HOLD本身就是
    #     该给出的合理结论，不是模型失灵
    #   - 强度型（strength低）：各因子都不强烈，市场缺乏明确方向，同样是正常
    #     的震荡市特征
    # 只有 nonzero_count<4（数据源大面积缺失）才是真正的"不可信"，需要单独标注。
    if signal.get("_confidence_degraded"):
        confidence_note = f"⚠️ 置信度偏低：{signal['_confidence_degraded']}，建议等数据恢复后再参考"
        confidence_reason = "data_missing"
    elif confidence < 45:
        if consistency < 0.6 and strength < 0.35:
            confidence_note = "置信度偏低是因为13个维度存在明显分歧（比如估值看空但资金面看多），且各因子强度都不突出——这正说明当前市场处于多空胶着的震荡期，HOLD本身就是合理结论，不代表模型失灵"
        elif consistency < 0.6:
            confidence_note = "置信度偏低主要因为各维度方向分歧较大（有的看多有的看空），市场缺乏一致预期，建议降低操作频率，等信号更清晰再决策"
        else:
            confidence_note = "置信度偏低主要因为各维度信号强度都不突出，市场缺乏明确方向，属于正常震荡市特征"
        confidence_reason = "market_divergence" if consistency < 0.6 else "weak_signal"
    else:
        confidence_note = ""
        confidence_reason = "normal"
    signal["confidence_note"] = confidence_note
    signal["confidence_reason"] = confidence_reason

    signal["confidence"] = confidence
    signal["score"] = round(final_score, 1)
    signal["details"] = [
        {"name": name, "score": round(s, 1), "weight": f"{w*100:.0f}%", "detail": detail, "category": cat}
        for s, w, name, detail, cat in scores
    ]
    # 因子不可得时不参与加权，但显式列出来（而不是静默消失），
    # 下游据此展示"该因子数据不可得、未参与打分"。
    signal["skippedFactors"] = skipped_factors

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
        buffett_signal = "SELL" if fgi > 60 else "HOLD"
        buffett_msg = f"⚠️ 估值偏高({vp}%){'+ 市场贪婪(' + str(round(fgi)) + ')' if fgi > 60 else ''}，巴菲特会谨慎——\"别人贪婪时我恐惧\"。" if fgi > 60 else f"估值偏高({vp}%)但市场情绪({fgi:.0f})未极端贪婪，巴菲特会持仓观望但不再加仓。"
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
        sig_val = result.get("signal", "neutral")
        overall = result.get("overall", "HOLD")
        # FIX 2026-06-14: 使用 generate_daily_signal() 已计算好的 confidence
        # 旧逻辑 confidence = round(abs(score - 50) + 50, 1) 也有同样问题
        confidence = result.get("confidence", 50)
        direction = "bullish" if overall in ("STRONG_BUY", "BUY") else ("bearish" if overall in ("STRONG_SELL", "SELL") else "neutral")
        ctx.modules_results["signal"] = {
            "available": True,
            "direction": direction,
            "confidence": confidence,
            "data": {"weighted_score": score, "signal": sig_val, "overall": overall, "factors": result.get("factors", {}), "masters": result.get("master_strategies", [])},
            "cost": "cpu",
        }
        ctx.modules_called.append("signal")
    except Exception as e:
        print(f"[signal.enrich] 解析失败: {e}")
        ctx.errors.append({"module": "signal", "error": str(e)})
        ctx.modules_skipped.append({"name": "signal", "reason": str(e)})
    return ctx
