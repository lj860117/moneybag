"""
持仓管理 API（股票 + 基金 CRUD / 盯盘 / 分析 / 关联智能）
==========================================================
从 main.py 提取的 P2 路由。

Design doc: docs/design/12-framework-refactor.md §四
"""
import os
import time

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["持仓管理"])

from services.stock_monitor import (
    load_stock_holdings, add_stock_holding, remove_stock_holding,
    update_stock_holding, get_stock_realtime, scan_all_holdings,
)
from services.fund_monitor import (
    load_fund_holdings, add_fund_holding, remove_fund_holding,
    update_fund_holding, get_fund_realtime, scan_all_fund_holdings,
)
from services.holding_intelligence import (
    scan_all_holding_intelligence, build_holding_context,
    get_stock_news as get_stock_related_news, get_stock_fund_flow, get_stock_industry,
)
from services.data_layer import get_stock_financials, get_fund_holding_detail

from api.shared_helpers import _build_market_context, _load_prompt_template, _alert_cooldown


# ---- 股票持仓 CRUD ----

@router.get("/api/stock-holdings")
def get_stock_holdings_api(userId: str = "default"):
    """获取股票持仓列表"""
    return {"holdings": load_stock_holdings(userId)}


@router.post("/api/stock-holdings")
def add_stock_holding_api(req: dict):
    """添加股票持仓"""
    code = req.get("code", "").strip()
    if not code:
        raise HTTPException(400, "股票代码不能为空")
    # 输入校验
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, f"股票代码格式错误：{code}（应为6位数字）")
    # 合法性校验：A股代码首位必须是 0/3/6/8
    if code[0] not in ("0", "3", "6", "8"):
        raise HTTPException(400, f"不是有效的A股代码：{code}")
    cost_price = float(req.get("costPrice", 0))
    shares = int(req.get("shares", 0))
    if cost_price <= 0:
        raise HTTPException(400, "成本价必须大于0")
    if shares <= 0:
        raise HTTPException(400, "持仓数量必须大于0")
    if cost_price > 100000:
        print(f"[HOLDINGS] 高成本价警告: {code} costPrice={cost_price}")
    uid = req.get("userId", "default")
    return add_stock_holding(
        code=code,
        name=req.get("name", ""),
        cost_price=cost_price,
        shares=shares,
        note=req.get("note", ""),
        user_id=uid,
    )


@router.delete("/api/stock-holdings/{code}")
def remove_stock_holding_api(code: str, userId: str = "default"):
    """删除股票持仓"""
    return remove_stock_holding(code, userId)


@router.put("/api/stock-holdings/{code}")
def update_stock_holding_api(code: str, req: dict):
    """更新股票持仓信息"""
    uid = req.pop("userId", "default")
    return update_stock_holding(code, user_id=uid, **{
        k: v for k, v in req.items()
        if k in ("costPrice", "shares", "note", "name")
    })


@router.get("/api/stock-holdings/realtime/{code}")
def get_stock_rt_api(code: str):
    """获取单只股票实时行情"""
    return get_stock_realtime(code)


@router.get("/api/stock-holdings/scan")
def scan_holdings_api(userId: str = "default"):
    """扫描全持仓 — 实时行情 + 异动信号"""
    return scan_all_holdings(userId)


# ---- 盯盘预警 ----

