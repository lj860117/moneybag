"""
钱袋子 — AI 对话分析
规则引擎 + LLM API 对话
"""
import os
from fastapi import HTTPException
from config import LLM_API_URL, LLM_API_KEY, LLM_MODEL
from models.schemas import ChatRequest
from services.data_layer import (
    get_fear_greed_index, get_valuation_percentile, get_technical_indicators,
)

# ---- AI 对话分析（纯业务逻辑，路由在 main.py 中注册）----

async def chat_analysis(req: ChatRequest):
    """AI 对话分析 — 回答用户的理财问题"""
    user_msg = req.message.strip()
    if not user_msg:
        raise HTTPException(400, "消息不能为空")

    # 构建市场上下文
    market_ctx = _build_market_context()
    portfolio_ctx = _build_portfolio_context(req.portfolio) if req.portfolio else "用户尚未建仓。"

    # 尝试调用 LLM（支持 OpenAI 兼容 API）
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            import httpx
            system_prompt = f"""你是「钱袋子」的 AI 理财分析师。你的职责：
1. 用通俗易懂的中文回答用户的理财问题
2. 基于真实市场数据给出分析（不编造数据）
3. 永远提醒用户"投资有风险"
4. 不推荐具体买卖时点，只分析趋势和逻辑
5. 回答控制在 200 字以内，简洁有力
6. 分析政策对行业和基金的影响（如降息利好债券、关税利空出口等）
7. 根据新闻事件预判可能的市场趋势（用"可能""趋势"等词汇，不用"一定""必然"）
8. 如果用户问到政策/新闻，结合下方的事件分析给出解读

当前市场数据：
{market_ctx}

用户持仓：
{portfolio_ctx}"""

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_msg},
                        ],
                        "max_tokens": 500,
                        "temperature": 0.7,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    return {"reply": reply, "source": "ai"}
        except Exception as e:
            print(f"[CHAT] LLM call failed: {e}")

    # 降级：规则引擎回答
    reply = _rule_based_reply(user_msg, market_ctx, portfolio_ctx)
    return {"reply": reply, "source": "rules"}


def _build_market_context() -> str:
    """构建市场数据上下文（含恐惧贪婪、技术指标、新闻）"""
    lines = []
    try:
        fgi_data = get_fear_greed_index()
        fgi = fgi_data["score"]
        lines.append(f"恐惧贪婪指数：{fgi:.0f}/100（{fgi_data['level']}）")
        dims = fgi_data.get("dimensions", {})
        if dims:
            dim_parts = [f"{d['label']}:{d['value']}" for d in dims.values()]
            lines.append(f"  ├ 细分：{', '.join(dim_parts)}")
    except Exception:
        lines.append("恐惧贪婪指数：暂无数据")

    # 估值
    try:
        val = get_valuation_percentile()
        lines.append(f"{val['index']}估值百分位：{val['percentile']}%（{val['level']}，{val.get('metric', '')}）")
    except Exception:
        pass

    # 技术指标
    try:
        tech = get_technical_indicators()
        lines.append(f"RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）")
        lines.append(f"MACD：{tech['macd']['trend']}")
        lines.append(f"布林带：{tech['bollinger']['position']}")
    except Exception:
        pass

    codes = {"110020": "沪深300", "050025": "标普500", "000216": "黄金"}
    for code, name in codes.items():
        nav = get_fund_nav(code)
        if nav["nav"] != "N/A":
            lines.append(f"{name}({code})：净值 {nav['nav']}，日涨跌 {nav['change']}%")

    # 宏观经济数据
    try:
        macro = get_macro_data()
        macro_parts = []
        for key, label in [("cpi", "CPI"), ("pmi", "PMI"), ("m2", "M2"), ("ppi", "PPI")]:
            item = macro.get(key, {})
            val = item.get("value")
            if val and val != "N/A":
                macro_parts.append(f"{label}:{val}")
        if macro_parts:
            lines.append(f"宏观数据：{' | '.join(macro_parts)}")
    except Exception:
        pass

    # 最新政策/国际新闻摘要
    try:
        policy = get_policy_news(5)
        valid = [n for n in policy if n["title"] != "政策资讯加载中..."]
        if valid:
            lines.append("\n最新政策/国际动态：")
            for n in valid[:5]:
                lines.append(f"  - {n['title']}")
    except Exception:
        pass

    # 新闻→持仓关联分析
    try:
        all_news = get_policy_news(10) + get_market_news(5)
        impacts = analyze_news_impact(all_news)
        if impacts:
            lines.append("\n事件对持仓的影响分析：")
            for imp in impacts[:3]:
                bull = "📈利好:" + ",".join(imp["bullish"]) if imp["bullish"] else ""
                bear = "📉利空:" + ",".join(imp["bearish"]) if imp["bearish"] else ""
                lines.append(f"  - [{imp['tag']}] {imp['impact']} {bull} {bear}")
    except Exception:
        pass

    return "\n".join(lines) if lines else "暂无市场数据"


