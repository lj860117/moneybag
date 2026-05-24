"""
投资会诊面板生成器
=================
用户问投资决策问题时（"现在能入场吗"、"该卖吗"），自动：
1. 调 steward fast pipeline 获取 18 模块数据（¥0）
2. 将模块数据映射为 4 大师视角（模板，¥0）
3. 构建综合判断 prompt（交给调用方做 1 次 LLM）

总成本 = 1 次 LLM ≈ ¥0.008，和普通聊天一样。
"""
from __future__ import annotations

import time
from typing import Optional


def generate_panel(user_id: str, question: str = "综合分析") -> dict:
    """生成投资会诊面板

    Returns:
        {
            "perspectives": [
                {"emoji": "🎩", "name": "巴菲特", "focus": "价值投资", "text": "..."},
                {"emoji": "📚", "name": "格雷厄姆", "focus": "安全边际", "text": "..."},
                {"emoji": "🔍", "name": "林奇", "focus": "实地研究", "text": "..."},
                {"emoji": "🌪", "name": "塔勒布", "focus": "反脆弱", "text": "..."},
            ],
            "synthesis_prompt": "...",  # 交给 LLM 做综合的 system+user prompt
            "data_summary": "...",      # 数据摘要（调试用）
            "elapsed_ms": 1200,
        }
    """
    t0 = time.time()

    # ---- Step 1: 跑 steward fast pipeline 获取多维数据 ----
    modules_data = _run_fast_pipeline(user_id, question)

    # ---- Step 2: 映射为 4 大师观点 ----
    perspectives = _build_perspectives(modules_data)

    # ---- Step 3: 构建综合判断 prompt ----
    synthesis_prompt = _build_synthesis_prompt(perspectives, question, modules_data)

    elapsed = int((time.time() - t0) * 1000)

    return {
        "perspectives": perspectives,
        "synthesis_prompt": synthesis_prompt,
        "data_summary": modules_data.get("_summary", ""),
        "elapsed_ms": elapsed,
    }


def _run_fast_pipeline(user_id: str, question: str) -> dict:
    """调 steward fast pipeline，获取模块数据（0 次 LLM）"""
    try:
        from services.steward import get_steward, classify_regime
        from services.decision_context import DecisionContext
        from services.pipeline_runner import PIPELINES

        steward = get_steward()
        ctx = DecisionContext(user_id=user_id, question=question)

        # Regime 分类
        regime_result = classify_regime()
        ctx.regime = regime_result["regime"]
        ctx.regime_confidence = regime_result["confidence"] / 100
        ctx.regime_description = regime_result.get("description", "")

        # 跑 fast pipeline（5步，0 次 LLM：load_user → regime → modules → risk → output）
        ctx = steward.runner.run("fast", ctx)

        # 提取模块结果
        data = {
            "regime": ctx.regime,
            "regime_desc": ctx.regime_description,
            "direction": ctx.direction,
            "confidence": ctx.confidence or ctx.final_confidence,
            "modules": {},
            "risk_level": ctx.risk_level or "normal",
            "risk_alerts": ctx.risk_alerts[:3] if ctx.risk_alerts else [],
        }

        # 从模块结果提取各维度数据
        for name, result in ctx.modules_results.items():
            if isinstance(result, dict) and result.get("available"):
                data["modules"][name] = {
                    "direction": result.get("direction", "neutral"),
                    "confidence": result.get("confidence", result.get("score", 0)),
                    "detail": result.get("detail", result.get("data", {})),
                }

        # 数据摘要
        data["_summary"] = f"regime={ctx.regime}, modules={len(data['modules'])}, direction={ctx.direction}"
        return data

    except Exception as e:
        print(f"[PANEL] pipeline failed: {e}")
        # 降级：尝试直接获取关键数据
        return _fallback_data()


def _fallback_data() -> dict:
    """Pipeline 失败时的降级数据获取"""
    data = {"regime": "unknown", "regime_desc": "", "direction": "neutral",
            "confidence": 0, "modules": {}, "risk_level": "normal", "risk_alerts": [],
            "_summary": "fallback mode"}
    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        fgi = get_fear_greed_index()
        val = get_valuation_percentile()
        data["modules"]["market_data"] = {
            "direction": "neutral",
            "confidence": 50,
            "detail": {"fgi": fgi, "valuation": val},
        }
    except Exception:
        pass
    return data


