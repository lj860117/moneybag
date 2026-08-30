"""
投资组合 API（交易流水 / 资产管理 / 净资产 / 盈亏 / 体检 / 风控 / 配置建议）
=============================================================================
从 main.py 提取的 P2 路由。

Design doc: docs/design/12-framework-refactor.md §四
"""
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["投资组合"])

from models.schemas import (
    Portfolio, TransactionRequest, AssetRequest, TopupRequest,
)
from services.data_layer import (
    get_fund_nav, get_valuation_percentile, get_fear_greed_index,
    _get_nav_on_date,
)
from services.portfolio_calc import (
    calc_holdings_from_transactions, ensure_v4_portfolio,
)
from services.persistence import load_user, save_user, user_write_lock
from services.risk import calc_risk_metrics, generate_risk_actions
from services.portfolio import generate_allocation_advice, get_recommend_allocations
from services.portfolio_overview import get_portfolio_overview
from services.unified_networth import calc_unified_networth
from services.portfolio_doctor import diagnose, stress_test, health_score, concentration_check
from services.ds_enhance import enhance_allocation_advice

from api.shared_helpers import _build_market_context


# ---- 交易流水 CRUD ----

@router.post("/api/portfolio/transaction")
def add_transaction(req: TransactionRequest):
    """添加交易记录（BUY/SELL/DIVIDEND）

    FIX 2026-08-30（并发丢更新）：用 user_write_lock 保护 RMW 临界区。
    ⚠️ 注意临界区边界：净值查询（_get_nav_on_date / get_fund_nav）是**网络调用**，
    可能耗时数秒，且完全不依赖用户数据 —— 所以放在锁**外**先算好。
    锁内只做 load → append → save 这段纯内存操作，把临界区压到最短，
    避免一个慢net请求把其他人的写请求全卡在锁上。
    """
    tx = req.transaction.dict()
    if not tx.get("id"):
        tx["id"] = f"tx_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    if not tx.get("date"):
        tx["date"] = datetime.now().isoformat()

    # ── 锁外：网络取净值（不依赖用户数据）──
    if tx["type"] == "BUY" and tx.get("amount", 0) > 0:
        if tx.get("shares", 0) <= 0 or tx.get("nav", 0) <= 0:
            nav_val = _get_nav_on_date(tx["code"], tx["date"])
            if not nav_val:
                nav_info = get_fund_nav(tx["code"])
                nav_val = float(nav_info["nav"]) if nav_info and nav_info["nav"] != "N/A" else None
            if nav_val and nav_val > 0:
                tx["nav"] = nav_val
                tx["shares"] = round(tx["amount"] / nav_val, 2)

    # ── 锁内：RMW 临界区 ──
    with user_write_lock(req.userId) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(req.userId)
        user = ensure_v4_portfolio(user)
        p = user["portfolio"]

        p["transactions"].append(tx)
        p["history"].append({
            "date": datetime.now().isoformat(),
            "action": tx["type"].lower(),
            "code": tx["code"],
            "amount": tx.get("amount", 0),
        })
        save_user(user)

    # ── 锁外：同步持仓到 fund_holdings 文件（写的是另一个文件，不占用户锁）──
    try:
        _sync_fund_holdings_file(user)
    except Exception as e:
        print(f"[PORTFOLIO] fund_holdings 同步失败（不影响交易）: {e}")

    return {"status": "ok", "transaction": tx}


def _sync_fund_holdings_file(user: dict):
    """将 portfolio.transactions 聚合后写入 fund_holdings_{userId}.json

    凌晨 cron（night_worker/stock_monitor）读此文件做持仓诊断和盯盘。
    每次交易操作后自动同步，保证 cron 数据不滞后。
    """
    from services.fund_monitor import save_fund_holdings

    user_id = user.get("userId", "")
    if not user_id:
        return

    txs = user.get("portfolio", {}).get("transactions", [])
    if not txs:
        save_fund_holdings([], user_id)
        return

    result = calc_holdings_from_transactions(txs)
    current = result.get("current_holdings", [])

    # 转换为 fund_monitor 格式：code, name, shares, cost/costNav
    holdings_for_cron = []
    for h in current:
        if h.get("shares", 0) > 0:
            holdings_for_cron.append({
                "code": h["code"],
                "name": h.get("name", ""),
                "shares": round(h["shares"], 2),
                "cost": round(h.get("avgNav", 0), 4),
                "costNav": round(h.get("avgNav", 0), 4),
            })

    save_fund_holdings(holdings_for_cron, user_id)