def _build_portfolio_context(p: Portfolio) -> str:
    if not p or not p.holdings:
        return "用户尚未建仓。"
    lines = [f"风险类型：{p.profile}，总投入：¥{p.amount:,.0f}"]
    for h in p.holdings:
        lines.append(f"  - {h.name}({h.code})：¥{h.amount:,.0f}，目标占比 {h.targetPct}%")
    return "\n".join(lines)


def _rule_based_reply(msg: str, market_ctx: str, portfolio_ctx: str) -> str:
    """规则引擎降级回答"""
    msg_lower = msg.lower()

    # 入场时机
    if any(k in msg_lower for k in ["什么时候买", "入手", "入场", "时机", "现在能买", "适合买", "抄底"]):
        val = get_valuation_percentile()
        fgi_data = get_fear_greed_index()
        fgi = fgi_data["score"]
        timing = val["percentile"] * 0.6 + (100 - fgi) * 0.4
        if timing < 30:
            tip = "🟢 **当前非常适合入场！** 估值低+市场恐惧，是历史上最佳买入窗口。"
        elif timing < 50:
            tip = "🟡 **适合定投入场。** 估值合理，按计划定投即可。"
        elif timing < 70:
            tip = "🟠 **谨慎入场。** 估值偏高，建议降低金额或等回调。"
        else:
            tip = "🔴 **不建议大额入场。** 估值高+市场贪婪，建议等待。"
        return f"📊 入场时机分析：\n\n{tip}\n\n{val['index']}估值百分位：{val['percentile']}%（{val['level']}）\n恐惧贪婪指数：{fgi:.0f}\n\n💡 建议：不管时机好坏，定投永远是对的。定投的精髓就是穿越牛熊，低估时多买、高估时少买。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 止盈止损 / 卖出 / 什么价位卖
    if any(k in msg_lower for k in ["卖", "止盈", "止损", "价位", "该出", "什么时候出", "锁定利润", "减仓", "到了多少"]):
        return f"🔔 止盈止损策略：\n\n钱袋子采用**分批止盈法**，根据你的风险类型自动设定目标：\n\n🐢 保守型：+15% 止盈 / -8% 止损\n🐰 稳健型：+20% 止盈 / -10% 止损\n🦊 平衡型：+30% 止盈 / -15% 止损\n🦁 进取型：+50% 止盈 / -20% 止损\n🦅 激进型：+80% 止盈 / -25% 止损\n\n📌 操作建议：\n1️⃣ **到了止盈线，不用全卖** — 卖 1/3 锁利润，剩余继续持有\n2️⃣ **到了止损线，先看原因** — 如果基金基本面没变，可能反而是加仓机会\n3️⃣ **不设绝对卖点** — 结合估值百分位综合判断\n\n你可以在首页的 AI 信号里实时看到自己的止盈止损状态 📊\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 智能定投 / 定投方式
    if any(k in msg_lower for k in ["定投", "智能", "固定还是", "怎么投", "投多少", "每月投"]):
        val = get_valuation_percentile()
        smart = calc_smart_dca(1000, val["percentile"])
        return f"🧠 智能定投 vs 固定定投：\n\n**固定定投**：每月投相同金额，简单省心，长期有效。\n**智能定投**：根据市场估值动态调整 — 低估多买、高估少买。\n\n钱袋子的智能定投策略：\n\n| 估值百分位 | 倍率 | 说明 |\n|-----------|------|------|\n| < 20% | 1.5x | 极度低估，多买 |\n| 20-30% | 1.3x | 低估，适当多买 |\n| 30-50% | 1.1x | 偏低，略多 |\n| 50-70% | 1.0x | 正常，标准额 |\n| 70-85% | 0.7x | 偏高，少买 |\n| > 85% | 0.3x | 高估，大幅减少 |\n\n📊 当前{val['index']}估值：{val['percentile']}%（{val['level']}）\n💡 建议本月倍率：{smart['multiplier']}x — {smart['advice']}\n\n智能定投比固定定投长期多赚约 15-20%，但需要坚持 3 年以上才能看到效果。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["跌", "亏", "赔", "绿", "下跌"]):
        return f"📉 市场波动是正常现象。\n\n{market_ctx}\n\n长期投资（3年+）能大幅平滑短期波动。如果你的资产配比还在目标范围内，建议保持定投节奏，不要恐慌卖出。记住投资铁律：跌了别卖，越跌越该买。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["涨", "赚", "红", "上涨", "牛"]):
        return f"📈 恭喜！不过也别过于乐观。\n\n{market_ctx}\n\n赚钱时更要冷静，检查一下各资产的占比是否偏离目标太多。如果某类资产涨太多导致占比过高，可以考虑再平衡——卖掉一点涨多的，买入涨少的。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 特定资产类问题（优先于通用"买/卖"匹配，避免"黄金还能买吗"被通用规则拦截）
    if any(k in msg_lower for k in ["黄金", "gold"]):
        nav = get_fund_nav("000216")
        news = get_fund_news("000216", 3)
        news_text = "\n".join([f"📰 {n['title']}" for n in news if n["title"] != "黄金市场动态获取中..."])
        return f"🪙 黄金近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n{news_text}\n\n黄金是经典避险资产，近年全球央行持续增持。在你的配置中作为分散风险的角色，建议保持目标占比即可，不用频繁操作。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["标普", "美股", "s&p", "sp500"]):
        nav = get_fund_nav("050025")
        news = get_fund_news("050025", 3)
        news_text = "\n".join([f"📰 {n['title']}" for n in news if "获取中" not in n["title"]])
        return f"🇺🇸 标普500近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n{news_text}\n\n标普500追踪美国500强企业，过去30年年化约10%。分散地域风险的核心配置。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["沪深", "a股", "300", "大盘"]):
        nav = get_fund_nav("110020")
        news = get_fund_news("110020", 3)
        news_text = "\n".join([f"📰 {n['title']}" for n in news if "获取中" not in n["title"]])
        return f"📊 沪深300近况：净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n{news_text}\n\n沪深300覆盖A股市值最大的300家公司，是A股的核心指数。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["债券", "债基", "纯债"]):
        nav = get_fund_nav("217022")
        return f"🏦 债券近况：招商产业债A 净值 {nav['nav']}，日涨跌 {nav['change']}%。\n\n纯债基金是组合的\"稳定器\"，历史几乎没有亏过年度。适合保守资金配置。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    if any(k in msg_lower for k in ["买", "加仓", "什么时候"]):
        return f"💰 关于买入时机：\n\n{market_ctx}\n\n定投的精髓就是「不择时」——每月固定日期买入，无论涨跌。这样长期下来会自动实现低买多、高买少。如果恐惧指数很高（市场极度悲观），可以适当多买一点。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 政策/大宗商品/行业影响分析
    if any(k in msg_lower for k in ["政策", "降息", "降准", "关税", "贸易战", "制裁", "战争", "地缘",
                                     "大宗", "油价", "原油", "opec", "影响", "利好", "利空",
                                     "行业", "板块", "半导体", "芯片", "基建"]):
        try:
            all_news = get_policy_news(10) + get_market_news(5)
            impacts = analyze_news_impact(all_news)
            if impacts:
                impact_lines = []
                for imp in impacts[:4]:
                    bull = "📈利好：" + "、".join(imp["bullish"]) if imp["bullish"] else ""
                    bear = "📉利空：" + "、".join(imp["bearish"]) if imp["bearish"] else ""
                    impact_lines.append(f"🏷️ **{imp['tag']}**\n{imp['impact']}\n{bull} {bear}\n涉及行业：{', '.join(imp['sectors'])}")
                impact_text = "\n\n".join(impact_lines)
                return f"🏛️ 当前事件对你持仓的影响分析：\n\n{impact_text}\n\n💡 建议：关注事件发展趋势，短期波动不改长期逻辑。如果你是定投模式，保持节奏即可。\n\n⚠️ 以上基于关键词匹配的初步分析，仅供参考，不构成投资建议。"
            else:
                return "🏛️ 当前暂未检测到对你持仓有显著影响的政策/事件。\n\n💡 保持定投节奏，不需要每天看新闻做决定。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        except Exception:
            return "🏛️ 政策影响分析暂时无法获取，请稍后再试。"

    # 新闻/资讯/发生了什么
    if any(k in msg_lower for k in ["新闻", "资讯", "消息", "发生", "怎么了", "什么情况", "为什么"]):
        news = get_market_news(5)
        news_lines = []
        for n in news[:5]:
            if n.get("url"):
                news_lines.append(f'📰 <a href="{n["url"]}" target="_blank" style="color:#F59E0B;text-decoration:underline">{n["title"]}</a>（{n["source"]}）')
            else:
                news_lines.append(f"📰 {n['title']}（{n['source']}）")
        news_text = "\n".join(news_lines)
        return f"📰 最新市场资讯：\n\n{news_text}\n\n💡 建议：关注大趋势，不要因为单条新闻做决定。投资看的是长期逻辑。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 技术分析
    if any(k in msg_lower for k in ["技术", "rsi", "macd", "布林", "超买", "超卖", "指标"]):
        tech = get_technical_indicators()
        return f"📊 沪深300技术指标：\n\n📈 RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）\n  └ >70 超买区，<30 超卖区\n\n📉 MACD：{tech['macd']['trend']}\n  └ DIF:{tech['macd']['dif']:.4f} DEA:{tech['macd']['dea']:.4f}\n\n📐 布林带：{tech['bollinger']['position']}\n  └ 上轨:{tech['bollinger']['upper']} 中轨:{tech['bollinger']['middle']} 下轨:{tech['bollinger']['lower']}\n\n💡 技术指标是辅助参考，不能单独作为买卖依据。结合估值+基本面综合判断更靠谱。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    # 宏观/经济/cpi/pmi
    if any(k in msg_lower for k in ["宏观", "经济", "cpi", "pmi", "通胀", "利率", "货币", "m2"]):
        events = get_macro_calendar()
        macro_text = "\n".join([f"{e['icon']} {e['name']}：{e['value']}（{e['date']}）\n  └ {e['impact']}" for e in events])
        return f"🏛️ 宏观经济数据：\n\n{macro_text}\n\n💡 宏观数据影响市场整体方向。CPI低+PMI>50+M2宽松 = 对股市友好的环境。\n\n⚠️ 以上仅供参考，不构成投资建议。"

    return f"🤔 关于你的问题：\n\n当前市场概况：\n{market_ctx}\n\n{portfolio_ctx}\n\n你可以问我：\n📰 「最近有什么新闻？」\n📊 「技术指标怎么样？」\n🏛️ 「宏观经济怎么样？」\n🎯 「现在适合入场吗？」\n💰 「什么时候该卖？」\n🧠 「定投多少合适？」\n\n⚠️ 以上仅供参考，不构成投资建议。"



