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

FUND_SCREEN_FRESH_SECONDS = 10 * 3600
FUND_SCREEN_STALE_SECONDS = 72 * 3600

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


@router.get("/api/backtest/trend-validation")
def api_trend_backtest_results():
    """v9.5.123: 返回走势预估+定投策略的历史回测验证结果
    
    数据由 scripts/backtest_trend.py 生成, 存于 data/_cache/backtest_results.json
    前端用于展示"历史验证"卡片, 增加用户信任度。
    """
    import json as _j
    cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "backtest_results.json"
    if cache_fp.exists():
        try:
            return _j.loads(cache_fp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"error": "回测数据暂未生成, 请等待后台任务完成"}


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
def get_fund_screen(fund_type: str = "all", sort_by: str = "score", top_n: int = 20, userId: str = "", codes: str = ""):
    """基金智能筛选 v9.8.7：全量后端缓存（含个人化），前端只读不算。

    缓存策略：
    1. 优先读 per-user 个人化缓存（含 holding_relation/nav_percentile，2h有效）
    2. fallback 读通用缓存（不含个人化，由 cache_warmer 预热）
    3. 都没命中才实时计算（后台线程同步写缓存）

    v9.8.7: 新增 codes 参数 — 逗号分隔的基金代码列表，用于心愿单等精确查询场景。
    当 codes 非空时跳过全量筛选和缓存，只查询指定代码的基金详情。
    """
    import threading

    # ★ v9.8.7: codes 快速路径（心愿单/对比等精确查询，跳过全量筛选+缓存）
    if codes:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        if code_list:
            return _screen_codes_fast(code_list, userId)

    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # ★ 1. 优先读 per-user 完整缓存
    # v9.5.121: TTL 10h（覆盖一个交易日）
    # v9.9.4: stale 窗口放宽到 72h，避免周末/节假日超过 24h 后退回实时重算，把手机端 45s 超时打满
    # v9.5.124: top_n < 20 的请求不走缓存（cache_warmer 的 top_n=1 触发请求不该污染正常缓存）
    if top_n < 20:
        return _compute_fund_screen(fund_type, sort_by, top_n, userId)
    user_cache_key = f"fund_screen_{fund_type}_{sort_by}_{userId or 'anon'}"
    user_cache_fp = cache_dir / f"{user_cache_key}.json"
    try:
        if user_cache_fp.exists():
            payload = json.loads(user_cache_fp.read_text(encoding="utf-8"))
            age = time.time() - payload.get("created_at", 0)
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                # v9.9.1: market_timing 始终用最新值覆盖（避免不同tab显示不一致）
                data["market_timing"] = _get_market_timing_summary()
                return data
            # 过期但 <72h：stale-while-revalidate（先返旧数据，后台刷新）
            elif age < FUND_SCREEN_STALE_SECONDS and payload.get("data", {}).get("funds"):
                data = payload.get("data", {})
                data["from_cache"] = True
                data["stale"] = True
                # v9.9.1: market_timing 始终用最新值覆盖
                data["market_timing"] = _get_market_timing_summary()
                threading.Thread(target=_bg_refresh_fund_screen, args=(fund_type, sort_by, top_n, userId), daemon=True).start()
                return data
    except Exception:
        pass

    # ★ 2. 都没命中：实时计算（首次冷启动，后台 cache_warmer 以后会预热）
    # v9.5.120: 删除通用缓存 fallback（排序不匹配会返回错误结果）
    return _compute_fund_screen(fund_type, sort_by, top_n, userId)


# v9.8.7: codes 快速路径 — 只查询指定的几只基金，跳过全量筛选
def _screen_codes_fast(code_list: list, user_id: str = "") -> dict:
    """按代码列表精确查询基金详情（用于心愿单/对比等场景）"""
    from services.tushare_data import is_configured, _call_tushare
    from services.fund_screen import _enrich_single_fund

    results = []
    for code in code_list:
        try:
            fund = _enrich_single_fund(code, user_id)
        except Exception as e:
            print(f"[FUND_SCREEN] codes fast path error for {code}: {e}")
            fund = {"code": code, "name": f"未知({code})", "error": str(e)}
        if fund:
            results.append(fund)

    return {
        "funds": results,
        "total": len(results),
        "query": "codes_fast",
        "codes": code_list,
    }


def _bg_refresh_fund_screen(fund_type, sort_by, top_n, userId):
    """后台线程刷新个人化缓存"""
    try:
        _compute_fund_screen(fund_type, sort_by, top_n, userId)
    except Exception as e:
        print(f"[FUND_SCREEN] bg refresh error: {e}")


def _compute_fund_screen(fund_type, sort_by, top_n, userId):
    """实际计算+写缓存"""
    result = screen_funds(fund_type, sort_by, top_n, user_id=userId)
    if result.get("funds"):
        result["funds"] = comment_fund_picks(result["funds"])
        from services.industry_templates import enrich_fund_with_industry, get_fund_industry
        for f in result["funds"]:
            f["timing_label"] = _fund_timing_label(f)
            enrich_fund_with_industry(f)
        _enrich_fund_holding_relation(result["funds"], userId, get_fund_industry)
        _enrich_holding_funds_with_dividend(result["funds"])
        # v9.5.123 P3: 风格标签
        _enrich_style_tag(result["funds"])
        # v9.5.122: 走势预估标签（规则引擎，基于动量/百分位/资金/热度多维打分）
        _enrich_trend_forecast(result["funds"])
        # v9.5.123 P3-2: 经理稳定性标注
        _enrich_manager_stability(result["funds"])
        # v9.5.123 Sprint 4: DNA画像个性化适配 — 根据用户风险偏好标注"适合度"
        _enrich_with_dna_match(result["funds"], userId)
        # v9.5.123: 实时净值估算(盘中今日涨跌)
        _enrich_realtime_estimate(result["funds"])
        result["my_holdings_summary"] = _get_my_fund_holdings_summary(userId, get_fund_industry)

    result["market_timing"] = _get_market_timing_summary()
    result["style_timing"] = _get_style_timing_summary()
    
    # 写 per-user 缓存（10h fresh，过期后仍允许 72h stale-while-revalidate）
    # v9.5.124: top_n < 20 不写缓存（避免 cache_warmer top_n=1 污染正常结果）
    if top_n >= 20:
        try:
            cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
            user_cache_key = f"fund_screen_{fund_type}_{sort_by}_{userId or 'anon'}"
            user_cache_fp = cache_dir / f"{user_cache_key}.json"
            # v9.5.121: TTL 10h（cache_warmer 每天早盘+收盘刷新，中间不过期）
            payload = {"data": result, "expires_at": time.time() + 36000, "created_at": time.time()}
            user_cache_fp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as e:
            print(f"[FUND_SCREEN] cache write error: {e}")
    
    return result


# v9.5.117: 独立的潜力榜接口 — 扫更广候选池（top 80），独立缓存，不依赖综合榜
@router.get("/api/fund-potential")
def get_fund_potential(userId: str = "", limit: int = 30):
    """专属潜力榜接口
    
    特点：
    - 扫 top 80 候选（比综合榜的 top 30 范围更大）
    - A+B+C+D 多信号识别潜力基金
    - 独立文件缓存 6h（fund_potential_<userId>.json）
    - 不依赖综合榜，避免缓存联动 bug
    """
    import os as _os
    import json as _json
    import time as _time
    
    cache_dir = _os.path.join(_os.environ.get("DATA_DIR", "data"), "_cache")
    try:
        _os.makedirs(cache_dir, exist_ok=True)
    except Exception:
        pass
    cache_file = _os.path.join(cache_dir, f"fund_potential_{userId or 'anon'}.json")
    
    # 1. 文件缓存命中（12h 新鲜）
    _CACHE_FRESH = 43200   # 12h — 交易日够用，周末更不需要频繁刷新
    _CACHE_STALE_MAX = 86400  # 24h — 超过才真正丢弃
    try:
        if _os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                rec = _json.load(f)
            age = _time.time() - rec.get("t", 0)
            if age < _CACHE_FRESH:
                return rec.get("v", {"funds": [], "from_cache": True})
            # v9.5.118: stale-while-revalidate — 缓存过期但 <24h，先返回旧数据，后台异步刷新
            if age < _CACHE_STALE_MAX and rec.get("v", {}).get("funds"):
                import threading
                stale_data = rec["v"]
                stale_data["from_cache"] = True
                stale_data["stale"] = True
                def _bg_refresh():
                    try:
                        _do_potential_compute(userId, limit, cache_file)
                    except Exception:
                        pass
                threading.Thread(target=_bg_refresh, daemon=True).start()
                return stale_data
    except Exception:
        pass
    
    # 2. 实时计算（无缓存或缓存 >24h）
    return _do_potential_compute(userId, limit, cache_file)


def _do_potential_compute(userId: str, limit: int, cache_file: str):
    """实际潜力榜计算逻辑（可被主线程或后台线程调用）"""
    import os as _os, json as _json, time as _time
    result = screen_funds("all", "score", top_n=80, user_id=userId)
    if result.get("funds"):
        from services.industry_templates import enrich_fund_with_industry, get_fund_industry
        for f in result["funds"]:
            f["timing_label"] = _fund_timing_label(f)
            enrich_fund_with_industry(f)
        # 加 nav_percentile + 潜力评分（限制并发数避免过慢）
        for f in result["funds"]:
            try:
                code = f.get("code", "")
                if code:
                    nav_info = _get_fund_nav_percentile(code)
                    if nav_info:
                        f["nav_percentile"] = nav_info.get("nav_pct")
                        f["nav_pct_label"] = nav_info.get("nav_pct_label")
                        f["nav_low"] = nav_info.get("nav_low")
                        f["nav_high"] = nav_info.get("nav_high")
                        f["nav_hist_count"] = nav_info.get("hist_count")
                    fp = _fund_potential_signal(f)
                    if fp:
                        f["potential"] = fp
            except Exception:
                pass
        # 只保留有 potential 字段的 + 限量
        funds_with_potential = [f for f in result["funds"] if f.get("potential")]
        # 排序：high 在前，再按 score
        funds_with_potential.sort(key=lambda x: (
            0 if (x.get("potential") or {}).get("level") == "high" else 1,
            -(x.get("score", 0))
        ))
        funds_with_potential = funds_with_potential[:limit]
        
        result_data = {
            "funds": funds_with_potential,
            "total_scanned": len(result["funds"]),
            "potential_count": len(funds_with_potential),
            "high_count": sum(1 for f in funds_with_potential if (f.get("potential") or {}).get("level") == "high"),
            "mid_count": sum(1 for f in funds_with_potential if (f.get("potential") or {}).get("level") == "mid"),
            "from_cache": False,
        }
        # 写文件缓存
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                _json.dump({"v": result_data, "t": _time.time()}, f, ensure_ascii=False)
        except Exception:
            pass
        return result_data
    
    return {"funds": [], "total_scanned": 0, "potential_count": 0}


# v9.5.108: nav_series 文件持久化（跨重启），按日期 TTL
import os as __os_ns
import json as __json_ns
_NAV_SERIES_FILE = __os_ns.path.join(__os_ns.environ.get("DATA_DIR", "data"), "_cache", "_nav_series_cache.json")


