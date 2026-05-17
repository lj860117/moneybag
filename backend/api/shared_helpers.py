"""
共享辅助函数（从 main.py 提取）
================================
包含被多个 api/ 路由文件共享的:
  - 市场上下文构建 (_build_market_context)
  - 持仓上下文构建 (_build_portfolio_context)
  - System prompt 加载与构建
  - 规则引擎降级回答 (_rule_based_reply)
  - 聊天意图分类 (classify_chat_intent)
  - OCR 处理 (_do_ocr)
  - 预警冷却字典 (_alert_cooldown)
  - 用户偏好默认值 (USER_DEFAULTS / USER_OVERRIDES)
  - 家庭成员常量 (FAMILY_MEMBERS / NICKNAMES)
  - 可用模型列表 (AVAILABLE_MODELS)
  - 静态文件缓存 (_cached_file_response)

Design doc: docs/design/12-framework-refactor.md §四
"""
from __future__ import annotations
import os
import json
import time
import re as _re
from pathlib import Path

from services.data_layer import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    get_technical_indicators, get_fund_news, get_market_news,
    get_macro_calendar, get_northbound_flow, get_margin_trading,
    get_shibor, get_dividend_yield, get_news_sentiment_score,
    get_policy_news, analyze_news_impact,
)
from services.signal import calc_smart_dca

from fastapi.responses import FileResponse
from infra.cache import MemoryCache


# ========================================================
# 市场上下文缓存
# ========================================================
_MARKET_CTX_TTL = 300  # 5 分钟缓存
_market_ctx_cache = MemoryCache(default_ttl=_MARKET_CTX_TTL)


