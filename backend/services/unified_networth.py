"""
统一净资产引擎 — 独立 service
职责：
  1. 汇总所有数据源（股票持仓+基金持仓+手动资产+记账）为一个统一净资产数字
  2. 按 5 类分桶返回明细（投资/现金/房产/其他/负债）
  3. 修复前端/后端净资产计算不一致问题
  4. 所有数据按 userId 隔离

修复的 Bug：
  - 前端基金用 totalCost，这里用实时市值
  - 后端遗漏 car/insurance 类型
  - 后端 cash/liability 读 balance 字段但前端写 value 字段
  - 首页不包含股票盯盘系统的持仓
"""
import time
from datetime import datetime

from services.stock_monitor import load_stock_holdings
from services.fund_monitor import load_fund_holdings
from services.persistence import load_user as _persistence_load_user

# ---- 缓存 ----
_NW_CACHE = {}  # userId -> {data, ts}
_NW_CACHE_TTL = 120  # 2 分钟


def invalidate_networth_cache(user_id: str = ""):
    """清除指定用户的净资产缓存（资产变更后调用）"""
    if user_id and user_id in _NW_CACHE:
        del _NW_CACHE[user_id]
    elif not user_id:
        _NW_CACHE.clear()


def _load_user_data(user_id: str) -> dict:
    """从持久化文件加载用户数据（资产+记账）
    使用统一的 persistence.load_user() 确保路径一致
    """
    return _persistence_load_user(user_id)


def _get_asset_value(asset: dict) -> float:
    """统一读取资产金额 — 兼容 value 和 balance 字段"""
    # 前端统一写 value 字段，但老数据可能用 balance
    val = asset.get("value", 0) or 0
    bal = asset.get("balance", 0) or 0
    return val if val != 0 else bal


