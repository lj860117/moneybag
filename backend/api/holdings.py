"""
持仓管理 API（股票 + 基金 CRUD / 盯盘 / 分析 / 关联智能）
==========================================================
从 main.py 提取的 P2 路由。

Design doc: docs/design/12-framework-refactor.md §四
"""
import os
import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["持仓管理"])

from services.stock_monitor import (
    load_stock_holdings, add_stock_holding, remove_stock_holding,
    update_stock_holding, get_stock_realtime, scan_all_holdings,
    save_stock_holdings,
)
from services.fund_monitor import (
    load_fund_holdings, add_fund_holding, remove_fund_holding,
    update_fund_holding, get_fund_realtime, scan_all_fund_holdings,
    save_fund_holdings,
)
from services.holding_intelligence import (
    scan_all_holding_intelligence, build_holding_context,
    get_stock_news as get_stock_related_news, get_stock_fund_flow, get_stock_industry,
)
from services.data_layer import get_stock_financials, get_fund_holding_detail

from api.shared_helpers import _build_market_context, _load_prompt_template, _alert_cooldown

from config import DATA_DIR

_SCAN_CACHE_DIR = DATA_DIR / "_cache"


def _read_scan_cache(name: str, max_age_hours: float = 2.0):
    """读取文件缓存（由 cache_warmer 写入），未过期则返回 data，否则 None"""
    fp = _SCAN_CACHE_DIR / f"{name}.json"
    if not fp.exists():
        return None
    try:
        payload = json.loads(fp.read_text(encoding="utf-8"))
        expires_at = payload.get("expires_at", 0)
        # 优先用 expires_at；兜底用 cached_at + max_age_hours
        if expires_at:
            if time.time() > expires_at:
                return None
        else:
            cached_at_str = payload.get("cached_at", "")
            if cached_at_str:
                cached_ts = datetime.fromisoformat(cached_at_str).timestamp()
                if time.time() - cached_ts > max_age_hours * 3600:
                    return None
        return payload.get("data")
    except Exception:
        return None


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
    # 数据源校验：检查是否真实上市股票
    try:
        from services.tushare_data import validate_stock_code
        check = validate_stock_code(code)
        if check["valid"] is False:
            raise HTTPException(400, f"股票代码不存在：{code}（{check['reason']}）")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[HOLDINGS] 股票代码校验降级: {e}")
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


@router.post("/api/stock-holdings/sync")
def sync_stock_holdings_api(req: dict):
    """v9.5.13: 批量覆盖式同步股票持仓（前端 localStorage → 后端整体替换）

    入参: {userId, holdings: [{code, name, costPrice, shares, note?}, ...]}
    行为: 完全覆盖该用户原有的持仓文件（不做合并），适用于前端交易流水变更后整体推送
    返回: {ok, count, before, after}
    """
    uid = (req.get("userId") or "default").strip() or "default"
    raw = req.get("holdings") or []
    if not isinstance(raw, list):
        raise HTTPException(400, "holdings 必须是数组")

    before = len(load_stock_holdings(uid))
    cleaned = []
    for h in raw:
        try:
            code = str(h.get("code", "")).strip()
            if not code or not code.isdigit() or len(code) != 6:
                continue
            if code[0] not in ("0", "3", "6", "8"):
                continue
            shares = int(h.get("shares", 0) or 0)
            cost_price = float(h.get("costPrice", h.get("avgPrice", 0)) or 0)
            if shares <= 0 or cost_price <= 0:
                continue
            # v9.5.119: 拒绝名称含基金关键词的"股票"（前端旧版本误判）
            name = h.get("name") or ""
            _fund_kw = ["基金", "混合", "债券", "货币", "指数", "ETF", "QDII", "联接", "LOF", "商品"]
            if any(kw in name for kw in _fund_kw):
                continue
            cleaned.append({
                "code": code,
                "name": (h.get("name") or "")[:60],
                "costPrice": cost_price,
                "shares": shares,
                "note": (h.get("note") or "")[:120],
                "addedAt": h.get("addedAt") or datetime.now().isoformat(),
            })
        except Exception as e:
            print(f"[SYNC/STOCK] skip invalid {h}: {e}")

    save_stock_holdings(cleaned, uid)
    after = len(cleaned)
    print(f"[SYNC/STOCK] {uid}: {before} → {after}")
    return {"ok": True, "count": after, "before": before, "after": after}


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
def scan_holdings_api(userId: str = "default", force: bool = False):
    """扫描全持仓 — 实时行情 + 异动信号
    
    优先读 cache_warmer 写的文件缓存（收盘后预热），命中则秒返回。
    force=true 跳过缓存强制重算。
    """
    if not force:
        cached = _read_scan_cache(f"stock_scan_{userId}", max_age_hours=2.0)
        if cached is not None:
            cached["from_cache"] = True
            return cached
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
    # 数据源校验：检查是否真实基金
    try:
        from services.tushare_data import validate_fund_code
        check = validate_fund_code(code)
        if check["valid"] is False:
            raise HTTPException(400, f"基金代码不存在：{code}（{check['reason']}）")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[HOLDINGS] 基金代码校验降级: {e}")
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


@router.post("/api/fund-holdings/sync")
def sync_fund_holdings_api(req: dict):
    """v9.5.13: 批量覆盖式同步基金持仓（前端 localStorage → 后端整体替换）

    入参: {userId, holdings: [{code, name, costNav, shares, note?}, ...]}
    行为: 完全覆盖该用户原有的持仓文件（不做合并）
    返回: {ok, count, before, after}
    """
    uid = (req.get("userId") or "default").strip() or "default"
    raw = req.get("holdings") or []
    if not isinstance(raw, list):
        raise HTTPException(400, "holdings 必须是数组")

    before = len(load_fund_holdings(uid))
    cleaned = []
    for h in raw:
        try:
            code = str(h.get("code", "")).strip()
            # 基金代码 6 位数字（兼容部分场内基金，跳过纯文本如 "余额宝"）
            if not code or not code.isdigit() or len(code) != 6:
                continue
            shares = float(h.get("shares", 0) or 0)
            cost_nav = float(h.get("costNav", h.get("avgPrice", 0)) or 0)
            if shares <= 0 or cost_nav <= 0:
                continue
            cleaned.append({
                "code": code,
                "name": (h.get("name") or "")[:60],
                "costNav": cost_nav,
                "shares": shares,
                "note": (h.get("note") or "")[:120],
                "addedAt": h.get("addedAt") or datetime.now().isoformat(),
            })
        except Exception as e:
            print(f"[SYNC/FUND] skip invalid {h}: {e}")

    save_fund_holdings(cleaned, uid)
    after = len(cleaned)
    print(f"[SYNC/FUND] {uid}: {before} → {after}")
    return {"ok": True, "count": after, "before": before, "after": after}