@router.put("/api/portfolio/transaction/{tx_id}")
def update_transaction(tx_id: str, req: TransactionRequest):
    """修改交易记录

    FIX 2026-08-30: user_write_lock 保护 RMW（纯内存操作，临界区很小）。
    """
    with user_write_lock(req.userId) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(req.userId)
        user = ensure_v4_portfolio(user)
        p = user["portfolio"]

        updated = None
        for i, tx in enumerate(p["transactions"]):
            if tx.get("id") == tx_id:
                updated = req.transaction.dict()
                updated["id"] = tx_id
                p["transactions"][i] = updated
                save_user(user)
                break

    if updated is None:
        raise HTTPException(404, f"Transaction {tx_id} not found")

    # 锁外：同步另一个文件
    try:
        _sync_fund_holdings_file(user)
    except Exception:
        pass
    return {"status": "ok", "transaction": updated}


@router.delete("/api/portfolio/transaction/{tx_id}")
def delete_transaction(tx_id: str, userId: str = ""):
    """删除交易记录

    FIX 2026-08-30: user_write_lock 保护 RMW。
    """
    if not userId:
        raise HTTPException(400, "userId required")

    with user_write_lock(userId) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(userId)
        user = ensure_v4_portfolio(user)
        p = user["portfolio"]

        original_len = len(p["transactions"])
        p["transactions"] = [tx for tx in p["transactions"] if tx.get("id") != tx_id]
        if len(p["transactions"]) == original_len:
            raise HTTPException(404, f"Transaction {tx_id} not found")

        save_user(user)

    # 锁外：同步另一个文件
    try:
        _sync_fund_holdings_file(user)
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/api/portfolio/history")
def get_transaction_history(userId: str = ""):
    """获取交易流水历史"""
    if not userId:
        return {"transactions": []}
    user = load_user(userId)
    user = ensure_v4_portfolio(user)
    txs = user["portfolio"].get("transactions", [])
    txs_sorted = sorted(txs, key=lambda t: t.get("date", ""), reverse=True)
    return {"transactions": txs_sorted}


# ---- 持仓计算 ----

