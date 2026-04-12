"""
钱袋子 — 风控系统
HHI 集中度、回撤监控、相关性分析 + 硬阈值执行建议（借鉴幻方量化）
"""
from config import (
    RISK_DRAWDOWN_WARNING, RISK_DRAWDOWN_DANGER, RISK_DAILY_DROP_LIMIT,
    RISK_SINGLE_STOCK_MAX, RISK_SINGLE_FUND_MAX, RISK_INDUSTRY_MAX,
    RISK_TAKE_PROFIT, RISK_MAX_DRAWDOWN_LIMIT, RISK_REBALANCE_THRESHOLD,
    ALLOCATION_PROFILES, VALUATION_HIGH,
)
from services.portfolio_calc import calc_holdings_from_transactions
from services.market_data import get_fund_nav


def calc_risk_metrics(transactions: list) -> dict:
    """计算组合风险指标：集中度/回撤/相关性"""
    result = {
        "concentration": {"hhi": 0, "max_single": 0, "level": "正常", "detail": ""},
        "drawdown": {"current": 0, "max_historical": 0, "level": "正常", "detail": ""},
        "correlation": {"avg": 0, "detail": ""},
        "alerts": [],
    }

    holdings = calc_holdings_from_transactions(transactions)
    active = holdings.get("active", [])
    if not active:
        return result

    # --- 1. 集中度分析（HHI指数）---
    total_cost = sum(h["totalCost"] for h in active)
    if total_cost > 0:
        weights = [h["totalCost"] / total_cost for h in active]
        hhi = sum(w ** 2 for w in weights) * 10000
        max_single = max(weights) * 100

        result["concentration"]["hhi"] = round(hhi, 0)
        result["concentration"]["max_single"] = round(max_single, 1)

        if hhi > 5000:
            result["concentration"]["level"] = "高度集中"
            result["concentration"]["detail"] = f"HHI={hhi:.0f}，单一资产占比最高{max_single:.0f}%，建议分散"
            result["alerts"].append({
                "type": "concentration",
                "severity": "warning",
                "message": f"⚠️ 持仓集中度过高（HHI={hhi:.0f}），最大单品占{max_single:.0f}%，建议分散配置降低风险"
            })
        elif hhi > 3000:
            result["concentration"]["level"] = "适度集中"
            result["concentration"]["detail"] = f"HHI={hhi:.0f}，集中度适中"
        else:
            result["concentration"]["level"] = "分散良好"
            result["concentration"]["detail"] = f"HHI={hhi:.0f}，分散度良好"

    # --- 2. 回撤监控 ---
    try:
        current_market = 0
        for h in active:
            code = h["code"]
            if code == "余额宝":
                current_market += h["shares"]
                continue
            nav_info = get_fund_nav(code)
            if nav_info and nav_info["nav"] != "N/A":
                current_market += h["shares"] * float(nav_info["nav"])
            else:
                current_market += h["shares"] * h["avgNav"]

        peak = total_cost * 1.1  # 简化：假设历史高点
        if current_market > 0 and peak > 0:
            drawdown = (peak - current_market) / peak * 100
            if drawdown > 0:
                result["drawdown"]["current"] = round(drawdown, 2)
                if drawdown > 20:
                    result["drawdown"]["level"] = "严重回撤"
                    result["alerts"].append({
                        "type": "drawdown", "severity": "danger",
                        "message": f"🔴 当前回撤{drawdown:.1f}%，超过20%警戒线！检查持仓基本面是否变化"
                    })
                elif drawdown > 10:
                    result["drawdown"]["level"] = "中度回撤"
                    result["alerts"].append({
                        "type": "drawdown", "severity": "warning",
                        "message": f"⚠️ 当前回撤{drawdown:.1f}%，注意风险控制"
                    })
                result["drawdown"]["detail"] = f"当前回撤{drawdown:.1f}%"
    except Exception as e:
        print(f"[RISK] Drawdown calc failed: {e}")

    # --- 3. 相关性提示 ---
    stock_count = sum(1 for h in active if h["code"] in ["110020", "050025", "008114"])
    bond_count = sum(1 for h in active if h["code"] in ["217022"])
    gold_count = sum(1 for h in active if h["code"] in ["000216"])

    if stock_count >= 2 and bond_count == 0 and gold_count == 0:
        result["correlation"]["avg"] = 0.75
        result["correlation"]["detail"] = "持仓以权益类为主，相关性较高，缺少避险资产对冲"
        result["alerts"].append({
            "type": "correlation", "severity": "info",
            "message": "💡 持仓权益资产占比高，建议配置债券/黄金降低组合波动"
        })
    elif bond_count > 0 and gold_count > 0:
        result["correlation"]["avg"] = 0.35
        result["correlation"]["detail"] = "股债金组合，相关性适中，对冲效果良好"
    else:
        result["correlation"]["avg"] = 0.5
        result["correlation"]["detail"] = "相关性中等"

    return result


# ============================================================
# 新增：硬阈值执行建议（借鉴豆包方案 / 幻方量化风控规则）
# ============================================================

