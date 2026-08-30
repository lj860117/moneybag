"""
钱袋子 — 家庭 CFO 首页聚合服务
================================
为首页"家庭 CFO 今日面板"提供一次性数据聚合。

全部纯规则计算，不调 LLM。每个模块独立 try/except，
单个模块失败不影响其他模块返回。

输出 6 个区块：
A. net_worth — 家庭净资产 + 分项 + 累计盈亏
B. alerts — 今日 1-3 条人话提醒
C. allocation — 资产配置占比
D. emotion — 情绪提醒（基于恐贪+涨跌）
E. todos — 本周待办
F. indices — 大盘指数今日涨跌（沪深300/上证/创业板）
"""
from __future__ import annotations
import time
from datetime import datetime, timedelta

from infra.cache import MemoryCache

# CFO Summary 结果缓存：60秒内相同用户直接返回（首页高频刷新场景）
_cfo_cache = MemoryCache(default_ttl=60)


def generate_cfo_summary(user_id: str, generate_todos: bool = True) -> dict:
    """聚合首页全部数据，单个模块失败不影响整体。

    性能优化：
    1. 结果级缓存 60s（用户刷新首页不重复计算）
    2. 并行获取外部数据（恐贪/期货/净资产/估值），避免串行等待

    Args:
        user_id: 用户ID
        generate_todos: 是否把 E 区块生成的待办**落库**。默认 True 保持向后兼容
            （真人页面请求）；机器驱动的只读路径（后台预热线程 _prewarm_loop）
            必须传 False —— 否则每 55 秒就往用户 JSON 写一条待办。
            注意：无论 True/False，返回结果里的 result["todos"] 显示内容都一样，
            这个开关只控制"要不要写盘"。详见 _generate_todos() 的注释。
    """
    # ── 缓存命中 → <5ms 返回 ──
    cache_key = f"cfo_{user_id}"
    cached = _cfo_cache.get(cache_key)
    if cached is not None:
        cached["from_cache"] = True
        return cached

    import concurrent.futures
    start = time.time()
    result = {
        "net_worth": None,
        "alerts": [],
        "allocation": None,
        "emotion": None,
        "todos": [],
        "indices": [],
        "timestamp": datetime.now().isoformat(),
    }

    # ── 并行获取所有外部数据（主要耗时点）──
    fear_greed = 50
    market_change = 0.0
    nw_data = None
    val_pct = 50

    def _fetch_fear_greed():
        from services.market_data import get_fear_greed_index
        return get_fear_greed_index()

    def _fetch_futures():
        from infra.data_source.macro.indicators import get_global_futures_snapshot
        return get_global_futures_snapshot()

    def _fetch_networth():
        from services.unified_networth import calc_unified_networth
        return calc_unified_networth(user_id)

    def _fetch_valuation():
        from services.market_data import get_valuation_percentile
        return get_valuation_percentile()

    def _fetch_indices():
        """获取三大指数今日涨跌幅"""
        from infra.data_source.market.stocks import get_index_daily
        indices = []
        symbols = [
            ("沪深300", "sh000300"),
            ("上证", "sh000001"),
            ("创业板", "sz399006"),
        ]
        for name, symbol in symbols:
            try:
                df = get_index_daily(symbol)
                if df is not None and len(df) >= 2:
                    today_close = float(df.iloc[-1]["close"])
                    yesterday_close = float(df.iloc[-2]["close"])
                    if yesterday_close > 0:
                        pct = round((today_close - yesterday_close) / yesterday_close * 100, 2)
                        indices.append({"name": name, "pct": pct})
            except Exception:
                pass
        return indices

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        f_fgi = pool.submit(_fetch_fear_greed)
        f_futures = pool.submit(_fetch_futures)
        f_nw = pool.submit(_fetch_networth)
        f_val = pool.submit(_fetch_valuation)
        f_idx = pool.submit(_fetch_indices)

    # 收集结果（每个独立 try/except，单个失败不影响其他）
    try:
        fgi = f_fgi.result(timeout=5)
        if fgi:
            fear_greed = fgi.get("score", 50)
    except Exception as e:
        print(f"[CFO] fear_greed fetch failed: {e}")

    try:
        futures = f_futures.result(timeout=5)
        if futures and futures.get("available") and futures.get("a50"):
            market_change = futures["a50"].get("change_pct", 0) or 0
    except Exception as e:
        print(f"[CFO] futures fetch failed: {e}")

    try:
        nw_data = f_nw.result(timeout=5)
    except Exception as e:
        print(f"[CFO] networth fetch failed: {e}")

    try:
        val_result = f_val.result(timeout=5)
        if val_result:
            val_pct = val_result.get("percentile", 50)
    except Exception as e:
        print(f"[CFO] valuation fetch failed: {e}")

    # ── F. 大盘指数 ──
    try:
        idx_result = f_idx.result(timeout=5)
        if idx_result:
            result["indices"] = idx_result
    except Exception as e:
        print(f"[CFO] indices fetch failed: {e}")

    # ── A. 净资产（从已获取数据构建）──
    try:
        result["net_worth"] = _format_net_worth(nw_data)
    except Exception as e:
        print(f"[CFO] net_worth format failed: {e}")

    # ── C. 资产配置（从已获取数据构建，不再重复调接口）──
    allocation_data = None
    try:
        allocation_data = _build_allocation(nw_data, val_pct, user_id)
        result["allocation"] = allocation_data
    except Exception as e:
        print(f"[CFO] allocation failed: {e}")

    # ── B. 今日提醒（纯规则，不调 LLM）──
    try:
        result["alerts"] = _generate_alerts(
            user_id, fear_greed, allocation_data, result.get("net_worth"), val_pct
        )
    except Exception as e:
        print(f"[CFO] alerts failed: {e}")

    # ── D. 情绪提醒 ──
    try:
        result["emotion"] = _generate_emotion(
            fear_greed, market_change, allocation_data
        )
    except Exception as e:
        print(f"[CFO] emotion failed: {e}")

    # ── E. 本周待办 ──
    try:
        result["todos"] = _generate_todos(
            user_id, allocation_data, persist=generate_todos
        )
    except Exception as e:
        print(f"[CFO] todos failed: {e}")

    result["elapsed"] = round(time.time() - start, 2)
    result["from_cache"] = False
    # 缓存结果（60s 内重复请求直接返回）
    _cfo_cache.set(cache_key, result, ttl=60)
    return result