def _build_market_context() -> str:
    """构建市场数据上下文（含恐惧贪婪、技术指标、新闻），5分钟缓存"""
    now = time.time()
    cache_key = "market_context"
    cached = _market_ctx_cache.get(cache_key)
    if cached is not None:
        return cached
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
        # 沪深300实时点位（从布林带 current 字段获取）
        hs300_price = tech.get('bollinger', {}).get('current', 0)
        if hs300_price:
            lines.append(f"沪深300指数：当前 {hs300_price} 点")
        lines.append(f"RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）")
        lines.append(f"MACD：{tech['macd']['trend']}")
        lines.append(f"布林带：{tech['bollinger']['position']}（上轨{tech['bollinger'].get('upper',0)} 中轨{tech['bollinger'].get('middle',0)} 下轨{tech['bollinger'].get('lower',0)}）")
    except Exception:
        pass

    codes = {"110020": "沪深300", "050025": "标普500", "000216": "黄金"}
    for code, name in codes.items():
        nav = get_fund_nav(code)
        if nav["nav"] != "N/A":
            lines.append(f"{name}({code})：净值 {nav['nav']}，日涨跌 {nav['change']}%")

    # 宏观经济数据
    try:
        macro = get_macro_calendar()
        macro_parts = []
        for key, label in [("cpi", "CPI"), ("pmi", "PMI"), ("m2", "M2"), ("ppi", "PPI")]:
            item = macro.get(key, {}) if isinstance(macro, dict) else {}
            val = item.get("value")
            if val and val != "N/A":
                macro_parts.append(f"{label}:{val}")
        if macro_parts:
            lines.append(f"宏观数据：{' | '.join(macro_parts)}")
    except Exception:
        pass

    # 最新政策/国际新闻摘要
    try:
        policy = get_policy_news(10)
        valid = [n for n in policy if n["title"] != "政策资讯加载中..."]
        if valid:
            BULL_KW = ["降息", "降准", "宽松", "利好", "上涨", "增持", "反弹", "刺激"]
            BEAR_KW = ["加息", "收紧", "利空", "下跌", "减持", "暴跌", "制裁", "关税"]
            lines.append("\n最新政策/国际动态：")
            for n in valid[:10]:
                title = n["title"]
                if any(k in title for k in BULL_KW):
                    mood = "[利好🟢]"
                elif any(k in title for k in BEAR_KW):
                    mood = "[利空🔴]"
                else:
                    mood = "[中性]"
                lines.append(f"  - {mood} {title}")
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

    # 全球市场数据
    try:
        from services.global_market import get_global_snapshot
        gs = get_global_snapshot()
        if gs.get("summary"):
            lines.append("")
            lines.append(gs["summary"])
    except Exception:
        pass

    # 国内政策数据
    try:
        from services.policy_data import get_policy_summary_for_context
        policy_ctx = get_policy_summary_for_context()
        if policy_ctx:
            lines.append("\n国内政策动态：")
            lines.append(policy_ctx)
    except Exception:
        pass

    # V8 扩展宏观
    try:
        from services.macro_v8 import get_v8_macro_summary
        v8_ctx = get_v8_macro_summary()
        if v8_ctx:
            lines.append("\n经济基本面：")
            lines.append(v8_ctx)
    except Exception:
        pass

    # 大宗商品 + ETF 资金流
    try:
        from services.market_factors import get_commodity_prices, get_etf_fund_flow
        comm = get_commodity_prices()
        if comm.get("available"):
            parts = []
            if comm.get("gold"):
                parts.append(f"黄金{comm['gold']['price']}{comm['gold']['unit']}({comm['gold']['change_pct']:+.1f}%)")
            if comm.get("copper"):
                parts.append(f"铜{comm['copper']['price']}{comm['copper']['unit']}({comm['copper']['change_pct']:+.1f}%)")
            if parts:
                lines.append(f"\n大宗商品：{'，'.join(parts)}")
    except Exception:
        pass

    try:
        from services.market_factors import get_etf_fund_flow
        etf = get_etf_fund_flow()
        if etf.get("available") and etf.get("top_inflow"):
            top = etf["top_inflow"][0]
            lines.append(f"ETF资金流：TOP流入 {top['name']}({top['flow']:.0f}万)")
    except Exception:
        pass

    # 资金面三件套
    try:
        north = get_northbound_flow()
        if north.get("available"):
            lines.append(f"\n资金面：北向资金今日{north.get('net_flow_today', 0):+.1f}亿，5日{north.get('net_flow_5d', 0):+.1f}亿（{north.get('trend', '')}）")
    except Exception:
        pass
    try:
        margin = get_margin_trading()
        if margin.get("available"):
            lines.append(f"融资融券：余额{margin.get('margin_balance', 0):.0f}亿，5日变动{margin.get('margin_change_5d', 0):+.1f}%")
    except Exception:
        pass
    try:
        shibor_data = get_shibor()
        if shibor_data.get("available"):
            lines.append(f"SHIBOR：隔夜{shibor_data.get('overnight', 0)}%（{shibor_data.get('trend', '')}）")
    except Exception:
        pass

    result = "\n".join(lines) if lines else "暂无市场数据"
    _market_ctx_cache.set("market_context", result, ttl=_MARKET_CTX_TTL)
    return result


# ========================================================
# 持仓上下文构建
# ========================================================