@router.post("/api/portfolio/holdings")
def get_holdings_v4(req: dict):
    """从交易流水计算当前持仓（V4）"""
    user_id = req.get("userId", "")
    if not user_id:
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])

    result = calc_holdings_from_transactions(txs)

    for h in result["active"]:
        code = h["code"]
        if code == "余额宝":
            h["currentNav"] = 1.0
            h["marketValue"] = h["shares"]
            h["pnl"] = h["shares"] - h["totalCost"]
            h["pnlPct"] = round(h["pnl"] / h["totalCost"] * 100, 2) if h["totalCost"] > 0 else 0
            continue

        nav_info = get_fund_nav(code)
        if nav_info and nav_info["nav"] != "N/A":
            current_nav = float(nav_info["nav"])
            h["currentNav"] = current_nav
            h["navDate"] = nav_info.get("date", "")
            h["dayChange"] = float(nav_info.get("change", "0"))
            h["marketValue"] = round(h["shares"] * current_nav, 2)
            h["pnl"] = round(h["marketValue"] - h["totalCost"], 2)
            h["pnlPct"] = round(h["pnl"] / h["totalCost"] * 100, 2) if h["totalCost"] > 0 else 0
        else:
            h["currentNav"] = h["avgNav"]
            h["marketValue"] = round(h["shares"] * h["avgNav"], 2)
            h["pnl"] = 0
            h["pnlPct"] = 0

    total_cost = sum(h["totalCost"] for h in result["active"])
    total_market = sum(h.get("marketValue", 0) for h in result["active"])
    total_pnl = total_market - total_cost
    total_realized = sum(result["realized"].values())

    return {
        "holdings": result["active"],
        "closed": result["closed"],
        "totalCost": round(total_cost, 2),
        "totalMarket": round(total_market, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
        "totalRealized": round(total_realized, 2),
        "realized": result["realized"],
    }


# ---- 资产管理 ----

@router.post("/api/assets")
def add_or_update_asset(req: AssetRequest):
    """添加或更新非投资类资产"""
    # 输入校验
    asset_data = req.asset.dict()
    value = asset_data.get("value", 0)
    asset_type = asset_data.get("type", "")
    if asset_type != "liability" and value < 0:
        raise HTTPException(400, "非负债类资产金额不能为负数（如需录入负债请使用 type=liability）")
    if abs(value) > 1_000_000_000:
        raise HTTPException(400, "金额异常，请确认是否正确（超过10亿）")

    # 大额警告标记
    _large_amount_warning = None
    if abs(value) > 10_000_000:  # > 1000万
        _large_amount_warning = f"⚠️ 金额较大（¥{abs(value):,.0f}），请确认是否正确。"

    asset = asset_data
    if not asset.get("id"):
        asset["id"] = f"a_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    if not asset.get("updated"):
        asset["updated"] = datetime.now().strftime("%Y-%m-%d")

    # FIX 2026-08-30: user_write_lock 保护 RMW（纯内存操作）
    with user_write_lock(req.userId) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(req.userId)
        user = ensure_v4_portfolio(user)
        p = user["portfolio"]

        existing_idx = None
        for i, a in enumerate(p.get("assets", [])):
            if a.get("id") == asset["id"]:
                existing_idx = i
                break

        if existing_idx is not None:
            p["assets"][existing_idx] = asset
        else:
            p.setdefault("assets", []).append(asset)

        save_user(user)

    # 锁外：失效缓存（不碰用户 JSON）
    try:
        from services.unified_networth import invalidate_networth_cache
        invalidate_networth_cache(req.userId)
    except Exception:
        pass
    result = {"status": "ok", "asset": asset}
    if _large_amount_warning:
        result["warning"] = _large_amount_warning
    return result


@router.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: str, userId: str = ""):
    """删除资产

    FIX 2026-08-30: user_write_lock 保护 RMW。
    """
    if not userId:
        raise HTTPException(400, "userId required")

    with user_write_lock(userId) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(userId)
        user = ensure_v4_portfolio(user)
        p = user["portfolio"]

        original_len = len(p.get("assets", []))
        p["assets"] = [a for a in p.get("assets", []) if a.get("id") != asset_id]
        if len(p.get("assets", [])) == original_len:
            raise HTTPException(404, f"Asset {asset_id} not found")

        save_user(user)

    # 锁外：失效缓存
    try:
        from services.unified_networth import invalidate_networth_cache
        invalidate_networth_cache(userId)
    except Exception:
        pass
    return {"status": "ok"}


@router.get("/api/assets")
def get_assets(userId: str = ""):
    """获取全部资产"""
    if not userId:
        return {"assets": []}
    user = load_user(userId)
    user = ensure_v4_portfolio(user)
    return {"assets": user["portfolio"].get("assets", [])}


# ---- 净资产 ----

@router.post("/api/portfolio/networth")
def calc_networth(req: dict):
    """计算净资产 = 投资市值 + 现金 + 固定资产 + 记账净现金流 - 负债"""
    user_id = req.get("userId", "")
    if not user_id:
        return {"netWorth": 0, "breakdown": {}}

    user = load_user(user_id)
    user = ensure_v4_portfolio(user)
    p = user["portfolio"]

    txs = p.get("transactions", [])
    holdings_result = calc_holdings_from_transactions(txs)
    investment_value = 0
    for h in holdings_result["active"]:
        code = h["code"]
        if code == "余额宝":
            investment_value += h["shares"]
            continue
        nav_info = get_fund_nav(code)
        if nav_info and nav_info["nav"] != "N/A":
            investment_value += h["shares"] * float(nav_info["nav"])
        else:
            investment_value += h["shares"] * h["avgNav"]

    assets = p.get("assets", [])
    def _av(a): return a.get("value", 0) or a.get("balance", 0) or 0
    cash_total = sum(_av(a) for a in assets if a.get("type") == "cash")
    property_total = sum(_av(a) for a in assets if a.get("type") == "property")
    car_total = sum(_av(a) for a in assets if a.get("type") == "car")
    insurance_total = sum(_av(a) for a in assets if a.get("type") == "insurance")
    other_total = sum(_av(a) for a in assets if a.get("type") == "other")
    liability_total = sum(abs(_av(a)) for a in assets if a.get("type") == "liability")

    ledger = user.get("ledger", [])
    ledger_income = sum(e.get("amount", 0) for e in ledger if e.get("direction") == "income")
    ledger_expense = sum(e.get("amount", 0) for e in ledger if e.get("direction", "expense") == "expense")
    ledger_net = ledger_income - ledger_expense

    net_worth = investment_value + cash_total + property_total + car_total + insurance_total + other_total + ledger_net - liability_total

    return {
        "netWorth": round(net_worth, 2),
        "breakdown": {
            "investment": round(investment_value, 2),
            "cash": round(cash_total, 2),
            "property": round(property_total, 2),
            "car": round(car_total, 2),
            "insurance": round(insurance_total, 2),
            "other": round(other_total, 2),
            "ledgerNet": round(ledger_net, 2),
            "liability": round(liability_total, 2),
        },
        "ledger": {
            "income": round(ledger_income, 2),
            "expense": round(ledger_expense, 2),
            "net": round(ledger_net, 2),
        },
    }


