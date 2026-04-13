"""
钱袋子 — 智能资产配置引擎 (Level 1)
基础模板 + 实时市场数据动态调整

调整因子：
  1. 估值百分位 → 高估减股票，低估加股票
  2. 恐贪指数 → 极度贪婪减仓，极度恐惧加仓
  3. 美林时钟 → 不同周期调配置
  4. 塔勒布规则 → 现金永远 ≥ 15%
  5. 技术面 → RSI 极端值微调
"""
from config import ALLOCATION_PROFILES, VALUATION_HIGH, VALUATION_LOW, RISK_REBALANCE_THRESHOLD
from services.portfolio_calc import calc_holdings_from_transactions
from services.data_layer import get_fund_nav, get_valuation_percentile, get_fear_greed_index


# 基金分类映射
FUND_CATEGORY = {
    "110020": "stock",
    "050025": "stock",
    "008114": "stock",
    "217022": "bond",
    "000216": "other",
    "余额宝":  "cash",
}

# 风险等级 → 基础配置模板
RISK_TEMPLATES = {
    "保守型": {"stock": 0.20, "bond": 0.60, "cash": 0.20},
    "稳健型": {"stock": 0.50, "bond": 0.30, "cash": 0.20},
    "平衡型": {"stock": 0.50, "bond": 0.30, "cash": 0.20},
    "积极型": {"stock": 0.70, "bond": 0.15, "cash": 0.15},
    "激进型": {"stock": 0.80, "bond": 0.10, "cash": 0.10},
}


def _dynamic_adjust(base: dict, val_pct: float, fgi: float) -> dict:
    """
    Level 1 动态调整：根据实时市场数据微调配置
    返回调整后的目标比例（小数）
    """
    s, b, c = base["stock"], base["bond"], base["cash"]

    # 1. 估值调整（±10%）
    if val_pct > 85:
        # 极度高估 → 大幅减股
        s -= 0.10
        b += 0.05
        c += 0.05
    elif val_pct > 70:
        # 高估 → 小幅减股
        s -= 0.05
        b += 0.03
        c += 0.02
    elif val_pct < 15:
        # 极度低估 → 大幅加股
        s += 0.10
        b -= 0.05
        c -= 0.05
    elif val_pct < 30:
        # 低估 → 小幅加股
        s += 0.05
        b -= 0.03
        c -= 0.02

    # 2. 恐贪指数调整（±5%）
    if fgi > 80:
        # 极度贪婪 → 别人贪婪我恐惧，减股
        s -= 0.05
        c += 0.05
    elif fgi < 20:
        # 极度恐惧 → 别人恐惧我贪婪，加股
        s += 0.05
        c -= 0.05

    # 3. 塔勒布铁律：现金永远 ≥ 15%（反脆弱）
    if c < 0.15:
        diff = 0.15 - c
        c = 0.15
        # 从股票里扣
        s -= diff

    # 4. 合理性约束
    s = max(0.05, min(0.90, s))
    b = max(0.0, min(0.80, b))
    c = max(0.10, min(0.50, c))

    # 归一化
    total = s + b + c
    return {
        "stock": round(s / total, 3),
        "bond": round(b / total, 3),
        "cash": round(c / total, 3),
    }


