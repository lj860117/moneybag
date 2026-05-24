"""
信号 & 策略 API（买卖信号 / 入场时机 / 定投 / 止盈止损 / 回测 / 筛选）
=======================================================================
从 main.py 提取的 P2 路由。

Design doc: docs/design/12-framework-refactor.md §四
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["信号与策略"])

from models.schemas import Portfolio
from services.data_layer import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    _get_nav_on_date,
    _macro_cache as macro_cache,
)
from services.signal import (
    generate_daily_signal, calc_smart_dca, calc_take_profit_strategy,
)
from services.backtest import run_backtest
from services.fund_screen import screen_funds
from services.stock_screen import screen_stocks
from services.ds_enhance import (
    comment_fund_picks, comment_stock_picks, interpret_daily_signal,
)
from services.backtest_engine import backtest_single, backtest_portfolio


# ---- 买卖信号 ----

@router.post("/api/signals")
def get_signals(portfolio: Portfolio):
    """根据持仓生成买卖信号（含入场时机/止盈止损/智能定投）"""
    signals = []

    if not portfolio.holdings:
        return signals

    total_amount = sum(h.amount for h in portfolio.holdings)
    if total_amount <= 0:
        return signals

    # 1. 入场时机
    val = get_valuation_percentile()
    if val["percentile"] < 30:
        signals.append({
            "icon": "🟢", "title": f"当前是好的入场时机！",
            "message": f"{val['index']}估值百分位 {val['percentile']}%（{val['level']}），处于近3年较低水平。历史上低估区间买入，持有3年盈利概率超85%。现在入场性价比高。",
            "type": "timing", "severity": "opportunity",
        })
    elif val["percentile"] < 50:
        signals.append({
            "icon": "🟡", "title": "入场时机尚可",
            "message": f"{val['index']}估值百分位 {val['percentile']}%（偏低估），不算贵也不算便宜。适合正常定投节奏入场，不用急着一把梭。",
            "type": "timing", "severity": "info",
        })
    elif val["percentile"] >= 70:
        signals.append({
            "icon": "🔴", "title": "现在入场要谨慎",
            "message": f"{val['index']}估值百分位 {val['percentile']}%（{val['level']}），处于近3年较高水平。建议不要一次性大额买入，可以用定投慢慢建仓，或等回调。",
            "type": "timing", "severity": "warning",
        })

    # 2. 止盈止损策略
    profile_name = portfolio.profile or "平衡型"
    total_cost = sum(h.amount for h in portfolio.holdings)
    total_market = 0
    can_calc = False
    for h in portfolio.holdings:
        if h.code == "余额宝":
            total_market += h.amount
            continue
        nav_info = get_fund_nav(h.code)
        if nav_info and nav_info["nav"] != "N/A":
            buy_nav = _get_nav_on_date(h.code, h.buyDate) if h.buyDate else None
            if buy_nav and buy_nav > 0:
                current_nav = float(nav_info["nav"])
                growth = (current_nav - buy_nav) / buy_nav
                total_market += h.amount * (1 + growth)
                can_calc = True
            else:
                total_market += h.amount
        else:
            total_market += h.amount

    if can_calc and total_cost > 0:
        tp = calc_take_profit_strategy(total_cost, total_market, profile_name)
        icon_map = {
            "reached_target": "🎯", "partial_profit": "📈",
            "stop_loss": "🚨", "in_loss": "📉", "holding": "💎",
        }
        signals.append({
            "icon": icon_map.get(tp["status"], "💰"),
            "title": f"止盈止损 | 目标+{tp['targetPct']}% / 止损{tp['stopLossPct']}%",
            "message": tp["action"],
            "type": "take_profit",
            "severity": "opportunity" if tp["status"] == "reached_target" else "warning" if tp["status"] == "stop_loss" else "info",
        })

    # 3. 智能定投建议
    monthly_invest = total_amount * 0.1
    smart_dca = calc_smart_dca(monthly_invest, val["percentile"])
    signals.append({
        "icon": "🧠",
        "title": f"智能定投：本月建议 ¥{smart_dca['smartAmount']:,.0f}",
        "message": f"基准定投 ¥{smart_dca['baseAmount']:,.0f}，{smart_dca['advice']}（估值{val['percentile']}%）。智能定投核心：低估多买、高估少买，长期能比固定定投多赚15-20%。",
        "type": "smart_dca", "severity": "info",
    })

    # 4. 再平衡信号
    for h in portfolio.holdings:
        current_pct = h.amount / total_amount * 100
        deviation = abs(current_pct - h.targetPct)
        if deviation > 5:
            direction = "偏多" if current_pct > h.targetPct else "偏少"
            signals.append({
                "icon": "⚖️", "title": f"{h.category}需要再平衡",
                "message": f"当前占比 {current_pct:.1f}%，目标 {h.targetPct}%，{direction} {deviation:.1f}%。建议调整。",
                "type": "rebalance", "severity": "warning",
            })

    # 5. 恐惧贪婪信号
    fgi_data = get_fear_greed_index()
    fgi = fgi_data["score"]
    fgi_level = fgi_data["level"]
    dims = fgi_data.get("dimensions", {})
    dim_text = "、".join([f"{d['label']}{d['value']}" for d in dims.values()]) if dims else ""
    if fgi >= 75:
        signals.append({
            "icon": "😱", "title": f"市场{fgi_level} — 可能是加仓机会",
            "message": f"恐惧贪婪指数 {fgi:.0f}/100（{fgi_level}）。{dim_text}。历史上极度恐惧时买入，长期收益概率较高。考虑用货币基金的弹药适当加仓。",
            "type": "fear", "severity": "opportunity",
        })
    elif fgi <= 25:
        signals.append({
            "icon": "🤑", "title": f"市场{fgi_level} — 注意风险",
            "message": f"恐惧贪婪指数 {fgi:.0f}/100（{fgi_level}）。{dim_text}。市场可能过热，建议不要追高，保持定投节奏即可。",
            "type": "greed", "severity": "warning",
        })

    # 6. 持仓时间检查
    if portfolio.holdings and portfolio.holdings[0].buyDate:
        try:
            buy_date = datetime.fromisoformat(portfolio.holdings[0].buyDate.replace("Z", "+00:00"))
            days_held = (datetime.now(buy_date.tzinfo) - buy_date).days
            if days_held < 30:
                signals.append({
                    "icon": "⏰", "title": "耐心持有",
                    "message": f"你才持有 {days_held} 天，投资是长跑。至少 3 年才能看到复利效果，别被短期波动影响心态。",
                    "type": "patience", "severity": "info",
                })
        except Exception:
            pass

    return signals


# ---- 入场时机 ----

@router.get("/api/timing")
def get_timing_advice():
    """获取当前入场时机建议（优先缓存）"""
    try:
        from services.precomputed_cache import get_precomputed
        pc_val = get_precomputed("valuation")
        pc_fgi = get_precomputed("fear_greed")
        if pc_val and pc_fgi:
            val = pc_val
            fgi_data = pc_fgi
            fgi = fgi_data.get("score", 50)
            timing_score = val.get("percentile", 50) * 0.6 + (100 - fgi) * 0.4
            if timing_score < 30:
                verdict, detail = "🟢 非常适合入场", "估值低+市场恐惧，历史最佳买入窗口。"
            elif timing_score < 50:
                verdict, detail = "🟡 适合定投入场", "估值合理，适合定投。"
            elif timing_score < 70:
                verdict, detail = "🟠 谨慎入场", "估值偏高，建议降低定投。"
            else:
                verdict, detail = "🔴 不建议入场", "估值高+市场贪婪，等回调。"
            return {"timingScore": round(timing_score, 1), "signal": verdict.split(" ")[0],
                    "verdict": verdict, "detail": detail,
                    "valuationPct": val.get("percentile", 50), "fgi": fgi,
                    "confidence": round(abs(timing_score - 50) / 50, 2),
                    "from_cache": True}
    except Exception:
        pass
    val = get_valuation_percentile()
    fgi_data = get_fear_greed_index()
    fgi = fgi_data["score"]

    timing_score = val["percentile"] * 0.6 + (100 - fgi) * 0.4

    if timing_score < 30:
        verdict = "🟢 非常适合入场"
        detail = "估值低 + 市场恐惧，历史上是最佳买入窗口。"
    elif timing_score < 50:
        verdict = "🟡 适合定投入场"
        detail = "估值合理，适合按计划定投，不建议一次性大额买入。"
    elif timing_score < 70:
        verdict = "🟠 谨慎入场"
        detail = "估值偏高，建议减少定投金额或暂缓，等待更好的机会。"
    else:
        verdict = "🔴 不建议入场"
        detail = "估值高 + 市场贪婪，建议保持现金等待回调，不追高。"

    return {
        "timingScore": round(timing_score, 1),
        "signal": verdict.split(" ")[0],
        "verdict": verdict,
        "detail": detail,
        "valuationPct": val["percentile"],
        "fgi": fgi,
        "fgiLevel": fgi_data["level"],
        "valuation": val,
        "confidence": round(abs(timing_score - 50) / 50, 2),
    }


# ---- 智能定投 ----

@router.post("/api/smart-dca")
def get_smart_dca(portfolio: Portfolio):
    """获取智能定投建议"""
    total = sum(h.amount for h in portfolio.holdings) if portfolio.holdings else 0
    base = total * 0.1 if total > 0 else 1000
    val = get_valuation_percentile()
    result = calc_smart_dca(base, val["percentile"])
    result["valuation"] = val
    return result


# ---- 止盈止损 ----

@router.post("/api/take-profit")
def get_take_profit(portfolio: Portfolio):
    """获取止盈止损建议"""
    profile = portfolio.profile or "平衡型"
    total_cost = sum(h.amount for h in portfolio.holdings) if portfolio.holdings else 0
    if total_cost <= 0:
        return {"message": "还没有持仓，买入后才能计算止盈止损策略。"}

    total_market = 0
    for h in portfolio.holdings:
        if h.code == "余额宝":
            total_market += h.amount
            continue
        nav_info = get_fund_nav(h.code)
        if nav_info and nav_info["nav"] != "N/A":
            buy_nav = _get_nav_on_date(h.code, h.buyDate) if h.buyDate else None
            if buy_nav and buy_nav > 0:
                growth = (float(nav_info["nav"]) - buy_nav) / buy_nav
                total_market += h.amount * (1 + growth)
            else:
                total_market += h.amount
        else:
            total_market += h.amount

    return calc_take_profit_strategy(total_cost, total_market, profile)


# ---- 每日信号 ----

@router.get("/api/daily-signal")
def get_daily_signal_api():
    """每日综合交易信号（优先凌晨预计算缓存）"""
    try:
        from services.precomputed_cache import get_precomputed
        cached = get_precomputed("daily_signal")
        if cached:
            cached["from_cache"] = True
            return cached
    except Exception:
        pass
    cache_key = "daily_signal"
    now = time.time()
    cached = macro_cache.get(cache_key)
    if cached is not None:
        return cached
    result = generate_daily_signal()
    macro_cache.set(cache_key, result, ttl=1800)
    return result


@router.get("/api/daily-signal/interpret")
def get_signal_interpretation():
    """每日信号 DeepSeek 解读"""
    signal = generate_daily_signal()
    interpretation = interpret_daily_signal(signal)
    signal["interpretation"] = interpretation
    return signal


# ---- 回测 ----

@router.get("/api/backtest")
def get_backtest(strategy: str = "smart_dca", years: int = 3, monthly: float = 1000):
    """回测智能定投 vs 固定定投（沪深300历史数据）"""
    cache_key = f"bt_{strategy}_{years}_{monthly}"
    now = time.time()
    cached = macro_cache.get(cache_key)
    if cached is not None:
        return cached
    result = run_backtest(strategy, years, monthly)
    macro_cache.set(cache_key, result, ttl=7200)
    return result


@router.get("/api/backtest/{code}")
def api_backtest_single(code: str, asset_type: str = "stock", years: int = 3):
    """单只股票/基金回测"""
    return backtest_single(code, asset_type, years)


@router.post("/api/backtest/portfolio")
def api_backtest_portfolio(req: dict):
    """组合回测（按权重加权）"""
    holdings = req.get("holdings", [])
    years = req.get("years", 3)
    return backtest_portfolio(holdings, years)


# ---- 筛选 ----

@router.get("/api/fund-screen")
def get_fund_screen(fund_type: str = "all", sort_by: str = "score", top_n: int = 20, userId: str = ""):
    """基金智能筛选：多维打分排序 + 质量过滤 + 用户持仓去重 + 时机粗评"""
    result = screen_funds(fund_type, sort_by, top_n, user_id=userId)
    if result.get("funds"):
        result["funds"] = comment_fund_picks(result["funds"])
        # 给每只基金加时机粗评
        for f in result["funds"]:
            f["timing_label"] = _fund_timing_label(f)
    # 大盘时机
    result["market_timing"] = _get_market_timing_summary()
    return result


@router.get("/api/stock-screen")
def get_stock_screen(top_n: int = 50):
    """AI多因子选股：30因子7维打分 V2 + DeepSeek 点评 TOP5 + 时机粗评"""
    # 优先读 cache_warmer 写入的文件缓存，避免每次实时计算（30-40秒）
    try:
        cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "stock_screen_50.json"
        if cache_fp.exists():
            payload = json.loads(cache_fp.read_text(encoding="utf-8"))
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                # 补充时机标签
                for s in data.get("stocks", []):
                    s["timing_label"] = _stock_timing_label(s)
                data["market_timing"] = _get_market_timing_summary()
                return data
    except Exception as e:
        print(f"[STOCK_SCREEN] 读文件缓存失败: {e}")
    # 文件缓存不存在或已过期，实时计算
    result = screen_stocks(top_n)
    if result.get("stocks"):
        result["stocks"] = comment_stock_picks(result["stocks"])
        for s in result["stocks"]:
            s["timing_label"] = _stock_timing_label(s)
    result["market_timing"] = _get_market_timing_summary()
    return result


# ============================================================
# 时机粗评辅助函数
# ============================================================

def _get_market_timing_summary() -> dict:
    """获取大盘时机摘要（复用 timing API 逻辑）"""
    try:
        from services.market_data import get_valuation_percentile, get_fear_greed_index
        val = get_valuation_percentile() or {}
        fgi = get_fear_greed_index() or {}
        pct = val.get("percentile", 50)
        fgi_score = fgi.get("score", 50)

        # 综合评分（估值占60%，情绪占40%）
        timing_score = pct * 0.6 + fgi_score * 0.4

        if timing_score > 70:
            signal = "🔴"
            verdict = "谨慎观望"
            detail = f"大盘偏贵（估值{pct:.0f}%分位），不宜追高"
        elif timing_score > 50:
            signal = "🟡"
            verdict = "中性等待"
            detail = f"估值{pct:.0f}%分位，可小额定投但别重仓"
        elif timing_score > 30:
            signal = "🟢"
            verdict = "可以布局"
            detail = f"估值{pct:.0f}%分位偏低，适合分批建仓"
        else:
            signal = "🟢🟢"
            verdict = "积极买入"
            detail = f"估值{pct:.0f}%分位极低，难得的布局机会"

        return {
            "signal": signal,
            "verdict": verdict,
            "detail": detail,
            "valuation_pct": pct,
            "fgi": fgi_score,
            "fgi_level": fgi.get("level", ""),
        }
    except Exception as e:
        print(f"[TIMING] market timing failed: {e}")
        return {"signal": "⚪", "verdict": "数据加载中", "detail": "", "valuation_pct": 0, "fgi": 0}


def _stock_timing_label(stock: dict) -> str:
    """个股时机粗评 — 基于 value 维度评分 + PE"""
    scores = stock.get("scores", {})
    value_score = scores.get("value", 50) if isinstance(scores, dict) else 50
    pe = stock.get("pe")

    # value 维度 > 70 说明估值偏低（多因子已综合PE/PB/股息率）
    if value_score >= 70:
        return "💚 偏便宜"
    elif value_score >= 45:
        return "⚪ 合理"
    else:
        # PE 极高也标注
        if pe and pe > 50:
            return "🔴 偏贵"
        return "🟡 略贵"


def _fund_timing_label(fund: dict) -> str:
    """基金时机粗评 — 基于近期回撤和收益趋势"""
    returns = fund.get("returns", {})
    r3m = returns.get("3m")  # 近3月收益
    r1y = returns.get("1y")  # 近1年收益

    # 近3月大跌（回撤）= 可能的买点
    if r3m is not None:
        if r3m < -10:
            return "💚 回调买点"
        elif r3m < -5:
            return "💚 小幅回调"
        elif r3m > 20:
            return "🔴 短期过热"
        elif r3m > 10:
            return "🟡 涨幅较大"

    # 近1年涨幅极高
    if r1y is not None:
        if r1y > 80:
            return "🔴 涨幅过大"
        elif r1y > 50:
            return "🟡 注意止盈"

    return "⚪ 正常"