# ---- 加仓 ----

@router.post("/api/portfolio/topup")
def topup_portfolio(req: TopupRequest):
    """加仓 — 批量生成 BUY 交易

    FIX 2026-08-30（并发丢更新）：这是本文件里风险最高的一处 —— 它批量追加多条
    交易，一旦丢更新，用户加的一整笔钱会凭空消失且完全无声无息。

    ⚠️ 临界区边界很关键：原实现在 load 和 save 之间的循环里逐个调
    get_fund_nav()（**每只基金一次网络请求**），若把整个函数包进锁，
    一次加仓 6 只基金就要在持锁状态下等 6 次网络往返，极易把别人顶到超时。
    因此改为：**锁外**先把全部 tx（含净值/份额）算好，**锁内**只做
    load → 批量 append → save。临界区从"数秒"压缩到"毫秒级"。
    """
    # ── 锁外：批量取净值、构造交易（不依赖用户数据）──
    new_txs = []
    for alloc in req.allocations:
        code = alloc.get("code", "")
        name = alloc.get("name", "")
        amount = alloc.get("amount", 0)
        if not code or amount <= 0:
            continue

        tx_id = f"tx_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        nav_val = None
        shares = 0

        if code != "余额宝":
            nav_info = get_fund_nav(code)
            if nav_info and nav_info["nav"] != "N/A":
                nav_val = float(nav_info["nav"])
                shares = round(amount / nav_val, 2)
        else:
            nav_val = 1.0
            shares = amount

        new_txs.append({
            "id": tx_id,
            "type": "BUY",
            "code": code,
            "name": name,
            "amount": amount,
            "shares": shares,
            "nav": nav_val or 0,
            "fee": 0,
            "date": datetime.now().isoformat(),
            "source": "topup",
            "note": f"加仓 ¥{amount:,.0f}",
        })

    # ── 锁内：RMW 临界区（纯内存，批量写入）──
    with user_write_lock(req.userId) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(req.userId)
        user = ensure_v4_portfolio(user)
        p = user["portfolio"]

        p["transactions"].extend(new_txs)
        p["history"].append({
            "date": datetime.now().isoformat(),
            "action": "topup",
            "amount": req.amount,
            "profile": req.profile,
        })
        save_user(user)

    return {"status": "ok", "transactions": new_txs, "count": len(new_txs)}


# ---- 数据迁移 ----

