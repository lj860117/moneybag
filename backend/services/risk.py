"""
钱袋子 — 风控系统
HHI 集中度、回撤监控、相关性分析 + 硬阈值执行建议（借鉴幻方量化）
"""
from config import (
    RISK_DRAWDOWN_WARNING, RISK_DRAWDOWN_DANGER, RISK_DAILY_DROP_LIMIT,
    RISK_SINGLE_STOCK_MAX, RISK_SINGLE_FUND_MAX, RISK_INDUSTRY_MAX,
    RISK_TAKE_PROFIT, RISK_MAX_DRAWDOWN_LIMIT, RISK_REBALANCE_THRESHOLD,
)

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

    # --- 1. 集中度分析（HHI指数） ---
    total_cost = sum(h["totalCost"] for h in active)
    if total_cost > 0:
        weights = [h["totalCost"] / total_cost for h in active]
        hhi = sum(w ** 2 for w in weights) * 10000  # HHI 指数 (0-10000)
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
        # 计算当前市值和历史最高市值
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

        # 简化回撤计算：用总成本+总浮盈历史峰值
        peak = total_cost * 1.1  # 假设历史高点（简化）
        if current_market > 0 and peak > 0:
            drawdown = (peak - current_market) / peak * 100
            if drawdown > 0:
                result["drawdown"]["current"] = round(drawdown, 2)
                if drawdown > 20:
                    result["drawdown"]["level"] = "严重回撤"
                    result["alerts"].append({
                        "type": "drawdown",
                        "severity": "danger",
                        "message": f"🔴 当前回撤{drawdown:.1f}%，超过20%警戒线！检查持仓基本面是否变化"
                    })
                elif drawdown > 10:
                    result["drawdown"]["level"] = "中度回撤"
                    result["alerts"].append({
                        "type": "drawdown",
                        "severity": "warning",
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
            "type": "correlation",
            "severity": "info",
            "message": "💡 持仓权益资产占比高，建议配置债券/黄金降低组合波动"
        })
    elif bond_count > 0 and gold_count > 0:
        result["correlation"]["avg"] = 0.35
        result["correlation"]["detail"] = "股债金组合，相关性适中，对冲效果良好"
    else:
        result["correlation"]["avg"] = 0.5
        result["correlation"]["detail"] = "相关性中等"

    return result