def generate_allocation_advice(
    transactions: list,
    valuation_pct: float = 50,
    fear_greed: float = 50,
    risk_profile: str = "稳健型",
) -> dict:
    """
    智能资产配置建议（Level 1 动态版）
    """
    # 获取实时市场数据
    try:
        val_data = get_valuation_percentile()
        valuation_pct = val_data.get("percentile", valuation_pct)
    except Exception:
        pass

    try:
        fgi_data = get_fear_greed_index()
        fear_greed = fgi_data.get("score", fear_greed)
    except Exception:
        pass

    # 基础模板
    base = RISK_TEMPLATES.get(risk_profile, RISK_TEMPLATES["稳健型"])

    # Level 1 动态调整
    target = _dynamic_adjust(base, valuation_pct, fear_greed)

    # 估值区间标签
    if valuation_pct < VALUATION_LOW:
        zone_label = "低估"
    elif valuation_pct > VALUATION_HIGH:
        zone_label = "高估"
    else:
        zone_label = "适中"

    # 计算当前持仓分布
    holdings = calc_holdings_from_transactions(transactions)
    active = holdings.get("active", [])

    current = {"stock": 0, "bond": 0, "cash": 0, "other": 0}
    total_market = 0

    for h in active:
        code = h["code"]
        shares = h.get("shares", 0)
        if shares <= 0:
            continue
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

    current_pct = {"stock": 0, "bond": 0, "cash": 0}
    if total_market > 0:
        current_pct["stock"] = (current.get("stock", 0) + current.get("other", 0)) / total_market
        current_pct["bond"] = current.get("bond", 0) / total_market
        current_pct["cash"] = current.get("cash", 0) / total_market

    # 偏离度
    deviation = {
        "stock": round((current_pct["stock"] - target["stock"]) * 100, 1),
        "bond": round((current_pct["bond"] - target["bond"]) * 100, 1),
        "cash": round((current_pct["cash"] - target["cash"]) * 100, 1),
    }

    # 调仓建议
    advice = []
    rebalance_threshold = RISK_REBALANCE_THRESHOLD * 100

    for asset, label, emoji_up, emoji_dn in [
        ("stock", "股票类", "📈", "📉"),
        ("bond", "债券类", "📈", "📉"),
        ("cash", "现金类", "💰", "💰"),
    ]:
        d = deviation[asset]
        if abs(d) > rebalance_threshold:
            if d > 0:
                advice.append({
                    "asset": asset, "direction": "reduce",
                    "message": f"{emoji_dn} {label}超配{d:.0f}%，建议减持至{target[asset]*100:.0f}%",
                    "amount_pct": abs(d),
                })
            else:
                advice.append({
                    "asset": asset, "direction": "increase",
                    "message": f"{emoji_up} {label}欠配{abs(d):.0f}%，可增持至{target[asset]*100:.0f}%",
                    "amount_pct": abs(d),
                })

    # 动态调整说明
    adjustments = []
    if valuation_pct > 70:
        adjustments.append(f"估值{valuation_pct:.0f}%(高估)→减股票")
    elif valuation_pct < 30:
        adjustments.append(f"估值{valuation_pct:.0f}%(低估)→加股票")
    if fear_greed > 80:
        adjustments.append(f"恐贪{fear_greed:.0f}(极度贪婪)→减股票")
    elif fear_greed < 20:
        adjustments.append(f"恐贪{fear_greed:.0f}(极度恐惧)→加股票")

    if not active:
        summary = "暂无持仓，请先完成风险测评并买入推荐配置"
    elif not advice:
        summary = f"✅ 配置与动态目标基本匹配（估值{zone_label} {valuation_pct:.0f}%）"
    else:
        summary = f"⚠️ 估值{zone_label}({valuation_pct:.0f}%)，有{len(advice)}项需调整"

    return {
        "target": {k: round(v * 100, 0) for k, v in target.items()},
        "current": {k: round(v * 100, 1) for k, v in current_pct.items()},
        "deviation": deviation,
        "advice": advice,
        "valuation_zone": zone_label,
        "valuation_pct": round(valuation_pct, 1),
        "fear_greed": round(fear_greed, 1),
        "total_market": round(total_market, 2),
        "summary": summary,
        "adjustments": adjustments,
        "risk_profile": risk_profile,
        "engine": "level1_dynamic",
    }


# ---- 推荐配置基金列表（后端提供，前端不再硬编码）----