@router.put("/api/fund-holdings/{code}")
def update_fund_holding_api(code: str, req: dict):
    """更新基金持仓信息"""
    uid = req.pop("userId", "default")
    return update_fund_holding(code, user_id=uid, **{
        k: v for k, v in req.items()
        if k in ("costNav", "shares", "note", "name")
    })


@router.get("/api/fund-holdings/detail/{code}")
def get_fund_holding_detail_api(code: str, userId: str = "default"):
    """v9.5.122: 持仓基金增强详情 — 通用信息 + 个人化持仓 + 诊断决策建议
    
    和选基榜单详情不同：这里是"我持有这只基金"的视角，重点是决策辅助。
    per-user 文件缓存 10h，后台 cache_warmer 预热。
    """
    import json as _json, time as _time, os as _os
    from pathlib import Path
    
    cache_dir = Path(_os.environ.get("DATA_DIR", "data")) / "_cache" / "holding_detail"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_fp = cache_dir / f"{code}_{userId}.json"
    
    # 读缓存（10h + stale 72h）
    try:
        if cache_fp.exists():
            payload = _json.loads(cache_fp.read_text(encoding="utf-8"))
            age = _time.time() - payload.get("created_at", 0)
            if age < 36000:
                return payload.get("data", {})
            if age < 259200 and payload.get("data"):
                import threading
                threading.Thread(target=_compute_holding_detail, args=(code, userId, str(cache_fp)), daemon=True).start()
                return payload.get("data", {})
    except Exception:
        pass
    
    result = _compute_holding_detail(code, userId, str(cache_fp))
    return result


