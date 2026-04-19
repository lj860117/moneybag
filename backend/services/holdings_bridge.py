"""
钱袋子 — 持仓存储统一桥接层（FIX 2026-04-19 F3）

背景：
  MoneyBag 有两套独立的持仓存储互不通信：
    1) V4 transactions 流水制（portfolio.transactions[]）— signal/risk/allocation/backtest 使用
    2) stock_holdings + fund_holdings 独立文件 — overview/monitor 使用

  走 transactions 建仓在 overview 完全看不到（反之亦然）。
  
设计：
  - 不改动底层存储结构（避免破坏性迁移）
  - 提供 unified_load_stock_holdings / unified_load_fund_holdings 两个函数
  - 优先读独立文件；空时回退到 V4 transactions 派生
  - 新 API 调用这里即可获得"两套合一"的视图
"""
from typing import List, Dict


def _holdings_from_transactions_stock(user_id: str) -> List[Dict]:
    """从 V4 transactions 派生股票持仓（A股代码：6位数字，6/0/3开头）"""
    try:
        from services.persistence import load_user
        from services.portfolio_calc import calc_holdings_from_transactions

        user = load_user(user_id)
        if not user:
            return []
        portfolio = user.get("portfolio", {})
        transactions = portfolio.get("transactions", [])
        if not transactions:
            return []

        calc = calc_holdings_from_transactions(transactions)
        active = calc.get("active", [])

        out = []
        for h in active:
            code = str(h.get("code", ""))
            # 只取 A 股股票（6 位数字）
            if not (code.isdigit() and len(code) == 6):
                continue
            if not (code[0] in ("6", "3") or code.startswith("000") or code.startswith("002") or code.startswith("688")):
                continue
            shares = h.get("shares", 0)
            total_cost = h.get("totalCost", 0)
            avg_nav = h.get("avgNav", 0)
            cost_price = avg_nav if avg_nav > 0 else (total_cost / shares if shares > 0 else 0)
            out.append({
                "code": code,
                "name": h.get("name", ""),
                "costPrice": round(cost_price, 3),
                "shares": shares,
                "note": "(from V4 transactions)",
                "industry": h.get("industry", ""),
                "addedAt": h.get("firstBuyDate", ""),
                "_source": "v4_transactions",
            })
        return out
    except Exception as e:
        print(f"[BRIDGE] stock from transactions failed: {e}")
        return []


def _holdings_from_transactions_fund(user_id: str) -> List[Dict]:
    """从 V4 transactions 派生基金持仓（非 A 股代码视为基金）"""
    try:
        from services.persistence import load_user
        from services.portfolio_calc import calc_holdings_from_transactions

        user = load_user(user_id)
        if not user:
            return []
        portfolio = user.get("portfolio", {})
        transactions = portfolio.get("transactions", [])
        if not transactions:
            return []

        calc = calc_holdings_from_transactions(transactions)
        active = calc.get("active", [])

        out = []
        for h in active:
            code = str(h.get("code", ""))
            # 跳过 A 股股票
            is_astock = (code.isdigit() and len(code) == 6 and
                         (code[0] in ("6", "3") or code.startswith("000") or
                          code.startswith("002") or code.startswith("688")))
            if is_astock:
                continue
            shares = h.get("shares", 0)
            total_cost = h.get("totalCost", 0)
            avg_nav = h.get("avgNav", 0)
            if avg_nav <= 0 and shares > 0:
                avg_nav = total_cost / shares
            out.append({
                "code": code,
                "name": h.get("name", ""),
                "costNav": round(avg_nav, 4),
                "shares": shares,
                "note": "(from V4 transactions)",
                "addedAt": h.get("firstBuyDate", ""),
                "_source": "v4_transactions",
            })
        return out
    except Exception as e:
        print(f"[BRIDGE] fund from transactions failed: {e}")
        return []


def unified_load_stock_holdings(user_id: str = "default") -> List[Dict]:
    """统一加载股票持仓：优先独立文件，空则回退到 V4 transactions"""
    from services.stock_monitor import load_stock_holdings
    primary = load_stock_holdings(user_id)
    if primary:
        return primary
    return _holdings_from_transactions_stock(user_id)


def unified_load_fund_holdings(user_id: str = "default") -> List[Dict]:
    """统一加载基金持仓：优先独立文件，空则回退到 V4 transactions"""
    from services.fund_monitor import load_fund_holdings
    primary = load_fund_holdings(user_id)
    if primary:
        return primary
    return _holdings_from_transactions_fund(user_id)


def unified_load_all_holdings(user_id: str = "default") -> Dict:
    """统一加载所有持仓（股票+基金）"""
    return {
        "stocks": unified_load_stock_holdings(user_id),
        "funds": unified_load_fund_holdings(user_id),
    }