def _build_perspectives(modules_data: dict) -> list:
    """将模块数据映射为 4 大师视角（纯模板，不调 LLM）"""

    regime = modules_data.get("regime", "unknown")
    regime_desc = modules_data.get("regime_desc", "")
    modules = modules_data.get("modules", {})

    # ---- 巴菲特：价值投资 —— 关注估值 + 股息 + 长期价值 ----
    buffett_text = _perspective_buffett(modules, regime)

    # ---- 格雷厄姆：安全边际 —— 关注资金面 + 风险控制 ----
    graham_text = _perspective_graham(modules, regime, modules_data)

    # ---- 林奇：实地研究 —— 关注行业 + 机构动向 + 基本面 ----
    lynch_text = _perspective_lynch(modules, regime)

    # ---- 塔勒布：反脆弱 —— 关注尾部风险 + 黑天鹅 ----
    taleb_text = _perspective_taleb(modules, regime, modules_data)

    return [
        {"emoji": "🎩", "name": "巴菲特", "focus": "价值投资", "text": buffett_text},
        {"emoji": "📚", "name": "格雷厄姆", "focus": "安全边际", "text": graham_text},
        {"emoji": "🔍", "name": "林奇", "focus": "实地研究", "text": lynch_text},
        {"emoji": "🌪", "name": "塔勒布", "focus": "反脆弱", "text": taleb_text},
    ]


def _perspective_buffett(modules: dict, regime: str) -> str:
    """巴菲特视角：估值是否合理？长期价值如何？"""
    parts = []

    # 直接调估值数据（模块 detail 可能是字符串）
    try:
        from services.market_data import get_valuation_percentile
        val = get_valuation_percentile() or {}
        pct = val.get("percentile", 0)
        pe = val.get("pe", 0)

        if pct:
            if pct > 80:
                parts.append(f"估值百分位{pct}%，市场整体偏贵，安全边际不足")
            elif pct > 60:
                parts.append(f"估值百分位{pct}%，不算便宜但也没泡沫")
            elif pct > 30:
                parts.append(f"估值百分位{pct}%，估值合理，适合优质资产")
            else:
                parts.append(f"估值百分位{pct}%，市场低估，价值投资者的好时机")
        if pe:
            parts.append(f"当前PE={pe:.1f}")
    except Exception as e:
        print(f"[PANEL] buffett valuation: {e}")

    # 股息/分红信息
    dividend_mod = modules.get("dividend", {})
    if isinstance(dividend_mod, dict):
        div_detail = dividend_mod.get("detail", {})
        if isinstance(div_detail, dict):
            dy = div_detail.get("yield")
            if dy and dy > 3:
                parts.append(f"股息率{dy:.1f}%，分红回报不错")

    # 结论
    if not parts:
        parts.append("当前数据有限，无法完整评估内在价值")

    if regime == "high_vol_bear":
        parts.append("高波熊市别急着出手，等恐慌时再看")
    elif regime == "trending_bull":
        parts.append("牛市也要看估值，贵了就少买")

    return "。".join(parts) + "。"


def _perspective_graham(modules: dict, regime: str, data: dict) -> str:
    """格雷厄姆视角：资金面安全吗？下行风险多大？"""
    parts = []

    # 直接调 Tushare 北向数据（模块 detail 是字符串摘要，不够用）
    try:
        from services.factor_data import get_northbound_flow, get_shibor, get_margin_trading
        north = get_northbound_flow() or {}
        if north.get("available") and north.get("net_flow_5d"):
            flow_5d = north["net_flow_5d"]
            if flow_5d < -200:
                parts.append(f"北向5日净流出{abs(flow_5d):.0f}亿，外资大幅撤退，防守为主")
            elif flow_5d < -50:
                parts.append(f"北向5日净流出{abs(flow_5d):.0f}亿，情绪偏谨慎")
            elif flow_5d > 100:
                parts.append(f"北向5日净流入{flow_5d:.0f}亿，资金面偏暖")
            else:
                parts.append("北向资金小幅波动，无明显方向")
        elif north.get("stale") or not north.get("available"):
            parts.append("北向数据暂不可用（数据源更新滞后）")

        margin = get_margin_trading() or {}
        if margin.get("change_5d_pct"):
            chg = margin["change_5d_pct"]
            if chg > 2:
                parts.append("融资余额快速增加，杠杆资金激进")
            elif chg < -2:
                parts.append("融资余额萎缩，场内去杠杆")
            else:
                parts.append(f"融资余额5日变化{chg:+.1f}%，杠杆平稳")

        shibor = get_shibor() or {}
        if shibor.get("overnight"):
            rate = shibor["overnight"]
            if rate > 2.5:
                parts.append(f"银行间利率{rate}%偏高，资金面紧张")
            elif rate < 1.5:
                parts.append(f"银行间利率{rate}%，流动性充裕")
            else:
                parts.append(f"银行间利率{rate}%，资金面正常")
    except Exception as e:
        print(f"[PANEL] graham data fetch: {e}")

    # 风控
    risk_level = data.get("risk_level", "normal")
    if risk_level in ("high", "extreme"):
        parts.append("⚠️ 风控亮红灯，建议降低仓位")

    if not parts:
        parts.append("资金面数据有限，无法完整评估安全边际")

    return "。".join(parts) + "。"