def _compute_holding_detail(code: str, userId: str, cache_fp_str: str) -> dict:
    """计算持仓基金增强详情"""
    import json as _json, time as _time, os as _os
    from services.persistence import load_user
    from api.fund_detail import fund_detail
    from api.signals import _get_fund_nav_percentile, _fund_potential_signal, _fund_timing_label
    from services.industry_templates import get_fund_industry
    
    result = {"code": code}
    
    # ====== 1. 通用基金信息（经理/规模/费率/分红） ======
    try:
        base_detail = fund_detail(code)
        if isinstance(base_detail, dict):
            result["name"] = base_detail.get("name", "")
            result["fund_type"] = base_detail.get("fund_type", "")
            result["scale_billion"] = base_detail.get("scale_billion")
            result["company"] = base_detail.get("company", "")
            result["founded"] = base_detail.get("founded", "")
            result["manager"] = base_detail.get("manager")
            result["purchase"] = base_detail.get("purchase")
            result["dividend"] = base_detail.get("dividend")
            result["top_holdings"] = base_detail.get("top_holdings")
            result["fee_rate"] = (base_detail.get("purchase") or {}).get("fee_rate")
    except Exception:
        pass
    
    # ====== 2. 个人化持仓数据（成本/份额/盈亏） ======
    try:
        user = load_user(userId)
        portfolio = user.get("portfolio") or {}
        txns = portfolio.get("transactions") or []
        # 找到该基金的交易记录
        fund_txns = [t for t in txns if t.get("code") == code]
        if fund_txns:
            total_shares = sum(t.get("shares", 0) for t in fund_txns)
            total_cost = sum(t.get("amount", 0) for t in fund_txns)
            avg_cost = total_cost / total_shares if total_shares > 0 else 0
            result["my_holding"] = {
                "shares": round(total_shares, 4),
                "total_cost": round(total_cost, 2),
                "avg_cost": round(avg_cost, 4),
                "buy_dates": [t.get("date", "") for t in fund_txns],
                "txn_count": len(fund_txns),
            }
    except Exception:
        pass
    
    # ====== 3. 诊断数据（百分位/收益/潜力/行业） ======
    try:
        # 净值百分位
        nav_info = _get_fund_nav_percentile(code)
        if nav_info:
            result["nav_percentile"] = nav_info.get("nav_pct")
            result["nav_pct_label"] = nav_info.get("nav_pct_label", "")
            result["nav_cur"] = nav_info.get("nav_cur")
            result["nav_high"] = nav_info.get("nav_high")
            result["nav_low"] = nav_info.get("nav_low")
            result["nav_hist_count"] = nav_info.get("hist_count")
            # 从 nav_series 计算收益率
            nav_series = nav_info.get("nav_series") or []
            nav_cur = nav_info.get("nav_cur", 0)
            if nav_cur and nav_series:
                n = len(nav_series)
                if n > 60:
                    r3m = round((nav_cur - nav_series[n-60]) / nav_series[n-60] * 100, 2) if nav_series[n-60] else None
                    result["return_3m"] = r3m
                if n > 200:
                    r1y = round((nav_cur - nav_series[0]) / nav_series[0] * 100, 2) if nav_series[0] else None
                    result["return_1y"] = r1y
                elif n > 0:
                    r_all = round((nav_cur - nav_series[0]) / nav_series[0] * 100, 2) if nav_series[0] else None
                    result["return_since_track"] = r_all
            # v9.5.122: 如果 nav_series 为空（百分位只返回统计不返回序列），用 nav_low/nav_high 估算
            if not result.get("return_1y") and nav_cur:
                nav_low = nav_info.get("nav_low", 0)
                if nav_low and nav_low > 0:
                    result["return_from_low"] = round((nav_cur - nav_low) / nav_low * 100, 1)
    except Exception:
        pass
    
    # v9.5.122: 如果百分位没算出收益率，从 Tushare 基金净值接口拉
    if not result.get("return_1y") and not result.get("return_3m"):
        try:
            from services.tushare_data import is_configured, get_fund_nav as ts_nav
            if is_configured():
                ts = ts_nav(code, days=250)
                navs = ts.get("navs") or []
                if navs and len(navs) >= 2:
                    cur_nav = float(navs[-1].get("unit_nav", 0))
                    # 近3月（~60交易日）
                    if len(navs) > 60:
                        nav_3m = float(navs[-60].get("unit_nav", 0))
                        if nav_3m > 0:
                            result["return_3m"] = round((cur_nav - nav_3m) / nav_3m * 100, 2)
                    # 近1年（~250交易日）
                    if len(navs) > 200:
                        nav_1y = float(navs[0].get("unit_nav", 0))
                        if nav_1y > 0:
                            result["return_1y"] = round((cur_nav - nav_1y) / nav_1y * 100, 2)
        except Exception:
            pass
    
    # 行业标签
    try:
        name = result.get("name", "")
        ind = get_fund_industry(name)
        result["industry_tag"] = ind.get("tag", "其他") if ind else "其他"
    except Exception:
        pass
    
    # 时机评估
    try:
        result["timing_label"] = _fund_timing_label(result)
    except Exception:
        pass
    
    # 潜力信号
    try:
        potential = _fund_potential_signal(result)
        if potential:
            result["potential"] = potential
    except Exception:
        pass
    
    # ====== 3.5 走势预估（8维完整分解） ======
    try:
        from api.signals import _enrich_trend_forecast
        # 包装为列表调用, include_dimensions=True 获取完整8维分解
        _trend_input = [result]
        _enrich_trend_forecast(_trend_input, include_dimensions=True)
        # trend_* 字段已直接写入 result
    except Exception as e:
        print(f"[HOLDING_DETAIL] trend_forecast failed for {code}: {e}")
    
    # ====== 3.6 双因子智能定投建议 ======
    try:
        from services.signal import calc_smart_dca_v2
        dca = calc_smart_dca_v2(
            trend_direction=result.get("trend_direction", "flat"),
            trend_score=result.get("trend_score", 0),
            trend_confidence=result.get("trend_confidence", 55),
            nav_percentile=result.get("nav_percentile"),
            trend_conflict=result.get("trend_conflict", ""),
        )
        result["dca"] = dca  # 完整定投建议（含 factors 详情）
    except Exception as e:
        print(f"[HOLDING_DETAIL] smart_dca_v2 failed for {code}: {e}")
    
    # ====== 4. 决策建议 ======
    try:
        advices = []
        nav_pct = result.get("nav_percentile")
        scale = result.get("scale_billion")
        potential = result.get("potential")
        my = result.get("my_holding") or {}
        avg_cost = my.get("avg_cost", 0)
        nav_cur = result.get("nav_cur", 0)
        pnl_pct = round((nav_cur - avg_cost) / avg_cost * 100, 2) if avg_cost and nav_cur else None
        result["pnl_pct"] = pnl_pct
        
        # 规模风险
        if scale is not None:
            if scale < 1:
                advices.append({"type": "risk", "icon": "⚠️", "text": f"规模仅{scale}亿，清盘风险较高，建议谨慎持有"})
            elif scale < 2:
                advices.append({"type": "caution", "icon": "📏", "text": f"规模{scale}亿偏小，关注是否持续缩水"})
            elif scale > 100:
                advices.append({"type": "info", "icon": "🏛️", "text": f"规模{scale}亿，大船稳当但转向慢"})
        
        # 百分位建议
        if nav_pct is not None:
            if nav_pct >= 90:
                advices.append({"type": "sell", "icon": "🔴", "text": f"历史百分位{nav_pct}%极高位，均值回归风险大，可考虑分批减仓"})
            elif nav_pct >= 70:
                advices.append({"type": "caution", "icon": "🟡", "text": f"百分位{nav_pct}%偏高，不建议加仓，持有观察"})
            elif nav_pct <= 20:
                advices.append({"type": "buy", "icon": "🟢", "text": f"百分位{nav_pct}%历史低位，有安全边际，可分批加仓"})
            elif nav_pct <= 40:
                advices.append({"type": "buy", "icon": "💚", "text": f"百分位{nav_pct}%中低位，估值合理偏低"})
        
        # 潜力信号
        if potential:
            level = potential.get("level", "")
            if level == "high":
                advices.append({"type": "buy", "icon": "🚀", "text": f"强潜力信号（{potential.get('signal_flags','')}），动量持续"})
            elif level == "mid":
                advices.append({"type": "hold", "icon": "📈", "text": f"中等潜力（{potential.get('signal_flags','')}）"})
        
        # 盈亏建议
        if pnl_pct is not None:
            if pnl_pct > 80:
                advices.append({"type": "sell", "icon": "🎯", "text": f"已盈利{pnl_pct:.0f}%，可考虑止盈部分锁定收益"})
            elif pnl_pct > 50:
                advices.append({"type": "caution", "icon": "💰", "text": f"盈利{pnl_pct:.0f}%，设好止盈线（如回撤10%减半仓）"})
            elif pnl_pct < -20:
                advices.append({"type": "risk", "icon": "📉", "text": f"亏损{pnl_pct:.0f}%，评估基本面是否恶化，否则可逢低补仓"})
        
        # 经理换届风险
        manager = result.get("manager") or {}
        if manager.get("tenure_years") and manager["tenure_years"] < 1:
            advices.append({"type": "risk", "icon": "👤", "text": f"基金经理{manager.get('name','')}任职仅{manager['tenure_years']:.1f}年，新任经理风格未验证"})
        
        # 组合重叠提示
        try:
            from services.fund_monitor import load_fund_holdings
            my_funds = load_fund_holdings(userId) or []
            my_industry = result.get("industry_tag", "其他")
            same_industry = [f.get("name","") for f in my_funds if get_fund_industry(f.get("name","")).get("tag","") == my_industry and f.get("code") != code]
            if same_industry and my_industry != "其他":
                advices.append({"type": "overlap", "icon": "🔄", "text": f"与{', '.join(same_industry[:2])}同属「{my_industry}」赛道，可考虑合并精简"})
        except Exception:
            pass
        
        result["advices"] = advices
        
        # 综合操作方向
        buy_signals = sum(1 for a in advices if a["type"] == "buy")
        sell_signals = sum(1 for a in advices if a["type"] in ("sell", "risk"))
        if sell_signals > buy_signals:
            result["action_direction"] = "减仓观望"
        elif buy_signals > sell_signals:
            result["action_direction"] = "适量加仓"
        else:
            result["action_direction"] = "持有观察"
    except Exception:
        result["advices"] = []
        result["action_direction"] = "持有观察"
    
    # 写缓存
    try:
        import json as _j
        with open(cache_fp_str, "w", encoding="utf-8") as f:
            _j.dump({"data": result, "created_at": _time.time()}, f, ensure_ascii=False, default=str)
    except Exception:
        pass
    
    return result