def _build_portfolio_context(p=None, user_id: str = "default") -> str:
    """构建用户持仓+盈亏+风控+配置建议的完整上下文（多用户隔离）"""
    from services.holding_intelligence import build_holding_context

    lines = []

    # 1. 基本持仓信息
    if p and p.holdings:
        lines.append(f"【用户画像】风险类型：{p.profile}，总投入：¥{p.amount:,.0f}")
        lines.append("【持仓明细】")
        for h in p.holdings:
            lines.append(f"  - {h.name}({h.code})：¥{h.amount:,.0f}，目标占比 {h.targetPct}%")
    else:
        # 后端主动拉取真实持仓（stock-holdings + fund-holdings + assets）
        _has_data = False
        try:
            from services.stock_monitor import load_stock_holdings
            from services.fund_monitor import load_fund_holdings
            stocks = load_stock_holdings(user_id) or []
            funds = load_fund_holdings(user_id) or []
            if stocks or funds:
                _has_data = True
                lines.append("【持仓明细】（后端真实数据）")
                for s in stocks:
                    lines.append(f"  - 股票：{s.get('name','?')}({s.get('code','')}) {s.get('shares',0)}股 成本¥{s.get('costPrice',0)}")
                for f in funds:
                    lines.append(f"  - 基金：{f.get('name','?')}({f.get('code','')}) {f.get('shares',0)}份 成本{f.get('costNav',0)}")
        except Exception:
            pass
        try:
            from services.unified_networth import calc_unified_networth
            nw = calc_unified_networth(user_id)
            if nw and nw.get("netWorth", 0) > 0:
                _has_data = True
                lines.append(f"  - 净资产：¥{nw['netWorth']:,.0f}（投资¥{nw.get('breakdown',{}).get('investment',{}).get('total',0):,.0f} + 现金¥{nw.get('breakdown',{}).get('cash',{}).get('total',0):,.0f}）")
        except Exception:
            pass
        if not _has_data:
            lines.append("用户尚未录入持仓/资产数据。")

    # 2. 风控状态
    vp_val = 50
    try:
        vp = get_valuation_percentile()
        vp_val = vp.get("percentile", 50)
        fgi_data = get_fear_greed_index()
        fgi_val = fgi_data.get("score", 50)
        from services.risk import generate_risk_actions
        actions = generate_risk_actions(vp_val, fgi_val)
        if actions:
            danger = [a for a in actions if a.get("level") == "danger"]
            warning = [a for a in actions if a.get("level") == "warning"]
            if danger or warning:
                lines.append("\n【⚠️ 风控预警】")
                for a in danger:
                    lines.append(f"  🔴 {a['message']}")
                for a in warning:
                    lines.append(f"  ⚠️ {a['message']}")
            else:
                lines.append("\n【风控状态】✅ 当前无风险预警")
    except Exception:
        pass

    # 3. 资产配置建议
    try:
        from services.portfolio import get_allocation_advice
        advice = get_allocation_advice(vp_val)
        if advice:
            t = advice.get("target", {})
            dev = advice.get("deviation", {})
            lines.append("\n【资产配置建议】")
            lines.append(f"  估值区间：{advice.get('valuation_zone', '未知')}")
            for k, label in [("stock", "股票"), ("bond", "债券"), ("cash", "现金")]:
                tgt = round(t.get(k, 0))
                d = round(dev.get(k, 0))
                lines.append(f"  {label}：目标{tgt}%，偏离{d:+d}%")
            if advice.get("summary"):
                lines.append(f"  建议：{advice['summary']}")
    except Exception:
        pass

    # 4. 持仓关联智能
    try:
        intel_ctx = build_holding_context(user_id)
        if intel_ctx:
            lines.append(intel_ctx)
    except Exception:
        pass

    # 5. 管理层增减持检查
    try:
        from services.macro_v8 import check_holding_management_change
        from services.stock_monitor import load_stock_holdings
        holdings = load_stock_holdings(user_id)
        codes = [h.get("code", "") for h in holdings if h.get("code")]
        if codes:
            mgmt_alerts = check_holding_management_change(codes)
            if mgmt_alerts:
                lines.append("\n【管理层增减持】")
                for a in mgmt_alerts[:3]:
                    lines.append(f"  {a['msg']}")
    except Exception:
        pass

    return "\n".join(lines) if lines else "用户尚未建仓。"


# ========================================================
# System Prompt 加载与构建
# ========================================================

_system_prompt_template = ""

def _load_prompt_template():
    global _system_prompt_template
    if not _system_prompt_template:
        p = Path(__file__).parent.parent / "prompts" / "system_prompt.md"
        if p.exists():
            _system_prompt_template = p.read_text(encoding="utf-8")
        else:
            _system_prompt_template = "你是钱袋子AI投顾，基于真实数据分析，不编造数字。"
    return _system_prompt_template


def _build_system_prompt(market_ctx: str, portfolio_ctx: str) -> str:
    """统一构建 DeepSeek system prompt"""
    template = _load_prompt_template()
    return f"""{template}

## 实时市场数据
{market_ctx}

## 用户持仓与风控
{portfolio_ctx}"""