@router.get("/api/watchlist/alerts")
def get_watchlist_alerts(userId: str = "default"):
    """盯盘预警轮询 — 前端每 15 秒调一次（交易时段）"""
    now = time.time()
    cooldown_sec = 1800  # 30 分钟冷却

    # 获取用户盯盘阈值
    from services.persistence import load_user
    user = load_user(userId)
    config = user.get("watchlist_config", {})
    stop_loss = config.get("stop_loss_pct", -0.08)
    take_profit = config.get("take_profit_pct", 0.20)
    price_range = config.get("price_alert_range", 0.05)

    alerts = []
    holdings = load_stock_holdings(userId)
    for h in holdings:
        code = h.get("code", "")
        if not code:
            continue
        try:
            rt = get_stock_realtime(code)
            price = rt.get("price")
            cost = h.get("costPrice", 0)
            if price and cost and cost > 0:
                pnl_pct = (price - cost) / cost
                # 止损
                if pnl_pct <= stop_loss:
                    key = f"stop_{code}"
                    if now - _alert_cooldown.get(key, 0) > cooldown_sec:
                        alerts.append({"type": "stop_loss", "code": code, "name": h.get("name", ""), "pnlPct": round(pnl_pct * 100, 2), "price": price, "level": "danger", "msg": f"{h.get('name', code)} 已跌 {pnl_pct*100:.1f}%，触发止损线({stop_loss*100:.0f}%)"})
                        _alert_cooldown[key] = now
                # 止盈
                if pnl_pct >= take_profit:
                    key = f"profit_{code}"
                    if now - _alert_cooldown.get(key, 0) > cooldown_sec:
                        alerts.append({"type": "take_profit", "code": code, "name": h.get("name", ""), "pnlPct": round(pnl_pct * 100, 2), "price": price, "level": "opportunity", "msg": f"{h.get('name', code)} 已涨 {pnl_pct*100:.1f}%，触发止盈线({take_profit*100:.0f}%)"})
                        _alert_cooldown[key] = now
                # 价格异动
                change_pct = rt.get("changePct", 0)
                if abs(change_pct) > price_range * 100:
                    key = f"move_{code}"
                    if now - _alert_cooldown.get(key, 0) > cooldown_sec:
                        direction = "大涨" if change_pct > 0 else "大跌"
                        alerts.append({"type": "price_move", "code": code, "name": h.get("name", ""), "changePct": change_pct, "price": price, "level": "warning", "msg": f"{h.get('name', code)} 今日{direction} {change_pct:+.2f}%"})
                        _alert_cooldown[key] = now
        except Exception:
            continue

    return {"alerts": alerts, "count": len(alerts), "timestamp": time.time()}


# ---- 股票深度分析 ----

@router.post("/api/stock-holdings/analyze")
async def analyze_stock_holdings(req: dict = {}):
    """收盘后 DeepSeek 深度分析全持仓（7 Skill 框架）"""
    uid = req.get("userId", "default")
    scan = scan_all_holdings(uid)
    if not scan.get("holdings"):
        return {"analysis": "暂无持仓股票，请先添加。", "source": "none"}

    from services.signal_scout import is_trading_day
    trading_day = is_trading_day()
    total_holdings = len(scan["holdings"])
    null_count = sum(1 for h in scan["holdings"] if h.get("price") is None)
    snapshot_count = sum(1 for h in scan["holdings"] if h.get("is_snapshot"))

    data_quality_notice = []
    if not trading_day:
        data_quality_notice.append("⚠️ 今天是非交易日，数据为最近一个交易日收盘快照")
    if null_count > 0:
        data_quality_notice.append(f"⚠️ {null_count}/{total_holdings} 只股票数据未能获取（price=null）")
    if snapshot_count > 0:
        data_quality_notice.append(f"📅 {snapshot_count}/{total_holdings} 只股票使用的是日线收盘数据（非盘中实时）")
    data_quality_str = " | ".join(data_quality_notice) if data_quality_notice else "✅ 实时数据"

    lines = [f"【股票持仓盯盘数据 — {data_quality_str}】"]
    for h in scan["holdings"]:
        ind = h.get("indicators") or {}
        price_str = f"¥{h['price']}" if h.get("price") is not None else "N/A(数据缺失)"
        chg_str = f"{h['changePct']:+.2f}%" if h.get("changePct") is not None else "N/A"
        pnl_str = f"盈亏{h['pnlPct']:+.1f}%" if h.get("pnlPct") is not None else "盈亏N/A"
        data_date = h.get("data_date", "")
        date_tag = f"[数据截至{data_date}]" if data_date else ""
        lines.append(
            f"  {h['name']}({h['code']}) 现价{price_str} "
            f"涨跌{chg_str} {pnl_str} "
            f"RSI={ind.get('rsi14','N/A')} MACD={ind.get('macd_trend','N/A')} "
            f"量比={ind.get('volume_ratio','N/A')} {date_tag}"
        )
    if scan.get("signals"):
        lines.append("\n【异动信号】")
        for s in scan["signals"]:
            lines.append(f"  [{s['level']}] {s['msg']}")

    stock_ctx = "\n".join(lines)
    market_ctx = _build_market_context()

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return {"analysis": stock_ctx, "source": "data_only", "scan": scan, "data_quality": data_quality_str}

    base_prompt = _load_prompt_template()
    system_prompt = (base_prompt + "\n\n"
        "🔴 数据诚信铁律（必须遵守）：\n"
        "1. 若持仓数据中字段为 N/A 或 null，绝对禁止编造具体数值。用『数据暂缺』『本次无法评估』代替。\n"
        "2. 若标注为『非交易日数据』或『日线收盘快照』，必须在分析中明确说明基于哪一天的数据。\n"
        "3. 禁止引用『PE约XX倍』『RSI=XX』等具体数字，除非原始数据中明确给出且不为 null。\n"
        "4. 分析深度与数据完整度成正比——数据缺失越多，分析就该越保守、越短，明确告诉用户『等开盘后数据更新再看』。"
    )
    user_prompt = f"""请对我的股票持仓做一次全面深度分析。

{stock_ctx}

{market_ctx}

请按以下结构回答（小白友好，每节≤200字）：
1. 📊 **总体结论**（一句话，明确方向和置信度）
2. 🟢 **多头观点** + 🔴 **空头观点**（各2-3条，用数据说话）
3. 🛡️ **操作建议**（按持仓每只给 1-2 句，避免长篇）
4. 📌 **数据说明**（本次使用什么数据、有哪些缺失）"""

    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        result = gw.call_sync(
            user_prompt,
            system=system_prompt,
            model_tier="llm_heavy",
            user_id=uid,
            module="stock_analyze",
            max_tokens=2000,
        )
        if result.get("fallback"):
            return {"analysis": stock_ctx, "source": "data_only", "scan": scan, "data_quality": data_quality_str}
        reply = result["content"]
        try:
            from services.analysis_history import save_analysis
            save_analysis(uid, "deepseek", "DeepSeek V4", "stock", reply, direction="unknown")
        except Exception as e:
            print(f"[HISTORY] stock analyze 存档失败: {e}")
        return {
            "analysis": reply,
            "source": "ai",
            "scan": scan,
            "data_quality": data_quality_str,
            "is_trading_day": trading_day,
        }
    except Exception as e:
        print(f"[STOCK_ANALYZE] LLM Gateway fail: {e}")

    return {"analysis": stock_ctx, "source": "data_only", "scan": scan, "data_quality": data_quality_str}