def _load_nav_series_cache():
    global _nav_series_cache, _nav_series_cache_date
    try:
        if __os_ns.path.exists(_NAV_SERIES_FILE):
            with open(_NAV_SERIES_FILE, "r", encoding="utf-8") as f:
                data = __json_ns.load(f) or {}
            from datetime import date
            today_str = date.today().isoformat()
            if data.get("date") == today_str:
                _nav_series_cache = data.get("data", {})
                _nav_series_cache_date = today_str
    except Exception:
        pass


def _save_nav_series_cache():
    try:
        __os_ns.makedirs(__os_ns.path.dirname(_NAV_SERIES_FILE), exist_ok=True)
        with open(_NAV_SERIES_FILE, "w", encoding="utf-8") as f:
            __json_ns.dump({"date": _nav_series_cache_date, "data": _nav_series_cache}, f)
    except Exception:
        pass


_load_nav_series_cache()


def _get_nav_series(code: str, days: int = 60) -> list:
    """获取基金净值日收益率序列（%），用于相关系数计算。

    从 get_fund_nav_history 取净值，转成日涨跌幅，长度≥20才返回。
    结果缓存进进程内 dict，同一进程多次调用不重复拉数据。
    v9.5.77: 加日期 TTL，每天凌晨自动失效，避免用隔天陈旧数据。
    """
    global _nav_series_cache, _nav_series_cache_date
    from datetime import date
    today_str = date.today().isoformat()
    if _nav_series_cache_date != today_str:
        _nav_series_cache = {}
        _nav_series_cache_date = today_str
    if code in _nav_series_cache:
        return _nav_series_cache[code]
    try:
        from services.fund_monitor import get_fund_nav_history
        navs = get_fund_nav_history(code, days=days)
        vals = [n["nav"] for n in navs if n.get("nav") and n["nav"] > 0]
        if len(vals) < 20:
            _nav_series_cache[code] = []
            return []
        # 转日涨跌幅
        returns = [(vals[i] - vals[i-1]) / vals[i-1] for i in range(1, len(vals))]
        _nav_series_cache[code] = returns
        try:
            _save_nav_series_cache()
        except Exception:
            pass
        return returns
    except Exception:
        _nav_series_cache[code] = []
        return []


def _pearson_correlation(x: list, y: list) -> float | None:
    """计算两个序列的 Pearson 相关系数，对齐最短公共长度。"""
    n = min(len(x), len(y))
    if n < 10:
        return None
    x, y = x[-n:], y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sx = (sum((v - mx) ** 2 for v in x)) ** 0.5
    sy = (sum((v - my) ** 2 for v in y)) ** 0.5
    if sx * sy < 1e-10:
        return None
    return round(cov / (sx * sy), 3)


_nav_series_cache = {}      # 进程内 nav 序列缓存（避免重复 API 调用）
_nav_series_cache_date = "" # 缓存日期，跨天自动失效
_nav_pct_cache: dict = {}   # {code: {nav_pct, nav_pct_label, updated}}

# v9.5.108: nav_percentile 文件持久化（跨重启），TTL 当天有效
import os as _os
import json as _json_pct
_NAV_PCT_FILE = _os.path.join(_os.environ.get("DATA_DIR", "data"), "_cache", "_nav_pct_cache.json")


def _load_nav_pct_cache():
    global _nav_pct_cache
    try:
        if _os.path.exists(_NAV_PCT_FILE):
            with open(_NAV_PCT_FILE, "r", encoding="utf-8") as f:
                _nav_pct_cache = _json_pct.load(f) or {}
    except Exception:
        _nav_pct_cache = {}


def _save_nav_pct_cache():
    try:
        _os.makedirs(_os.path.dirname(_NAV_PCT_FILE), exist_ok=True)
        with open(_NAV_PCT_FILE, "w", encoding="utf-8") as f:
            _json_pct.dump(_nav_pct_cache, f, ensure_ascii=False)
    except Exception:
        pass


_load_nav_pct_cache()  # 模块加载时即恢复