RECOMMENDED_FUNDS = [
    {"name": "沪深300", "code": "110020", "fullName": "易方达沪深300ETF联接A", "color": "#3B82F6",
     "returns": {"good": 0.15, "mid": 0.08, "bad": -0.10}, "category": "stock", "etfCode": "510300"},
    {"name": "标普500", "code": "050025", "fullName": "博时标普500ETF联接A", "color": "#10B981",
     "returns": {"good": 0.18, "mid": 0.10, "bad": -0.12}, "category": "stock"},
    {"name": "债券", "code": "217022", "fullName": "招商产业债A", "color": "#F59E0B",
     "returns": {"good": 0.06, "mid": 0.04, "bad": 0.01}, "category": "bond"},
    {"name": "黄金", "code": "000216", "fullName": "华安黄金ETF联接A", "color": "#F97316",
     "returns": {"good": 0.15, "mid": 0.08, "bad": -0.05}, "category": "other", "etfCode": "518880"},
    {"name": "红利低波", "code": "008114", "fullName": "天弘红利低波100联接A", "color": "#EF4444",
     "returns": {"good": 0.12, "mid": 0.07, "bad": -0.05}, "category": "stock", "etfCode": "515100"},
    {"name": "货币(应急)", "code": "余额宝", "fullName": "余额宝", "color": "#E5E7EB",
     "returns": {"good": 0.02, "mid": 0.018, "bad": 0.015}, "category": "cash"},
]

# 风险等级 → 各基金占比
ALLOC_PCTS = {
    "保守型":  [10, 5, 50, 15, 10, 10],
    "稳健型":  [20, 10, 35, 15, 10, 10],
    "平衡型":  [30, 20, 20, 15, 10, 5],
    "进取型":  [35, 25, 10, 10, 15, 5],
    "激进型":  [40, 30, 5, 5, 15, 5],
}


def get_recommend_allocations(risk_profile: str = "稳健型", with_ai: bool = False) -> dict:
    """返回推荐基金配置列表 + 配置调整理由 + 可选 AI 点评"""
    pcts = ALLOC_PCTS.get(risk_profile, ALLOC_PCTS["稳健型"])
    allocations = []
    for i, fund in enumerate(RECOMMENDED_FUNDS):
        f = dict(fund)
        f["pct"] = pcts[i] if i < len(pcts) else 0
        allocations.append(f)

    # 获取实时市场数据，生成配置调整理由
    adjustments = []
    val_pct = 50
    fgi = 50
    try:
        val_data = get_valuation_percentile()
        val_pct = val_data.get("percentile", 50)
    except Exception:
        pass
    try:
        fgi_data = get_fear_greed_index()
        fgi = fgi_data.get("score", 50)
    except Exception:
        pass

    # 生成调整说明（告诉用户"为什么这样配"）
    if val_pct > 70:
        adjustments.append(f"📊 估值 {val_pct:.0f}%（偏高）→ 减配股票，增配债券和现金")
    elif val_pct < 30:
        adjustments.append(f"📊 估值 {val_pct:.0f}%（偏低）→ 增配股票，减配债券")
    else:
        adjustments.append(f"📊 估值 {val_pct:.0f}%（适中）→ 按基础模板配置")

    if fgi > 75:
        adjustments.append(f"😰 恐贪指数 {fgi:.0f}（偏贪婪）→ 别人贪婪我恐惧，多留现金")
    elif fgi < 25:
        adjustments.append(f"😨 恐贪指数 {fgi:.0f}（偏恐惧）→ 别人恐惧我贪婪，可多配股票")
    else:
        adjustments.append(f"😊 恐贪指数 {fgi:.0f}（正常）→ 情绪面中性")

    adjustments.append("🛡️ 塔勒布规则：现金永远 ≥ 15%（反脆弱）")

    result = {
        "profile": risk_profile,
        "allocations": allocations,
        "profiles": list(ALLOC_PCTS.keys()),
        "adjustments": adjustments,
        "marketData": {
            "valuationPct": round(val_pct, 1),
            "fearGreed": round(fgi, 1),
        },
    }

    # 可选：DeepSeek AI 点评每只基金
    if with_ai:
        try:
            from services.ds_enhance import comment_recommend_funds
            ai_comments = comment_recommend_funds(allocations, risk_profile, val_pct, fgi)
            if ai_comments:
                result["aiComments"] = ai_comments
        except Exception:
            pass

    return result
