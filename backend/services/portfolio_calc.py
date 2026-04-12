"""
钱袋子 — 持仓计算引擎
V4 交易流水制的核心：从流水算持仓、V3→V4迁移
"""
import uuid
from datetime import datetime
from typing import Optional
from services.data_layer import get_fund_nav, _get_nav_on_date

# ---- V4 核心计算引擎 ----

def calc_holdings_from_transactions(transactions: list[dict]) -> dict:
    """从交易流水计算当前持仓（加权平均成本法）
    参考：Ghostfolio Portfolio Calculator
    """
    holdings = {}  # code -> {shares, totalCost, avgNav, name, txCount}
    realized = {}  # code -> 已实现盈亏

    sorted_txs = sorted(transactions, key=lambda t: t.get("date", ""))

    for tx in sorted_txs:
        code = tx.get("code", "")
        if not code:
            continue

        if code not in holdings:
            holdings[code] = {
                "code": code,
                "name": tx.get("name", ""),
                "shares": 0,
                "totalCost": 0,
                "avgNav": 0,
                "txCount": 0,
                "firstBuyDate": tx.get("date", ""),
            }
        h = holdings[code]

        tx_type = tx.get("type", "BUY")
        if tx_type == "BUY":
            amount = tx.get("amount", 0)
            fee = tx.get("fee", 0)
            shares = tx.get("shares", 0)
            h["totalCost"] += amount + fee
            h["shares"] += shares
            h["avgNav"] = h["totalCost"] / h["shares"] if h["shares"] > 0 else 0
            h["txCount"] += 1
            if not h.get("name"):
                h["name"] = tx.get("name", "")

        elif tx_type == "SELL":
            shares_to_sell = tx.get("shares", 0)
            sell_nav = tx.get("nav", 0)
            fee = tx.get("fee", 0)
            if h["shares"] > 0 and shares_to_sell > 0:
                sell_cost = shares_to_sell * h["avgNav"]
                sell_revenue = shares_to_sell * sell_nav - fee
                realized[code] = realized.get(code, 0) + (sell_revenue - sell_cost)
                h["totalCost"] -= sell_cost
                h["shares"] -= shares_to_sell
                if h["shares"] < 0:
                    h["shares"] = 0

        elif tx_type == "DIVIDEND":
            div_amount = tx.get("amount", 0)
            realized[code] = realized.get(code, 0) + div_amount

    active = [h for h in holdings.values() if h["shares"] > 0]
    closed = [h for h in holdings.values() if h["shares"] <= 0 and h["txCount"] > 0]

    return {
        "active": active,
        "realized": realized,
        "closed": closed,
    }


def migrate_v3_to_v4(old_portfolio: dict) -> dict:
    """将 V3 holdings 快照转为 V4 交易流水"""
    transactions = []
    old_holdings = old_portfolio.get("holdings", [])

    for h in old_holdings:
        code = h.get("code", "")
        if not code:
            continue
        tx_id = f"migrate_{code}_{uuid.uuid4().hex[:6]}"
        transactions.append({
            "id": tx_id,
            "type": "BUY",
            "code": code,
            "name": h.get("name", ""),
            "amount": h.get("amount", 0),
            "shares": 0,  # 待后端补算
            "nav": 0,     # 待后端补算
            "fee": 0,
            "date": h.get("buyDate", datetime.now().isoformat()),
            "source": "recommend",
            "note": "V3迁移",
        })

    return {
        "transactions": transactions,
        "assets": [],
        "profile": old_portfolio.get("profile"),
        "history": old_portfolio.get("history", []),
        "version": 4,
    }


def ensure_v4_portfolio(user_data: dict) -> dict:
    """确保用户数据中的 portfolio 是 V4 格式"""
    p = user_data.get("portfolio")
    if not p:
        user_data["portfolio"] = {
            "transactions": [],
            "assets": [],
            "profile": None,
            "history": [],
            "version": 4,
        }
        return user_data

    if p.get("version") == 4:
        return user_data

    # V3 → V4 迁移
    if "holdings" in p and p["holdings"]:
        user_data["portfolio"] = migrate_v3_to_v4(p)
        # 补算净值和份额
        for tx in user_data["portfolio"]["transactions"]:
            if tx["shares"] == 0 and tx["amount"] > 0:
                buy_nav = _get_nav_on_date(tx["code"], tx["date"])
                if buy_nav and buy_nav > 0:
                    tx["nav"] = buy_nav
                    tx["shares"] = round(tx["amount"] / buy_nav, 2)
                else:
                    # 无法获取历史净值，用当前净值近似
                    nav_info = get_fund_nav(tx["code"])
                    if nav_info and nav_info["nav"] != "N/A":
                        tx["nav"] = float(nav_info["nav"])
                        tx["shares"] = round(tx["amount"] / float(nav_info["nav"]), 2)
                    else:
                        tx["nav"] = 1.0
                        tx["shares"] = tx["amount"]
    else:
        user_data["portfolio"] = {
            "transactions": [],
            "assets": [],
            "profile": p.get("profile"),
            "history": p.get("history", []),
            "version": 4,
        }

    return user_data