def _perspective_lynch(modules: dict, regime: str) -> str:
    """林奇视角：行业轮动到哪了？机构怎么看？"""
    parts = []

    # 行业轮动
    sector_mod = modules.get("sector_rotation", {})
    sector_detail = sector_mod.get("detail", {})
    if not isinstance(sector_detail, dict):
        sector_detail = {}
    top_gainers = sector_detail.get("top_gainers", [])
    if top_gainers:
        names = [s.get("name", "?") for s in top_gainers[:3]]
        parts.append(f"近期热点板块：{'、'.join(names)}")
    else:
        parts.append("板块轮动不明显，缺乏主线")

    # 券商研报
    broker_mod = modules.get("broker_research", {})
    broker_detail = broker_mod.get("detail", {})
    if not isinstance(broker_detail, dict):
        broker_detail = {}
    consensus = broker_detail.get("consensus", "")
    if consensus:
        consensus_map = {"看多": "机构整体看多", "看空": "机构偏谨慎",
                         "中性": "机构观点分歧"}
        parts.append(consensus_map.get(consensus, f"机构共识：{consensus}"))

    # 新闻情绪
    news_mod = modules.get("news_data", {})
    if news_mod.get("direction") == "bearish":
        parts.append("新闻面偏负面，市场信心不足")
    elif news_mod.get("direction") == "bullish":
        parts.append("新闻面偏正面，市场情绪回暖")

    if not parts:
        parts.append("行业和机构数据有限")

    return "。".join(parts) + "。"


def _perspective_taleb(modules: dict, regime: str, data: dict) -> str:
    """塔勒布视角：有没有黑天鹅？极端风险在哪？"""
    parts = []

    # 地缘风险
    geo_mod = modules.get("geopolitical", {})
    geo_detail = geo_mod.get("detail", {})
    if not isinstance(geo_detail, dict):
        geo_detail = {}
    geo_level = geo_detail.get("level", "low")
    geo_severity = geo_detail.get("max_severity", 0)

    geo_map = {"low": "低", "normal": "低", "moderate": "中等",
               "elevated": "偏高", "high": "高", "extreme": "极高"}
    geo_cn = geo_map.get(geo_level, geo_level)

    if geo_severity >= 3:
        parts.append(f"⚠️ 地缘风险{geo_cn}（严重度{geo_severity}/5），黑天鹅概率上升")
        events = geo_detail.get("top_events", [])
        if events:
            evt_title = events[0].get("title", "")[:30] if isinstance(events[0], dict) else str(events[0])[:30]
            parts.append(f"关注事件：{evt_title}")
    elif geo_severity >= 2:
        parts.append(f"地缘风险{geo_cn}，有不确定性但未到极端")
    else:
        parts.append("地缘风险低，没有明显黑天鹅信号")

    # 波动率 / 市场广度
    market_mod = modules.get("market_data", {})
    fgi_detail = market_mod.get("detail", {}).get("fgi", {})
    if isinstance(fgi_detail, dict):
        score = fgi_detail.get("score", 50)
        if score < 25:
            parts.append(f"恐慌指数{score}，市场极度恐惧=反脆弱者的机会")
        elif score > 75:
            parts.append(f"贪婪指数{score}，群体过度乐观=脆弱信号")

    # 风控预警
    risk_alerts = data.get("risk_alerts", [])
    if risk_alerts:
        parts.append(f"风控预警{len(risk_alerts)}条，注意尾部风险")

    if regime == "high_vol_bear":
        parts.append("高波动环境=不确定性高，仓位要轻")

    if not parts:
        parts.append("暂无明显极端风险信号")

    return "。".join(parts) + "。"


def _build_synthesis_prompt(perspectives: list, question: str, modules_data: dict) -> str:
    """构建给 LLM 的综合判断 prompt"""
    regime = modules_data.get("regime_desc", modules_data.get("regime", ""))
    direction = modules_data.get("direction", "neutral")

    views_text = "\n".join(
        f"【{p['name']}（{p['focus']}）】{p['text']}"
        for p in perspectives
    )

    return f"""你是家庭资产管理教练，用户问：「{question}」

当前市场状态：{regime}
Pipeline 综合方向：{direction}

以下是 4 位大师各自的观点（基于实时数据）：
{views_text}

请用 100 字以内给出你的综合判断。要求：
1. 先给一句结论（能/不能/等等看）
2. 最核心的 1-2 个理由
3. 给一个具体可执行的建议
4. 说人话，像朋友微信消息

禁止：预测具体点位/目标价、建议满仓梭哈、说"稳赚"。"""