# ============================================================
# A. 净资产
# ============================================================

def _format_net_worth(nw) -> dict:
    """从已获取的 unified-networth 数据格式化输出，含累计盈亏"""
    if not nw:
        return {"total": 0, "breakdown": {}}

    breakdown = nw.get("breakdown", {})
    invest_data = breakdown.get("investment") or {}

    # 计算累计盈亏：市值 - 成本（合并盯盘系统 + V4交易流水两个数据源）
    total_market = invest_data.get("total", 0)
    total_cost = 0
    # 盯盘系统基金
    fund_items = invest_data.get("fundItems") or []
    for item in fund_items:
        shares = item.get("shares", 0) or 0
        cost_nav = item.get("costNav", 0) or 0
        total_cost += shares * cost_nav
    # V4 交易流水基金（v9.5.119: 之前漏算了这部分，导致盈亏百分比基数过小）
    txn_fund_items = invest_data.get("txnFundItems") or []
    for item in txn_fund_items:
        shares = item.get("shares", 0) or 0
        cost_nav = item.get("costNav", 0) or 0
        total_cost += shares * cost_nav
    # 股票也算进来
    stock_items = invest_data.get("stockItems") or []
    for item in stock_items:
        total_cost += item.get("totalCost", 0) or 0

    total_pnl = round(total_market - total_cost, 2) if total_cost > 0 else 0
    total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0

    return {
        "total": nw.get("netWorth", 0),
        "investment": total_market,
        "cash": (breakdown.get("cash") or {}).get("total", 0),
        "property": (breakdown.get("property") or {}).get("total", 0),
        "liability": (breakdown.get("liability") or {}).get("total", 0),
        "health_grade": nw.get("healthGrade", ""),
        "health_score": nw.get("healthScore", 0),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "fund_count": len(fund_items),
        "stock_count": len(stock_items),
    }


# 兼容旧调用
def _get_net_worth(user_id: str) -> dict:
    from services.unified_networth import calc_unified_networth
    return _format_net_worth(calc_unified_networth(user_id))


