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
import time
import json

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "portfolio",
    "scope": "private",
    "input": ["user_id", "risk_profile"],
    "output": "allocation_advice",
    "cost": "llm_light",
    "tags": ["配置", "投顾", "动态调整", "AI选基"],
    "description": "智能资产配置：估值+恐贪+美林时钟+塔勒布规则动态调整+AI选基",
    "layer": "output",
    "priority": 6,
}

from config import ALLOCATION_PROFILES, VALUATION_HIGH, VALUATION_LOW, RISK_REBALANCE_THRESHOLD
from services.portfolio_calc import calc_holdings_from_transactions
from services.data_layer import get_fund_nav, get_valuation_percentile, get_fear_greed_index

# ---- AI 动态选基缓存 ----
_ai_fund_cache = {}
_AI_FUND_CACHE_TTL = 3600  # 1 小时

# 颜色池（用于饼图）
_FUND_COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#F97316", "#EF4444", "#8B5CF6",
                "#EC4899", "#14B8A6", "#F43F5E", "#6366F1"]


def _ai_pick_funds(risk_profile: str, val_pct: float, fgi: float) -> list:
    """让 DeepSeek 从 fund_screen 排行里选出最优 5 只基金 + 货币基金兜底
    返回: [{name, code, fullName, color, category, returns, aiReason}, ...]
    """
    cache_key = f"ai_fund_{risk_profile}_{int(val_pct)}_{int(fgi)}"
    now = time.time()
    if cache_key in _ai_fund_cache and now - _ai_fund_cache[cache_key]["ts"] < _AI_FUND_CACHE_TTL:
        return _ai_fund_cache[cache_key]["data"]

    try:
        from services.fund_screen import screen_funds

        # 拉各类型 TOP 10
        stock_funds = screen_funds(fund_type="stock", top_n=10).get("funds", [])
        bond_funds = screen_funds(fund_type="bond", top_n=10).get("funds", [])
        index_funds = screen_funds(fund_type="index", top_n=10).get("funds", [])
        qdii_funds = screen_funds(fund_type="qdii", top_n=5).get("funds", [])

        # 构造候选列表给 LLM
        candidate_text = ""
        all_candidates = []
        for label, funds in [("股票型", stock_funds), ("债券型", bond_funds),
                             ("指数型", index_funds), ("QDII", qdii_funds)]:
            for f in funds[:8]:
                r = f.get("returns", {})
                line = f"{f['code']} {f['name']} | 1年{r.get('1y','N/A')}% 6月{r.get('6m','N/A')}% 3月{r.get('3m','N/A')}% | 评分{f.get('score','N/A')}"
                candidate_text += f"[{label}] {line}\n"
                all_candidates.append({**f, "type_label": label})

        if not all_candidates:
            return None  # 降级到硬编码

        # 调 DeepSeek
        from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL
        if not LLM_API_KEY:
            return None

        # 风险等级→偏好描述
        risk_desc = {
            "保守型": "极度保守，债券为主(60%+)，股票极少",
            "稳健型": "稳健优先，债券30-40%，股票30-40%，均衡配置",
            "平衡型": "攻守平衡，股票40-50%，债券20-30%",
            "进取型": "偏进攻，股票60-70%，适度配债",
            "激进型": "激进进攻，股票70%+，高弹性品种优先",
        }

        prompt = f"""你是专业基金投资顾问。请从以下基金候选池中，为「{risk_profile}」用户挑选最优 5 只基金。

当前市场环境：
- 估值百分位: {val_pct:.0f}%（{'高估' if val_pct > 70 else '低估' if val_pct < 30 else '适中'}）
- 恐贪指数: {fgi:.0f}（{'极度贪婪' if fgi > 80 else '极度恐惧' if fgi < 20 else '正常'}）
- 风险偏好: {risk_desc.get(risk_profile, '稳健')}

候选基金池（类型 | 代码 名称 | 收益率 | 评分）：
{candidate_text}

要求：
1. 选 5 只，涵盖不同类型（不要全选同类型）
2. 根据市场环境调整选择（高估多配债/低估多配股）
3. 给每只基金一句话理由（15字内）
4. 给出建议占比（5只加起来=95%，剩5%给货币基金应急）

返回 JSON 数组，格式：
[{{"code":"110020","name":"沪深300","reason":"低估值反弹机会","pct":25,"category":"stock"}},...]
只返回 JSON 数组，不要其他内容。"""

        import httpx
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                LLM_API_URL,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            if resp.status_code != 200:
                print(f"[AI_FUND] LLM failed: {resp.status_code}")
                return None

            text = resp.json()["choices"][0]["message"]["content"]
            import re
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if not json_match:
                print(f"[AI_FUND] LLM no JSON in response")
                return None

            picks = json.loads(json_match.group())

        # 构造标准格式
        result = []
        candidate_map = {f["code"]: f for f in all_candidates}
        for i, p in enumerate(picks[:5]):
            code = str(p.get("code", ""))
            cand = candidate_map.get(code, {})
            cat = p.get("category", "stock")
            r = cand.get("returns", {})
            result.append({
                "name": p.get("name", cand.get("name", code)),
                "code": code,
                "fullName": cand.get("name", p.get("name", "")),
                "color": _FUND_COLORS[i % len(_FUND_COLORS)],
                "pct": p.get("pct", 19),
                "category": cat,
                "assetType": "fund",  # AI选出的都是基金
                "aiReason": p.get("reason", ""),
                "score": cand.get("score", 0),
                "returns": {
                    "good": round((r.get("1y", 15) or 15) / 100, 2),
                    "mid": round((r.get("6m", 5) or 5) / 100, 2),
                    "bad": round((r.get("3m", -5) or -5) / 100, 2),
                },
            })

        # 加货币基金兜底（5%）
        result.append({
            "name": "货币(应急)",
            "code": "余额宝",
            "fullName": "余额宝",
            "color": "#E5E7EB",
            "pct": 5,
            "category": "cash",
            "assetType": "fund",
            "aiReason": "应急储备",
            "returns": {"good": 0.02, "mid": 0.018, "bad": 0.015},
        })

        print(f"[AI_FUND] Picked {len(result)} funds for {risk_profile}")
        _ai_fund_cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as e:
        print(f"[AI_FUND] Failed: {e}")
        import traceback
        traceback.print_exc()
        return None


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
    FIX 2026-04-19 V7.2: 全部步长从 config.ALLOCATION_ADJUST 读取
    """
    from config import ALLOCATION_ADJUST as _ADJ
    s, b, c = base["stock"], base["bond"], base["cash"]

    # 1. 估值调整（±10%）
    if val_pct > 85:
        # 极度高估 → 大幅减股
        d = _ADJ["valuation_extreme_high"]
        s += d["s"]; b += d["b"]; c += d["c"]
    elif val_pct > 70:
        # 高估 → 小幅减股
        d = _ADJ["valuation_high"]
        s += d["s"]; b += d["b"]; c += d["c"]
    elif val_pct < 15:
        # 极度低估 → 大幅加股
        d = _ADJ["valuation_extreme_low"]
        s += d["s"]; b += d["b"]; c += d["c"]
    elif val_pct < 30:
        # 低估 → 小幅加股
        d = _ADJ["valuation_low"]
        s += d["s"]; b += d["b"]; c += d["c"]

    # 2. 恐贪指数调整（±5%）
    if fgi > 80:
        # 极度贪婪 → 别人贪婪我恐惧，减股
        d = _ADJ["fgi_extreme_greed"]
        s += d["s"]; c += d["c"]
    elif fgi < 20:
        # 极度恐惧 → 别人恐惧我贪婪，加股
        d = _ADJ["fgi_extreme_fear"]
        s += d["s"]; c += d["c"]

    # 3. 塔勒布铁律：现金永远 ≥ cash_floor（反脆弱）
    _cash_floor = _ADJ["cash_floor"]
    if c < _cash_floor:
        diff = _cash_floor - c
        c = _cash_floor
        # 从股票里扣
        s -= diff

    # 4. 合理性约束
    s = max(_ADJ["stock_min"], min(_ADJ["stock_max"], s))
    b = max(0.0, min(_ADJ["bond_max"], b))
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
     "returns": {"good": 0.15, "mid": 0.08, "bad": -0.10}, "category": "stock", "assetType": "fund", "etfCode": "510300"},
    {"name": "标普500", "code": "050025", "fullName": "博时标普500ETF联接A", "color": "#10B981",
     "returns": {"good": 0.18, "mid": 0.10, "bad": -0.12}, "category": "stock", "assetType": "fund"},
    {"name": "债券", "code": "217022", "fullName": "招商产业债A", "color": "#F59E0B",
     "returns": {"good": 0.06, "mid": 0.04, "bad": 0.01}, "category": "bond", "assetType": "fund"},
    {"name": "黄金", "code": "000216", "fullName": "华安黄金ETF联接A", "color": "#F97316",
     "returns": {"good": 0.15, "mid": 0.08, "bad": -0.05}, "category": "other", "assetType": "fund", "etfCode": "518880"},
    {"name": "红利低波", "code": "008114", "fullName": "天弘红利低波100联接A", "color": "#EF4444",
     "returns": {"good": 0.12, "mid": 0.07, "bad": -0.05}, "category": "stock", "assetType": "fund", "etfCode": "515100"},
    {"name": "货币(应急)", "code": "余额宝", "fullName": "余额宝", "color": "#E5E7EB",
     "returns": {"good": 0.02, "mid": 0.018, "bad": 0.015}, "category": "cash", "assetType": "fund"},
]

# 风险等级 → 各基金占比
ALLOC_PCTS = {
    "保守型":  [10, 5, 50, 15, 10, 10],
    "稳健型":  [20, 10, 35, 15, 10, 10],
    "平衡型":  [30, 20, 20, 15, 10, 5],
    "进取型":  [35, 25, 10, 10, 15, 5],
    "激进型":  [40, 30, 5, 5, 15, 5],
}


def get_recommend_allocations(risk_profile: str = "稳健型", with_ai: bool = False, preference: str = "fund") -> dict:
    """返回推荐配置列表 + 配置理由 + 可选 AI 点评
    preference: fund(纯基金，默认) / stock(纯股票) / mixed(50%基金+50%股票)
    """
    # 获取实时市场数据
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

    # 生成调整说明
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

    allocations = []

    if preference == "fund":
        # 纯基金模式 — 优先 AI 动态选基，失败降级硬编码
        ai_picks = _ai_pick_funds(risk_profile, val_pct, fgi)
        if ai_picks:
            allocations = ai_picks
            adjustments.append("🤖 AI 根据市场环境从全量基金排行中动态精选")
        else:
            # 降级：硬编码 6 只经典基金
            pcts = ALLOC_PCTS.get(risk_profile, ALLOC_PCTS["稳健型"])
            for i, fund in enumerate(RECOMMENDED_FUNDS):
                f = dict(fund)
                f["pct"] = pcts[i] if i < len(pcts) else 0
                allocations.append(f)
            adjustments.append("⚠️ AI 选基暂不可用，使用经典配置")

    elif preference == "stock":
        # 纯股票模式 — 从选股引擎拿 TOP 6
        try:
            from services.stock_screen import screen_stocks
            result = screen_stocks(top_n=6)
            stocks = result.get("stocks", [])
            if stocks:
                # 根据风险等级分配权重
                risk_weights = {
                    "保守型": [25, 20, 20, 15, 10, 10],
                    "稳健型": [20, 20, 18, 16, 14, 12],
                    "平衡型": [20, 18, 18, 16, 14, 14],
                    "进取型": [22, 20, 18, 16, 12, 12],
                    "激进型": [25, 22, 18, 15, 12, 8],
                }
                weights = risk_weights.get(risk_profile, risk_weights["稳健型"])
                for i, s in enumerate(stocks[:6]):
                    allocations.append({
                        "name": s.get("name", s.get("code", "未知")),
                        "code": s.get("code", ""),
                        "fullName": s.get("name", ""),
                        "pct": weights[i] if i < len(weights) else 10,
                        "color": ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#F97316"][i % 6],
                        "category": "stock",
                        "assetType": "stock",  # 明确标记：个股，只能券商买
                        "score": s.get("totalScore", 0),
                        "returns": {"good": 0.25, "mid": 0.12, "bad": -0.15},
                    })
                adjustments.append(f"📊 推荐 {len(allocations)} 只选股引擎 TOP 精选个股")
        except Exception as e:
            adjustments.append(f"⚠️ 选股引擎暂不可用: {str(e)[:50]}")
            # 降级为基金
            pcts = ALLOC_PCTS.get(risk_profile, ALLOC_PCTS["稳健型"])
            for i, fund in enumerate(RECOMMENDED_FUNDS):
                f = dict(fund)
                f["pct"] = pcts[i] if i < len(pcts) else 0
                allocations.append(f)

    elif preference == "mixed":
        # 混合模式 — 50% 基金 + 50% 股票
        # 基金部分：取前 3 只核心基金（沪深300/标普500/债券）
        pcts_base = ALLOC_PCTS.get(risk_profile, ALLOC_PCTS["稳健型"])
        fund_slice = RECOMMENDED_FUNDS[:3]  # 沪深300, 标普500, 债券
        fund_pcts = [15, 10, 25]  # 基金占 50%
        for i, fund in enumerate(fund_slice):
            f = dict(fund)
            f["pct"] = fund_pcts[i] if i < len(fund_pcts) else 10
            allocations.append(f)

        # 股票部分：选股引擎 TOP 3
        try:
            from services.stock_screen import screen_stocks
            result = screen_stocks(top_n=3)
            stocks = result.get("stocks", [])
            stock_pcts = [20, 17, 13]  # 股票占 50%
            for i, s in enumerate(stocks[:3]):
                allocations.append({
                    "name": s.get("name", s.get("code", "未知")),
                    "code": s.get("code", ""),
                    "fullName": s.get("name", ""),
                    "pct": stock_pcts[i] if i < len(stock_pcts) else 15,
                    "color": ["#EF4444", "#8B5CF6", "#F97316"][i % 3],
                    "category": "stock",
                    "assetType": "stock",  # 明确标记：个股
                    "score": s.get("totalScore", 0),
                    "returns": {"good": 0.22, "mid": 0.10, "bad": -0.12},
                })
            adjustments.append(f"🔄 混合模式：{len(fund_slice)} 只基金 + {len(stocks[:3])} 只精选股票")
        except Exception as e:
            # 股票部分降级为剩余基金
            adjustments.append(f"⚠️ 选股引擎暂不可用，混合模式降级: {str(e)[:50]}")
            for i, fund in enumerate(RECOMMENDED_FUNDS[3:]):
                f = dict(fund)
                f["pct"] = [20, 15, 15][i] if i < 3 else 10
                allocations.append(f)
    else:
        # 未知偏好，默认基金
        pcts = ALLOC_PCTS.get(risk_profile, ALLOC_PCTS["稳健型"])
        for i, fund in enumerate(RECOMMENDED_FUNDS):
            f = dict(fund)
            f["pct"] = pcts[i] if i < len(pcts) else 0
            allocations.append(f)

    result = {
        "profile": risk_profile,
        "preference": preference,
        "allocations": allocations,
        "profiles": list(ALLOC_PCTS.keys()),
        "adjustments": adjustments,
        "marketData": {
            "valuationPct": round(val_pct, 1),
            "fearGreed": round(fgi, 1),
        },
    }

    # 可选：DeepSeek AI 点评
    if with_ai:
        try:
            from services.ds_enhance import comment_recommend_funds
            ai_comments = comment_recommend_funds(allocations, risk_profile, val_pct, fgi)
            if ai_comments:
                result["aiComments"] = ai_comments
        except Exception:
            pass

    return result