# ========================================================
# 聊天意图分类
# ========================================================

_INTENT_RULES = [
    (["入场", "时机", "现在适合买", "适合买", "该买吗", "能买吗", "进场", "能进场", "抄底", "适合入"], "timing", "/api/timing"),
    (["定投", "DCA", "每月投", "定投多少", "怎么投", "投多少"], "smart_dca", "/api/smart-dca"),
    (["止盈", "止损", "该卖吗", "减仓", "该出", "锁定利润"], "take_profit", None),
    (["持仓分析", "诊断", "体检", "检查持仓"], "portfolio_doctor", "/api/portfolio-doctor/diagnose"),
    (["配置建议", "资产配置", "怎么分配"], "allocation", None),
    (["新闻", "今天发生", "消息面", "利空", "利好", "什么情况"], "news", None),
    (["宏观", "GDP", "CPI", "利率", "经济", "PMI", "通胀", "M2"], "macro", None),
    (["估值", "PE", "PB", "贵不贵"], "valuation", None),
    (["基金", "选基", "推荐基金"], "fund", None),
    (["北向", "外资", "净流入"], "northbound", None),
    (["情绪", "恐惧", "贪婪", "恐慌", "市场情绪", "散户情绪"], "sentiment", None),
]


def classify_chat_intent(msg: str) -> dict:
    """规则引擎意图分类（不调 LLM，毫秒级）

    增加否定约束检测：如果消息包含"不要/别给/不需要"+ 关键词，不触发对应意图
    """
    msg_lower = msg.lower()

    # 否定模式：如果用户说"不要买卖建议"，不应触发 take_profit
    _NEGATION_PATTERNS = ["不要", "别给", "不需要", "不用", "不想要", "禁止"]
    has_negation = any(neg in msg_lower for neg in _NEGATION_PATTERNS)

    for keywords, intent, api in _INTENT_RULES:
        for kw in keywords:
            if kw in msg_lower:
                # 如果带否定词且关键词紧跟否定词，跳过
                if has_negation:
                    for neg in _NEGATION_PATTERNS:
                        if neg in msg_lower:
                            neg_pos = msg_lower.index(neg)
                            kw_pos = msg_lower.index(kw)
                            # 否定词在关键词前面20字以内，认为是否定
                            if 0 <= kw_pos - neg_pos <= 20:
                                break
                    else:
                        return {"intent": intent, "keyword": kw, "api": api}
                    continue  # 被否定了，跳过这个意图
                return {"intent": intent, "keyword": kw, "api": api}
    return {"intent": "general", "keyword": None, "api": None}


# ========================================================
# 规则引擎降级回答
# ========================================================