# ============================================================
# B. 今日提醒（纯规则引擎，不调 LLM）
# ============================================================

def _generate_alerts(user_id: str, fear_greed: int,
                     allocation: dict | None, net_worth: dict | None,
                     val_pct: int = 50) -> list:
    """从已有数据提炼 1-3 条人话提醒，按优先级排序"""
    alerts = []

    # 规则 1: 恐贪指数极端值
    if fear_greed >= 75:
        alerts.append({
            "level": "warning",
            "text": f"市场恐贪指数 {fear_greed}，已进入贪婪区，注意追高风险。"
        })
    elif fear_greed <= 25:
        alerts.append({
            "level": "opportunity",
            "text": f"市场恐贪指数 {fear_greed}，恐惧蔓延，可能是逢低布局的机会。"
        })

    # 规则 2: 配置偏离
    if allocation and allocation.get("deviation"):
        dev = allocation["deviation"]
        for category, pct in dev.items():
            if abs(pct) > 10:
                label = {"stock": "股票", "bond": "债券", "cash": "现金"}.get(category, category)
                if pct > 0:
                    alerts.append({
                        "level": "warning",
                        "text": f"{label}仓位超出目标 {abs(pct):.0f}%，本周不建议继续加仓。"
                    })
                else:
                    alerts.append({
                        "level": "info",
                        "text": f"{label}配置低于目标 {abs(pct):.0f}%，可考虑适当补仓。"
                    })

    # 规则 3: 现金储备不足
    if net_worth:
        cash = net_worth.get("cash", 0)
        # 粗算月支出：如果有 ledger 数据，取平均；否则按总资产 5% 估算
        monthly_expense = _estimate_monthly_expense(user_id, net_worth)
        if monthly_expense > 0 and cash < monthly_expense * 6:
            months = cash / monthly_expense if monthly_expense > 0 else 0
            alerts.append({
                "level": "danger",
                "text": f"现金储备约 {months:.0f} 个月生活费，低于 6 个月安全线，暂停新增高风险资产。"
            })

    # 规则 4: 获取风控动作（使用已获取的 val_pct，不再重复调接口）
    try:
        from services.risk import generate_risk_actions
        from services.stock_monitor import load_stock_holdings
        holdings = load_stock_holdings(user_id) or []
        if holdings:
            risk_result = generate_risk_actions(holdings, val_pct)
            danger_actions = [a for a in (risk_result.get("actions") or [])
                           if a.get("level") == "danger"]
            for a in danger_actions[:1]:  # 最多取 1 条
                alerts.append({"level": "danger", "text": a.get("action", "")})
    except Exception:
        pass

    # 去重，最多 3 条，按优先级排序
    priority = {"danger": 0, "warning": 1, "opportunity": 2, "info": 3}
    alerts.sort(key=lambda x: priority.get(x.get("level", "info"), 9))
    return alerts[:3]


def _estimate_monthly_expense(user_id: str, net_worth: dict) -> float:
    """估算月支出"""
    try:
        from config import DATA_DIR
        import json
        user_file = DATA_DIR / "users" / f"{user_id}.json"
        if user_file.exists():
            data = json.loads(user_file.read_text(encoding="utf-8"))
            ledger = data.get("ledger", [])
            if ledger:
                # 取最近 90 天支出
                cutoff = (datetime.now() - timedelta(days=90)).isoformat()
                expenses = [e.get("amount", 0) for e in ledger
                          if e.get("direction") != "income" and e.get("date", "") >= cutoff]
                if expenses:
                    return sum(expenses) / 3  # 3个月平均
    except Exception:
        pass
    # 降级：用总资产的 3% 估算月支出
    total = net_worth.get("total", 0)
    return total * 0.03 if total > 0 else 10000


# ============================================================
# C. 资产配置
# ============================================================