# ---- 基金持仓 CRUD ----

@router.get("/api/fund-holdings")
def get_fund_holdings_api(userId: str = "default"):
    """获取基金持仓列表"""
    return {"holdings": load_fund_holdings(userId)}


@router.post("/api/fund-holdings")
def add_fund_holding_api(req: dict):
    """添加基金持仓"""
    code = req.get("code", "").strip()
    if not code:
        raise HTTPException(400, "基金代码不能为空")
    # 输入校验
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, f"基金代码格式错误：{code}（应为6位数字）")
    cost_nav = float(req.get("costNav", 0))
    shares = float(req.get("shares", 0))
    if cost_nav <= 0:
        raise HTTPException(400, "成本净值必须大于0")
    if shares <= 0:
        raise HTTPException(400, "持仓份额必须大于0")
    uid = req.get("userId", "default")
    return add_fund_holding(
        code=code,
        name=req.get("name", ""),
        cost_nav=cost_nav,
        shares=shares,
        note=req.get("note", ""),
        user_id=uid,
    )


@router.delete("/api/fund-holdings/{code}")
def remove_fund_holding_api(code: str, userId: str = "default"):
    """删除基金持仓"""
    return remove_fund_holding(code, userId)


@router.put("/api/fund-holdings/{code}")
def update_fund_holding_api(code: str, req: dict):
    """更新基金持仓信息"""
    uid = req.pop("userId", "default")
    return update_fund_holding(code, user_id=uid, **{
        k: v for k, v in req.items()
        if k in ("costNav", "shares", "note", "name")
    })


@router.get("/api/fund-holdings/realtime/{code}")
def get_fund_rt_api(code: str):
    """获取单只基金实时估值"""
    return get_fund_realtime(code)


@router.get("/api/fund-holdings/scan")
def scan_fund_holdings_api(userId: str = "default"):
    """扫描全基金持仓 — 估值 + 风控 + 异动"""
    return scan_all_fund_holdings(userId)