def _rule_based_reply_structured(msg: str, market_ctx: str, portfolio_ctx: str) -> dict | None:
    """规则引擎结构化回答 — 命中返回 {text, confidence, intent}，不命中返回 None。

    confidence=0.85 表示规则精准匹配（用真实数据计算），比 LLM 编造更可靠。
    """
    msg_lower = msg.lower()

    # 入场时机
    if any(k in msg_lower for k in ["什么时候买", "入手", "入场", "时机", "现在能买", "适合买", "抄底", "能进场"]):
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
        text = f"📊 入场时机分析：\n\n{tip}\n\n{val['index']}估值百分位：{val['percentile']}%（{val['level']}）\n恐惧贪婪指数：{fgi:.0f}\n\n💡 建议：不管时机好坏，定投永远是对的。定投的精髓就是穿越牛熊，低估时多买、高估时少买。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "timing"}

    # 止盈止损
    if any(k in msg_lower for k in ["卖", "止盈", "止损", "价位", "该出", "什么时候出", "锁定利润", "减仓", "到了多少"]):
        text = "🔔 止盈止损策略：\n\n钱袋子采用**分批止盈法**，根据你的风险类型自动设定目标：\n\n🐢 保守型：+15% 止盈 / -8% 止损\n🐰 稳健型：+20% 止盈 / -10% 止损\n🦊 平衡型：+30% 止盈 / -15% 止损\n🦁 进取型：+50% 止盈 / -20% 止损\n🦅 激进型：+80% 止盈 / -25% 止损\n\n📌 操作建议：\n1️⃣ **到了止盈线，不用全卖** — 卖 1/3 锁利润，剩余继续持有\n2️⃣ **到了止损线，先看原因** — 如果基金基本面没变，可能反而是加仓机会\n3️⃣ **不设绝对卖点** — 结合估值百分位综合判断\n\n你可以在首页的 AI 信号里实时看到自己的止盈止损状态 📊\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "take_profit"}

    # 智能定投
    if any(k in msg_lower for k in ["定投", "智能", "固定还是", "怎么投", "投多少", "每月投", "dca"]):
        val = get_valuation_percentile()
        smart = calc_smart_dca(1000, val["percentile"])
        text = f"🧠 智能定投 vs 固定定投：\n\n**固定定投**：每月投相同金额，简单省心，长期有效。\n**智能定投**：根据市场估值动态调整 — 低估多买、高估少买。\n\n钱袋子的智能定投策略：\n\n| 估值百分位 | 倍率 | 说明 |\n|-----------|------|------|\n| < 20% | 1.5x | 极度低估，多买 |\n| 20-30% | 1.3x | 低估，适当多买 |\n| 30-50% | 1.1x | 偏低，略多 |\n| 50-70% | 1.0x | 正常，标准额 |\n| 70-85% | 0.7x | 偏高，少买 |\n| > 85% | 0.3x | 高估，大幅减少 |\n\n📊 当前{val['index']}估值：{val['percentile']}%（{val['level']}）\n💡 建议本月倍率：{smart['multiplier']}x — {smart['advice']}\n\n智能定投比固定定投长期多赚约 15-20%，但需要坚持 3 年以上才能看到效果。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "dca"}

    # 市场情绪 / 恐惧贪婪
    if any(k in msg_lower for k in ["情绪", "恐惧", "贪婪", "恐慌", "fgi", "市场情绪", "散户情绪"]):
        fgi_data = get_fear_greed_index()
        fgi = fgi_data["score"]
        if fgi < 25:
            level = "极度恐惧 😱"
            advice = "历史上极度恐惧时买入，半年后大概率盈利。"
        elif fgi < 40:
            level = "恐惧 😰"
            advice = "市场悲观情绪浓，适合逆向加仓。"
        elif fgi < 60:
            level = "中性 😐"
            advice = "情绪平稳，按计划操作即可。"
        elif fgi < 75:
            level = "贪婪 😊"
            advice = "市场乐观，注意控制仓位。"
        else:
            level = "极度贪婪 🤑"
            advice = "市场过热，考虑适当减仓锁利。"
        text = f"🎭 市场情绪分析：\n\n恐惧贪婪指数：**{fgi:.0f}** — {level}\n\n{advice}\n\n{market_ctx}\n\n💡 「别人恐惧时我贪婪」说的容易做起来难，但数据不会骗人。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "sentiment"}

    # 宏观经济
    if any(k in msg_lower for k in ["宏观", "经济", "cpi", "pmi", "通胀", "利率", "货币", "m2", "gdp"]):
        events = get_macro_calendar()
        macro_text = "\n".join([f"{e['icon']} {e['name']}：{e['value']}（{e['date']}）\n  └ {e['impact']}" for e in events])
        text = f"🏛️ 宏观经济数据：\n\n{macro_text}\n\n💡 宏观数据影响市场整体方向。CPI低+PMI>50+M2宽松 = 对股市友好的环境。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "macro_summary"}

    # 新闻/资讯
    if any(k in msg_lower for k in ["新闻", "资讯", "消息", "发生", "怎么了", "什么情况", "为什么", "利空", "利好"]):
        # 检查是否有个股实体（如果问"茅台有什么利空"，只返回茅台相关新闻）
        _STOCK_NAMES = {"茅台": "600519", "宁德": "300750", "比亚迪": "002594",
                        "腾讯": "00700", "阿里": "09988", "中兴": "000063",
                        "平安": "601318", "招商": "600036", "格力": "000651"}
        entity_name = None
        entity_code = None
        for name, code in _STOCK_NAMES.items():
            if name in msg:
                entity_name = name
                entity_code = code
                break
        # 如果没匹配到常见股票名，尝试从用户持仓匹配
        if not entity_name and portfolio_ctx:
            import re
            # 从 portfolio_ctx 里提取股票名
            stock_matches = re.findall(r'股票：(.+?)\((\d{6})\)', portfolio_ctx)
            for sname, scode in stock_matches:
                if sname[:2] in msg or sname in msg:
                    entity_name = sname
                    entity_code = scode
                    break

        if entity_name:
            # 个股新闻查询：只返回该股票相关的
            try:
                from services.news_data import get_stock_news
                stock_news = get_stock_news(entity_code, limit=5)
                if stock_news:
                    news_lines = [f"📰 {n.get('title', '')}（{n.get('source', '')}）" for n in stock_news[:5]]
                    text = f"📰 {entity_name}最新消息：\n\n" + "\n".join(news_lines) + "\n\n⚠️ 以上仅供参考，不构成投资建议。"
                else:
                    text = f"📰 当前没有检索到与{entity_name}直接相关的重大新闻/利空。\n\n如果你听到某个消息想确认，可以直接告诉我具体内容，我来帮你分析真假和影响。\n\n⚠️ 以上仅供参考。"
                return {"text": text, "confidence": 0.80, "intent": "stock_news"}
            except Exception:
                pass

        # 泛市场新闻
        news = get_market_news(5)
        news_lines = []
        for n in news[:5]:
            if n.get("url"):
                news_lines.append(f'📰 <a href="{n["url"]}" target="_blank" style="color:#F59E0B;text-decoration:underline">{n["title"]}</a>（{n["source"]}）')
            else:
                news_lines.append(f"📰 {n['title']}（{n['source']}）")
        news_text = "\n".join(news_lines)
        text = f"📰 最新市场资讯：\n\n{news_text}\n\n💡 建议：关注大趋势，不要因为单条新闻做决定。投资看的是长期逻辑。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "news"}

    # 技术分析
    if any(k in msg_lower for k in ["技术", "rsi", "macd", "布林", "超买", "超卖", "指标"]):
        tech = get_technical_indicators()
        text = f"📊 沪深300技术指标：\n\n📈 RSI(14)：{tech['rsi']}（{tech['rsi_signal']}）\n  └ >70 超买区，<30 超卖区\n\n📉 MACD：{tech['macd']['trend']}\n  └ DIF:{tech['macd']['dif']:.4f} DEA:{tech['macd']['dea']:.4f}\n\n📐 布林带：{tech['bollinger']['position']}\n  └ 上轨:{tech['bollinger']['upper']} 中轨:{tech['bollinger']['middle']} 下轨:{tech['bollinger']['lower']}\n\n💡 技术指标是辅助参考，不能单独作为买卖依据。结合估值+基本面综合判断更靠谱。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "technicals"}

    # 政策/地缘/影响
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
                text = f"🏛️ 当前事件对你持仓的影响分析：\n\n{impact_text}\n\n💡 建议：关注事件发展趋势，短期波动不改长期逻辑。如果你是定投模式，保持节奏即可。\n\n⚠️ 以上基于关键词匹配的初步分析，仅供参考，不构成投资建议。"
                return {"text": text, "confidence": 0.85, "intent": "macro_impact"}
        except Exception:
            pass
        return None

    # 市场下跌安慰
    if any(k in msg_lower for k in ["跌", "亏", "赔", "绿", "下跌"]):
        text = f"📉 市场波动是正常现象。\n\n{market_ctx}\n\n长期投资（3年+）能大幅平滑短期波动。如果你的资产配比还在目标范围内，建议保持定投节奏，不要恐慌卖出。记住投资铁律：跌了别卖，越跌越该买。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "market_down"}

    # 市场上涨
    if any(k in msg_lower for k in ["涨", "赚", "红", "上涨", "牛"]):
        text = f"📈 恭喜！不过也别过于乐观。\n\n{market_ctx}\n\n赚钱时更要冷静，检查一下各资产的占比是否偏离目标太多。如果某类资产涨太多导致占比过高，可以考虑再平衡——卖掉一点涨多的，买入涨少的。\n\n⚠️ 以上仅供参考，不构成投资建议。"
        return {"text": text, "confidence": 0.85, "intent": "market_up"}

    # 不命中 → 返回 None，交给 LLM
    return None