def _build_allocation(nw, val_pct: int = 50, user_id: str = "") -> dict | None:
    """从已获取的 unified-networth + 估值百分位构建配置数据

    数据分层：
    - actual_cash：用户手录的现金资产（明确知道是现金）
    - actual_stock：用户直接持有的股票市值
    - fund_equity/bond/cash：基金穿透估算的内部仓位（估算值，非确定值）

    两层不混合：配置图优先展示"实际持有"，基金穿透仅作参考。
    """
    try:
        if not nw or nw.get("netWorth", 0) <= 0:
            return None

        breakdown = nw.get("breakdown", {})
        inv = (breakdown.get("investment") or {}).get("total", 0)
        actual_cash = (breakdown.get("cash") or {}).get("total", 0)  # 手录现金
        total = inv + actual_cash
        if total <= 0:
            return None

        # 直接持有股票市值（非基金）
        actual_stock_mv = 0.0
        try:
            from services.stock_monitor import get_stock_holdings
            sh = get_stock_holdings(user_id) if user_id else []
            has_direct_stock = bool(sh and len(sh) > 0)
            # 简单用持仓数量估算（实际市值需要实时行情，这里用成本近似）
            for s in (sh or []):
                actual_stock_mv += float(s.get("shares", 0)) * float(s.get("costPrice", s.get("cost", 0)))
        except Exception:
            has_direct_stock = False

        # 基金穿透估算
        fund_equity = 0.0
        fund_bond = 0.0
        fund_cash_est = 0.0  # 基金内部现金估算
        used_precise = False

        if user_id:
            try:
                from services.portfolio_overview import get_portfolio_overview
                overview = get_portfolio_overview(user_id)
                alloc = overview.get("allocation", {})
                if alloc.get("equity", 0) > 0 or alloc.get("bond", 0) > 0:
                    inv_ratio = inv / total if total > 0 else 0
                    fund_equity = round(alloc["equity"] * inv_ratio / 100 * 100, 1)
                    fund_bond = round(alloc["bond"] * inv_ratio / 100 * 100, 1)
                    fund_cash_est = round(alloc.get("cash", 0) * inv_ratio / 100 * 100, 1)
                    used_precise = True
            except Exception as e:
                print(f"[CFO] portfolio_overview fallback: {e}")

        if not used_precise:
            fund_equity = round(inv / total * 100, 1)
            fund_bond = 0.0
            fund_cash_est = 0.0

        # 合并：手录股票 + 基金穿透股权
        actual_stock_pct = round(actual_stock_mv / total * 100, 1) if total > 0 else 0
        equity_pct = round(fund_equity + actual_stock_pct, 1)
        bond_pct = round(fund_bond, 1)
        # 现金：手录现金 和 基金估算现金 分开存储
        actual_cash_pct = round(actual_cash / total * 100, 1)
        # 总现金 = 手录现金 + 基金内现金估算
        cash_pct = round(actual_cash_pct + fund_cash_est, 1)

        current = {
            "stock": equity_pct,
            "equity": equity_pct,
            "bond": bond_pct,
            "cash": cash_pct,
            # 分层数据（前端可用来区分显示）
            "actual_cash_pct": actual_cash_pct,       # 手录现金占比
            "fund_cash_est_pct": fund_cash_est,        # 基金估算现金占比
            "actual_stock_pct": actual_stock_pct,      # 直接持股占比
            "fund_equity_pct": fund_equity,            # 基金穿透股权占比
        }

        if val_pct > 70:
            target = {"stock": 40, "bond": 35, "cash": 25}
        elif val_pct < 30:
            target = {"stock": 70, "bond": 20, "cash": 10}
        else:
            target = {"stock": 55, "bond": 30, "cash": 15}

        deviation = {
            "stock": round(current["stock"] - target["stock"], 1),
            "bond": round(current["bond"] - target["bond"], 1),
            "cash": round(current["cash"] - target["cash"], 1),
        }

        return {
            "current": current,
            "target": target,
            "deviation": deviation,
            "zone": "高估" if val_pct > 70 else "低估" if val_pct < 30 else "适中",
            "total_market": round(total, 0),
            "has_direct_stock": has_direct_stock,
        }
    except Exception as e:
        print(f"[CFO] _build_allocation error: {e}")
        return None


# 兼容旧调用
def _get_allocation(user_id: str) -> dict | None:
    from services.unified_networth import calc_unified_networth
    from services.market_data import get_valuation_percentile
    nw = calc_unified_networth(user_id)
    try:
        val = get_valuation_percentile()
        val_pct = val.get("percentile", 50)
    except Exception:
        val_pct = 50
    return _build_allocation(nw, val_pct)