@router.post("/api/portfolio/migrate")
def migrate_portfolio(req: dict):
    """手动触发 V3→V4 数据迁移

    FIX 2026-08-30: user_write_lock 保护 RMW。
    """
    user_id = req.get("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    with user_write_lock(user_id) as acquired:
        if not acquired:
            raise HTTPException(503, "系统繁忙（用户数据写锁超时），请稍后重试")
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        save_user(user)
    return {"status": "ok", "version": user["portfolio"].get("version", 4)}


# ---- 盈亏计算 ----

@router.post("/api/portfolio/pnl")
def calc_portfolio_pnl(portfolio: Portfolio):
    """计算持仓的实时盈亏"""
    if not portfolio.holdings:
        return {"totalCost": 0, "totalMarket": 0, "totalPnl": 0, "totalPnlPct": 0, "holdings": []}

    results = []
    total_cost = 0
    total_market = 0

    for h in portfolio.holdings:
        cost = h.amount
        total_cost += cost

        nav_info = get_fund_nav(h.code) if h.code != "余额宝" else None
        is_estimate = False
        if nav_info and nav_info["nav"] != "N/A":
            current_nav = float(nav_info["nav"])
            nav_date = nav_info["date"]
            change_pct = float(nav_info.get("change", "0"))
            is_estimate = bool(nav_info.get("is_estimate"))
        else:
            if h.buyDate:
                try:
                    buy_dt = datetime.fromisoformat(h.buyDate.replace("Z", "+00:00"))
                    days = max((datetime.now(buy_dt.tzinfo) - buy_dt).days, 0)
                except Exception:
                    days = 0
            else:
                days = 0
            daily_rate = 0.018 / 365
            current_nav = None
            nav_date = None
            change_pct = 0
            market_val = cost * (1 + daily_rate * days)
            results.append({
                "code": h.code,
                "name": h.name,
                "category": h.category,
                "cost": round(cost, 2),
                "marketValue": round(market_val, 2),
                "pnl": round(market_val - cost, 2),
                "pnlPct": round((market_val - cost) / cost * 100, 2) if cost > 0 else 0,
                "nav": "余额宝",
                "navDate": datetime.now().strftime("%Y-%m-%d"),
                "dayChange": 0,
            })
            total_market += market_val
            continue

        buy_nav = None
        # 优先路径：前端传了 cost_nav（买入均价净值）→ 直接用 (current-cost)/cost 算收益率
        if h.cost_nav and h.cost_nav > 0:
            buy_nav = h.cost_nav
        # 次选路径：buyDate 有效且不是今天 → 查历史净值
        elif h.buyDate:
            try:
                buy_dt = datetime.fromisoformat(h.buyDate.replace("Z", "+00:00"))
                today = datetime.now(buy_dt.tzinfo).date()
                if buy_dt.date() < today:  # 只有历史日期才查，今天传今天必然查不到
                    buy_nav = _get_nav_on_date(h.code, h.buyDate)
            except Exception:
                pass

        if buy_nav and buy_nav > 0:
            growth = (current_nav - buy_nav) / buy_nav
            if h.shares and h.shares > 0:
                # 有份额：market_val = shares * current_nav
                market_val = h.shares * current_nav
            else:
                # 无份额：用增长率推算
                market_val = cost * (1 + growth)
        elif h.shares and h.shares > 0:
            # 只有份额（无买入净值）：market_val = shares * current_nav
            market_val = h.shares * current_nav
        else:
            # 无任何参考：保持成本不变（pnl=0），等用户补录数据
            market_val = cost

        pnl = market_val - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0
        total_market += market_val

        results.append({
            "code": h.code,
            "name": h.name,
            "category": h.category,
            "cost": round(cost, 2),
            "marketValue": round(market_val, 2),
            "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2),
            "nav": current_nav,
            "navDate": nav_date,
            "dayChange": change_pct,
            "isEstimate": is_estimate,  # v9.5.115: 是否盘中估值（与支付宝一致）
        })

    total_pnl = total_market - total_cost
    return {
        "totalCost": round(total_cost, 2),
        "totalMarket": round(total_market, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
        "holdings": results,
    }


# ---- 持仓体检 ----

@router.get("/api/portfolio-doctor/diagnose")
def portfolio_doctor_api(userId: str = ""):
    """完整持仓体检 — 压力测试+集中度+健康评分"""
    if not userId:
        raise HTTPException(400, "userId required")
    return diagnose(userId)


@router.get("/api/portfolio-doctor/stress-test")
def portfolio_stress_test_api(userId: str = ""):
    """压力测试 — 模拟极端场景对持仓冲击"""
    if not userId:
        raise HTTPException(400, "userId required")
    report = diagnose(userId)
    return report.get("stress_test", {"scenarios": [], "summary": "无数据"})


@router.get("/api/portfolio-doctor/health")
def portfolio_health_api(userId: str = ""):
    """健康评分 — 综合 0-100 分"""
    if not userId:
        raise HTTPException(400, "userId required")
    report = diagnose(userId)
    return report.get("health", {"score": 0, "grade": "❓"})


@router.get("/api/portfolio/overview")
def portfolio_overview_api(userId: str = "default"):
    """汇总全资产概览（股票+基金+配置占比+健康评分）"""
    return get_portfolio_overview(userId)


@router.get("/api/unified-networth")
def unified_networth_api(userId: str = ""):
    """统一净资产 — 合并所有数据源（股票+基金+手动资产+负债）"""
    if not userId:
        return {"netWorth": 0, "breakdown": {}}
    return calc_unified_networth(userId)


@router.get("/api/family/portfolio-summary")
def family_portfolio_summary(userId: str = ""):
    """家庭持仓汇总 — 只统计 LeiJiang + BuLuoGeLi 两个正式账号"""
    if not userId:
        return {"available": False}

    # 家庭成员写死（只有你和老婆，排除所有测试账号）
    family_members = ["LeiJiang", "BuLuoGeLi"]

    # 聚合每个成员的净资产
    members = []
    for mid in family_members:
        try:
            nw = calc_unified_networth(mid)
            inv = nw.get("breakdown", {}).get("investment", {})
            cash = nw.get("breakdown", {}).get("cash", {}).get("total", 0)
            liab = nw.get("breakdown", {}).get("liability", {}).get("total", 0)
            # v9.5.15: 计算该成员的投资盈亏（fundItems + stockItems + txnFundItems）
            items = (inv.get("fundItems") or []) + (inv.get("stockItems") or []) + (inv.get("txnFundItems") or [])
            total_cost = 0; total_mv = 0
            for it in items:
                mv = it.get("marketValue", 0) or 0
                shares = it.get("shares", 0) or 0
                # txnFundItems 已自带 pnl 字段，直接用；其它从 cost*shares 算
                if "pnl" in it and shares > 0:
                    cost = mv - (it.get("pnl") or 0)
                elif "costNav" in it and shares > 0:
                    cost = (it.get("costNav") or 0) * shares
                elif "costPrice" in it and shares > 0:
                    cost = (it.get("costPrice") or 0) * shares
                else:
                    cost = mv  # 没成本数据 → 盈亏 0
                total_cost += cost; total_mv += mv
            pnl = round(total_mv - total_cost, 2)
            pnl_pct = round((total_mv - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0
            members.append({
                "userId": mid,
                "stockTotal": inv.get("stockTotal", 0),
                "fundTotal": inv.get("fundTotal", 0) + inv.get("txnFundTotal", 0),
                "investTotal": inv.get("total", 0),
                "cashTotal": cash,
                "liabilityTotal": liab,
                "netWorth": nw.get("netWorth", 0),
                "stockCount": inv.get("stockCount", 0),
                "fundCount": inv.get("fundCount", 0),
                "pnl": pnl,
                "pnlPct": pnl_pct,
                # v9.5.16: 家庭成员持仓明细，前端弹窗显示对方有哪些基金 + 涨幅
                "holdings": [
                    {
                        "code": it.get("code", ""),
                        "name": it.get("name", ""),
                        "marketValue": round(it.get("marketValue", 0) or 0, 2),
                        "pnl": round(it.get("pnl", 0) or 0, 2),
                        "pnlPct": round(it.get("pnlPct", 0) or 0, 2),
                        "type": "stock" if "costPrice" in it else "fund",
                    }
                    for it in sorted(items, key=lambda x: -(x.get("marketValue", 0) or 0))[:20]
                    if (it.get("marketValue", 0) or 0) > 0
                ],
            })
        except Exception as e:
            print(f"[FAMILY] {mid} skip: {e}")
            members.append({"userId": mid, "investTotal": 0, "stockTotal": 0, "fundTotal": 0, "cashTotal": 0, "liabilityTotal": 0, "netWorth": 0, "stockCount": 0, "fundCount": 0, "pnl": 0, "pnlPct": 0, "holdings": []})

    family_invest = sum(m["investTotal"] for m in members)
    family_net = sum(m["netWorth"] for m in members)

    return {
        "available": True,
        "familyTotal": family_invest,
        "familyNetWorth": family_net,
        "members": members,
    }


# ---- 风控指标 ----

@router.post("/api/risk-metrics")
def get_risk_metrics_api(req: dict):
    """获取组合风控指标（集中度/回撤/相关性）"""
    user_id = req.get("userId", "")
    if not user_id:
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])
    return calc_risk_metrics(txs)


@router.get("/api/risk-metrics")
def get_risk_metrics_get(userId: str = ""):
    """GET 版本（insight.js 用 ?userId= 调用）"""
    return get_risk_metrics_api({"userId": userId})


@router.post("/api/risk-actions")
def get_risk_actions_api(req: dict):
    """风控硬阈值执行建议"""
    user_id = req.get("userId", "")
    if not user_id:
        txs = req.get("transactions", [])
    else:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        txs = user["portfolio"].get("transactions", [])
    try:
        vp = get_valuation_percentile()
        val_pct = vp.get("percentile", 50) if isinstance(vp, dict) else 50
    except Exception:
        val_pct = 50
    return generate_risk_actions(txs, val_pct)


@router.get("/api/risk-actions")
def get_risk_actions_get(userId: str = ""):
    """GET 版本（insight.js 用 ?userId= 调用）"""
    return get_risk_actions_api({"userId": userId})


# ---- 配置建议 ----

@router.post("/api/allocation-advice")
def get_allocation_advice_api(req: dict):
    """大类资产配置建议（股/债/现金目标比例+偏离度）

    优先从 unified-networth + stock/fund holdings 获取真实资产分布，
    旧 transactions 作为降级方案。
    """
    user_id = req.get("userId", "")

    try:
        vp = get_valuation_percentile()
        val_pct = vp.get("percentile", 50) if isinstance(vp, dict) else 50
    except Exception:
        val_pct = 50
    try:
        fgi = get_fear_greed_index()
        fg_val = fgi.get("score", 50) if isinstance(fgi, dict) else 50
    except Exception:
        fg_val = 50

    # 尝试从真实 holdings/assets 计算配置
    if user_id:
        try:
            from services.unified_networth import calc_unified_networth
            nw = calc_unified_networth(user_id)
            if nw and nw.get("netWorth", 0) > 0:
                breakdown = nw.get("breakdown", {})
                inv = (breakdown.get("investment") or {}).get("total", 0)
                cash = (breakdown.get("cash") or {}).get("total", 0)
                liability = (breakdown.get("liability") or {}).get("total", 0)
                total = inv + cash  # 不含负债的总资产

                if total > 0:
                    # 简单分类：投资=股票类，现金=现金类（后续可细分债券）
                    current_pct = {
                        "stock": round(inv / total * 100, 1),
                        "bond": 0,
                        "cash": round(cash / total * 100, 1),
                    }
                    # 动态目标
                    if val_pct > 70:
                        target = {"stock": 40, "bond": 35, "cash": 25}
                        zone = "高估"
                    elif val_pct < 30:
                        target = {"stock": 70, "bond": 20, "cash": 10}
                        zone = "低估"
                    else:
                        target = {"stock": 55, "bond": 30, "cash": 15}
                        zone = "适中"

                    deviation = {
                        "stock": round(current_pct["stock"] - target["stock"], 1),
                        "bond": round(current_pct["bond"] - target["bond"], 1),
                        "cash": round(current_pct["cash"] - target["cash"], 1),
                    }

                    advice = []
                    for asset, label in [("stock", "股票类"), ("bond", "债券类"), ("cash", "现金类")]:
                        d = deviation[asset]
                        if abs(d) > 10:
                            if d > 0:
                                advice.append({"asset": asset, "direction": "reduce",
                                    "message": f"📉 {label}超配{d:.0f}%，建议减持至{target[asset]}%"})
                            else:
                                advice.append({"asset": asset, "direction": "increase",
                                    "message": f"📈 {label}欠配{abs(d):.0f}%，可增持至{target[asset]}%"})

                    result = {
                        "target": target,
                        "current": current_pct,
                        "deviation": deviation,
                        "advice": advice,
                        "valuation_zone": zone,
                        "valuation_pct": round(val_pct, 1),
                        "fear_greed": round(fg_val, 1),
                        "total_market": round(total, 2),
                        "summary": f"✅ 资产配置分析（估值{zone} {val_pct:.0f}%）" if not advice else f"⚠️ 有{len(advice)}项需调整",
                    }
                    market_ctx = _build_market_context()
                    result = enhance_allocation_advice(result, market_ctx=market_ctx)
                    return result
        except Exception as e:
            print(f"[ALLOC] unified-networth approach failed: {e}")

    # 降级：旧 transactions 方式
    txs = []
    if user_id:
        user = load_user(user_id)
        user = ensure_v4_portfolio(user)
        portfolio = user.get("portfolio") or {}
        txs = portfolio.get("transactions", [])
    else:
        txs = req.get("transactions", [])

    result = generate_allocation_advice(txs, val_pct, fg_val)
    market_ctx = _build_market_context()
    result = enhance_allocation_advice(result, market_ctx=market_ctx)
    return result


@router.get("/api/recommend-alloc")
def get_recommend_alloc(profile: str = "稳健型", with_ai: bool = False, preference: str = "fund"):
    """推荐配置列表（基金/股票/混合）+ 配置理由 + 可选 AI 点评"""
    return get_recommend_allocations(profile, with_ai=with_ai, preference=preference)
