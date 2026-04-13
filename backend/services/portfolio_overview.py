"""
资产总览引擎 — 独立 service
职责：
  1. 汇总股票+基金+其他资产的净资产
  2. 计算资产配置占比（股/债/现）
  3. 偏离度检测
  4. 为持仓页提供统一 Hero 数据
"""
import json
from pathlib import Path
from datetime import datetime

# ---- 导入各持仓模块 ----
from services.stock_monitor import load_stock_holdings, scan_all_holdings
from services.fund_monitor import load_fund_holdings, scan_all_fund_holdings


def get_portfolio_overview(user_id: str = "default") -> dict:
    """汇总全资产，返回统一概览数据"""
    # 1. 股票持仓
    stock_holdings = load_stock_holdings(user_id)
    stock_total_mv = 0
    stock_total_cost = 0
    stock_count = 0
    for h in stock_holdings:
        if h.get("costPrice") and h.get("shares"):
            stock_total_cost += h["costPrice"] * h["shares"]
            stock_count += 1
        # market value 需要 scan 才有，这里先用 cost 估算
        stock_total_mv += h.get("costPrice", 0) * h.get("shares", 0)

    # 2. 基金持仓
    fund_holdings = load_fund_holdings(user_id)
    fund_total_mv = 0
    fund_total_cost = 0
    fund_count = 0
    fund_stock_type = 0  # 股票型/混合型基金的市值
    fund_bond_type = 0   # 债券型基金的市值
    fund_money_type = 0  # 货币基金的市值

    for h in fund_holdings:
        cost = h.get("costNav", 0) * h.get("shares", 0)
        fund_total_cost += cost
        fund_total_mv += cost  # 先用成本估算，scan 会更新
        fund_count += 1

        # 按基金名称粗分类型
        name = (h.get("name") or "").lower()
        if any(k in name for k in ["货币", "money", "余额", "现金"]):
            fund_money_type += cost
        elif any(k in name for k in ["债", "bond", "纯债", "信用"]):
            fund_bond_type += cost
        else:
            fund_stock_type += cost

    # 3. 总资产
    total_mv = stock_total_mv + fund_total_mv
    total_cost = stock_total_cost + fund_total_cost
    total_pnl = total_mv - total_cost if total_cost > 0 else 0
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    # 4. 资产配置占比（股票类/债券类/现金类）
    equity = stock_total_mv + fund_stock_type  # 股票持仓 + 股票型基金
    bond = fund_bond_type
    cash = fund_money_type
    total_for_alloc = equity + bond + cash

    allocation = {
        "equity": round(equity / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "bond": round(bond / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "cash": round(cash / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
    }

    # 默认目标配置（基于稳健型）
    target = {"equity": 50, "bond": 30, "cash": 20}
    deviation = {
        "equity": round(allocation["equity"] - target["equity"], 1),
        "bond": round(allocation["bond"] - target["bond"], 1),
        "cash": round(allocation["cash"] - target["cash"], 1),
    }

    # 5. 健康评分（简化版 Seeking Alpha Health Score）
    health_score = 100
    health_issues = []

    # 集中度检查
    if stock_count + fund_count > 0:
        if stock_count + fund_count < 3:
            health_score -= 20
            health_issues.append("持仓过于集中（<3 只），建议分散")
        if stock_count + fund_count > 20:
            health_score -= 10
            health_issues.append("持仓过多（>20 只），难以跟踪")

    # 配置偏离检查
    max_dev = max(abs(deviation["equity"]), abs(deviation["bond"]), abs(deviation["cash"]))
    if max_dev > 20:
        health_score -= 25
        health_issues.append(f"资产配置严重偏离目标（最大偏离 {max_dev}%）")
    elif max_dev > 10:
        health_score -= 10
        health_issues.append(f"资产配置偏离目标（{max_dev}%），建议再平衡")

    # 全部是股票类
    if total_for_alloc > 0 and allocation["equity"] > 90:
        health_score -= 15
        health_issues.append("几乎全部是权益类，缺乏防御性配置")

    health_score = max(0, health_score)
    health_grade = "🟢 健康" if health_score >= 80 else "🟡 一般" if health_score >= 60 else "🔴 需调整"

    # 6. 再平衡建议
    rebalance = []
    if total_for_alloc > 0:
        for asset, label in [("equity", "股票类"), ("bond", "债券类"), ("cash", "现金类")]:
            d = deviation[asset]
            if abs(d) > 10:
                direction = "reduce" if d > 0 else "increase"
                emoji = "📉" if d > 0 else "📈"
                amount = abs(d) / 100 * total_for_alloc
                rebalance.append({
                    "asset": asset,
                    "label": label,
                    "direction": direction,
                    "deviation": d,
                    "amount": round(amount, 0),
                    "message": f"{emoji} {label}{'超配' if d > 0 else '欠配'}{abs(d):.0f}%，"
                               f"建议{'减持' if d > 0 else '增持'} ¥{amount:,.0f}",
                })

    return {
        "totalMarketValue": round(total_mv, 2),
        "totalCost": round(total_cost, 2),
        "totalPnl": round(total_pnl, 2),
        "totalPnlPct": round(total_pnl_pct, 2),
        "stockCount": stock_count,
        "fundCount": fund_count,
        "stockValue": round(stock_total_mv, 2),
        "fundValue": round(fund_total_mv, 2),
        "allocation": allocation,
        "target": target,
        "deviation": deviation,
        "healthScore": health_score,
        "healthGrade": health_grade,
        "healthIssues": health_issues,
        "rebalance": rebalance,
        "updatedAt": datetime.now().isoformat(),
    }
