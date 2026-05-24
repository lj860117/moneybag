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
    """基金智能筛选：多维打分排序 + 质量过滤 + 用户持仓去重 + 时机粗评 + 行业解读 + 持仓关联分析"""

    # ★ 优先读文件缓存（cache_warmer 在 after_close/weekend 时写入）
    cache_key = f"fund_screen_{fund_type}"
    try:
        cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / f"{cache_key}.json"
        if cache_fp.exists():
            payload = json.loads(cache_fp.read_text(encoding="utf-8"))
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                # 有缓存：只补充实时的 market_timing + 持仓关联（个人化数据不能缓存）
                if data.get("funds"):
                    from services.industry_templates import enrich_fund_with_industry, get_fund_industry
                    for f in data["funds"]:
                        if not f.get("timing_label"):
                            f["timing_label"] = _fund_timing_label(f)
                        enrich_fund_with_industry(f)
                    _enrich_fund_holding_relation(data["funds"], userId, get_fund_industry)
                    data["my_holdings_summary"] = _get_my_fund_holdings_summary(userId, get_fund_industry)
                data["market_timing"] = _get_market_timing_summary()
                data["style_timing"] = _get_style_timing_summary()
                data["from_cache"] = True
                return data
    except Exception as e:
        print(f"[FUND_SCREEN] 文件缓存读取失败，降级实时计算: {e}")

    result = screen_funds(fund_type, sort_by, top_n, user_id=userId)
    if result.get("funds"):
        result["funds"] = comment_fund_picks(result["funds"])
        from services.industry_templates import enrich_fund_with_industry, get_fund_industry
        for f in result["funds"]:
            f["timing_label"] = _fund_timing_label(f)
            enrich_fund_with_industry(f)
        _enrich_fund_holding_relation(result["funds"], userId, get_fund_industry)
        result["my_holdings_summary"] = _get_my_fund_holdings_summary(userId, get_fund_industry)

    # 大盘时机 + 行业/风格估值分位
    result["market_timing"] = _get_market_timing_summary()
    result["style_timing"] = _get_style_timing_summary()
    return result


def _enrich_fund_holding_relation(funds: list, user_id: str, get_fund_industry_fn) -> None:
    """给推荐基金列表标注与用户持仓的关联（已持仓/风格重叠/新敞口）。可复用于缓存和实时两条路径。"""
    if not user_id or not funds:
        return
    try:
        from services.fund_monitor import load_fund_holdings
        my_funds = load_fund_holdings(user_id) or []
        if not my_funds:
            return
        my_tags: set = set()
        my_codes = {f.get("code", "") for f in my_funds}
        for mf in my_funds:
            match = get_fund_industry_fn(mf.get("name", ""))
            if match.get("tag"):
                my_tags.add(match["tag"])
        for f in funds:
            code = f.get("code", "")
            f_tag = f.get("industry_tag", "")
            if code in my_codes:
                f["holding_relation"] = "🔵 已持仓"
                f["holding_hint"] = "你已经持有这只基金"
            elif f_tag and f_tag in my_tags:
                f["holding_relation"] = "🟡 风格重叠"
                f["holding_hint"] = f"你已有{f_tag}方向的基金，买入会加重该方向集中度"
            elif f_tag:
                f["holding_relation"] = "🟢 新敞口"
                f["holding_hint"] = f"你目前没有{f_tag}方向，可作为分散配置考虑"
            else:
                f["holding_relation"] = "⚪ 未分类"
                f["holding_hint"] = "无法判断与持仓关系，请自行评估"
    except Exception as e:
        print(f"[FUND_SCREEN] holding_relation failed: {e}")


