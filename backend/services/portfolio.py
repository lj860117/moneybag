"""
钱袋子 — 大类资产配置建议
根据估值水平和恐惧贪婪指数，给出股/债/现金目标比例 + 偏离建议
参考：豆包方案 + 幻方量化风控规则
"""
from config import ALLOCATION_PROFILES, VALUATION_HIGH, VALUATION_LOW, RISK_REBALANCE_THRESHOLD
from services.portfolio_calc import calc_holdings_from_transactions
from services.data_layer import get_fund_nav


# 基金分类映射
FUND_CATEGORY = {
    "110020": "stock",   # 沪深300
    "050025": "stock",   # 标普500
    "008114": "stock",   # 红利低波
    "217022": "bond",    # 招商产业债
    "000216": "other",   # 黄金（归为其他/避险）
    "余额宝":  "cash",   # 货币基金
}


def generate_allocation_advice(
    transactions: list,
    valuation_pct: float = 50,
    fear_greed: float = 50,
) -> dict:
    """
    生成大类资产配置建议
    返回: {
        target: {stock, bond, cash},    # 目标比例
        current: {stock, bond, cash},   # 当前比例
        deviation: {stock, bond, cash}, # 偏离度
        advice: [...],                  # 具体调仓建议
        valuation_zone: str,            # 估值区间
        summary: str,                   # 一句话总结
    }
    """
    # 1. 根据估值确定目标比例
    if valuation_pct < VALUATION_LOW:
        zone = "low"
        zone_label = "低估"
    elif valuation_pct > VALUATION_HIGH:
        zone = "high"
        zone_label = "高估"
    else:
        zone = "mid"
        zone_label = "适中"

    target = ALLOCATION_PROFILES[zone]

    # 2. 计算当前持仓的大类分布
    holdings = calc_holdings_from_transactions(transactions)
    active = holdings.get("active", [])

    current = {"stock": 0, "bond": 0, "cash": 0, "other": 0}
    total_market = 0

    for h in active:
        code = h["code"]
        shares = h.get("shares", 0)
        if shares <= 0:
            continue

        # 获取当前市值
        if code == "余额宝":
            market_value = shares
        else:
            nav_info = get_fund_nav(code)
            if nav_info and nav_info["nav"] != "N/A":
                market_value = shares * float(nav_info["nav"])
            else:
                market_value = shares * h.get("avgNav", 1)

        category = FUND_CATEGORY.get(code, "stock")
        current[category] = current.get(category, 0) + market_value
        total_market += market_value

    # 转换为比例
    current_pct = {"stock": 0, "bond": 0, "cash": 0}
    if total_market > 0:
        current_pct["stock"] = (current.get("stock", 0) + current.get("other", 0)) / total_market
        current_pct["bond"] = current.get("bond", 0) / total_market
        current_pct["cash"] = current.get("cash", 0) / total_market

    # 3. 计算偏离度
    deviation = {
        "stock": round((current_pct["stock"] - target["stock"]) * 100, 1),
        "bond": round((current_pct["bond"] - target["bond"]) * 100, 1),
        "cash": round((current_pct["cash"] - target["cash"]) * 100, 1),
    }

    # 4. 生成调仓建议
    advice = []
    rebalance_threshold = RISK_REBALANCE_THRESHOLD * 100  # config中0.08 → 8(%)

    if abs(deviation["stock"]) > rebalance_threshold:
        if deviation["stock"] > 0:
            advice.append({
                "asset": "stock", "direction": "reduce",
                "message": f"📉 股票类超配{deviation['stock']:.0f}%，建议减持至{target['stock']*100:.0f}%",
                "amount_pct": abs(deviation["stock"]),
            })
        else:
            advice.append({
                "asset": "stock", "direction": "increase",
                "message": f"📈 股票类欠配{abs(deviation['stock']):.0f}%，可增持至{target['stock']*100:.0f}%",
                "amount_pct": abs(deviation["stock"]),
            })

    if abs(deviation["bond"]) > rebalance_threshold:
        if deviation["bond"] < 0:
            advice.append({
                "asset": "bond", "direction": "increase",
                "message": f"📈 债券类欠配{abs(deviation['bond']):.0f}%，建议增持至{target['bond']*100:.0f}%",
                "amount_pct": abs(deviation["bond"]),
            })
        else:
            advice.append({
                "asset": "bond", "direction": "reduce",
                "message": f"📉 债券类超配{deviation['bond']:.0f}%，可适当减持",
                "amount_pct": abs(deviation["bond"]),
            })

    if abs(deviation["cash"]) > rebalance_threshold:
        if deviation["cash"] < 0:
            advice.append({
                "asset": "cash", "direction": "increase",
                "message": f"💰 现金不足，建议补充至{target['cash']*100:.0f}%作为应急弹药",
                "amount_pct": abs(deviation["cash"]),
            })

    # 5. 生成总结
    if not active:
        summary = "暂无持仓，请先完成风险测评并买入推荐配置"
    elif not advice:
        summary = f"✅ 当前配置与{zone_label}估值目标基本匹配，无需调仓"
    else:
        summary = f"⚠️ 当前估值{zone_label}({valuation_pct:.0f}%)，有{len(advice)}项配置偏离需调整"

    return {
        "target": {k: round(v * 100, 0) for k, v in target.items()},
        "current": {k: round(v * 100, 1) for k, v in current_pct.items()},
        "deviation": deviation,
        "advice": advice,
        "valuation_zone": zone_label,
        "valuation_pct": round(valuation_pct, 1),
        "total_market": round(total_market, 2),
        "summary": summary,
    }