# ============================================================
# D. 情绪提醒
# ============================================================

def _generate_emotion(fear_greed: int, market_change: float,
                      allocation: dict | None) -> dict:
    """基于恐贪指数 + 市场涨跌生成情绪提醒"""
    # 计算当前股票仓位
    stock_pct = 0
    if allocation and allocation.get("current"):
        stock_pct = allocation["current"].get("stock", 0)

    if fear_greed >= 70 and market_change > 1.0:
        return {
            "icon": "🔥",
            "title": "今天市场大涨，你可能会想追。",
            "body": f"但恐贪指数已到 {fear_greed}（贪婪区），当前股票仓位 {stock_pct:.0f}%，先看纪律，不要临时加仓。",
            "tone": "caution"
        }
    elif fear_greed <= 30 and market_change < -1.0:
        return {
            "icon": "😰",
            "title": "今天市场大跌，恐慌情绪蔓延。",
            "body": "但历史上恐惧时期往往是长线买入的好机会——前提是你的现金储备够用。不要恐慌卖出。",
            "tone": "reassure"
        }
    elif fear_greed >= 60:
        return {
            "icon": "📊",
            "title": "市场情绪偏乐观，保持平常心。",
            "body": "按原计划执行即可，不需要因为涨了就加仓。",
            "tone": "calm"
        }
    elif fear_greed <= 40:
        return {
            "icon": "🌊",
            "title": "市场情绪偏悲观，不要被吓到。",
            "body": "短期波动正常，如果基本面没变，持有就好。",
            "tone": "calm"
        }
    else:
        return {
            "icon": "☀️",
            "title": "市场正常波动，按计划执行。",
            "body": "今天没有需要特别关注的情绪信号。",
            "tone": "neutral"
        }


# ============================================================
# E. 本周待办
# ============================================================

def _generate_todos(user_id: str, allocation: dict | None,
                    persist: bool = True) -> list:
    """基于当前数据状态生成本周待办（显示用），可选地落库。

    FIX 2026-08-30（P0：读操作不应有写副作用）
    ------------------------------------------------
    这个函数原本无条件调用 todo_manager.create_todo() 落库，而它自己位于
    generate_cfo_summary()（纯读接口）的调用链上，且被 55 秒一次的后台预热
    线程 _prewarm_loop() 反复触发 —— 相当于"每次读仪表板就写一条待办"，
    153,093 条垃圾待办（33.9MB）就是这么堆出来的。

    现在拆成两件事：
      - 计算显示用的 todos 标题列表 —— 纯函数，永远执行，零写入
      - 落库 —— 由 persist 开关控制
    机器驱动的只读路径（预热线程）传 persist=False，真人页面请求保持 True。
    即使某个调用方漏传，todo_manager.create_todo() 的幂等窗口也会兜住
    （同规则同窗口内只留一条 open）。两道防线，缺一不可：
      解耦解决"写得太频繁"，幂等解决"写重复内容"。

    Args:
        user_id: 用户ID
        allocation: 资产配置数据（可能为 None）
        persist: 是否把生成的待办写入用户数据库（默认 True 保持向后兼容）

    Returns:
        最多 4 条待办标题（显示用）
    """
    create_todo = None
    if persist:
        from services.todo_manager import create_todo

    todos = []
    todo_objects = []
    today = datetime.now()
    weekday = today.weekday()  # 0=周一, 6=周日

    # 规则 1: 配置偏离大 → 检查再平衡
    if allocation and allocation.get("deviation"):
        max_dev = max(abs(v) for v in allocation["deviation"].values()) if allocation["deviation"] else 0
        if max_dev > 15:
            title = "检查资产配置是否需要再平衡（偏离已超 15%）"
            todos.append(title)
            # 自动保存到数据库（仅 persist=True）
            if create_todo:
                try:
                    todo_obj = create_todo(
                        user_id,
                        title,
                        rule_triggered="allocation_deviation_gt_15",
                        due_by_days=7,
                        metadata={"deviation": max_dev}
                    )
                    if todo_obj:
                        todo_objects.append(todo_obj)
                except Exception as e:
                    print(f"[CFO] 创建 todo 失败: {e}")

    # 规则 2: 周末 → 家庭复盘
    if weekday >= 4:  # 周五/六/日
        title = "本周末和家人做一次财务小复盘"
        todos.append(title)
        if create_todo:
            try:
                todo_obj = create_todo(
                    user_id,
                    title,
                    rule_triggered="weekly_review",
                    due_by_days=3,
                )
                if todo_obj:
                    todo_objects.append(todo_obj)
            except Exception:
                pass

    # 规则 3: 检查记账
    try:
        from config import DATA_DIR
        import json
        from hashlib import sha256
        user_hash = sha256(user_id.encode()).hexdigest()
        user_file = DATA_DIR / "users" / f"{user_hash}.json"
        if user_file.exists():
            data = json.loads(user_file.read_text(encoding="utf-8"))
            ledger = data.get("ledger", [])
            if ledger:
                last_entry = max((e.get("date", "") for e in ledger), default="")
                if last_entry:
                    days_since = (today - datetime.fromisoformat(last_entry.replace("Z", ""))).days
                    if days_since > 5:
                        title = f"已 {days_since} 天没记账，补录近期消费"
                        todos.append(title)
                        if create_todo:
                            try:
                                todo_obj = create_todo(
                                    user_id,
                                    title,
                                    rule_triggered="accounting_overdue",
                                    due_by_days=2,
                                    metadata={"days_overdue": days_since}
                                )
                                if todo_obj:
                                    todo_objects.append(todo_obj)
                            except Exception:
                                pass
            else:
                title = "开始记录日常收支（每周花 2 分钟）"
                todos.append(title)
    except Exception:
        pass

    # 规则 4: 有持仓但没设过目标
    if allocation and not allocation.get("target"):
        title = "设置你的目标资产配置比例"
        todos.append(title)
        if create_todo:
            try:
                todo_obj = create_todo(
                    user_id,
                    title,
                    rule_triggered="no_target_config",
                    due_by_days=7,
                )
                if todo_obj:
                    todo_objects.append(todo_obj)
            except Exception:
                pass

    # 返回最多 4 条（显示用）
    return todos[:4]