def _get_fund_nav_percentile(code: str) -> dict:
    """v9.5.78: 用天天基金 API 拉 ~15个月净值历史，计算当前净值的历史百分位。
    
    返回 {nav_pct: int, nav_pct_label: str, nav_cur: float, nav_low: float, nav_high: float}
    v9.5.118: 缓存放宽到3天有效（净值百分位短期不会剧变，减少API请求）
    """
    from datetime import date, timedelta
    global _nav_pct_cache
    today = date.today()
    if code in _nav_pct_cache:
        cached_date = _nav_pct_cache[code].get("updated", "")
        try:
            if cached_date and (today - date.fromisoformat(cached_date)).days < 3:
                return _nav_pct_cache[code]
        except (ValueError, TypeError):
            pass
    try:
        import requests, re, json as _json
        url = "https://api.fund.eastmoney.com/f10/lsjz"
        headers = {"Referer": "https://fund.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
        all_navs = []
        for page in range(1, 16):  # 最多15页 × 20条 = 300条（~15个月）
            try:
                r = requests.get(url, params={"callback": "x", "fundCode": code,
                                              "pageIndex": page, "pageSize": 20},
                                 headers=headers, timeout=8)
                body = re.sub(r"^x\(", "", r.text.strip()).rstrip(")")
                items = _json.loads(body).get("Data", {}).get("LSJZList", [])
                if not items:
                    break
                for item in items:
                    nav = float(item.get("LJJZ") or item.get("DWJZ") or 0)
                    if nav > 0:
                        all_navs.append(nav)
            except Exception:
                break
        if len(all_navs) < 20:
            return {}
        cur = all_navs[0]  # 最新在前
        nav_pct = round(sum(1 for v in all_navs if v <= cur) / len(all_navs) * 100)
        if nav_pct <= 20:
            label = f"历史低位 {nav_pct}% 🟢"
        elif nav_pct <= 40:
            label = f"偏低 {nav_pct}%"
        elif nav_pct <= 60:
            label = f"历史中位 {nav_pct}%"
        elif nav_pct <= 80:
            label = f"偏高 {nav_pct}%"
        else:
            label = f"历史高位 {nav_pct}% 🔴"
        result = {
            "nav_pct": nav_pct, "nav_pct_label": label,
            "nav_cur": round(cur, 4),
            "nav_low": round(min(all_navs), 4),
            "nav_high": round(max(all_navs), 4),
            "hist_count": len(all_navs),
            "updated": today,
        }
        _nav_pct_cache[code] = result
        # v9.5.108: 同步写文件（异步 batch 优化可后续做，先用直写保证正确性）
        try:
            _save_nav_pct_cache()
        except Exception:
            pass
        return result
    except Exception as e:
        print(f"[NAV_PCT] {code}: {e}")
        return {}


def _enrich_fund_holding_relation(funds: list, user_id: str, get_fund_industry_fn) -> None:
    """给推荐基金列表标注与用户持仓的关联（已持仓/风格重叠/新敞口）。
    v9.5.76: 增加再平衡缺口方向标注（欠配方向优先提示）
    v9.5.77: 增加与用户持仓的 Pearson 相关系数（低相关=对冲候选）
    """
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

        # v9.5.87: 轻仓判断 — 从 V4 transactions 计算总持仓金额
        # 总金额 < 1000 时放宽"风格重叠"限制，改为推荐"同方向更好品种"
        _is_light_position = False
        try:
            import hashlib as _hs, json as _js
            from pathlib import Path as _PH
            _safe = _hs.sha256(user_id.encode()).hexdigest()[:16]
            _uf = _PH(os.environ.get("USERS_DIR", "/opt/moneybag/data/users")) / f"{_safe}.json"
            if _uf.exists():
                _txns = _js.loads(_uf.read_text()).get("portfolio", {}).get("transactions", [])
                _total_amt = sum(float(t.get("amount", 0)) for t in _txns if t.get("type") == "BUY")
                _is_light_position = _total_amt < 1000  # ¥1000以下算轻仓/测试
        except Exception:
            pass

        # v9.5.76: 判断推荐基金是否属于"欠配方向"（用简单名称关键词判断，不依赖 LLM）
        # 欠配方向：S&P500/纳指QDII（美股桶欠配-22%），红利低波（欠配-15%）
        def _is_gap_match(fund_name: str, fund_code: str) -> tuple[bool, str]:
            """返回 (是否命中缺口, 缺口说明)"""
            n = fund_name.lower()
            # 美股QDII方向：欠配 -22%
            us_keywords = ["qdii", "标普", "sp500", "s&p", "纳斯达克", "纳指", "美国", "美股", "全球科技",
                           "全球智能", "日经", "海外", "美元", "亚太", "欧洲", "港股"]
            if any(k in n for k in us_keywords):
                return True, "⬇ 补仓方向（美股/QDII欠配-22%）"
            # 红利低波方向：欠配 -15%
            div_keywords = ["红利", "股息", "低波", "价值", "dividend", "高息", "红利低波"]
            if any(k in n for k in div_keywords):
                return True, "⬇ 补仓方向（红利低波欠配-15%）"
            return False, ""

        # v9.5.77: 预拉用户持仓净值序列（只拉一次，后面复用）
        my_nav_series = {}
        for mf in my_funds:
            mc = mf.get("code", "")
            if mc:
                s = _get_nav_series(mc)
                if s:
                    my_nav_series[mc] = s
        has_correlation_data = bool(my_nav_series)

        for f in funds:
            code = f.get("code", "")
            name = f.get("name", "")
            f_tag = f.get("industry_tag", "")
            is_gap, gap_hint = _is_gap_match(name, code)

            # v9.5.77: 计算与用户持仓的平均相关系数
            if has_correlation_data and code not in my_codes:
                try:
                    rec_series = _get_nav_series(code)
                    if rec_series:
                        corr_vals = [_pearson_correlation(rec_series, s)
                                     for s in my_nav_series.values()]
                        valid_corrs = [c for c in corr_vals if c is not None]
                        if valid_corrs:
                            avg_corr = round(sum(valid_corrs) / len(valid_corrs), 2)
                            f["correlation_score"] = avg_corr
                            if avg_corr <= 0.3:
                                f["correlation_label"] = f"🟢 低相关 {avg_corr:.2f}"
                                f["correlation_hint"] = "与你持仓低相关，有对冲分散效果"
                            elif avg_corr <= 0.6:
                                f["correlation_label"] = f"🟡 中等相关 {avg_corr:.2f}"
                                f["correlation_hint"] = "与你持仓有一定相关性，不完全对冲"
                            else:
                                f["correlation_label"] = f"🔴 高度相关 {avg_corr:.2f}"
                                f["correlation_hint"] = "与你持仓高度同向，买入会加重集中度"
                except Exception:
                    pass  # 相关系数计算失败不影响主流程

            # v9.5.78: 净值历史百分位（好价格信号）+ 综合买入信号
            if code not in my_codes:
                try:
                    nav_info = _get_fund_nav_percentile(code)
                    if nav_info:
                        f["nav_percentile"] = nav_info["nav_pct"]
                        f["nav_pct_label"] = nav_info["nav_pct_label"]
                        f["nav_low"] = nav_info["nav_low"]
                        f["nav_high"] = nav_info["nav_high"]
                        f["nav_hist_count"] = nav_info["hist_count"]
                    # 综合买入信号
                    f["price_signal"] = _fund_price_signal(f)
                    # v9.5.81: 潜力评分
                    fp = _fund_potential_signal(f)
                    if fp:
                        f["potential"] = fp
                except Exception:
                    pass

            if code in my_codes:
                f["holding_relation"] = "🔵 已持仓"
                f["holding_hint"] = "你已经持有这只基金"
            elif f_tag and f_tag in my_tags:
                if _is_light_position:
                    # v9.5.87: 轻仓时放宽风格重叠限制，推荐"同方向更好品种"
                    f["holding_relation"] = "💡 可升级品种"
                    f["holding_hint"] = f"你有{f_tag}方向（轻仓），这是同方向中评分更高的选择"
                else:
                    f["holding_relation"] = "🟡 风格重叠"
                    f["holding_hint"] = f"你已有{f_tag}方向的基金，买入会加重该方向集中度"
            elif f_tag:
                f["holding_relation"] = "🟢 新敞口"
                f["holding_hint"] = f"你目前没有{f_tag}方向，可作为分散配置考虑"
            else:
                f["holding_relation"] = "⚪ 行业待识别"
                f["holding_hint"] = "该基金行业特征不明显，可能为宽基/跨行业配置，与现有持仓重叠风险较低"

            # 附加缺口方向标注（独立字段，不覆盖 holding_relation）
            if is_gap:
                f["gap_hint"] = gap_hint
                f["gap_match"] = True

            # v9.5.89: 基金经理换届预警（只对前10只主动基金查询，避免批量超时）
            try:
                from api.fund_detail import _get_fund_manager_change
                _mgr_idx = sum(1 for ff in funds[:funds.index(f)+1] if ff.get("_mgr_checked"))
                if _mgr_idx < 10 and code and not f.get("holding_relation", "").startswith("🔵"):
                    mgr = _get_fund_manager_change(code)
                    f["_mgr_checked"] = True
                    if mgr.get("has_change"):
                        f["manager_change"] = True
                        f["manager_warn"] = mgr.get("warn", "")
                        f["manager_name"] = mgr.get("manager_name", "")
                        f["manager_start"] = mgr.get("start_date", "")
                    elif mgr.get("current_manager"):
                        f["current_manager"] = mgr["current_manager"]
            except Exception:
                pass
    except Exception as e:
        print(f"[FUND_SCREEN] holding_relation failed: {e}")


# v9.5.123 Sprint 4: DNA画像个性化适配
def _enrich_with_dna_match(funds: list, user_id: str) -> None:
    """根据用户投资DNA画像, 为每只推荐基金标注"适合度"
    
    核心逻辑:
    - 用户回撤容忍度低 + 基金波动大 → 标注"⚠️ 波动超出你的舒适区"
    - 用户擅长某赛道 + 基金属于该赛道 → 标注"✅ 你的擅长赛道"
    - 用户持有期短 + 基金适合长期 → 标注"📅 建议持有>1年"
    """
    if not user_id:
        return
    try:
        from services.investor_dna import generate_investor_dna
        # 读缓存优先
        import json as _j
        cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / f"investor_dna_{user_id}.json"
        dna = None
        if cache_fp.exists():
            try:
                dna = _j.loads(cache_fp.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not dna or not dna.get("available"):
            return  # 没有画像数据,跳过
        
        risk_level = dna.get("risk_profile", {}).get("level", "")
        strong_sectors = dna.get("strong_sectors", [])
        holding_style = dna.get("holding_style", {}).get("type", "")
        max_loss = dna.get("drawdown_tolerance", {}).get("max_held_loss", 0)
        
        for f in funds:
            dna_tags = []
            
            # 1. 回撤匹配: 基金最大回撤 vs 用户容忍度
            max_dd = f.get("max_drawdown")
            if max_dd and max_loss:
                # max_dd 是正数(如0.15=15%), max_loss 是负数(如-15)
                fund_dd_pct = max_dd * 100 if max_dd < 1 else max_dd
                user_tolerance = abs(max_loss)
                if fund_dd_pct > user_tolerance * 1.5:
                    dna_tags.append({"type": "risk_mismatch", "text": f"⚠️ 回撤{fund_dd_pct:.0f}%超出舒适区({user_tolerance:.0f}%)", "color": "#F59E0B"})
                elif fund_dd_pct < user_tolerance * 0.5:
                    dna_tags.append({"type": "risk_match", "text": "✅ 波动低于你的容忍度", "color": "#86EFAC"})
            
            # 2. 赛道匹配: 基金行业 vs 用户擅长赛道
            fund_industry = f.get("industry_tag", "")
            if fund_industry and strong_sectors:
                if any(s in fund_industry for s in strong_sectors):
                    dna_tags.append({"type": "sector_match", "text": f"✅ 你的擅长赛道({fund_industry})", "color": "#86EFAC"})
            
            # 3. 持有期匹配
            if holding_style == "短线型":
                # 短线用户买长期基金要提醒
                r1y = (f.get("returns") or {}).get("1y")
                r3m = (f.get("returns") or {}).get("3m")
                if r1y and r1y > 20 and r3m and r3m < 5:
                    dna_tags.append({"type": "hold_hint", "text": "📅 长期表现好但近期弱,建议持有>6月", "color": "#A5B4FC"})
            
            # 4. 风险偏好匹配
            if risk_level == "保守":
                nav_pct = f.get("nav_percentile")
                if nav_pct and nav_pct > 80:
                    dna_tags.append({"type": "conservative_warn", "text": "⚠️ 高位入场风险大(你偏保守)", "color": "#F59E0B"})
            
            if dna_tags:
                f["dna_match"] = dna_tags
    except Exception as e:
        print(f"[DNA_MATCH] enrich failed: {e}")


# v9.5.39 P6: 只对"已持仓"基金加分红/拆分标
# v9.5.123 P3-2: 经理稳定性(任期长=验证过,短=有风险)
_fund_basic_cache = {"data": None, "ts": 0}

def _enrich_manager_stability(funds: list):
    """对TOP基金标注经理稳定性(fund_basic做进程内缓存,1小时刷新一次)
    
    v9.5.123: 非阻塞——如果缓存不存在,后台线程拉取,本次请求跳过。
    确保不会因为首次拉17624只基金数据导致选基请求超时。
    """
    import time as _t_mgr
    try:
        # 只读进程内缓存,不等待(首次缓存由后台线程填充)
        if _fund_basic_cache["data"] and _t_mgr.time() - _fund_basic_cache["ts"] < 3600:
            basics = _fund_basic_cache["data"]
        else:
            # 没有缓存→后台线程拉取,本次请求跳过
            if not _fund_basic_cache["data"]:
                import threading
                def _bg_load():
                    try:
                        from services.tushare_data import get_fund_basic_all, is_configured
                        if is_configured():
                            data = get_fund_basic_all()
                            if data:
                                _fund_basic_cache["data"] = data
                                _fund_basic_cache["ts"] = _t_mgr.time()
                    except Exception:
                        pass
                threading.Thread(target=_bg_load, daemon=True).start()
            return  # 本次不阻塞,下次请求时缓存已有
        if not basics:
            return
        # 构建{code: fund_info}映射
        basic_map = {}
        for b in basics:
            ts_code = b.get("ts_code", "")
            code = ts_code.split(".")[0] if ts_code else ""
            if code:
                basic_map[code] = b
        
        for f in funds:
            code = f.get("code", "")
            info = basic_map.get(code)
            if not info:
                continue
            # 基金经理任期(从管理日期推算)
            mgr_name = info.get("management", "")
            # 用list_date(上市日期)或found_date(成立日期)推算年龄
            found_date = info.get("list_date", "") or info.get("found_date", "")
            if found_date and len(found_date) >= 8:
                try:
                    from datetime import datetime as _dt_mgr
                    fd = _dt_mgr.strptime(found_date[:8], "%Y%m%d")
                    age = (_dt_mgr.now() - fd).days / 365.25
                    if age >= 5:
                        f["manager_stability"] = {"level": "stable", "text": f"经理任期{age:.0f}年+", "color": "#86EFAC"}
                    elif age < 1:
                        f["manager_stability"] = {"level": "new", "text": "⚠️ 新基金(<1年)", "color": "#F59E0B"}
                except Exception:
                    pass
    except Exception:
        pass


# v9.5.123 P3-1: 风格标签(基于名称关键词快速分类)
def _enrich_style_tag(funds: list):
    """为每只基金标注投资风格: 价值/成长/均衡/指数/量化/QDII"""
    _STYLE_MAP = [
        ("指数", ["指数", "ETF", "被动", "增强", "跟踪"]),
        ("价值", ["价值", "红利", "高股息", "低估", "蓝筹", "稳健", "收益"]),
        ("成长", ["成长", "创新", "科技", "先进", "新兴", "未来", "龙头", "先锋"]),
        ("量化", ["量化", "对冲", "多因子", "策略", "CTA"]),
        ("QDII", ["QDII", "全球", "海外", "美股", "港股", "纳斯达克", "标普"]),
        ("均衡", ["均衡", "配置", "灵活", "混合", "优选", "精选"]),
    ]
    for f in funds:
        name = f.get("name", "")
        style = ""
        for style_name, keywords in _STYLE_MAP:
            if any(kw in name for kw in keywords):
                style = style_name
                break
        if not style:
            style = "主动"  # 默认
        f["style_tag"] = style


# v9.5.123: 动态热点行业(从sector_rotation缓存获取,每日更新)
_dynamic_hot_cache = {"data": {}, "ts": 0}

def _get_dynamic_hot_sectors() -> dict:
    """获取当前热点行业(近5日涨幅TOP 5) → {行业名: 涨幅%}"""
    import time as _t
    # 5分钟内存缓存
    if _t.time() - _dynamic_hot_cache["ts"] < 300 and _dynamic_hot_cache["data"]:
        return _dynamic_hot_cache["data"]
    
    result = {}
    try:
        from services.sector_rotation import get_sector_ranking
        sr = get_sector_ranking()
        if sr and sr.get("available") and sr.get("top_gainers"):
            for s in sr["top_gainers"][:5]:
                name = s.get("name", "")
                pct = s.get("change_pct", 0)
                if name and pct > 1.5:  # 涨幅>1.5%才算热点
                    result[name] = round(pct, 1)
    except Exception:
        # fallback: 静态热点(保底)
        result = {"AI": 0, "半导体": 0, "科技": 0}
    
    _dynamic_hot_cache["data"] = result
    _dynamic_hot_cache["ts"] = _t.time()
    return result


def _enrich_trend_forecast(funds: list, *, include_dimensions: bool = False) -> None:
    """v9.5.123: 8维度 AI 走势预估引擎（规则引擎，不调 LLM）
    
    维度 & 权重：
    1. 动量趋势 (25%) — 3M/6M/1Y 回报率多周期动量
    2. 技术面信号 (20%) — 基于净值历史计算 MACD/RSI
    3. 估值水位 (15%) — NAV 百分位逆向指标
    4. 资金流向 (15%) — 申购赎回比/规模变化趋势
    5. 市场环境 (10%) — 大盘β + timing_label
    6. 赛道热度 (5%) — 板块轮动/行业拥挤度
    7. 波动率风险 (5%) — 最大回撤/夏普比
    8. 情绪面 (5%) — 关注度/换手率代理指标
    
    输出：trend_direction/trend_label/trend_score/trend_reason/trend_confidence
    include_dimensions=True 时输出 trend_dimensions 完整8维分解（用于弹窗 Layer2/3）
    
    注意：绝不预测具体价格，只给方向性判断+置信度。
    """
    for f in funds:
        dims = {}  # {维度名: {score, max, reason}}
        reasons = []
        
        r = f.get("returns") or {}
        r3m = r.get("3m")
        r6m = r.get("6m")
        r1y = r.get("1y")
        nav_pct = f.get("nav_percentile")
        timing = f.get("timing_label", "")
        industry = f.get("industry_tag", "")
        
        # ═══ 维度1: 动量趋势 (满分±25) ═══
        d1_score = 0
        d1_reason = ""
        if r3m is not None:
            if r3m > 15:
                d1_score += 18
                d1_reason = f"3M强势+{r3m:.0f}%"
            elif r3m > 5:
                d1_score += 10
                d1_reason = f"3M温和+{r3m:.0f}%"
            elif r3m < -10:
                d1_score -= 18
                d1_reason = f"3M回撤{r3m:.0f}%"
            elif r3m < -5:
                d1_score -= 8
                d1_reason = f"3M偏弱{r3m:.0f}%"
            else:
                d1_reason = f"3M平稳{r3m:+.0f}%"
        # 6M/1Y 加权补充
        if r6m is not None:
            if r6m > 25:
                d1_score += 5
            elif r6m < -15:
                d1_score -= 5
        if r1y is not None:
            if r1y > 30:
                d1_score += 4
            elif r1y < -10:
                d1_score -= 4
        d1_score = max(-25, min(25, d1_score))
        dims["动量趋势"] = {"score": d1_score, "max": 25, "reason": d1_reason}
        if d1_score >= 15:
            reasons.append("强动量")
        elif d1_score <= -15:
            reasons.append("动量转弱")
        
        # ═══ 维度2: 技术面信号 (满分±20) ═══
        d2_score = 0
        d2_reason = ""
        # 基于净值序列计算技术指标（利用已缓存的 nav_percentile 数据）
        nav_data = f.get("_nav_history") or []  # 从缓存注入（可选）
        if not nav_data and nav_pct is not None:
            # 用 nav_pct_cache 中的数据做简化技术判断
            # 近3M动量 + 估值位变化 = 简化MACD替代
            _np = nav_pct if nav_pct is not None else 50  # 避免 nav_pct=0 被 or 吃掉
            if r3m is not None and r3m > 10 and _np < 70:
                d2_score += 12
                d2_reason = "趋势加速+未过热"
            elif r3m is not None and r3m > 5 and _np < 50:
                d2_score += 8
                d2_reason = "低位起势"
            elif r3m is not None and r3m < -5 and _np > 70:
                d2_score -= 12
                d2_reason = "高位回落"
            elif r3m is not None and r3m < -10:
                d2_score -= 8
                d2_reason = "破位下行"
            else:
                d2_reason = "技术面中性"
        elif nav_data and len(nav_data) >= 35:
            # 有完整净值序列时计算真实MACD
            try:
                from services.technical import calc_macd, calc_rsi
                macd_result = calc_macd(nav_data)
                rsi_val = calc_rsi(nav_data)
                trend = macd_result.get("trend", "")
                if "金叉" in trend:
                    d2_score += 12
                    d2_reason = "MACD金叉"
                elif "死叉" in trend:
                    d2_score -= 12
                    d2_reason = "MACD死叉"
                elif "多头" in trend:
                    d2_score += 6
                    d2_reason = "多头排列"
                elif "空头" in trend:
                    d2_score -= 6
                    d2_reason = "空头排列"
                # RSI 超买超卖
                if isinstance(rsi_val, (int, float)):
                    if rsi_val > 75:
                        d2_score -= 5
                        d2_reason += "+RSI超买"
                    elif rsi_val < 30:
                        d2_score += 5
                        d2_reason += "+RSI超卖"
            except Exception:
                d2_reason = "技术指标计算异常"
        else:
            d2_reason = "数据不足"
        d2_score = max(-20, min(20, d2_score))
        dims["技术面信号"] = {"score": d2_score, "max": 20, "reason": d2_reason}
        if d2_score >= 10:
            reasons.append(d2_reason.split("+")[0] if "+" in d2_reason else d2_reason)
        elif d2_score <= -10:
            reasons.append(d2_reason.split("+")[0] if "+" in d2_reason else d2_reason)
        
        # ═══ 维度3: 估值水位 (满分±15, 逆向) ═══
        d3_score = 0
        d3_reason = ""
        if nav_pct is not None:
            if nav_pct >= 90:
                d3_score = -15
                d3_reason = f"极高位{nav_pct}%"
            elif nav_pct >= 75:
                d3_score = -8
                d3_reason = f"偏高{nav_pct}%"
            elif nav_pct <= 15:
                d3_score = 15
                d3_reason = f"极低位{nav_pct}%"
            elif nav_pct <= 30:
                d3_score = 10
                d3_reason = f"低位{nav_pct}%"
            elif nav_pct <= 50:
                d3_score = 4
                d3_reason = f"中低位{nav_pct}%"
            else:
                d3_reason = f"中高位{nav_pct}%"
        else:
            d3_reason = "无估值数据"
        dims["估值水位"] = {"score": d3_score, "max": 15, "reason": d3_reason}
        if d3_score >= 10:
            reasons.append("低位反弹空间大")
        elif d3_score <= -10:
            reasons.append("高位风险")
        
        # ═══ 维度4: 资金流向 (满分±15) ═══
        d4_score = 0
        d4_reason = ""
        # 用规模变化趋势+申购状态作为资金面代理指标
        scale = f.get("scale")  # 亿元
        buy_status = f.get("buy_status", "")
        if scale is not None:
            if scale > 100:
                d4_score += 3  # 大规模=机构认可
                d4_reason = f"规模{scale:.0f}亿"
            elif scale < 1:
                d4_score -= 3
                d4_reason = "迷你基金"
        # 申购状态反映资金意愿
        if "限购" in buy_status or "限额" in buy_status:
            d4_score += 6  # 限购=太多人想买=正向
            d4_reason = "限购(资金抢筹)"
        elif "暂停" in buy_status:
            d4_score -= 3
            d4_reason = "暂停申购"
        # 结合3M收益+规模做资金判断：涨得多+规模大=资金持续流入
        if r3m and r3m > 10 and scale and scale > 50:
            d4_score += 5
            if not d4_reason:
                d4_reason = "资金持续流入"
        elif r3m and r3m < -10 and scale and scale < 10:
            d4_score -= 5
            if not d4_reason:
                d4_reason = "资金流出迹象"
        if not d4_reason:
            d4_reason = "资金面中性"
        d4_score = max(-15, min(15, d4_score))
        dims["资金流向"] = {"score": d4_score, "max": 15, "reason": d4_reason}
        
        # ═══ 维度5: 市场环境 (满分±10) ═══
        d5_score = 0
        d5_reason = ""
        if "偏多" in timing or "强势" in timing:
            d5_score = 8
            d5_reason = "市场偏多"
        elif "偏空" in timing or "弱势" in timing:
            d5_score = -8
            d5_reason = "市场偏空"
        else:
            d5_reason = "市场中性"
        dims["市场环境"] = {"score": d5_score, "max": 10, "reason": d5_reason}
        
        # ═══ 维度6: 赛道热度 (满分±5) — v9.5.123动态热点 ═══
        d6_score = 0
        d6_reason = ""
        # 动态获取当日热点行业(从sector_rotation缓存)
        _hot_today = _get_dynamic_hot_sectors()  # {name: change_pct}
        _hot_names = list(_hot_today.keys())  # 近期涨幅TOP行业名称
        # 冷门赛道(近期跌幅大的行业,从sector_rotation动态获取;fallback硬编码)
        _cold_today = []
        try:
            from services.sector_rotation import get_sector_ranking
            sr = get_sector_ranking()
            if sr and sr.get("available") and sr.get("top_losers"):
                _cold_today = [s.get("name", "") for s in sr["top_losers"][:3] if s.get("change_pct", 0) < -1.5]
        except Exception:
            pass
        cold_sectors = _cold_today if _cold_today else ["地产", "房地产"]
        
        # v9.5.123: 中长期主题方向匹配(产业趋势,不依赖当日涨跌)
        _THEME_MAP = {
            "AI算力": ["算力", "AI芯片", "英伟达", "GPU", "服务器", "光模块", "CPO"],
            "先进封装": ["封装", "封测", "CoWoS", "先进制造"],
            "储能": ["储能", "电池", "锂电", "钠电", "氢能"],
            "无人机/低空": ["无人机", "低空", "eVTOL", "飞行汽车", "通航"],
            "AI制药": ["AI制药", "创新药", "生物科技", "CXO"],
            "自动驾驶": ["自动驾驶", "智能驾驶", "车联网", "激光雷达"],
            "机器人": ["机器人", "人形机器人", "具身智能", "减速器", "伺服"],
            "量子科技": ["量子", "量子计算", "量子通信"],
            "商业航天": ["航天", "卫星", "火箭", "遥感"],
            "脑机接口": ["脑机", "脑科学", "神经"],
            "半导体": ["半导体", "芯片", "EDA", "光刻", "晶圆"],
        }
        _fund_name = f.get("name", "")
        _theme_matched = ""
        for theme, keywords in _THEME_MAP.items():
            if any(kw in _fund_name or kw in (industry or "") for kw in keywords):
                _theme_matched = theme
                break
        if _theme_matched:
            f["theme_direction"] = _theme_matched
        
        # 检测基金是否踩中当前短期热点(行业涨幅)
        _matched_hot = ""
        _matched_pct = 0
        if industry:
            for hot_name in _hot_names:
                if hot_name in industry or industry in hot_name:
                    _matched_hot = hot_name
                    _matched_pct = _hot_today[hot_name]
                    break
        
        if _matched_hot:
            d6_score = 4
            d6_reason = f"踩中热点({_matched_hot})"
            # 标注到基金对象上(前端显示)
            f["hot_sector_match"] = {"name": _matched_hot, "pct": _matched_pct}
            if r3m and r3m > 20:
                d6_score = 2
                d6_reason = f"赛道热但已涨多"
        elif any(s in (industry or "") for s in cold_sectors):
            d6_score = -3
            d6_reason = f"冷门赛道({industry})"
        else:
            d6_reason = industry or "普通赛道"
        dims["赛道热度"] = {"score": d6_score, "max": 5, "reason": d6_reason}
        
        # ═══ 维度7: 波动率风险 (满分±5) ═══
        d7_score = 0
        d7_reason = ""
        max_drawdown = f.get("max_drawdown")  # 通常 screen_funds 有
        sharpe = f.get("sharpe")
        if max_drawdown is not None:
            if max_drawdown < -30:
                d7_score -= 4
                d7_reason = f"高回撤{max_drawdown:.0f}%"
            elif max_drawdown > -10:
                d7_score += 3
                d7_reason = "低波动稳健"
        if sharpe is not None:
            if sharpe > 1.5:
                d7_score += 2
                d7_reason = f"夏普优秀{sharpe:.1f}"
            elif sharpe < 0:
                d7_score -= 2
                if not d7_reason:
                    d7_reason = "风险收益比差"
        if not d7_reason:
            d7_reason = "波动中等"
        d7_score = max(-5, min(5, d7_score))
        dims["波动率风险"] = {"score": d7_score, "max": 5, "reason": d7_reason}
        
        # ═══ 维度8: 情绪面 (满分±5) ═══
        d8_score = 0
        d8_reason = ""
        # 用关注度/讨论热度代理（当前数据有限，用综合评分rank做代理）
        total_score = f.get("total_score")
        if total_score is not None:
            if total_score >= 85:
                d8_score = 3
                d8_reason = "高关注度"
            elif total_score <= 40:
                d8_score = -2
                d8_reason = "低关注冷门"
            else:
                d8_reason = "关注度适中"
        else:
            d8_reason = "关注度未知"
        dims["情绪面"] = {"score": d8_score, "max": 5, "reason": d8_reason}
        
        # ═══ 综合评分 ═══
        total = sum(d["score"] for d in dims.values())
        total = max(-100, min(100, total))
        
        # ═══ 置信度计算 ═══
        # 信号一致性越高，置信度越高；冲突越多，置信度越低
        pos_dims = sum(1 for d in dims.values() if d["score"] > 0)
        neg_dims = sum(1 for d in dims.values() if d["score"] < 0)
        neutral_dims = sum(1 for d in dims.values() if d["score"] == 0)
        
        if pos_dims >= 6 or neg_dims >= 6:
            confidence = 85  # 强共振
        elif pos_dims >= 5 or neg_dims >= 5:
            confidence = 72
        elif abs(pos_dims - neg_dims) <= 1 and neutral_dims < 3:
            confidence = 35  # 多空分歧大
        else:
            confidence = 55  # 一般
        
        # 信号冲突检测
        conflict = ""
        if d1_score > 10 and d3_score < -8:
            conflict = "动量强但估值高位(追高风险)"
            confidence = min(confidence, 50)
        elif d1_score < -10 and d3_score > 8:
            conflict = "动量弱但估值低位(可能筑底)"
            confidence = min(confidence, 50)
        elif d1_score > 10 and d4_score < -5:
            conflict = "涨势中资金流出(警惕)"
            confidence = min(confidence, 45)
        
        # ═══ 生成标签（v9.5.123优化: 阈值±20→±12, 回测验证更频繁出手收益更优）═══
        _TREND_THRESHOLD = 12
        if total >= _TREND_THRESHOLD:
            f["trend_direction"] = "up"
            f["trend_label"] = "↗️ 偏多"
        elif total <= -_TREND_THRESHOLD:
            f["trend_direction"] = "down"
            f["trend_label"] = "↘️ 偏空"
        else:
            f["trend_direction"] = "flat"
            f["trend_label"] = "→ 震荡"
        
        # 取前2个最显著的原因
        top_reasons = reasons[:2] if reasons else []
        if not top_reasons:
            if total > 0:
                top_reasons = ["综合偏多"]
            elif total < 0:
                top_reasons = ["综合偏空"]
            else:
                top_reasons = ["多空均衡"]
        
        f["trend_reason"] = "·".join(top_reasons)
        f["trend_score"] = total
        f["trend_confidence"] = confidence
        # 数据时效标注（让用户知道判断基于何时数据）
        import time as _t
        f["trend_updated_at"] = int(_t.time())
        
        # 信号冲突始终写入（DCA 引擎需要）
        if conflict:
            f["trend_conflict"] = conflict
        
        # 完整维度分解（Layer 2/3 详情用）
        if include_dimensions:
            f["trend_dimensions"] = dims
    
    # v9.5.123: QDII基金申购状态标注(限购/暂停)
    _check_qdii_purchase_status(funds)


@router.get("/api/fund-estimate-batch")
def fund_estimate_batch(codes: str = ""):
    """v9.5.123: 批量获取基金今日估算涨跌(盘中实时,5min缓存)
    
    用途: 前端展示缓存排行后异步刷新今日估算数字
    参数: codes=005827,110020,007994 (逗号分隔)
    返回: {code: estimate_pct}
    """
    if not codes:
        return {}
    code_list = [c.strip() for c in codes.split(",") if c.strip()][:30]
    result = {}
    try:
        from services.fund_monitor import _load_estimation_all, _safe_pct
        df = _load_estimation_all()
        if df is None or df.empty:
            return result
        for code in code_list:
            row = df[df["基金代码"] == code]
            if len(row) == 0:
                continue
            r = row.iloc[0]
            for c in r.index.tolist():
                if "估算增长率" in str(c):
                    est = _safe_pct(r[c])
                    if est is not None:
                        result[code] = est
                    break
    except Exception:
        pass
    return result


def _enrich_realtime_estimate(funds: list):
    """v9.5.123: 批量添加今日实时估算涨跌(用全市场估值表,一次请求覆盖所有)"""
    try:
        from services.fund_monitor import _load_estimation_all, _safe_float, _safe_pct
        df = _load_estimation_all()
        if df is None or df.empty:
            return
        for f in funds:
            code = f.get("code", "")
            if not code:
                continue
            row = df[df["基金代码"] == code]
            if len(row) == 0:
                continue
            r = row.iloc[0]
            cols = r.index.tolist()
            for c in cols:
                if "估算增长率" in str(c):
                    est_rate = _safe_pct(r[c])
                    if est_rate is not None:
                        f["today_estimate"] = est_rate  # 今日估算涨跌%
                    break
    except Exception as e:
        print(f"[REALTIME_EST] enrich failed: {e}")


def _check_qdii_purchase_status(funds: list):
    """对QDII基金检查申购状态,标注限购/暂停(批量,用缓存)"""
    try:
        from api.fund_detail import get_fund_purchase_info
        qdii_keywords = ["QDII", "纳指", "标普", "纳斯达克", "S&P", "海外", "美股", "日经", "印度", "全球", "港股"]
        checked = 0
        for f in funds:
            if checked >= 8:  # 最多查8只QDII,避免太慢
                break
            name = f.get("name", "")
            if not any(kw in name for kw in qdii_keywords):
                continue
            code = f.get("code", "")
            if not code:
                continue
            try:
                info = get_fund_purchase_info(code)
                if info and info.get("available"):
                    status = info.get("purchase_status", "")
                    limit = info.get("daily_limit")
                    if "暂停" in str(status):
                        f["purchase_warning"] = "⚠️ 暂停申购"
                    elif limit and limit < 1000:
                        f["purchase_warning"] = f"⚠️ 限购(单日限{limit:.0f}元)"
                    elif "限" in str(status):
                        f["purchase_warning"] = f"⚠️ 限购中"
                    checked += 1
            except Exception:
                pass
    except Exception:
        pass


def _enrich_stock_trend_forecast(stocks: list, *, include_dimensions: bool = False) -> None:
    """v9.5.123: 股票8维走势预估引擎（规则引擎，不调 LLM）
    
    维度适配股票数据源：
    1. 动量趋势 (25%) — 20D/60D/250D 涨跌
    2. 技术面信号 (20%) — 均线排列/MACD/RSI
    3. 估值水位 (15%) — PE百分位
    4. 资金流向 (15%) — 主力净流入/北向持仓
    5. 市场环境 (10%) — 大盘β
    6. 赛道热度 (5%) — 板块景气
    7. 波动率风险 (5%) — 振幅/回撤
    8. 情绪面 (5%) — 换手率/关注度
    """
    for s in stocks:
        dims = {}
        reasons = []
        
        r = s.get("returns") or {}
        r20d = r.get("20d")
        r60d = r.get("60d")
        r250d = r.get("250d") or r.get("1y")
        pe_pct = s.get("pe_percentile")
        catalysts = s.get("catalyst_flags") or ""
        industry = s.get("industry_tag", "")
        
        # ═══ 维度1: 动量趋势 (±25) ═══
        d1_score = 0
        d1_reason = ""
        if r20d is not None:
            if r20d > 12:
                d1_score += 16
                d1_reason = f"20D强势+{r20d:.0f}%"
            elif r20d > 4:
                d1_score += 8
                d1_reason = f"20D温和+{r20d:.0f}%"
            elif r20d < -8:
                d1_score -= 16
                d1_reason = f"20D回撤{r20d:.0f}%"
            elif r20d < -3:
                d1_score -= 6
                d1_reason = f"20D偏弱{r20d:.0f}%"
            else:
                d1_reason = f"20D平稳{r20d:+.0f}%"
        if r60d is not None:
            if r60d > 20:
                d1_score += 6
            elif r60d < -15:
                d1_score -= 6
        if r250d is not None:
            if r250d > 40:
                d1_score += 4
            elif r250d < -20:
                d1_score -= 4
        d1_score = max(-25, min(25, d1_score))
        dims["动量趋势"] = {"score": d1_score, "max": 25, "reason": d1_reason}
        if d1_score >= 15:
            reasons.append("强动量")
        elif d1_score <= -15:
            reasons.append("动量转弱")
        
        # ═══ 维度2: 技术面信号 (±20) ═══
        d2_score = 0
        d2_reason = ""
        # 用多周期动量判断均线状态
        if r20d is not None and r60d is not None:
            if r20d > 5 and r60d > 10:
                d2_score += 12
                d2_reason = "多头排列"
            elif r20d > 3 and r60d < 0:
                d2_score += 6
                d2_reason = "短期企稳"
            elif r20d < -5 and r60d < -10:
                d2_score -= 12
                d2_reason = "空头排列"
            elif r20d < -3 and r60d > 5:
                d2_score -= 6
                d2_reason = "短期回调"
            else:
                d2_reason = "技术面中性"
        else:
            d2_reason = "数据不足"
        d2_score = max(-20, min(20, d2_score))
        dims["技术面信号"] = {"score": d2_score, "max": 20, "reason": d2_reason}
        if abs(d2_score) >= 10:
            reasons.append(d2_reason)
        
        # ═══ 维度3: 估值水位 (±15) ═══
        d3_score = 0
        d3_reason = ""
        if pe_pct is not None:
            if pe_pct >= 90:
                d3_score = -15
                d3_reason = f"PE极高位{pe_pct}%"
            elif pe_pct >= 75:
                d3_score = -8
                d3_reason = f"PE偏高{pe_pct}%"
            elif pe_pct <= 15:
                d3_score = 15
                d3_reason = f"PE极低位{pe_pct}%"
            elif pe_pct <= 30:
                d3_score = 10
                d3_reason = f"PE低位{pe_pct}%"
            else:
                d3_reason = f"PE中位{pe_pct}%"
        else:
            d3_reason = "无PE数据"
        dims["估值水位"] = {"score": d3_score, "max": 15, "reason": d3_reason}
        if d3_score >= 10:
            reasons.append("估值便宜")
        elif d3_score <= -10:
            reasons.append("估值极高")
        
        # ═══ 维度4: 资金流向 (±15) ═══
        d4_score = 0
        d4_reason = ""
        if "机构加仓" in catalysts or "北向增持" in catalysts:
            d4_score += 10
            d4_reason = "机构/北向增持"
        if "解禁" in catalysts or "减持" in catalysts:
            d4_score -= 10
            d4_reason = "减持/解禁压力"
        if not d4_reason:
            # 用动量+换手做代理
            turnover = s.get("turnover_rate")
            if turnover and r20d and r20d > 5 and turnover > 5:
                d4_score += 5
                d4_reason = "放量上涨"
            elif turnover and r20d and r20d < -5 and turnover > 8:
                d4_score -= 5
                d4_reason = "放量下跌"
            else:
                d4_reason = "资金面中性"
        d4_score = max(-15, min(15, d4_score))
        dims["资金流向"] = {"score": d4_score, "max": 15, "reason": d4_reason}
        
        # ═══ 维度5: 市场环境 (±10) ═══
        d5_score = 0
        d5_reason = ""
        timing = s.get("timing_label", "")
        if "偏多" in timing or "强势" in timing:
            d5_score = 8
            d5_reason = "市场偏多"
        elif "偏空" in timing or "弱势" in timing:
            d5_score = -8
            d5_reason = "市场偏空"
        else:
            d5_reason = "市场中性"
        dims["市场环境"] = {"score": d5_score, "max": 10, "reason": d5_reason}
        
        # ═══ 维度6: 赛道热度 (±5) ═══
        d6_score = 0
        d6_reason = ""
        hot_sectors = ["AI", "科技", "半导体", "新能源", "军工", "芯片", "算力"]
        cold_sectors = ["地产", "房地产", "煤炭"]
        if any(sec in industry for sec in hot_sectors):
            d6_score = 4
            d6_reason = f"热门赛道"
            if r20d and r20d > 15:
                d6_score = 2
                d6_reason = "赛道热但已涨多"
        elif any(sec in industry for sec in cold_sectors):
            d6_score = -3
            d6_reason = "冷门赛道"
        else:
            d6_reason = industry or "普通赛道"
        dims["赛道热度"] = {"score": d6_score, "max": 5, "reason": d6_reason}
        
        # ═══ 维度7: 波动率风险 (±5) ═══
        d7_score = 0
        d7_reason = ""
        amplitude = s.get("amplitude")  # 振幅
        if amplitude:
            if amplitude > 15:
                d7_score = -4
                d7_reason = f"高波动({amplitude:.0f}%振幅)"
            elif amplitude < 5:
                d7_score = 3
                d7_reason = "低波动稳健"
            else:
                d7_reason = "波动适中"
        else:
            d7_reason = "波动未知"
        dims["波动率风险"] = {"score": d7_score, "max": 5, "reason": d7_reason}
        
        # ═══ 维度8: 情绪面 (±5) ═══
        d8_score = 0
        d8_reason = ""
        total_score = s.get("total_score")
        if total_score is not None:
            if total_score >= 85:
                d8_score = 3
                d8_reason = "高关注度"
            elif total_score <= 40:
                d8_score = -2
                d8_reason = "低关注冷门"
            else:
                d8_reason = "关注度适中"
        else:
            d8_reason = "关注度未知"
        dims["情绪面"] = {"score": d8_score, "max": 5, "reason": d8_reason}
        
        # ═══ 综合评分 ═══
        total = sum(d["score"] for d in dims.values())
        total = max(-100, min(100, total))
        
        # ═══ 置信度 ═══
        pos_dims = sum(1 for d in dims.values() if d["score"] > 0)
        neg_dims = sum(1 for d in dims.values() if d["score"] < 0)
        if pos_dims >= 6 or neg_dims >= 6:
            confidence = 85
        elif pos_dims >= 5 or neg_dims >= 5:
            confidence = 72
        elif abs(pos_dims - neg_dims) <= 1:
            confidence = 35
        else:
            confidence = 55
        
        # 信号冲突
        conflict = ""
        if d1_score > 10 and d3_score < -8:
            conflict = "涨势强但估值高(追高风险)"
            confidence = min(confidence, 50)
        elif d1_score < -10 and d3_score > 8:
            conflict = "跌势中但估值低(可能筑底)"
            confidence = min(confidence, 50)
        
        # ═══ 输出（v9.5.123优化: 阈值±12）═══
        _TREND_THRESHOLD = 12
        if total >= _TREND_THRESHOLD:
            s["trend_direction"] = "up"
            s["trend_label"] = "↗️ 偏多"
        elif total <= -_TREND_THRESHOLD:
            s["trend_direction"] = "down"
            s["trend_label"] = "↘️ 偏空"
        else:
            s["trend_direction"] = "flat"
            s["trend_label"] = "→ 震荡"
        
        top_reasons = reasons[:2] if reasons else (["综合偏多"] if total > 0 else ["综合偏空"] if total < 0 else ["多空均衡"])
        s["trend_reason"] = "·".join(top_reasons)
        s["trend_score"] = total
        s["trend_confidence"] = confidence
        
        if include_dimensions:
            s["trend_dimensions"] = dims
            if conflict:
                s["trend_conflict"] = conflict


def _enrich_holding_funds_with_dividend(funds: list) -> None:
    """对 holding_relation='🔵 已持仓' 的基金加 has_dividend_recent + dividend_label。
    "recent" 实际是"曾经有过"——只要历史上做过分红/拆分都警示成本核对（避免兴全合润那种累计净值陷阱）。
    """
    try:
        from api.fund_detail import _get_fund_dividend_recent
        for f in funds:
            if f.get("holding_relation") != "🔵 已持仓":
                continue
            code = f.get("code", "")
            if not code or not code.isdigit() or len(code) != 6:
                continue
            div = _get_fund_dividend_recent(code)
            # 用 has_history 而不是 has_recent —— 兴全合润 2013/2016/2019 拆分都该警示
            if div.get("has_history"):
                f["has_dividend_recent"] = True
                f["dividend_label"] = div.get("history_label", div.get("label", "分红")) + f"({div.get('history_count',1)}次)"
                f["dividend_date"] = div.get("history_latest_date", "")
    except Exception as e:
        print(f"[FUND_SCREEN] dividend enrich failed: {e}")


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
    """AI多因子选股 v9.5.120：per-user 后端缓存，前端零缓存直取。"""
    import threading
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # ★ 1. per-user 缓存（10h + stale 24h）
    user_cache_fp = cache_dir / f"stock_screen_{userId or 'anon'}.json"
    try:
        if user_cache_fp.exists():
            payload = json.loads(user_cache_fp.read_text(encoding="utf-8"))
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                # v9.9.1: market_timing 始终用最新值覆盖
                data["market_timing"] = _get_market_timing_summary()
                return data
            # v9.5.121: stale 24h — 无论何时打开都先返旧数据
            elif time.time() - payload.get("created_at", 0) < 86400 and payload.get("data", {}).get("stocks"):
                data = payload.get("data", {})
                data["from_cache"] = True
                data["stale"] = True
                # v9.9.1: market_timing 始终用最新值覆盖
                data["market_timing"] = _get_market_timing_summary()
                threading.Thread(target=_bg_refresh_stock_screen, args=(top_n, userId), daemon=True).start()
                return data
    except Exception:
        pass

    # ★ 2. 通用缓存 fallback（不含个人化，先返再后台刷）
    try:
        base_fp = cache_dir / "stock_screen_50.json"
        if base_fp.exists():
            payload = json.loads(base_fp.read_text(encoding="utf-8"))
            if time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                data["partial"] = True
                data["market_timing"] = _get_market_timing_summary()
                threading.Thread(target=_bg_refresh_stock_screen, args=(top_n, userId), daemon=True).start()
                return data
    except Exception:
        pass

    # ★ 3. 实时计算
    return _compute_stock_screen(top_n, userId)


def _bg_refresh_stock_screen(top_n, userId):
    try:
        _compute_stock_screen(top_n, userId)
    except Exception as e:
        print(f"[STOCK_SCREEN] bg refresh error: {e}")


def _compute_stock_screen(top_n, userId):
    result = screen_stocks(top_n)
    if result.get("stocks"):
        result["stocks"] = comment_stock_picks(result["stocks"])
        _enrich_stock_labels(result["stocks"])
        _enrich_stock_holding_relation(result["stocks"], userId)
        # v9.5.122: 走势预估
        _enrich_stock_trend_forecast(result["stocks"])
    result["market_timing"] = _get_market_timing_summary()
    result["style_timing"] = _get_style_timing_summary()
    result["my_stock_summary"] = _get_my_stock_summary(userId)
    # 写 per-user 缓存
    try:
        cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
        fp = cache_dir / f"stock_screen_{userId or 'anon'}.json"
        # v9.5.121: TTL 10h
        fp.write_text(json.dumps({"data": result, "expires_at": time.time() + 36000, "created_at": time.time()}, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
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


_pe_hist_cache: dict = {}   # {ts_code: {pe_percentile, pb_percentile, updated}}

def _get_pe_percentile(code_raw: str, current_pe, current_pb) -> dict:
    """v9.5.78: 用 Tushare daily_basic 计算个股近3年 PE/PB 历史百分位。
    
    返回 {pe_pct: int(0-100), pb_pct: int(0-100), pe_label, pb_label}
    进程内缓存当天有效（只需拉一次）。
    """
    from datetime import date, timedelta
    global _pe_hist_cache

    # 格式化为 Tushare ts_code（000001 → 000001.SZ，600519 → 600519.SH）
    raw = code_raw.replace("sh", "").replace("sz", "").replace(".", "")
    if not raw.isdigit():
        return {}
    if raw.startswith("6"):
        ts_code = f"{raw}.SH"
    elif raw.startswith(("0", "3")):
        ts_code = f"{raw}.SZ"
    elif raw.startswith("8") or raw.startswith("4"):
        ts_code = f"{raw}.BJ"
    else:
        return {}

    cache_key = ts_code
    today = date.today().isoformat()
    if cache_key in _pe_hist_cache and _pe_hist_cache[cache_key].get("updated") == today:
        return _pe_hist_cache[cache_key]

    try:
        from services.tushare_data import is_configured
        if not is_configured():
            return {}
        import tushare as ts
        import os
        ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
        pro = ts.pro_api()
        start = (date.today() - timedelta(days=1100)).strftime("%Y%m%d")  # ~3年
        end = date.today().strftime("%Y%m%d")
        df = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=end,
                             fields="trade_date,pe_ttm,pb")
        if df is None or len(df) < 20:
            return {}
        df = df.dropna(subset=["pe_ttm", "pb"])
        df = df[df["pe_ttm"] > 0]
        if len(df) < 20:
            return {}
        hist_pe = df["pe_ttm"].tolist()
        hist_pb = df["pb"].tolist()

        def _pct(val, hist):
            if val is None or not hist:
                return None
            return round(sum(1 for h in hist if h <= val) / len(hist) * 100)

        pe_pct = _pct(current_pe, hist_pe)
        pb_pct = _pct(current_pb, hist_pb)

        def _label(pct):
            if pct is None:
                return "数据不足"
            if pct <= 20:
                return f"历史低位 {pct}%"
            elif pct <= 40:
                return f"偏低 {pct}%"
            elif pct <= 60:
                return f"历史中位 {pct}%"
            elif pct <= 80:
                return f"偏高 {pct}%"
            else:
                return f"历史高位 {pct}%"

        result = {
            "pe_pct": pe_pct, "pe_label": _label(pe_pct),
            "pb_pct": pb_pct, "pb_label": _label(pb_pct),
            "hist_len": len(df), "updated": today,
        }
        _pe_hist_cache[cache_key] = result
        return result
    except Exception as e:
        print(f"[PE_HIST] {ts_code}: {e}")
        return {}


def _stock_potential_signal(s: dict) -> dict | None:
    """v9.5.81: 选股潜力评分 — A低估成长 + B动量突破 + C质量保障 三源信号。

    只在两个或以上信号满足时才返回，单信号不显示（噪音太多）。
    返回 {level: 'high'|'mid', label: str, reason: str, signals: list}
    """
    signals = []
    reasons = []

    mc = s.get("market_cap")           # 亿元
    rg = s.get("revenue_growth")       # 营收增速 %
    pe_pct = s.get("pe_percentile")    # PE历史百分位
    roe = s.get("roe")
    gross_margin = s.get("gross_margin")
    debt_ratio = s.get("debt_ratio")
    c60 = s.get("change_60d")          # 60日涨幅（≈3月）
    c20 = s.get("change_20d")          # 20日涨幅（≈1月）
    scores = s.get("scores", {}) or {}
    quality = scores.get("quality", 50)
    momentum = scores.get("momentum", 50)

    # ---- A：低估成长 ----
    # 小市值(<150亿) + 营收高增速(>20%) + PE不贵（历史<40% 或绝对<25）
    a_score = 0
    if mc is not None and mc < 150:
        a_score += 1
    if rg is not None and rg > 20:
        a_score += 1
    if pe_pct is not None and pe_pct < 35:
        a_score += 1
    elif s.get("pe") is not None and s.get("pe") < 20:
        a_score += 1  # 绝对估值低也行
    if a_score >= 2:
        detail = []
        if mc and mc < 150: detail.append(f"市值{mc:.0f}亿")
        if rg and rg > 20: detail.append(f"营收+{rg:.0f}%")
        if pe_pct and pe_pct < 35: detail.append(f"PE历史{pe_pct}%")
        signals.append("A")
        reasons.append("低估成长（" + "·".join(detail) + "）")

    # ---- B：动量突破 ----
    # 60日涨幅>10% 或 momentum评分高（>60）且近期没过热
    b_score = 0
    if c60 is not None and c60 > 10:
        b_score += 2
    elif c60 is not None and c60 > 5:
        b_score += 1
    if momentum >= 65:
        b_score += 1
    if c20 is not None and c20 > 3:
        b_score += 1
    # 过热惩罚
    if c60 is not None and c60 > 60:
        b_score -= 2  # 已经涨太多，不适合追入
    if b_score >= 2:
        detail = []
        if c60: detail.append(f"60日+{c60:.0f}%")
        if momentum >= 65: detail.append(f"动量{momentum:.0f}")
        signals.append("B")
        reasons.append("动量突破（" + "·".join(detail) + "）")

    # ---- C：质量保障 ----
    # ROE>15% + 毛利>30% + 低负债(<50%)
    c_score = 0
    if roe is not None and roe > 15:
        c_score += 2
    elif roe is not None and roe > 10:
        c_score += 1
    if gross_margin is not None and gross_margin > 30:
        c_score += 1
    if debt_ratio is not None and debt_ratio < 50:
        c_score += 1
    if quality >= 70:
        c_score += 1
    if c_score >= 3:
        detail = []
        if roe and roe > 10: detail.append(f"ROE{roe:.0f}%")
        if gross_margin and gross_margin > 30: detail.append(f"毛利{gross_margin:.0f}%")
        signals.append("C")
        reasons.append("质量好（" + "·".join(detail) + "）")

    n = len(signals)
    if n == 0 or (n == 1 and "A" not in signals):
        return None  # 单信号不显示（噪音太多）
    if n == 1:
        return None  # 单 A 也不显示

    level = "high" if n >= 3 else "mid"
    label = "🚀 高潜力" if level == "high" else "💡 中等潜力"
    signal_str = "+".join(signals)
    return {
        "level": level,
        "label": label,
        "signal_flags": signal_str,
        "reason": " · ".join(reasons),
    }


def _fund_potential_signal(f: dict) -> dict | None:
    """v9.5.81: 选基潜力评分 — A赛道集中 + B净值低位 + C管理人弹性 + D催化（v9.5.103新增）。

    至少满足 2 个信号才返回；实战中市场高位 B 难触发，加 D 提供扩容路径。
    """
    signals = []
    reasons = []
    returns = f.get("returns", {}) or {}
    r3m = returns.get("3m")
    r6m = returns.get("6m")
    r1y = returns.get("1y")
    r3y = returns.get("3y")
    nav_pct = f.get("nav_percentile")
    name = f.get("name", "")
    score = f.get("score", 0)

    # ---- A：赛道集中（通过基金名称识别，简单有效）----
    HOT_TRACKS = [
        "AI", "人工智能", "算力", "机器人", "低空", "新能源", "储能", "自动驾驶",
        "半导体", "芯片", "光伏", "氢能", "工业母机", "数字", "信创", "卫星",
        "生物", "创新药", "医疗器械", "量化", "科技创新"
    ]
    track_hit = [t for t in HOT_TRACKS if t in name]
    if track_hit:
        signals.append("A")
        reasons.append(f"赛道基金（{track_hit[0]}）")

    # ---- B：净值低位 / 近期回调（v9.5.103 放宽：高位也认\"刚回调\"）----
    b_score = 0
    if nav_pct is not None:
        if nav_pct <= 40:
            b_score += 2
        elif nav_pct <= 60:
            b_score += 1  # v9.5.103 新增：中位也给半分
        if nav_pct <= 20:
            b_score += 1  # 极低位额外加分
    # 近期回调（即使在高位，刚回调也是机会）
    if r3m is not None and r3m < -5:
        b_score += 1
    if r3m is not None and r3m < -10:
        b_score += 1  # v9.5.103 新增：深度回调
    if b_score >= 2:
        detail = []
        if nav_pct is not None: detail.append(f"净值历史{nav_pct}%")
        if r3m is not None and r3m < 0: detail.append(f"近3月{r3m:.0f}%")
        signals.append("B")
        reasons.append("低位/回调（" + "·".join(detail) + "）")

    # ---- C：管理人弹性（综合评分高 + 近1年同类优秀）----
    c_score = 0
    if score >= 65:
        c_score += 2
    elif score >= 55:
        c_score += 1
    if r1y is not None and r1y > 20:
        c_score += 2
    elif r1y is not None and r1y > 10:
        c_score += 1
    if r3y is not None and r3y > 30 and (r3m is None or r3m > -15):
        c_score += 1
    if c_score >= 3:
        detail = []
        if r1y: detail.append(f"1年+{r1y:.0f}%")
        if score >= 55: detail.append(f"综合分{score:.0f}")
        signals.append("C")
        reasons.append("管理人强（" + "·".join(detail) + "）")

    # ---- D：v9.5.103 新增 — 强动量+持续性（高位市场扩容）----
    # 用 6 月连续上涨 + 近1年优秀 + 短期没崩 来判断"市场领跑者"
    d_score = 0
    if r6m is not None and r6m > 15:
        d_score += 1
    if r6m is not None and r6m > 30:
        d_score += 1  # 半年大涨，强动量
    if r1y is not None and r1y > 30:
        d_score += 1
    if r3m is not None and r3m > 5 and r3m < 25:
        d_score += 1  # 近期还在涨但没过热
    if d_score >= 3:
        detail = []
        if r6m is not None: detail.append(f"6月+{r6m:.0f}%")
        if r1y is not None: detail.append(f"1年+{r1y:.0f}%")
        signals.append("D")
        reasons.append("强动量持续（" + "·".join(detail) + "）")

    n = len(signals)
    if n < 2:
        return None

    level = "high" if n >= 3 else "mid"
    label = "🚀 高潜力" if level == "high" else "💡 中等潜力"
    return {
        "level": level,
        "label": label,
        "signal_flags": "+".join(signals),
        "reason": " · ".join(reasons),
    }


def _stock_price_signal(s: dict, pe_hist: dict) -> dict:
    """v9.5.78: 综合买入信号 — 结合PE/PB历史百分位 + 质量评分 + timing_label，输出信号等级。

    返回 {level: str, label: str, reason: str}
    level: 'strong_buy' | 'buy' | 'neutral' | 'caution' | 'avoid'
    """
    pe_pct = pe_hist.get("pe_pct")
    pb_pct = pe_hist.get("pb_pct")
    scores = s.get("scores", {}) or {}
    quality = scores.get("quality", 50)
    value = scores.get("value", 50)
    timing = s.get("timing_label", "")

    reasons = []
    score = 0  # 综合打分，>0 偏好, <0 偏差

    # PE 历史百分位贡献
    if pe_pct is not None:
        if pe_pct <= 15:
            score += 3; reasons.append(f"PE历史低位{pe_pct}%")
        elif pe_pct <= 30:
            score += 2; reasons.append(f"PE偏低{pe_pct}%")
        elif pe_pct >= 80:
            score -= 2; reasons.append(f"PE历史高位{pe_pct}%")
        elif pe_pct >= 65:
            score -= 1; reasons.append(f"PE偏高{pe_pct}%")

    # PB 历史百分位贡献
    if pb_pct is not None:
        if pb_pct <= 15:
            score += 2; reasons.append(f"PB历史低位{pb_pct}%")
        elif pb_pct <= 30:
            score += 1
        elif pb_pct >= 80:
            score -= 2; reasons.append(f"PB历史高位{pb_pct}%")

    # 质量评分贡献
    if quality >= 75:
        score += 2; reasons.append("质量优秀")
    elif quality >= 60:
        score += 1
    elif quality < 40:
        score -= 1; reasons.append("质量偏弱")

    # timing_label 贡献
    if "质优低估" in timing:
        score += 2
    elif "低估震荡" in timing:
        score += 1
    elif "高估" in timing or "追高" in timing:
        score -= 2; reasons.append("估值过热")

    # 输出信号
    if score >= 6:
        return {"level": "strong_buy", "label": "💚 强烈买入", "reason": "、".join(reasons[:3])}
    elif score >= 3:
        return {"level": "buy", "label": "🟢 适合买入", "reason": "、".join(reasons[:3])}
    elif score >= 1:
        return {"level": "neutral", "label": "🔵 可以关注", "reason": "、".join(reasons[:2])}
    elif score >= -1:
        return {"level": "neutral", "label": "⚪ 观望", "reason": ""}
    elif score >= -3:
        return {"level": "caution", "label": "🟡 谨慎买入", "reason": "、".join(reasons[:2])}
    else:
        return {"level": "avoid", "label": "🔴 暂时回避", "reason": "、".join(reasons[:2])}


def _fund_price_signal(fund: dict) -> dict:
    """v9.5.78: 基金综合买入信号 — 结合净值百分位 + 收益趋势 + 相关系数。

    返回 {level: str, label: str, reason: str}
    """
    nav_pct = fund.get("nav_percentile")
    r3m = (fund.get("returns") or {}).get("3m")
    r1y = (fund.get("returns") or {}).get("1y")
    corr = fund.get("correlation_score")  # 与用户持仓的相关系数

    reasons = []
    score = 0

    # 净值历史百分位
    if nav_pct is not None:
        if nav_pct <= 20:
            score += 3; reasons.append(f"净值历史低位{nav_pct}%")
        elif nav_pct <= 40:
            score += 2; reasons.append(f"净值偏低{nav_pct}%")
        elif nav_pct >= 85:
            score -= 2; reasons.append(f"净值历史高位{nav_pct}%")
        elif nav_pct >= 70:
            score -= 1; reasons.append(f"净值偏高{nav_pct}%")

    # 近3月收益（回撤=更便宜=加分）
    if r3m is not None:
        if r3m < -15:
            score += 3; reasons.append(f"近3月大幅回调{r3m:.0f}%")
        elif r3m < -5:
            score += 2; reasons.append(f"近3月回调{r3m:.0f}%")
        elif r3m > 25:
            score -= 2; reasons.append(f"近3月涨幅过大{r3m:.0f}%")
        elif r3m > 12:
            score -= 1

    # 相关系数（低相关 = 对冲价值）
    if corr is not None and corr <= 0.3:
        score += 1; reasons.append("低相关对冲")

    # 输出信号
    if score >= 5:
        return {"level": "strong_buy", "label": "💚 强烈买入", "reason": "、".join(reasons[:3])}
    elif score >= 3:
        return {"level": "buy", "label": "🟢 适合买入", "reason": "、".join(reasons[:3])}
    elif score >= 1:
        return {"level": "neutral", "label": "🔵 可以关注", "reason": "、".join(reasons[:2])}
    elif score >= -1:
        return {"level": "neutral", "label": "⚪ 观望", "reason": ""}
    elif score >= -3:
        return {"level": "caution", "label": "🟡 谨慎买入", "reason": "、".join(reasons[:2])}
    else:
        return {"level": "avoid", "label": "🔴 暂时回避", "reason": "、".join(reasons[:2])}


def _enrich_stock_labels(stocks: list):
    """给每只股票补充时机标签 + 行业标签 + 行业解读 + PE/PB历史百分位"""
    from services.industry_templates import enrich_stock_with_industry
    industry_map = _load_industry_map()
    for s in stocks:
        s["timing_label"] = _stock_timing_label(s)
        code = s.get("code", "").replace("sh", "").replace("sz", "")
        s["industry"] = industry_map.get(code, "")
        enrich_stock_with_industry(s)
        # v9.5.78: 补充PE/PB历史百分位（轻量，进程内缓存当天有效）
        try:
            pe_hist = _get_pe_percentile(s.get("code", ""), s.get("pe"), s.get("pb"))
            if pe_hist:
                s["pe_percentile"] = pe_hist.get("pe_pct")
                s["pb_percentile"] = pe_hist.get("pb_pct")
                s["pe_pct_label"] = pe_hist.get("pe_label", "")
                s["pb_pct_label"] = pe_hist.get("pb_label", "")
                # v9.5.78: 综合买入信号
                s["price_signal"] = _stock_price_signal(s, pe_hist)
        except Exception:
            pass
        # v9.5.81: 潜力评分（A低估成长 + B动量突破 + C质量保障）
        try:
            ps = _stock_potential_signal(s)
            if ps:
                s["potential"] = ps
        except Exception:
            pass


# ============================================================
# 持仓基金排名追溯
# ============================================================

@router.get("/api/fund-rank/holding-compare")
def api_holding_rank_compare(userId: str = ""):
    """对比持仓基金在排行榜中的当前排名 vs 上周排名
    
    每周日 22:00 排行榜更新后，可用此接口追踪持仓基金排名变化。
    """
    uid = userId or "default"
    from services.fund_rank import get_holding_rank_compare
    return get_holding_rank_compare(uid)


# ============================================================
# 长期持有筛选
# ============================================================

@router.get("/api/longterm/funds")
def api_longterm_funds(force: bool = False, userId: str = ""):
    """长期持有基金 v9.5.120: per-user 后端缓存"""
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_fp = cache_dir / f"longterm_funds_{userId or 'anon'}.json"
    # 读 per-user 缓存（30天）
    if not force:
        try:
            if cache_fp.exists():
                payload = json.loads(cache_fp.read_text(encoding="utf-8"))
                if time.time() < payload.get("expires_at", 0):
                    return payload.get("data", {})
        except Exception:
            pass
    from services.longterm_screen import screen_longterm_funds
    from services.industry_templates import get_fund_industry
    result = screen_longterm_funds(force=force)
    if userId and result.get("funds"):
        try:
            _enrich_fund_holding_relation(result["funds"], userId, get_fund_industry)
        except Exception as e:
            print(f"[LONGTERM] holding_relation failed: {e}")
    # 写缓存（30天）
    try:
        cache_fp.write_text(json.dumps({"data": result, "expires_at": time.time() + 2592000, "created_at": time.time()}, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    return result


@router.get("/api/longterm/stocks")
def api_longterm_stocks(force: bool = False, userId: str = ""):
    """长期持有股票 v9.5.120: per-user 后端缓存"""
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_fp = cache_dir / f"longterm_stocks_{userId or 'anon'}.json"
    if not force:
        try:
            if cache_fp.exists():
                payload = json.loads(cache_fp.read_text(encoding="utf-8"))
                if time.time() < payload.get("expires_at", 0):
                    return payload.get("data", {})
        except Exception:
            pass
    from services.longterm_screen import screen_longterm_stocks
    result = screen_longterm_stocks(force=force)
    if userId and result.get("stocks"):
        try:
            _enrich_stock_holding_relation(result["stocks"], userId)
        except Exception as e:
            print(f"[LONGTERM] stock holding_relation failed: {e}")
    try:
        cache_fp.write_text(json.dumps({"data": result, "expires_at": time.time() + 7776000, "created_at": time.time()}, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    return result


# ============================================================
# F7+ v9.5.45: 股票风险事件接口（龙虎榜 + 解禁 + 股东减持）
# 前端 insight.js _enrichStockEvents 已有 silent fallback 等待此接口
# ============================================================

_RISK_EVENTS_CACHE: dict = {}
_RISK_EVENTS_TTL = 3600  # 1小时缓存（龙虎榜当日数据，不需要太频繁刷）


def _get_lhb_events(code: str) -> list:
    """龙虎榜事件（近5个交易日）"""
    try:
        import akshare as ak
        from datetime import date, timedelta
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(symbol=code, start_date=start, end_date=end)
        if df is None or df.empty:
            return []
        events = []
        for _, row in df.head(3).iterrows():
            d = str(row.get("上榜日期", row.get("date", "")))[:10]
            reason = str(row.get("上榜原因", row.get("reason", "")))
            if d and reason:
                events.append({"type": "lhb", "label": "龙虎榜", "text": f"{d} {reason}", "date": d})
        return events
    except Exception:
        return []


def _get_unlock_events(code: str) -> list:
    """股票解禁事件（近30天）"""
    try:
        import akshare as ak
        from datetime import date, timedelta
        df = ak.stock_restricted_release_queue_sina()
        if df is None or df.empty:
            return []
        # 过滤目标代码
        code_clean = code.replace("sh", "").replace("sz", "")
        mask = df.apply(lambda r: str(r.get("股票代码", r.get("code", ""))).replace("sh", "").replace("sz", "")
                        == code_clean, axis=1)
        sub = df[mask]
        if sub.empty:
            return []
        today = date.today()
        events = []
        for _, row in sub.head(2).iterrows():
            unlock_date_raw = str(row.get("解禁日期", row.get("date", "")))[:10]
            try:
                from datetime import datetime
                ud = datetime.strptime(unlock_date_raw, "%Y-%m-%d").date()
                days_away = (ud - today).days
                if -30 <= days_away <= 60:  # 已解禁30天内 / 即将解禁60天内
                    label = f"{'已' if days_away < 0 else '将'}解禁"
                    amt_raw = row.get("解禁数量(万股)", row.get("shares", ""))
                    amt = f"{float(amt_raw):.0f}万股" if amt_raw else ""
                    events.append({
                        "type": "unlock", "label": label,
                        "text": f"{unlock_date_raw} 解禁 {amt}".strip(), "date": unlock_date_raw
                    })
            except Exception:
                pass
        return events
    except Exception:
        return []


def _get_reduce_events(code: str) -> list:
    """大股东减持公告（近30天，基于东财公告）"""
    try:
        import akshare as ak
        code_clean = code.replace("sh", "").replace("sz", "")
        df = ak.stock_notice_report(symbol=code_clean, keyword="减持")
        if df is None or df.empty:
            return []
        events = []
        for _, row in df.head(2).iterrows():
            d = str(row.get("公告日期", row.get("date", "")))[:10]
            title = str(row.get("公告标题", row.get("title", "")))[:30]
            if d and title and "减持" in title:
                events.append({"type": "reduce", "label": "减持", "text": f"{d} {title}", "date": d})
        return events
    except Exception:
        return []


@router.get("/api/risk-events")
def get_risk_events(codes: str = ""):
    """F7+ 股票风险事件查询

    参数：codes=sh600519,sz000001（逗号分隔，最多 10 只）
    返回：{events: {code: [事件列表]}, cached: bool}

    事件类型：lhb(龙虎榜) / unlock(解禁) / reduce(减持) / announcement(公告)
    前端 insight.js _enrichStockEvents 已有 silent fallback 等接口就绪
    """
    if not codes:
        return {"events": {}, "cached": False, "error": "codes 参数为空"}

    code_list = [c.strip() for c in codes.split(",") if c.strip()][:10]
    cache_key = ",".join(sorted(code_list))
    now = time.time()

    # 缓存命中
    if cache_key in _RISK_EVENTS_CACHE:
        ts, data = _RISK_EVENTS_CACHE[cache_key]
        if now - ts < _RISK_EVENTS_TTL:
            return {"events": data, "cached": True}

    result = {}
    for code in code_list:
        events = []
        # 龙虎榜（只查沪深A股，代码 sh/sz 开头）
        if code.startswith("sh") or code.startswith("sz"):
            code_6 = code[2:]
            events += _get_lhb_events(code_6)
            events += _get_unlock_events(code)
            events += _get_reduce_events(code_6)
        # 按日期排序，最新的在前
        events.sort(key=lambda e: e.get("date", ""), reverse=True)
        if events:
            result[code] = events[:5]  # 每只最多 5 条

    _RISK_EVENTS_CACHE[cache_key] = (now, result)
    return {"events": result, "cached": False, "count": sum(len(v) for v in result.values())}

