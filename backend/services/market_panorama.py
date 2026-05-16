"""
钱袋子 — 市场全景聚合服务
============================
为"市场全景"页提供一次性数据聚合，让用户直观了解：
- 市场整体热度（牛/熊/震荡）
- 热点板块
- 重要新闻
- 各资产类别判断（股票/基金/黄金）
- 机构观点

全部纯规则/读缓存，不调 LLM。
"""
from __future__ import annotations
import time
from datetime import datetime


def generate_market_panorama() -> dict:
    """聚合市场全景数据"""
    start = time.time()
    result = {
        "temperature": None,
        "hot_sectors": [],
        "news": [],
        "asset_judgment": None,
        "broker_view": None,
        "timestamp": datetime.now().isoformat(),
    }

    # ── 1. 市场温度（恐贪+估值+综合信号）──
    fear_greed = 50
    valuation_pct = 50
    signal_overall = "HOLD"
    signal_summary = ""

    try:
        from services.market_data import get_fear_greed_index, get_valuation_percentile
        fgi = get_fear_greed_index()
        if fgi:
            fear_greed = fgi.get("score", 50)
        val = get_valuation_percentile()
        if val:
            valuation_pct = val.get("percentile", 50)
    except Exception:
        pass

    try:
        from services.precomputed_cache import get_precomputed
        signal = get_precomputed("daily_signal")
        if signal:
            signal_overall = signal.get("overall", "HOLD")
            signal_summary = signal.get("summary", "")
    except Exception:
        pass

    # 翻译信号为人话
    temp_map = {
        "STRONG_BUY": {"label": "强烈看多", "icon": "🔥", "color": "green", "advice": "适合积极入场"},
        "BUY": {"label": "偏多", "icon": "🟢", "color": "green", "advice": "适合按计划定投或小额加仓"},
        "HOLD": {"label": "震荡观望", "icon": "🟡", "color": "yellow", "advice": "按兵不动，等待明确信号"},
        "SELL": {"label": "偏空", "icon": "🟠", "color": "orange", "advice": "谨慎操作，控制仓位"},
        "STRONG_SELL": {"label": "强烈看空", "icon": "🔴", "color": "red", "advice": "防守为主，不建议加仓"},
    }
    temp_info = temp_map.get(signal_overall, temp_map["HOLD"])

    result["temperature"] = {
        "overall": temp_info["label"],
        "icon": temp_info["icon"],
        "advice": temp_info["advice"],
        "fear_greed": fear_greed,
        "fear_greed_level": _fgi_level(fear_greed),
        "valuation_pct": valuation_pct,
        "valuation_level": _val_level(valuation_pct),
        "signal_summary": signal_summary,
    }

    # ── 2. 热点板块 ──
    try:
        from services.sector_rotation import get_sector_ranking
        sr = get_sector_ranking()
        if sr and sr.get("available") and sr.get("top_gainers"):
            result["hot_sectors"] = [
                {"name": s.get("name", ""), "change_pct": s.get("change_pct", 0)}
                for s in sr["top_gainers"][:5]
            ]
    except Exception:
        pass

    # ── 3. 重要新闻 ──
    try:
        from services.news_data import get_market_news
        news = get_market_news(limit=5)
        result["news"] = [
            {"title": n.get("title", ""), "source": n.get("source", "")}
            for n in news[:5]
        ]
    except Exception:
        pass

    # ── 4. 各资产判断（纯规则） ──
    result["asset_judgment"] = _judge_assets(fear_greed, valuation_pct, signal_overall)

    # ── 5. 机构观点 ──
    try:
        from services.broker_research import get_broker_consensus
        br = get_broker_consensus()
        if br:
            consensus_map = {"看多": "看多", "看空": "看空", "中性": "中性",
                           "bullish": "看多", "bearish": "看空", "neutral": "中性"}
            result["broker_view"] = {
                "consensus": consensus_map.get(br.get("consensus", ""), br.get("consensus", "中性")),
                "hot_industries": br.get("top_industries", [])[:3],
            }
    except Exception:
        pass

    result["elapsed"] = round(time.time() - start, 2)
    return result


def _fgi_level(score: int) -> str:
    """恐贪指数翻译"""
    if score >= 75:
        return "极度贪婪"
    elif score >= 60:
        return "偏贪婪"
    elif score >= 40:
        return "中性"
    elif score >= 25:
        return "偏恐惧"
    else:
        return "极度恐惧"


def _val_level(pct: float) -> str:
    """估值百分位翻译"""
    if pct >= 80:
        return "偏贵"
    elif pct >= 60:
        return "中等偏高"
    elif pct >= 40:
        return "合理"
    elif pct >= 20:
        return "偏低"
    else:
        return "很便宜"


def _judge_assets(fear_greed: int, valuation_pct: float, signal: str) -> dict:
    """基于恐贪+估值+信号，给出各资产类别的判断"""

    # A 股
    if valuation_pct >= 80:
        stock_judge = {"icon": "🟡", "label": "观望", "reason": f"估值偏高({valuation_pct:.0f}%分位)，追高风险大"}
    elif valuation_pct <= 30 and fear_greed <= 40:
        stock_judge = {"icon": "🟢", "label": "适合入场", "reason": f"估值偏低+市场恐惧，历史好时机"}
    elif signal in ("BUY", "STRONG_BUY"):
        stock_judge = {"icon": "🟢", "label": "适合定投", "reason": "多数指标偏多，可按计划加仓"}
    elif signal in ("SELL", "STRONG_SELL"):
        stock_judge = {"icon": "🔴", "label": "谨慎", "reason": "信号偏空，控制仓位"}
    else:
        stock_judge = {"icon": "🟡", "label": "观望为主", "reason": "方向不明，等待信号"}

    # 基金（长期定投视角，更宽容）
    if valuation_pct <= 50:
        fund_judge = {"icon": "🟢", "label": "适合定投", "reason": "估值合理偏低，长期布局好时机"}
    elif valuation_pct <= 70:
        fund_judge = {"icon": "🟡", "label": "减少定投", "reason": "估值中等，可降低定投金额"}
    else:
        fund_judge = {"icon": "🟠", "label": "暂停定投", "reason": f"估值偏高({valuation_pct:.0f}%)，等回调再投"}

    # 黄金（避险+通胀对冲）
    if fear_greed >= 70:
        gold_judge = {"icon": "🟡", "label": "谨慎追高", "reason": "市场贪婪时黄金可能已涨较多"}
    elif fear_greed <= 30:
        gold_judge = {"icon": "🟢", "label": "可配置", "reason": "避险情绪升温，黄金有配置价值"}
    else:
        gold_judge = {"icon": "🟡", "label": "持有为主", "reason": "作为底仓配置，不急于加减"}

    return {
        "stock": stock_judge,
        "fund": fund_judge,
        "gold": gold_judge,
    }