# ============================================================
# 后台预热：定期刷新活跃用户的 CFO 缓存
# ============================================================

_PREWARM_INTERVAL = 55  # 每 55 秒刷新一次（< 60s 缓存 TTL，保证用户永远命中）
_prewarm_started = False


def _get_active_users() -> list:
    """获取需要预热的用户列表（家庭成员）"""
    from api.shared_helpers import FAMILY_MEMBERS
    return FAMILY_MEMBERS


def _prewarm_loop():
    """后台线程：循环预热 CFO 缓存

    FIX 2026-08-30：这个线程是 todos 膨胀的主要写入源
    ------------------------------------------------
    每 55 秒 × 2 个家庭成员 ≈ 每天 3140 次 generate_cfo_summary()，
    而原来每次都会调 create_todo() 落库 —— 这就是 1444 条/天的来源。
    预热是纯粹的"机器读"，绝不应该产生写副作用，故传 generate_todos=False。
    真人访问路径（api/steward.py → generate_cfo_summary）仍保持落库能力。
    """
    import threading
    while True:
        try:
            users = _get_active_users()
            for uid in users:
                try:
                    # force=False 但缓存 key 是 _cfo_cache，不是 _NW_CACHE
                    # 直接调用 generate_cfo_summary 即可刷新缓存
                    cache_key = f"cfo_{uid}"
                    # 清除旧缓存强制重算
                    _cfo_cache.delete(cache_key)
                    # generate_todos=False：预热只读，不写用户 JSON
                    generate_cfo_summary(uid, generate_todos=False)
                except Exception as e:
                    print(f"[CFO-PREWARM] {uid} failed: {e}")
        except Exception as e:
            print(f"[CFO-PREWARM] loop error: {e}")
        time.sleep(_PREWARM_INTERVAL)


def start_cfo_prewarm():
    """启动后台预热线程（在 main.py 启动时调用一次）"""
    global _prewarm_started
    if _prewarm_started:
        return
    _prewarm_started = True
    import threading
    t = threading.Thread(target=_prewarm_loop, daemon=True, name="cfo-prewarm")
    t.start()
    print("[CFO-PREWARM] 后台预热线程已启动")