# ---- 基金深度分析 ----

@router.post("/api/fund-holdings/analyze")
async def analyze_fund_holdings(req: dict = {}):
    """DeepSeek 深度分析全基金持仓（7 Skill 框架）"""
    uid = req.get("userId", "default")
    scan = scan_all_fund_holdings(uid)
    if not scan.get("holdings"):
        return {"analysis": "暂无基金持仓，请先添加。", "source": "none"}

    lines = ["【基金持仓盯盘数据】"]
    for h in scan["holdings"]:
        rt = h.get("realtime") or {}
        risk = h.get("risk") or {}
        pnl_str = f"盈亏{h['pnlPct']:+.1f}%" if h.get("pnlPct") is not None else ""
        est_str = f"估算{rt.get('estRate', 'N/A')}%" if rt.get("estRate") is not None else ""
        lines.append(
            f"  {h['name']}({h['code']}) 估值¥{rt.get('estNav','N/A')} "
            f"{est_str} {pnl_str} "
            f"回撤={risk.get('maxDrawdown','N/A')} 波动={risk.get('volatility','N/A')} "
            f"连跌{risk.get('downDays',0)}天"
        )
    if scan.get("alerts"):
        lines.append("\n【基金异动信号】")
        for a in scan["alerts"]:
            lines.append(f"  [{a['level']}] {a.get('fund','')} {a['msg']}")

    fund_ctx = "\n".join(lines)
    market_ctx = _build_market_context()

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return {"analysis": fund_ctx, "source": "data_only"}

    system_prompt = _load_prompt_template()
    user_prompt = f"""请对我的基金持仓做一次全面深度分析。

{fund_ctx}

{market_ctx}

请按以下结构回答：
1. 📊 总体评估（一句话结论）
2. 逐只分析（每只基金：估值判断+回撤风险+配置建议）
3. 🛡️ 风控经理总结（组合风险+配置调整建议）"""

    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        result = gw.call_sync(
            user_prompt,
            system=system_prompt,
            model_tier="llm_heavy",
            user_id=uid,
            module="fund_analyze",
            max_tokens=2000,
        )
        if result.get("fallback"):
            return {"analysis": fund_ctx, "source": "data_only", "scan": scan}
        reply = result["content"]
        try:
            from services.analysis_history import save_analysis
            save_analysis(uid, "deepseek", "DeepSeek V4", "fund", reply, direction="unknown")
        except Exception as e:
            print(f"[HISTORY] fund analyze 存档失败: {e}")
        return {
            "analysis": reply,
            "source": "ai",
            "scan": scan,
        }
    except Exception as e:
        print(f"[FUND_ANALYZE] LLM Gateway fail: {e}")

    return {"analysis": fund_ctx, "source": "data_only", "scan": scan}


# ---- 持仓关联智能 ----

@router.get("/api/holding-intelligence/{code}")
def get_single_holding_intel(code: str):
    """获取单只持仓股票的关联智能（新闻+资金流+行业+解禁）"""
    result = {}
    try:
        result["news"] = get_stock_related_news(code)
    except Exception:
        result["news"] = []
    try:
        result["fund_flow"] = get_stock_fund_flow(code)
    except Exception:
        result["fund_flow"] = None
    try:
        result["industry"] = get_stock_industry(code)
    except Exception:
        result["industry"] = ""
    try:
        from services.market_factors import check_holding_unlock
        unlocks = check_holding_unlock([code])
        if unlocks:
            result["unlock_risk"] = unlocks[0].get("msg", "")
    except Exception:
        pass
    return result


@router.get("/api/holding-intelligence")
def holding_intel_api(userId: str = "default"):
    """全持仓智能扫描（个股新闻+资金流+行业+解禁）"""
    return scan_all_holding_intelligence(userId)


# ---- 数据缺口补齐 ----

@router.get("/api/stock/financials/{code}")
def get_stock_fin(code: str):
    """个股核心财务数据（ROE/EPS/营收增速）"""
    return get_stock_financials(code)


@router.get("/api/fund/holdings/{code}")
def get_fund_holdings_detail(code: str):
    """基金持仓明细（前10大重仓股+占净值比）"""
    return get_fund_holding_detail(code)