def _get_my_fund_holdings_summary(user_id: str, get_fund_industry_fn) -> dict:
    """获取用户基金持仓摘要（用于选基 my_holdings_summary 字段）。"""
    if not user_id:
        return {}
    try:
        from services.fund_monitor import load_fund_holdings
        my_funds = load_fund_holdings(user_id) or []
        if not my_funds:
            return {"count": 0, "tags": [], "hint": "暂无基金持仓记录"}
        my_tags: set = set()
        for mf in my_funds:
            match = get_fund_industry_fn(mf.get("name", ""))
            if match.get("tag"):
                my_tags.add(match["tag"])
        return {
            "count": len(my_funds),
            "tags": sorted(my_tags),
            "hint": f"你已持有 {len(my_funds)} 只基金，覆盖方向：{', '.join(sorted(my_tags)) or '未分类'}",
        }
    except Exception as e:
        return {}


@router.get("/api/stock-screen")
def get_stock_screen(top_n: int = 50, userId: str = ""):
    """AI多因子选股：30因子7维打分 V2 + DeepSeek 点评 + 时机粗评 + 行业标签 + 持仓关联"""
    # 优先读 cache_warmer 写入的文件缓存，避免每次实时计算（30-40秒）
    try:
        cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "stock_screen_50.json"
        if cache_fp.exists():
            payload = json.loads(cache_fp.read_text(encoding="utf-8"))
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                _enrich_stock_labels(data.get("stocks", []))
                data["market_timing"] = _get_market_timing_summary()
                _enrich_stock_holding_relation(data.get("stocks", []), userId)
                data["my_stock_summary"] = _get_my_stock_summary(userId)
                return data
    except Exception as e:
        print(f"[STOCK_SCREEN] 读文件缓存失败: {e}")
    # 文件缓存不存在或已过期，实时计算
    result = screen_stocks(top_n)
    if result.get("stocks"):
        result["stocks"] = comment_stock_picks(result["stocks"])
        _enrich_stock_labels(result["stocks"])
        _enrich_stock_holding_relation(result["stocks"], userId)
    result["market_timing"] = _get_market_timing_summary()
    result["style_timing"] = _get_style_timing_summary()  # 行业/风格流动情况
    result["my_stock_summary"] = _get_my_stock_summary(userId)
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

        # ★ regime 对应的行业流动提示（让用户明白"轮动/牛市"意味着什么）
        regime_hints = {
            "牛市":   "全面上涨行情，科技、消费、金融普涨，追随龙头即可",
            "熊市":   "系统性下跌，防守为主，关注高股息、现金流优质股",
            "震荡":   "指数区间震荡，精选个股，低吸高抛，短线为主",
            "轮动":   "资金在行业间流动，AI/科技已高位，医药/消费/金融红利估值偏低，关注补涨方向",
            "rotation": "资金在行业间流动，关注尚未启动的低估板块",
        }
        # 从接口的 regime 字段读（需要传入）—— 这里先用估值+恐贪推断 regime 描述
        if pct >= 80 and fgi_score >= 60:
            regime_key = "牛市"
        elif pct <= 30 and fgi_score <= 30:
            regime_key = "熊市"
        elif 40 <= pct <= 70:
            regime_key = "震荡"
        else:
            regime_key = "轮动"
        regime_hint = regime_hints.get(regime_key, "")

        return {
            "signal": signal,
            "verdict": verdict,
            "detail": detail,
            "regime": regime_key,
            "regime_hint": regime_hint,
            "valuation_pct": pct,
            "fgi": fgi_score,
            "fgi_level": fgi.get("level", ""),
        }
    except Exception as e:
        print(f"[TIMING] market timing failed: {e}")
        return {"signal": "⚪", "verdict": "数据加载中", "detail": "", "valuation_pct": 0, "fgi": 0}