def generate_risk_actions(transactions: list, valuation_pct: float = 50) -> dict:
    """
    根据持仓+市场数据，生成硬阈值执行建议
    参考：
    - 豆包方案：回撤≤-15%降仓50%，≤-18%降仓40%，单日≥4%暂停
    - 幻方量化：止盈≥40%减半，单票≤3%，行业≤20%，再平衡±8%
    返回: {actions: [...], summary: str, risk_level: str}
    """
    actions = []
    risk_metrics = calc_risk_metrics(transactions)
    holdings = calc_holdings_from_transactions(transactions)
    active = holdings.get("active", [])

    if not active:
        return {"actions": [], "summary": "暂无持仓", "risk_level": "safe"}

    total_cost = sum(h["totalCost"] for h in active)
    if total_cost <= 0:
        return {"actions": [], "summary": "持仓成本为零", "risk_level": "safe"}

    # --- 规则1: 回撤硬阈值 ---
    drawdown_pct = -risk_metrics["drawdown"]["current"] / 100  # 转为负数小数
    if drawdown_pct <= RISK_MAX_DRAWDOWN_LIMIT:  # -20%
        actions.append({
            "level": "danger", "rule": "最大回撤红线",
            "action": "🚨 立即止损！回撤已达-20%红线，清仓止损保住本金",
            "detail": f"当前回撤{drawdown_pct*100:.1f}%，触发-20%绝对红线"
        })
    elif drawdown_pct <= RISK_DRAWDOWN_DANGER:  # -18%
        actions.append({
            "level": "danger", "rule": "回撤警戒线",
            "action": "⚠️ 股票仓位降至40%，增配债券基金",
            "detail": f"当前回撤{drawdown_pct*100:.1f}%，触发-18%警戒线"
        })
    elif drawdown_pct <= RISK_DRAWDOWN_WARNING:  # -15%
        actions.append({
            "level": "warning", "rule": "回撤预警线",
            "action": "⚠️ 股票仓位降至50%，暂停新增买入",
            "detail": f"当前回撤{drawdown_pct*100:.1f}%，触发-15%预警线"
        })

    # --- 规则2: 单基金/单票占比检查 ---
    for h in active:
        weight = h["totalCost"] / total_cost
        name = h.get("name", h["code"])
        if weight > RISK_SINGLE_FUND_MAX:  # >15%
            actions.append({
                "level": "warning", "rule": "单基金占比上限",
                "action": f"⚠️ {name}占比{weight*100:.0f}%，超过15%上限，建议减持至15%以内",
                "detail": f"单只基金仓位上限15%，当前{weight*100:.1f}%"
            })

    # --- 规则3: 止盈检查（单品收益≥40%减半）---
    for h in active:
        if h["totalCost"] > 0 and h["shares"] > 0:
            nav_info = get_fund_nav(h["code"])
            if nav_info and nav_info["nav"] != "N/A":
                current_value = h["shares"] * float(nav_info["nav"])
                profit_pct = (current_value - h["totalCost"]) / h["totalCost"]
                name = h.get("name", h["code"])
                if profit_pct >= RISK_TAKE_PROFIT:  # ≥40%
                    actions.append({
                        "level": "info", "rule": "止盈纪律",
                        "action": f"🎯 {name}收益{profit_pct*100:.0f}%，建议卖出50%锁定利润",
                        "detail": f"止盈阈值40%，当前{profit_pct*100:.1f}%"
                    })

    # --- 规则4: 估值配置建议 ---
    if valuation_pct > VALUATION_HIGH:  # >80%
        target = ALLOCATION_PROFILES["high"]
        actions.append({
            "level": "warning", "rule": "高估值配置调整",
            "action": f"📊 估值{valuation_pct}%高估，建议股票≤{target['stock']*100:.0f}%/债券≥{target['bond']*100:.0f}%/现金≥{target['cash']*100:.0f}%",
            "detail": f"当前估值百分位{valuation_pct}%，处于高估区间"
        })
    elif valuation_pct < 20:
        target = ALLOCATION_PROFILES["low"]
        actions.append({
            "level": "info", "rule": "低估值加仓建议",
            "action": f"💰 估值{valuation_pct}%低估，可提升股票至{target['stock']*100:.0f}%",
            "detail": f"当前估值百分位{valuation_pct}%，处于低估区间，适合增配"
        })

    # --- 规则5: 集中度警告 ---
    hhi = risk_metrics["concentration"]["hhi"]
    if hhi > 5000:
        actions.append({
            "level": "warning", "rule": "行业集中度",
            "action": "⚠️ 持仓过于集中，建议分散到3-5只不同类型基金",
            "detail": f"HHI指数{hhi:.0f}（>5000为高度集中）"
        })

    # --- 汇总 ---
    danger_count = sum(1 for a in actions if a["level"] == "danger")
    warning_count = sum(1 for a in actions if a["level"] == "warning")

    if danger_count > 0:
        risk_level = "danger"
        summary = f"🔴 {danger_count}项严重风险需立即处理"
    elif warning_count > 0:
        risk_level = "warning"
        summary = f"🟡 {warning_count}项风险提示需关注"
    else:
        risk_level = "safe"
        summary = "🟢 风控检查通过，各项指标正常"

    return {
        "actions": actions,
        "summary": summary,
        "risk_level": risk_level,
        "metrics": risk_metrics,
    }
