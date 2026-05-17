"""
钱袋子 — 家庭 CFO 首页聚合服务
================================
为首页"家庭 CFO 今日面板"提供一次性数据聚合。

全部纯规则计算，不调 LLM。每个模块独立 try/except，
单个模块失败不影响其他模块返回。

输出 5 个区块：
A. net_worth — 家庭净资产 + 分项
B. alerts — 今日 1-3 条人话提醒
C. allocation — 资产配置占比
D. emotion — 情绪提醒（基于恐贪+涨跌）
E. todos — 本周待办
"""
from __future__ import annotations
import time
from datetime import datetime, timedelta

from infra.cache import MemoryCache

# CFO Summary 结果缓存：60秒内相同用户直接返回（首页高频刷新场景）
_cfo_cache = MemoryCache(default_ttl=60)


def generate_cfo_summary(user_id: str) -> dict:
    """聚合首页全部数据，单个模块失败不影响整体。

    性能优化：
    1. 结果级缓存 60s（用户刷新首页不重复计算）
    2. 并行获取外部数据（恐贪/期货/净资产/估值），避免串行等待
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        f_fgi = pool.submit(_fetch_fear_greed)
        f_futures = pool.submit(_fetch_futures)
        f_nw = pool.submit(_fetch_networth)
        f_val = pool.submit(_fetch_valuation)

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

    # ── A. 净资产（从已获取数据构建）──
    try:
        result["net_worth"] = _format_net_worth(nw_data)
    except Exception as e:
        print(f"[CFO] net_worth format failed: {e}")

    # ── C. 资产配置（从已获取数据构建，不再重复调接口）──
    allocation_data = None
    try:
        allocation_data = _build_allocation(nw_data, val_pct)
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
        result["todos"] = _generate_todos(user_id, allocation_data)
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
    """从已获取的 unified-networth 数据格式化输出"""
    if not nw:
        return {"total": 0, "breakdown": {}}

    breakdown = nw.get("breakdown", {})
    return {
        "total": nw.get("netWorth", 0),
        "investment": (breakdown.get("investment") or {}).get("total", 0),
        "cash": (breakdown.get("cash") or {}).get("total", 0),
        "property": (breakdown.get("property") or {}).get("total", 0),
        "liability": (breakdown.get("liability") or {}).get("total", 0),
        "health_grade": nw.get("healthGrade", ""),
        "health_score": nw.get("healthScore", 0),
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

def _build_allocation(nw, val_pct: int = 50) -> dict | None:
    """从已获取的 unified-networth + 估值百分位构建配置数据（不再重复调接口）"""
    try:
        if not nw or nw.get("netWorth", 0) <= 0:
            return None

        breakdown = nw.get("breakdown", {})
        inv = (breakdown.get("investment") or {}).get("total", 0)
        cash = (breakdown.get("cash") or {}).get("total", 0)
        total = inv + cash
        if total <= 0:
            return None

        current = {
            "stock": round(inv / total * 100, 1),
            "bond": 0,
            "cash": round(cash / total * 100, 1),
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

def _generate_todos(user_id: str, allocation: dict | None) -> list:
    """基于当前数据状态自动生成本周待办并保存到数据库"""
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
            # 自动保存到数据库
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