def _get_style_timing_summary() -> dict:
    """获取各类基金风格/行业的时机摘要。

    用基金近期平均收益率 + 大盘估值 + AKShare行业指数估值（如可用），
    给出各风格的"高位/低位/适中"判断，辅助投资者判断哪类方向更有性价比。
    """
    try:
        from services.market_data import get_valuation_percentile
        from services.fund_rank import _load_fund_rank_data
        from services.utils import safe_float as _sf, find_col as _fc
        import re as _re

        val = get_valuation_percentile() or {}
        market_pct = val.get("percentile", 50)

        # 加载基金排行数据，按行业聚合计算近期均值
        rank_data = _load_fund_rank_data()
        # 行业关键词 → 标签 映射
        STYLE_KW = {
            "科技/AI":    ["科技", "科创", "创新", "AI", "信息", "TMT", "产业"],
            "半导体":     ["半导体", "芯片", "集成电路"],
            "新能源":     ["新能源", "碳中和", "光伏", "风电", "储能"],
            "医药":       ["医药", "医疗", "健康", "生物", "创新药"],
            "消费":       ["消费", "食品", "饮料", "白酒", "家电"],
            "军工":       ["军工", "国防", "装备", "航天"],
            "金融/红利":  ["金融", "银行", "券商", "红利", "价值"],
            "海外/QDII":  ["QDII", "纳斯达克", "标普", "海外", "美国", "全球"],
            "港股":       ["港股", "恒生", "H股"],
            "指数/宽基":  ["300", "500", "1000", "ETF联接", "沪深"],
        }

        style_returns = {k: [] for k in STYLE_KW}

        if rank_data:
            for code, row in rank_data.items():
                try:
                    cols = list(row.index) if hasattr(row, "index") else list(row.keys())
                    name = str(row.get(_fc(cols, ["基金名称", "简称"]) or cols[1] if len(cols) > 1 else "", ""))
                    r1y = _sf(row.get(_fc(cols, ["近1年"]), None))
                    r3m = _sf(row.get(_fc(cols, ["近3月"]), None))
                    if r1y is None and r3m is None:
                        continue
                    for style, kws in STYLE_KW.items():
                        if any(kw in name for kw in kws):
                            if r3m is not None:
                                style_returns[style].append(r3m)
                            break
                except Exception:
                    continue

        styles = []
        for style, returns in style_returns.items():
            if len(returns) < 3:
                continue
            avg_3m = sum(returns) / len(returns)
            # 近3月均涨幅判断时机：>15% 偏高位，<-5% 偏低位
            if avg_3m > 20:
                timing = "🔴 高位"
                hint = f"近3月均涨{avg_3m:.0f}%，性价比偏低"
            elif avg_3m > 10:
                timing = "🟡 偏高"
                hint = f"近3月均涨{avg_3m:.0f}%，适合小仓观望"
            elif avg_3m > 0:
                timing = "🟢 适中"
                hint = f"近3月均涨{avg_3m:.0f}%，可适量配置"
            elif avg_3m > -10:
                timing = "🟢 偏低"
                hint = f"近3月均跌{abs(avg_3m):.0f}%，关注反弹机会"
            else:
                timing = "🟢🟢 低位"
                hint = f"近3月均跌{abs(avg_3m):.0f}%，逢低布局机会"

            styles.append({
                "style": style,
                "avg_3m": round(avg_3m, 1),
                "fund_count": len(returns),
                "timing": timing,
                "hint": hint,
            })

        # 按近3月收益排序（低→高，低位靠前）
        styles.sort(key=lambda x: x["avg_3m"])

        return {
            "styles": styles,
            "note": f"基于{sum(len(v) for v in style_returns.values())}只基金近3月收益聚合，大盘估值分位{market_pct:.0f}%",
        }
    except Exception as e:
        print(f"[STYLE_TIMING] failed: {e}")
        return {"styles": [], "note": "风格估值数据加载失败"}


def _stock_timing_label(stock: dict) -> str:
    """个股时机粗评 — 综合 value/momentum/quality 三个维度 + PE + 涨跌幅，输出有实际意义的标签"""
    scores = stock.get("scores", {}) or {}
    value_score = scores.get("value", 50)
    momentum_score = scores.get("momentum", 50)
    quality_score = scores.get("quality", 50)
    pe = stock.get("pe")
    change_pct = stock.get("change_pct", 0) or 0  # 今日涨跌幅

    # ---- 优质 + 便宜 + 上涨动量 = 最佳买点 ----
    if value_score >= 65 and momentum_score >= 55 and quality_score >= 65:
        return "💚 质优低估"

    # ---- 估值低但动量弱（可能还在下跌） ----
    if value_score >= 65 and momentum_score < 40:
        return "💛 低估震荡"

    # ---- 高质量但价格合理（好公司，不便宜） ----
    if quality_score >= 80 and value_score >= 40:
        return "⚪ 质优合理"

    # ---- 估值偏贵 ----
    if value_score < 30:
        if pe and pe > 60:
            return "🔴 高估高PE"
        return "🔴 估值偏贵"

    # ---- 动量强但估值偏高（追高风险） ----
    if momentum_score >= 70 and value_score < 40:
        return "🟡 动量追高"

    # ---- 默认合理区间 ----
    return "⚪ 均衡"


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