def _rule_based_reply(msg: str, market_ctx: str, portfolio_ctx: str) -> str:
    """规则引擎降级回答（兼容旧调用方）"""
    result = _rule_based_reply_structured(msg, market_ctx, portfolio_ctx)
    if result:
        return result["text"]
    # 兜底回复
    return f"🤔 关于你的问题：\n\n当前市场概况：\n{market_ctx}\n\n{portfolio_ctx}\n\n你可以问我：\n📰 「最近有什么新闻？」\n📊 「技术指标怎么样？」\n🏛️ 「宏观经济怎么样？」\n🎯 「现在适合入场吗？」\n💰 「什么时候该卖？」\n🧠 「定投多少合适？」\n\n⚠️ 以上仅供参考，不构成投资建议。"


# ========================================================
# OCR 处理
# ========================================================

async def _do_ocr(file_path: Path, content: bytes) -> dict:
    """执行 OCR，优先用 LLM 多模态（通过 gateway），降级用本地 OCR"""
    from services.llm_gateway import LLMGateway
    gw = LLMGateway.instance()
    vision_model = os.environ.get("LLM_VISION_MODEL", "gpt-4o-mini")

    try:
        import base64
        b64 = base64.b64encode(content).decode()
        mime = "image/jpeg"
        if str(file_path).endswith(".png"):
            mime = "image/png"

        messages = [
            {"role": "system", "content": """你是一个金融记录识别助手。请识别截图类型并提取信息。

支持的截图类型：
1. 支付宝/微信消费记录 → 提取: 金额(amount), 商家(merchant), 分类(category:餐饮/交通/购物/娱乐/医疗/教育/其他), 备注(note)
2. 支付宝/微信账单列表 → 提取: 多条记录records[{amount, merchant, date}]
3. 银行卡交易记录 → 提取: 金额(amount), 交易类型(tx_type:转入/转出), 余额(bank_balance), 银行名(bank_name)
4. 基金买入确认 → 提取: 基金名(fund_name), 基金代码(fund_code), 买入金额(amount), 确认份额(shares), 确认净值(nav), 日期(date)
5. 基金赎回确认 → 提取: 基金名(fund_name), 基金代码(fund_code), 赎回份额(shares), 到账金额(amount), 确认净值(nav), 日期(date)
6. 工资条/收入 → 提取: 税后金额(amount), 日期(date)

返回JSON格式:
{
  "screenshot_type": "consumption|bill_list|bank_tx|fund_buy|fund_sell|income",
  "amount": 数值,
  "merchant": "商家名",
  "category": "分类",
  "note": "备注",
  "fund_code": "基金代码(如有)",
  "fund_name": "基金名(如有)",
  "shares": 份额数(如有),
  "nav": 净值(如有),
  "date": "日期(如有)",
  "bank_balance": 银行余额(如有),
  "records": [多条记录(如有)],
  "confidence": 0.95
}"""},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": "请识别这张截图的信息，返回 JSON。"},
            ]},
        ]

        llm_result = gw.call_multimodal(
            messages,
            model=vision_model,
            user_id="",
            module="ocr_vision",
            max_tokens=800,
        )

        if not llm_result.get("fallback") and llm_result.get("content"):
            text = llm_result["content"]
            import re
            json_match = re.search(r'\{[^}]+\}', text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                result = {
                    "amount": float(parsed.get("amount", 0)),
                    "merchant": parsed.get("merchant", ""),
                    "category": parsed.get("category", "其他"),
                    "note": parsed.get("note", ""),
                    "source": "llm_vision",
                    "screenshot_type": parsed.get("screenshot_type", "consumption"),
                    "fund_code": parsed.get("fund_code", ""),
                    "fund_name": parsed.get("fund_name", ""),
                    "shares": float(parsed.get("shares", 0)),
                    "nav": float(parsed.get("nav", 0)),
                    "date": parsed.get("date", ""),
                    "bank_balance": float(parsed.get("bank_balance", 0)),
                    "records": parsed.get("records", []),
                    "confidence": float(parsed.get("confidence", 0)),
                    "raw": text,
                }
                return result
    except Exception as e:
        print(f"[OCR] LLM vision failed: {e}")

    # 方案2：本地 OCR（pytesseract）
    try:
        from PIL import Image
        import pytesseract
        import re

        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")

        amounts = re.findall(r'[\d]+\.[\d]{2}', text)
        amount = max([float(a) for a in amounts]) if amounts else 0

        return {
            "amount": amount,
            "merchant": "",
            "category": "其他",
            "note": text[:100],
            "source": "tesseract",
            "raw": text[:500],
        }
    except Exception as e:
        print(f"[OCR] Tesseract failed: {e}")

    return {
        "amount": 0,
        "merchant": "",
        "category": "其他",
        "note": "OCR 识别失败，请手动输入",
        "source": "none",
        "raw": "",
    }


# ========================================================
# 预警冷却
# ========================================================
_alert_cooldown = {}  # {alert_key: last_alert_time}


# ========================================================
# 用户偏好默认值
# ========================================================
USER_DEFAULTS = {
    "display_mode": "pro",
    "risk_profile": "balanced",
    "push_preferences": {
        "morning_brief": True,
        "closing_review": True,
        "risk_alert": True,
        "trade_signal": True,
        "breaking_news": True,
    },
    "watchlist_config": {
        "stop_loss_pct": -0.08,
        "take_profit_pct": 0.20,
        "price_alert_range": 0.05,
    },
}

USER_OVERRIDES = {
    "LeiJiang": {
        "display_mode": "pro",
        "risk_profile": "growth",
        "push_preferences": {
            "morning_brief": True, "closing_review": True,
            "risk_alert": True, "trade_signal": True, "breaking_news": True,
        },
        "watchlist_config": {
            "stop_loss_pct": -0.10, "take_profit_pct": 0.25, "price_alert_range": 0.05,
        },
    },
    "BuLuoGeLi": {
        "display_mode": "pro",
        "risk_profile": "balanced",
        "push_preferences": {
            "morning_brief": True, "closing_review": False,
            "risk_alert": True, "trade_signal": False, "breaking_news": False,
        },
        "watchlist_config": {
            "stop_loss_pct": -0.05, "take_profit_pct": 0.15, "price_alert_range": 0.03,
        },
    },
}


# ========================================================
# 家庭成员常量
# ========================================================
FAMILY_MEMBERS = ["LeiJiang", "BuLuoGeLi"]
NICKNAMES = {"LeiJiang": "厉害了哥", "BuLuoGeLi": "部落格里"}


# ========================================================
# 可用模型列表
# ========================================================
AVAILABLE_MODELS = [
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
    {"id": "deepseek-reasoner", "name": "DeepSeek R1 (深度思考)", "provider": "deepseek", "base": "https://api.deepseek.com/v1", "env_key": "LLM_API_KEY"},
]


# ========================================================
# 静态文件缓存
# ========================================================
_CACHE_RULES = {
    ".js": "public, max-age=300, stale-while-revalidate=86400",
    ".css": "public, max-age=300, stale-while-revalidate=86400",
    ".png": "public, max-age=604800",
    ".ico": "public, max-age=604800",
    ".json": "public, max-age=60",
    ".html": "no-cache",
}


def _cached_file_response(fp: Path) -> FileResponse:
    """返回带 Cache-Control 的 FileResponse"""
    suffix = fp.suffix.lower()
    headers = {}
    if suffix in _CACHE_RULES:
        headers["Cache-Control"] = _CACHE_RULES[suffix]
    return FileResponse(fp, headers=headers)