@router.get("/api/fund-holdings/realtime/{code}")
def get_fund_rt_api(code: str):
    """获取单只基金实时估值"""
    return get_fund_realtime(code)


@router.get("/api/fund-holdings/scan")
def scan_fund_holdings_api(userId: str = "default", force: bool = False):
    """扫描全基金持仓 — 估值 + 风控 + 异动
    
    优先读 cache_warmer 写的文件缓存（收盘后预热），命中则秒返回。
    force=true 跳过缓存强制重算。
    """
    if not force:
        cached = _read_scan_cache(f"fund_scan_{userId}", max_age_hours=2.0)
        if cached is not None:
            cached["from_cache"] = True
            return cached
    return scan_all_fund_holdings(userId)


# ═══ v9.5.123: 持仓异动预警 + 行为偏差检测 + 关联暴露 ═══

@router.get("/api/fund-holdings/alerts")
def holding_alerts_api(userId: str = "default"):
    """v9.5.123: AI独有价值 — 持仓异动预警 + 行为偏差检测 + 重仓股重叠预警
    
    3大模块：
    1. 异动预警：持仓基金日跌超同类均值→预警
    2. 行为偏差：检测追涨杀跌/频繁交易/集中度过高
    3. 关联暴露：多只基金重仓同一股票→实际暴露度超标
    
    per-user 2h 文件缓存，cache_warmer 盘后预热。
    """
    import json as _json, time as _time, os as _os
    from pathlib import Path
    
    cache_dir = Path(_os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_fp = cache_dir / f"holding_alerts_{userId}.json"
    
    # 读缓存 (2h)
    try:
        if cache_fp.exists():
            payload = _json.loads(cache_fp.read_text(encoding="utf-8"))
            if _time.time() - payload.get("created_at", 0) < 7200:
                return payload.get("data", {})
    except Exception:
        pass
    
    result = _compute_holding_alerts(userId)
    
    # 写缓存
    try:
        cache_fp.write_text(_json.dumps({"data": result, "created_at": _time.time()}, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    return result


def _compute_holding_alerts(userId: str) -> dict:
    """计算持仓三大预警"""
    from services.persistence import load_user
    from services.fund_monitor import load_fund_holdings, get_fund_realtime
    
    alerts = []  # 异动预警
    behavior_warnings = []  # 行为偏差
    overlap_warnings = []  # 关联暴露
    
    # ── 1. 异动预警：持仓基金 vs 同类均值 ──
    try:
        my_funds = load_fund_holdings(userId) or []
        for f in my_funds[:20]:  # 最多检测20只
            code = f.get("code", "")
            if not code:
                continue
            rt = get_fund_realtime(code)
            if not rt:
                continue
            est_rate = rt.get("estRate") or rt.get("nav_change_pct")
            if est_rate is None:
                continue
            # 异动阈值：日跌>2% 或 日涨>3%
            if est_rate < -2.0:
                alerts.append({
                    "type": "drop_anomaly",
                    "level": "warning",
                    "code": code,
                    "name": f.get("name", code),
                    "text": f"📉 {f.get('name', code)} 今日跌幅 {est_rate:.2f}%，超过正常波动范围",
                    "value": est_rate,
                    "action": "关注止损线,评估基本面是否恶化",
                })
            elif est_rate > 3.0:
                alerts.append({
                    "type": "surge_anomaly",
                    "level": "info",
                    "code": code,
                    "name": f.get("name", code),
                    "text": f"🔥 {f.get('name', code)} 今日涨幅 +{est_rate:.2f}%，关注是否需要止盈",
                    "value": est_rate,
                    "action": "设好止盈线,涨多了适当减仓锁利",
                })
    except Exception as e:
        print(f"[ALERTS] anomaly detection failed: {e}")
    
    # ── 2. 行为偏差检测：分析交易记录 ──
    try:
        user = load_user(userId)
        portfolio = user.get("portfolio") or {}
        txns = portfolio.get("transactions") or []
        
        if txns:
            from datetime import datetime, timedelta
            now = datetime.now()
            # 近3个月交易
            cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
            recent_txns = [t for t in txns if (t.get("date", "") or "") >= cutoff]
            
            # 检测1: 频繁交易（月均>5次）
            if len(recent_txns) > 15:
                monthly_avg = round(len(recent_txns) / 3, 1)
                behavior_warnings.append({
                    "type": "frequent_trading",
                    "level": "warning",
                    "text": f"⚡ 近3月交易{len(recent_txns)}次(月均{monthly_avg}次)，频繁交易会增加成本且难跑赢定投",
                    "action": "建议设定交易纪律:每月最多操作2次",
                })
            
            # 检测2: 追涨杀跌（高位买+低位卖）
            buy_at_high = 0
            sell_at_low = 0
            for t in recent_txns:
                action = t.get("action", "")
                nav_pct = t.get("nav_percentile")  # 如果交易记录有百分位
                if nav_pct is None:
                    continue
                if action == "buy" and nav_pct > 75:
                    buy_at_high += 1
                elif action == "sell" and nav_pct < 30:
                    sell_at_low += 1
            
            if buy_at_high >= 2:
                behavior_warnings.append({
                    "type": "chasing_high",
                    "level": "danger",
                    "text": f"🎢 近3月有{buy_at_high}次在历史高位(>75%百分位)买入，存在追涨风险",
                    "action": "高位不加仓是纪律底线,用定投替代主观择时",
                })
            if sell_at_low >= 2:
                behavior_warnings.append({
                    "type": "panic_sell",
                    "level": "danger",
                    "text": f"😱 近3月有{sell_at_low}次在历史低位(<30%百分位)卖出，可能是恐慌割肉",
                    "action": "低位正是加仓时机而非卖出,设好心理止损线后坚持",
                })
            
            # 检测3: 单只集中度过高
            from collections import Counter
            code_counts = Counter(t.get("code", "") for t in txns if t.get("action") == "buy")
            total_buy = sum(code_counts.values())
            if total_buy > 0:
                for code, cnt in code_counts.most_common(1):
                    pct = cnt / total_buy * 100
                    if pct > 50:
                        name = next((t.get("name", code) for t in txns if t.get("code") == code), code)
                        behavior_warnings.append({
                            "type": "concentration",
                            "level": "warning",
                            "text": f"🎯 {name} 占你买入次数的{pct:.0f}%，单只集中度过高",
                            "action": "建议单只基金仓位不超过组合的30%,分散降风险",
                        })
    except Exception as e:
        print(f"[ALERTS] behavior detection failed: {e}")
    
    # ── 3. 关联暴露预警：重仓股重叠 ──
    try:
        from api.fund_detail import fund_detail
        my_funds = load_fund_holdings(userId) or []
        # 收集所有持仓基金的重仓股
        all_top_stocks = {}  # {stock_name: [fund_names]}
        for f in my_funds[:10]:  # 最多检测10只
            code = f.get("code", "")
            fund_name = f.get("name", code)
            if not code:
                continue
            try:
                detail = fund_detail(code)
                if not detail:
                    continue
                top_holdings = detail.get("top_holdings") or []
                for stock in top_holdings[:5]:  # 取前5大重仓
                    stock_name = stock.get("name", "")
                    if stock_name:
                        if stock_name not in all_top_stocks:
                            all_top_stocks[stock_name] = []
                        all_top_stocks[stock_name].append(fund_name)
            except Exception:
                continue
        
        # 找出被多只基金同时重仓的股票
        for stock_name, fund_names in all_top_stocks.items():
            if len(fund_names) >= 3:  # 3只以上基金重仓同一只股票
                overlap_warnings.append({
                    "type": "stock_overlap",
                    "level": "warning",
                    "stock": stock_name,
                    "funds": fund_names[:4],
                    "text": f"🔗 {stock_name} 被你持有的 {len(fund_names)} 只基金同时重仓({', '.join(fund_names[:3])}{'等' if len(fund_names)>3 else ''})",
                    "action": f"实际对{stock_name}的暴露度远超表面,如果该股暴跌将连带多只基金亏损",
                })
            elif len(fund_names) >= 2:
                overlap_warnings.append({
                    "type": "stock_overlap_mild",
                    "level": "info",
                    "stock": stock_name,
                    "funds": fund_names,
                    "text": f"📎 {stock_name} 被 {', '.join(fund_names)} 共同重仓",
                    "action": "轻度重叠,暂无需担心",
                })
    except Exception as e:
        print(f"[ALERTS] overlap detection failed: {e}")
    
    return {
        "alerts": alerts,
        "behavior_warnings": behavior_warnings,
        "overlap_warnings": [w for w in overlap_warnings if w.get("level") != "info"][:5],  # 只返回warning级别的前5个
        "total_issues": len(alerts) + len(behavior_warnings) + len([w for w in overlap_warnings if w.get("level") != "info"]),
        "summary": _build_alert_summary(alerts, behavior_warnings, overlap_warnings),
    }


def _build_alert_summary(alerts, behavior, overlap) -> str:
    """一句话总结预警状态"""
    issues = []
    if alerts:
        issues.append(f"{len(alerts)}个异动")
    if behavior:
        issues.append(f"{len(behavior)}个行为偏差")
    serious_overlap = [w for w in overlap if w.get("level") != "info"]
    if serious_overlap:
        issues.append(f"{len(serious_overlap)}个重仓重叠")
    if not issues:
        return "✅ 当前无需关注的风险"
    return f"⚠️ 发现 {'、'.join(issues)}，建议关注"


@router.get("/api/fund-holdings/enrich")
def enrich_fund_holdings_api(userId: str = "default"):
    """v9.5.121: 为持仓基金返回丰富诊断数据（评分/百分位/潜力/行业/收益率/加减仓建议）
    
    用于选基-持仓视图，替代原来的 fund-screen top_n=2000 慢查询。
    读 per-user 文件缓存（2h），后台 cache_warmer 预热。
    """
    import json as _json, time as _time
    from pathlib import Path
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_fp = cache_dir / f"holdings_enrich_{userId}.json"
    
    # 读缓存（2h）
    try:
        if cache_fp.exists():
            payload = _json.loads(cache_fp.read_text(encoding="utf-8"))
            if _time.time() < payload.get("expires_at", 0):
                return payload.get("data", {})
    except Exception:
        pass
    
    # 实时计算
    result = _compute_holdings_enrich(userId)
    
    # 写缓存
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        # v9.5.121: TTL 10h（cache_warmer 每天早盘+收盘刷新）
        cache_fp.write_text(_json.dumps({"data": result, "expires_at": _time.time() + 36000, "created_at": _time.time()}, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    
    return result


def _compute_holdings_enrich(userId: str) -> dict:
    """计算持仓基金的丰富诊断数据（限制总耗时，避免数据源不可用时无限等待）"""
    import json as _json, time as _time
    import concurrent.futures
    from services.fund_monitor import load_fund_holdings
    from services.persistence import load_user
    
    # 获取持仓基金列表
    my_funds = load_fund_holdings(userId) or []
    
    # 也从 V4 transactions 获取
    user = load_user(userId)
    portfolio = user.get("portfolio") or {}
    txns = portfolio.get("transactions") or []
    # 聚合 V4 交易的基金 codes
    txn_codes = set()
    for t in txns:
        code = t.get("code", "")
        if code and len(code) == 6 and code.isdigit():
            txn_codes.add(code)
    
    # 合并所有持仓代码
    all_codes = set()
    for f in my_funds:
        code = f.get("code", "")
        if code and len(code) == 6:
            all_codes.add(code)
    all_codes.update(txn_codes)
    
    if not all_codes:
        return {"funds": [], "count": 0}
    
    # 为每只基金计算评分/百分位/行业/潜力
    from api.signals import _get_fund_nav_percentile, _fund_potential_signal, _fund_timing_label
    from services.industry_templates import get_fund_industry
    # returns 从 nav_percentile 返回的 nav_series 计算，不需要额外导入
    
    # 先构建基本信息（不需要网络请求，瞬间完成）
    fund_basics = []
    for code in sorted(all_codes):
        name = ""
        for f in my_funds:
            if f.get("code") == code:
                name = f.get("name", "")
                break
        if not name:
            for t in txns:
                if t.get("code") == code:
                    name = t.get("name", "")
                    break
        ind = get_fund_industry(name)
        fund_basics.append({
            "code": code,
            "name": name,
            "industry_tag": ind.get("tag", "其他") if ind else "其他"
        })
    
    # 用线程池并发为每只基金拉 nav_percentile + returns（单只最多3秒）
    def _enrich_one(info):
        code = info["code"]
        try:
            nav_info = _get_fund_nav_percentile(code)
            if nav_info:
                info["nav_percentile"] = nav_info.get("nav_pct")
                info["nav_pct_label"] = nav_info.get("nav_pct_label", "")
                # v9.5.121: 也返回 nav_cur 供前端做净值 fallback（realtime 不可用时）
                if nav_info.get("nav_cur"):
                    info["nav_cur"] = nav_info["nav_cur"]
        except Exception:
            pass
        # 收益率：从 nav_percentile 返回的 nav_cur + nav_series 计算
        # （简化版，避免额外 HTTP 请求）
        try:
            if nav_info:
                nav_cur = nav_info.get("nav_cur", 0)
                nav_series = nav_info.get("nav_series") or []
                if nav_cur > 0 and nav_series:
                    # 找 ~3月前 和 ~1年前的净值
                    n = len(nav_series)
                    nav_3m = nav_series[max(0, n - 60)] if n > 60 else nav_series[0]  # 约3个月
                    nav_1y = nav_series[0] if n >= 200 else (nav_series[max(0, n - 200)] if n > 200 else None)
                    returns = {}
                    if nav_3m and nav_3m > 0:
                        returns["3m"] = round((nav_cur - nav_3m) / nav_3m * 100, 2)
                    if nav_1y and nav_1y > 0:
                        returns["1y"] = round((nav_cur - nav_1y) / nav_1y * 100, 2)
                    if returns:
                        info["returns"] = returns
        except Exception:
            pass
        # 时机 + 潜力 + 评分
        info["timing_label"] = _fund_timing_label(info)
        try:
            potential = _fund_potential_signal(info)
            if potential:
                info["potential"] = potential
        except Exception:
            pass
        # 简化评分
        score = 0
        r = info.get("returns") or {}
        if r.get("1y") and r["1y"] > 0:
            score += min(r["1y"] * 0.5, 25)
        if r.get("3m") and r["3m"] > 0:
            score += min(r["3m"] * 0.3, 15)
        nav_pct = info.get("nav_percentile")
        if nav_pct is not None:
            if nav_pct <= 30:
                score += 10
            elif nav_pct >= 80:
                score -= 5
        info["score"] = round(score)
        return info
    
    # 总超时15秒（周末数据源不可用时快速返回已有数据）
    enriched = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_enrich_one, fb): fb for fb in fund_basics}
            done, _ = concurrent.futures.wait(futures, timeout=15)
            for f in done:
                try:
                    enriched.append(f.result())
                except Exception:
                    enriched.append(futures[f])
            # 超时未完成的直接用基础信息
            for f in futures:
                if f not in done:
                    enriched.append(futures[f])
    except Exception:
        enriched = fund_basics
    
    enriched.sort(key=lambda x: x.get("code", ""))
    
    # v9.5.123: 为每只持仓基金添加8维走势预估
    try:
        from api.signals import _enrich_trend_forecast
        _enrich_trend_forecast(enriched, include_dimensions=False)
    except Exception as e:
        print(f"[HOLDINGS_ENRICH] trend_forecast failed: {e}")
    
    # v9.5.123: 双因子智能定投建议
    try:
        from services.signal import calc_smart_dca_v2
        for f in enriched:
            if f.get("trend_direction"):
                dca = calc_smart_dca_v2(
                    trend_direction=f.get("trend_direction", "flat"),
                    trend_score=f.get("trend_score", 0),
                    trend_confidence=f.get("trend_confidence", 55),
                    nav_percentile=f.get("nav_percentile"),
                    trend_conflict=f.get("trend_conflict", ""),
                )
                f["dca_multiplier"] = dca["multiplier"]
                f["dca_label"] = dca["label"]
                f["dca_advice"] = dca["advice"]
    except Exception as e:
        print(f"[HOLDINGS_ENRICH] smart_dca_v2 failed: {e}")
    
    return {"funds": enriched, "count": len(enriched)}


# ---- v9.5.121: AI 持仓深度体检（per-user 24h 缓存，前端秒开） ----

@router.get("/api/fund-holdings/ai-checkup")
def ai_checkup_api(userId: str = "default"):
    """AI 深度体检：DeepSeek Pro 对持仓组合做全维度分析。
    
    维度：风格暴露/集中度风险/相关性/再平衡建议/夏普预估/市场匹配度
    缓存：per-user 24h文件缓存，cache_warmer 预热。
    降级：Pro → Flash → 纯数据兜底。
    """
    import json as _json, time as _time
    from pathlib import Path
    
    cache_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_fp = cache_dir / f"ai_checkup_{userId}.json"
    
    # 读缓存（24h）
    try:
        if cache_fp.exists():
            payload = _json.loads(cache_fp.read_text(encoding="utf-8"))
            if _time.time() < payload.get("expires_at", 0):
                data = payload.get("data", {})
                data["from_cache"] = True
                return data
    except Exception:
        pass
    
    # 实时计算
    result = _compute_ai_checkup(userId)
    
    # 写缓存（24h）
    try:
        cache_fp.write_text(_json.dumps({"data": result, "expires_at": _time.time() + 86400, "created_at": _time.time()}, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    
    return result


def _compute_ai_checkup(userId: str) -> dict:
    """执行 AI 深度体检"""
    import time as _time
    
    # 1. 收集数据（从 enrich 接口获取已有数据）
    enrich_data = _compute_holdings_enrich(userId)
    funds = enrich_data.get("funds", [])
    if not funds:
        return {"status": "no_holdings", "analysis": "暂无基金持仓数据", "source": "none"}
    
    # 2. 构建分析数据摘要
    fund_lines = []
    industries = {}
    total_nav_pct = []
    for f in funds:
        nav_pct = f.get("nav_percentile")
        ind = f.get("industry_tag", "其他")
        r = f.get("returns") or {}
        potential = f.get("potential")
        
        line = f"- {f.get('name','?')}({f.get('code','')}): "
        parts = []
        if nav_pct is not None:
            parts.append(f"净值百分位{nav_pct}%")
            total_nav_pct.append(nav_pct)
        if ind and ind != "其他":
            parts.append(f"行业={ind}")
        if r.get("1y") is not None:
            parts.append(f"近1年{r['1y']:+.1f}%")
        if r.get("3m") is not None:
            parts.append(f"近3月{r['3m']:+.1f}%")
        if potential:
            parts.append(f"潜力={potential.get('level','')}")
        line += ", ".join(parts) if parts else "数据不足"
        fund_lines.append(line)
        
        industries[ind] = industries.get(ind, 0) + 1
    
    # 行业分布
    ind_lines = [f"  {k}: {v}只 ({v/len(funds)*100:.0f}%)" for k, v in sorted(industries.items(), key=lambda x: -x[1])]
    
    # 集中度
    top_ind = max(industries.items(), key=lambda x: x[1]) if industries else ("", 0)
    concentration = top_ind[1] / len(funds) * 100 if funds else 0
    
    # 平均百分位
    avg_pct = sum(total_nav_pct) / len(total_nav_pct) if total_nav_pct else None
    
    # 3. 获取市场环境数据
    market_ctx = _build_market_context()
    
    # 4. 构造 Prompt
    data_section = f"""【持仓组合数据】共 {len(funds)} 只基金
{chr(10).join(fund_lines)}

【行业分布】
{chr(10).join(ind_lines)}
- 最大集中度: {top_ind[0]} {concentration:.0f}%
- 平均净值百分位: {f'{avg_pct:.0f}%' if avg_pct else '数据不足'}

{market_ctx}"""

    system_prompt = """你是专业的基金投资组合分析师。请对用户的持仓组合做一次全面深度体检。

规则：
1. 绝不预测价格，绝不给出具体仓位百分比或买卖金额
2. 不提"建议买入X%"这类话，只给方向性建议
3. 用数据说话，引用具体百分位/集中度/收益率
4. 如果数据不足无法判断，明确说"数据不足，无法判断"
5. 回答务必简洁精炼，每个维度 1-3 句话"""

    user_prompt = f"""{data_section}

请按以下6个维度逐一分析：

1. ⚖️ 风格暴露：成长/价值偏向，大盘/中小盘偏向
2. 🎯 集中度风险：行业集中度是否过高，赛道重叠情况
3. 📈 估值位置：基于百分位的整体高/低判断，哪些处于极端位置
4. 🔄 再平衡方向：哪些方向可适当加配/减配（不给具体比例）
5. 🌍 市场匹配度：当前持仓 vs 宏观环境是否顺风/逆风
6. 💡 一句话总结：当前组合最该关注的1件事"""

    # 5. 调 LLM（Pro → Flash 降级）
    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()
        
        # 先用 Pro
        result = gw.call_sync(
            user_prompt,
            system=system_prompt,
            model_tier="llm_heavy",
            user_id=userId,
            module="ai_checkup",
            max_tokens=1500,
        )
        
        if result.get("content"):
            return {
                "status": "ok",
                "analysis": result["content"],
                "source": "ai_pro",
                "model": result.get("model", "deepseek-v4-pro"),
                "dimensions": {
                    "fund_count": len(funds),
                    "avg_nav_pct": round(avg_pct) if avg_pct else None,
                    "concentration": round(concentration),
                    "top_industry": top_ind[0],
                    "industries": len(industries),
                },
                "generated_at": _time.strftime("%Y-%m-%d %H:%M"),
            }
        
        # Pro 失败，降级 Flash
        result = gw.call_sync(
            user_prompt,
            system=system_prompt,
            model_tier="llm_light",
            user_id=userId,
            module="ai_checkup_fallback",
            max_tokens=1200,
        )
        if result.get("content"):
            return {
                "status": "ok",
                "analysis": result["content"],
                "source": "ai_flash",
                "model": result.get("model", "deepseek-v4-flash"),
                "dimensions": {
                    "fund_count": len(funds),
                    "avg_nav_pct": round(avg_pct) if avg_pct else None,
                    "concentration": round(concentration),
                    "top_industry": top_ind[0],
                    "industries": len(industries),
                },
                "generated_at": _time.strftime("%Y-%m-%d %H:%M"),
            }
    except Exception as e:
        print(f"[AI_CHECKUP] LLM failed: {e}")
    
    # 兜底：纯数据摘要
    return {
        "status": "data_only",
        "analysis": f"🤖 AI 分析暂不可用，以下为数据摘要：\n\n"
                    f"持仓 {len(funds)} 只基金，覆盖 {len(industries)} 个行业\n"
                    f"最大集中度：{top_ind[0]} {concentration:.0f}%\n"
                    f"{'平均百分位：' + str(round(avg_pct)) + '% — ' + ('⚠️ 整体偏高位' if avg_pct and avg_pct > 70 else '✅ 位置适中' if avg_pct else '') if avg_pct else ''}",
        "source": "data_fallback",
        "dimensions": {
            "fund_count": len(funds),
            "avg_nav_pct": round(avg_pct) if avg_pct else None,
            "concentration": round(concentration),
            "top_industry": top_ind[0],
            "industries": len(industries),
        },
        "generated_at": _time.strftime("%Y-%m-%d %H:%M"),
    }


# ---- 基金深度分析（旧版 POST 接口，保留兼容） ----

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


# ═══ v9.5.123 Sprint 4: 护城河 — DNA画像 + 决策复盘 + 自进化 ═══

@router.get("/api/investor/dna")
def investor_dna_api(userId: str = "default"):
    """投资DNA画像: 风险偏好/持有风格/擅长赛道/行为弱点/回撤容忍度"""
    import json as _json, time as _time
    
    cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / f"investor_dna_{userId}.json"
    # 缓存7天(画像不会频繁变化)
    if cache_fp.exists():
        try:
            data = _json.loads(cache_fp.read_text(encoding="utf-8"))
            if _time.time() - datetime.fromisoformat(data.get("generated_at", "2020-01-01")).timestamp() < 604800:
                return data
        except Exception:
            pass
    
    from services.investor_dna import generate_investor_dna
    return generate_investor_dna(userId)


@router.get("/api/investor/sell-reviews")
def sell_reviews_api(userId: str = "default"):
    """决策复盘: 卖出后追踪净值变化,判断决策质量"""
    from services.investor_dna import track_sell_decisions
    return track_sell_decisions(userId)


@router.get("/api/investor/weight-evolution")
def weight_evolution_api():
    """AI权重自进化: 根据回测准确率动态调整8维权重"""
    from services.investor_dna import evolve_weights
    return evolve_weights()


# ═══ v9.5.123 Sprint 3: 家庭合并视图 + 目标追踪 ═══

@router.get("/api/family/overview")
def family_overview_api():
    """家庭合并持仓全景视图: 行业分布/重叠/互补度/风险提示"""
    import json as _json, time as _time
    from pathlib import Path
    
    cache_fp = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "family_view.json"
    # 读缓存(6h)
    if cache_fp.exists():
        try:
            payload = _json.loads(cache_fp.read_text(encoding="utf-8"))
            if _time.time() - payload.get("created_at", 0) < 21600:
                return payload.get("data", {})
        except Exception:
            pass
    # 实时生成
    try:
        from scripts.monthly_report import generate_family_view
        return generate_family_view()
    except Exception as e:
        return {"error": str(e)}


@router.get("/api/family/monthly-report")
def family_monthly_report_api():
    """获取最近一期月度家庭报告"""
    import json as _json
    from pathlib import Path
    
    report_dir = Path(os.environ.get("DATA_DIR", "data")) / "_cache" / "monthly_reports"
    if not report_dir.exists():
        return {"error": "暂无月报, 下月1号自动生成"}
    # 找最新的报告
    reports = sorted(report_dir.glob("report_*.json"), reverse=True)
    if reports:
        try:
            return _json.loads(reports[0].read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"error": "暂无月报"}


@router.post("/api/goals/set")
# TODO(2026-08-30) 坏味道：`req: dict = {}` 用可变对象作默认参数。
#   Python 的默认值在**函数定义时**创建并跨调用共享，一旦有代码就地修改 req
#   就会污染后续所有请求。本次未改，因为改签名（如 `req: dict | None = None`
#   或改用 Pydantic model）可能影响 FastAPI 的请求体解析行为，需要单独验证。
#   本文件内多处端点都是这个写法，建议统一整改。
def set_financial_goal(req: dict = {}):
    """设定财务目标(如3年攒50万)
    
    请求: {userId, name: "装修费", target_amount: 200000, deadline: "2029-01", monthly_save: 5000}
    """
    from services.persistence import load_user, save_user, user_write_lock
    
    uid = req.get("userId", "default")
    goal = {
        "name": req.get("name", "目标"),
        "target_amount": float(req.get("target_amount", 0)),
        "deadline": req.get("deadline", ""),
        "monthly_save": float(req.get("monthly_save", 0)),
        "created_at": datetime.now().isoformat(),
    }
    
    if goal["target_amount"] <= 0:
        return {"ok": False, "error": "目标金额必须大于0"}
    
    # FIX 2026-08-30: 原来这里是 save_user(uid, user) —— 传了 2 个参数，
    # 而 persistence.save_user 的签名是 save_user(data)，只收 1 个。
    # 结果这个端点自上线起每次调用必抛
    # `TypeError: save_user() takes 1 positional argument but 2 were given` → 500，
    # 从未成功保存过一次。因为 save_user 是函数内 late import，静态检查也扫不出来。
    # 同时补上 user_write_lock：这是标准 RMW 临界区（load → 改 portfolio → save），
    # 不加锁的话并发请求会互相覆盖（uvicorn 把 sync 端点跑在线程池里，是真并发）。
    with user_write_lock(uid) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(uid)
        portfolio = user.get("portfolio") or {}
        goals = portfolio.get("financial_goals") or []
        goals.append(goal)
        portfolio["financial_goals"] = goals[-5:]  # 最多5个目标
        user["portfolio"] = portfolio
        save_user(user)
        total_goals = len(portfolio["financial_goals"])
    
    return {"ok": True, "goal": goal, "total_goals": total_goals}


@router.get("/api/goals")
def get_financial_goals(userId: str = "default"):
    """获取用户财务目标及进度"""
    from services.persistence import load_user
    
    user = load_user(userId)
    portfolio = user.get("portfolio") or {}
    goals = portfolio.get("financial_goals") or []
    
    # 计算每个目标的进度
    today = date.today()
    for g in goals:
        target = g.get("target_amount", 0)
        monthly = g.get("monthly_save", 0)
        created = g.get("created_at", "")[:10]
        if created and monthly > 0 and target > 0:
            try:
                start = datetime.strptime(created, "%Y-%m-%d").date()
                months_elapsed = (today.year - start.year) * 12 + (today.month - start.month)
                saved = monthly * months_elapsed
                g["progress_pct"] = round(min(saved / target * 100, 100), 1)
                g["saved_estimate"] = round(saved)
                # 预计达成时间
                months_needed = int(target / monthly) if monthly > 0 else 999
                finish_date = start + timedelta(days=months_needed * 30)
                g["estimated_finish"] = finish_date.strftime("%Y-%m")
            except Exception:
                g["progress_pct"] = 0
    
    return {"goals": goals, "total": len(goals)}


# ═══ v9.5.123 Sprint 2: 止盈止损纪律线 ═══

@router.post("/api/fund-holdings/discipline")
# TODO(2026-08-30) 坏味道：`req: dict = {}` 用可变对象作默认参数（同
#   set_financial_goal，原因见那里的注释）。本次未改，需单独验证 FastAPI 解析行为。
def set_discipline_line(req: dict = {}):
    """设定基金止盈/止损纪律线
    
    请求: {userId, code, take_profit: 30, stop_loss: -20}
    - take_profit: 盈利百分比(正数), 到达后推送止盈提醒
    - stop_loss: 亏损百分比(负数), 到达后推送止损提醒
    - 设为 null/0 = 取消该线
    """
    from services.persistence import load_user, save_user, user_write_lock
    
    uid = req.get("userId", "default")
    code = req.get("code", "")
    take_profit = req.get("take_profit")
    stop_loss = req.get("stop_loss")
    
    if not code:
        return {"ok": False, "error": "缺少基金代码"}
    
    # FIX 2026-08-30: 同 set_financial_goal —— 原来是 save_user(uid, user)，
    # 多传一个参数导致 TypeError → 该端点自上线起每次必 500，纪律线从未保存成功。
    # 一并补上 user_write_lock 保护 RMW 临界区。
    with user_write_lock(uid) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(uid)
        portfolio = user.get("portfolio") or {}
        lines = portfolio.get("discipline_lines") or {}
        
        if take_profit or stop_loss:
            lines[code] = {}
            if take_profit and take_profit > 0:
                lines[code]["take_profit"] = float(take_profit)
            if stop_loss and stop_loss < 0:
                lines[code]["stop_loss"] = float(stop_loss)
        else:
            # 取消纪律线
            lines.pop(code, None)
        
        portfolio["discipline_lines"] = lines
        user["portfolio"] = portfolio
        save_user(user)
    
    return {"ok": True, "code": code, "lines": lines.get(code, {}), "total_lines": len(lines)}


@router.get("/api/fund-holdings/discipline")
def get_discipline_lines(userId: str = "default"):
    """获取用户所有纪律线设定"""
    from services.persistence import load_user
    
    user = load_user(userId)
    portfolio = user.get("portfolio") or {}
    lines = portfolio.get("discipline_lines") or {}
    return {"lines": lines, "total": len(lines)}