def _enrich_stock_holding_relation(stocks: list, user_id: str) -> None:
    """给每只推荐股票标注与用户持仓的关联：已持有 / 同行业 / 新方向"""
    if not user_id or not stocks:
        return
    try:
        from services.stock_monitor import load_stock_holdings
        my_stocks = load_stock_holdings(user_id) or []
        if not my_stocks:
            return

        # 提取已持仓的代码 + 行业
        my_codes = set()
        my_industries = set()
        for ms in my_stocks:
            c = ms.get("code", "").replace("sh", "").replace("sz", "")
            my_codes.add(c)
            ind = ms.get("industry", "")
            if ind:
                my_industries.add(ind)

        for s in stocks:
            code = s.get("code", "").replace("sh", "").replace("sz", "")
            industry = s.get("industry", "")

            if code in my_codes:
                s["stock_relation"] = "🔵 已持有"
                s["stock_relation_hint"] = "你已经持有这只股票"
            elif industry and industry in my_industries:
                s["stock_relation"] = "🟡 同行业"
                s["stock_relation_hint"] = f"你已有 {industry} 行业持仓，集中度会增加"
            else:
                s["stock_relation"] = "🟢 新方向"
                s["stock_relation_hint"] = f"{industry or '未知行业'}，与你现有持仓无重叠"
    except Exception as e:
        print(f"[STOCK_SCREEN] holding_relation failed: {e}")


def _get_my_stock_summary(user_id: str) -> dict:
    """获取用户股票持仓摘要"""
    if not user_id:
        return {}
    try:
        from services.stock_monitor import load_stock_holdings
        stocks = load_stock_holdings(user_id) or []
        if not stocks:
            return {"count": 0, "hint": "暂无股票持仓记录"}
        industries = list({s.get("industry", "") for s in stocks if s.get("industry")})
        return {
            "count": len(stocks),
            "industries": industries,
            "hint": f"你已持有 {len(stocks)} 只股票，行业：{', '.join(industries) or '未分类'}",
        }
    except Exception as e:
        return {}


# ---- 行业信息补充（Tushare stock_basic 缓存） ----

_industry_cache = {}  # code → industry（进程内缓存，重启清空）


def _load_industry_map() -> dict:
    """从 Tushare stock_basic 批量获取行业映射（全市场一次性拉取，缓存1天）"""
    global _industry_cache
    if _industry_cache:
        return _industry_cache
    try:
        from services.tushare_data import _call_tushare, is_configured
        if not is_configured():
            return {}
        rows = _call_tushare("stock_basic", {"list_status": "L"}, "ts_code,industry")
        if rows:
            for r in rows:
                code = r.get("ts_code", "").split(".")[0]
                if code and r.get("industry"):
                    _industry_cache[code] = r["industry"]
            print(f"[STOCK_SCREEN] 行业映射: {len(_industry_cache)} 只")
    except Exception as e:
        print(f"[STOCK_SCREEN] 行业映射加载失败: {e}")
    return _industry_cache


def _enrich_stock_labels(stocks: list):
    """给每只股票补充时机标签 + 行业标签 + 行业解读"""
    from services.industry_templates import enrich_stock_with_industry
    industry_map = _load_industry_map()
    for s in stocks:
        s["timing_label"] = _stock_timing_label(s)
        code = s.get("code", "").replace("sh", "").replace("sz", "")
        s["industry"] = industry_map.get(code, "")
        enrich_stock_with_industry(s)