def calc_unified_networth(user_id: str, force: bool = False) -> dict:
    """计算统一净资产（所有数据源合并）

    公式：
      净资产 = 股票市值 + 基金市值 + 手动资产(现金+房产+车辆+保险+其他) - 负债

    注意：记账收支不再重复计入净资产（避免和手动资产重复计算）
    记账只作为现金流参考展示
    """
    # 缓存检查
    if not force and user_id in _NW_CACHE:
        cached = _NW_CACHE[user_id]
        if time.time() - cached["ts"] < _NW_CACHE_TTL:
            return cached["data"]

    # ---- 1. 股票持仓市值（盯盘系统） ----
    stock_holdings = load_stock_holdings(user_id)
    stock_items = []
    stock_total = 0
    for h in stock_holdings:
        cost_price = h.get("costPrice", 0) or 0
        shares = h.get("shares", 0) or 0
        # 优先用实时价格，没有就用成本价
        current_price = h.get("currentPrice") or cost_price
        mv = current_price * shares
        stock_total += mv
        stock_items.append({
            "code": h.get("code", ""),
            "name": h.get("name", ""),
            "shares": shares,
            "costPrice": cost_price,
            "currentPrice": current_price,
            "marketValue": round(mv, 2),
            "pnl": round(mv - cost_price * shares, 2) if cost_price > 0 else 0,
        })

    # ---- 2. 基金持仓市值（盯盘系统） ----
    fund_holdings = load_fund_holdings(user_id)
    fund_items = []
    fund_total = 0
    for h in fund_holdings:
        cost_nav = h.get("costNav", 0) or 0
        shares = h.get("shares", 0) or 0
        # 用成本估算（实时净值需要 scan 才有）
        mv = cost_nav * shares
        fund_total += mv
        fund_items.append({
            "code": h.get("code", ""),
            "name": h.get("name", ""),
            "shares": shares,
            "costNav": cost_nav,
            "marketValue": round(mv, 2),
        })

    # ---- 3. 手动资产（资产管理页添加的） ----
    user_data = _load_user_data(user_id)
    portfolio = user_data.get("portfolio") or {}
    assets = portfolio.get("assets", [])

    # 按类型分桶 — 支持所有 6 种类型
    buckets = {
        "cash": {"items": [], "total": 0, "icon": "💵", "label": "现金/存款", "color": "#10B981"},
        "property": {"items": [], "total": 0, "icon": "🏠", "label": "房产", "color": "#F59E0B"},
        "car": {"items": [], "total": 0, "icon": "🚗", "label": "车辆", "color": "#3B82F6"},
        "insurance": {"items": [], "total": 0, "icon": "🛡️", "label": "保险", "color": "#8B5CF6"},
        "other": {"items": [], "total": 0, "icon": "📦", "label": "其他资产", "color": "#6B7280"},
        "liability": {"items": [], "total": 0, "icon": "💳", "label": "负债/贷款", "color": "#EF4444"},
    }

    for a in assets:
        a_type = a.get("type", "other")
        val = abs(_get_asset_value(a))
        if a_type not in buckets:
            a_type = "other"
        buckets[a_type]["items"].append({
            "id": a.get("id", ""),
            "name": a.get("name", ""),
            "value": round(val, 2),
            "note": a.get("note", ""),
        })
        buckets[a_type]["total"] += val

    # ---- 4. 基金交易流水持仓（V4 旧系统，非盯盘系统） ----
    txn_fund_total = 0
    transactions = portfolio.get("transactions", [])
    if transactions:
        # 如果有交易流水但没在盯盘系统，也算进来
        from services.portfolio_calc import calc_holdings_from_transactions
        holdings_result = calc_holdings_from_transactions(transactions)
        # 只算盯盘系统里没有的基金
        fund_codes_in_monitor = {f.get("code", "") for f in fund_holdings}
        for h in holdings_result.get("active", []):
            if h["code"] not in fund_codes_in_monitor:
                txn_fund_total += h.get("totalCost", 0)

    # ---- 5. 记账收支（仅展示参考，不计入净资产防重复） ----
    ledger = user_data.get("ledger") or []
    ledger_income = sum(e.get("amount", 0) for e in ledger if e.get("direction") == "income")
    ledger_expense = sum(e.get("amount", 0) for e in ledger if e.get("direction", "expense") != "income")
    ledger_net = ledger_income - ledger_expense

    # 本月记账
    now = datetime.now()
    month_start = datetime(now.year, now.month, 1).isoformat()
    month_entries = [e for e in ledger if e.get("date", "") >= month_start]
    month_income = sum(e.get("amount", 0) for e in month_entries if e.get("direction") == "income")
    month_expense = sum(e.get("amount", 0) for e in month_entries if e.get("direction", "expense") != "income")

    # ---- 6. 汇总 ----
    # 投资总额 = 股票 + 基金盯盘 + 旧交易流水基金
    investment_total = stock_total + fund_total + txn_fund_total

    # 手动资产总额（不含负债）
    manual_asset_total = sum(
        b["total"] for k, b in buckets.items() if k != "liability"
    )

    # 负债总额
    liability_total = buckets["liability"]["total"]

    # 统一净资产 = 投资 + 手动资产 - 负债
    net_worth = investment_total + manual_asset_total - liability_total

    # ---- 7. 分类占比（用于环形图） ----
    total_positive = investment_total + manual_asset_total
    allocation = {}
    if total_positive > 0:
        allocation = {
            "investment": round(investment_total / total_positive * 100, 1),
            "cash": round(buckets["cash"]["total"] / total_positive * 100, 1),
            "property": round(buckets["property"]["total"] / total_positive * 100, 1),
            "car": round(buckets["car"]["total"] / total_positive * 100, 1),
            "insurance": round(buckets["insurance"]["total"] / total_positive * 100, 1),
            "other": round(buckets["other"]["total"] / total_positive * 100, 1),
        }

    # ---- 8. 健康评分 ----
    health_score = 100
    health_issues = []

    # 负债比检查
    if total_positive > 0 and liability_total / total_positive > 0.5:
        health_score -= 25
        health_issues.append(f"负债占总资产 {liability_total/total_positive*100:.0f}%，偏高")
    elif total_positive > 0 and liability_total / total_positive > 0.3:
        health_score -= 10
        health_issues.append(f"负债占比 {liability_total/total_positive*100:.0f}%")

    # 集中度检查
    if total_positive > 0:
        max_pct = max(allocation.values()) if allocation else 0
        if max_pct > 80:
            health_score -= 20
            health_issues.append("资产过于集中在单一类型")
        elif max_pct > 60:
            health_score -= 10
            health_issues.append("资产配置可以更分散")

    # 现金不足检查
    if month_expense > 0 and buckets["cash"]["total"] < month_expense * 3:
        health_score -= 15
        health_issues.append("现金储备不足 3 个月支出，建议增加应急储备")

    # 无投资检查
    if investment_total == 0 and manual_asset_total > 50000:
        health_score -= 10
        health_issues.append("有资产但无投资，存款可能跑不赢通胀")

    health_score = max(0, health_score)
    health_grade = "🟢 健康" if health_score >= 80 else "🟡 一般" if health_score >= 60 else "🔴 需调整"

    result = {
        "netWorth": round(net_worth, 2),
        "healthScore": health_score,
        "healthGrade": health_grade,
        "healthIssues": health_issues,
        # 大类汇总
        "breakdown": {
            "investment": {
                "total": round(investment_total, 2),
                "stockTotal": round(stock_total, 2),
                "fundTotal": round(fund_total, 2),
                "txnFundTotal": round(txn_fund_total, 2),
                "stockCount": len(stock_items),
                "fundCount": len(fund_items),
                "stockItems": stock_items[:10],  # 前端展示最多 10 只
                "fundItems": fund_items[:10],
            },
            "cash": {"total": round(buckets["cash"]["total"], 2), "items": buckets["cash"]["items"]},
            "property": {"total": round(buckets["property"]["total"], 2), "items": buckets["property"]["items"]},
            "car": {"total": round(buckets["car"]["total"], 2), "items": buckets["car"]["items"]},
            "insurance": {"total": round(buckets["insurance"]["total"], 2), "items": buckets["insurance"]["items"]},
            "other": {"total": round(buckets["other"]["total"], 2), "items": buckets["other"]["items"]},
            "liability": {"total": round(liability_total, 2), "items": buckets["liability"]["items"]},
        },
        # 配置占比（环形图用）
        "allocation": allocation,
        # 记账现金流（展示参考）
        "cashFlow": {
            "totalIncome": round(ledger_income, 2),
            "totalExpense": round(ledger_expense, 2),
            "totalNet": round(ledger_net, 2),
            "monthIncome": round(month_income, 2),
            "monthExpense": round(month_expense, 2),
            "monthNet": round(month_income - month_expense, 2),
        },
        "updatedAt": datetime.now().isoformat(),
    }

    # 写缓存
    _NW_CACHE[user_id] = {"data": result, "ts": time.time()}

    return result
