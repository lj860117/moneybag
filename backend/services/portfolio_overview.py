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

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "portfolio_overview",
    "scope": "private",
    "input": ["user_id"],
    "output": "overview",
    "cost": "cpu",
    "tags": ["组合总览", "配置占比", "偏离度"],
    "description": "资产总览：股票+基金汇总净资产+配置占比+偏离度检测",
    "layer": "data",
    "priority": 2,
}

# ---- 导入各持仓模块 ----
# FIX 2026-04-19 F3: 改用 holdings_bridge 统一两套存储（独立文件 + V4 transactions）
from services.holdings_bridge import unified_load_stock_holdings, unified_load_fund_holdings
from services.stock_monitor import scan_all_holdings
from services.fund_monitor import scan_all_fund_holdings
# FIX 2026-05-20 MB-008: 导入统一的基金分类器，避免代码重复
from services.fund_classifier import classify_and_allocate


def get_portfolio_overview(user_id: str = "default") -> dict:
    """汇总全资产，返回统一概览数据"""
    # 1. 股票持仓 — 用实时价格计算市值
    # FIX 2026-08-09: 排查代码漂移发现本地版本这里退化成"直接用成本价当市值"
    # （注释写"需要scan才有"但从未真正调用scan/批量取价），导致资产总览的
    # 股票市值/配置占比在涨跌幅大时明显失真。合并回服务器版本的实时取价逻辑。
    stock_holdings = unified_load_stock_holdings(user_id)
    stock_total_mv = 0
    stock_total_cost = 0
    stock_count = 0

    # 批量获取实时价格
    stock_rt_map = {}
    try:
        from services.stock_monitor import get_stock_realtime
        for h in stock_holdings:
            code = h.get("code", "")
            if code:
                rt = get_stock_realtime(code)
                if rt and rt.get("price"):
                    stock_rt_map[code] = rt["price"]
    except Exception:
        pass

    for h in stock_holdings:
        cost_price = h.get("costPrice", 0) or 0
        shares = h.get("shares", 0) or 0
        code = h.get("code", "")
        if cost_price and shares:
            stock_total_cost += cost_price * shares
            stock_count += 1
        # 用实时价格，没有就用成本价
        current_price = stock_rt_map.get(code) or cost_price
        stock_total_mv += current_price * shares

    # 2. 基金持仓 — FIX 2026-05-20 MB-008: 使用新的分类器，支持混合/QDII 基金
    fund_holdings = unified_load_fund_holdings(user_id)
    fund_total_mv = 0
    fund_total_cost = 0
    fund_count = 0
    fund_equity = 0      # 基金中的股票类占比
    fund_bond = 0        # 基金中的债券类占比
    fund_money = 0       # 基金中的现金类占比
    fund_gold = 0        # 基金中的黄金占比

    for h in fund_holdings:
        cost_nav = h.get("costNav", 0) or 0
        shares = h.get("shares", 0) or 0
        cost = cost_nav * shares
        fund_total_cost += cost
        # 优先用实时净值计算市值，fallback 到成本净值
        current_nav = cost_nav
        code = h.get("code", "")
        if code:
            try:
                from services.market_data import get_fund_nav as _get_nav
                nav_info = _get_nav(code)
                if nav_info and nav_info.get("nav") not in ("N/A", None, ""):
                    current_nav = float(nav_info["nav"])
            except Exception:
                pass
        fund_total_mv += current_nav * shares
        fund_count += 1

        # 使用新分类器，支持混合/QDII 基金的比例分配
        allocation = classify_and_allocate(
            code=h.get("code", ""),
            name=h.get("name", ""),
            nav_cost=h.get("costNav", 0),
            shares=h.get("shares", 0),
        )
        fund_equity += allocation["equity"]
        fund_bond += allocation["bond"]
        fund_money += allocation["money"]
        fund_gold += allocation["gold"]

    # 3. 总资产
    total_mv = stock_total_mv + fund_total_mv
    total_cost = stock_total_cost + fund_total_cost
    total_pnl = total_mv - total_cost if total_cost > 0 else 0
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    # 4. 资产配置占比（股票类/债券类/现金类/黄金）
    #
    # FIX 2026-09-15 (gold 桶): gold 现在是**独立第 4 档**，不再并进 equity。
    #   旧代码是 `equity = stock_total_mv + fund_equity + fund_gold`，即分类侧
    #   （fund_classifier.KNOWN_FUND_TYPES 有 000216/518880 → "gold"）分出来的
    #   第 4 档，在展示层被显式吞进 equity，allocation 输出只有 3 个键。
    #   这与同一次 MB-008 提交里的 risk.py:172
    #       `has_hedge = bond_n > 0 or gold_n > 0`
    #   直接矛盾 —— 风控侧把黄金当权益的**对冲资产**，这里却把它当**权益本身**。
    #   二者不可能同时成立，故判定为 bug：归入 equity 是错的。
    #
    # TODO(单独排期，本轮不动): fund_equity/bond/money/gold 走的是**成本**口径
    #   （fund_classifier.classify_and_allocate: `total_cost = nav_cost * shares`），
    #   而 stock_total_mv 走的是**市值**口径（本文件上方 :67 用实时价），
    #   两者被加进同一个 total_for_alloc。属独立缺陷，另行立项。
    equity = stock_total_mv + fund_equity
    bond = fund_bond
    cash = fund_money
    gold = fund_gold
    # 分母保持不变：gold 只是从 equity 挪出来单列，仍计入总配置口径。
    # 由此产生的不变量：equity% 的降幅恰好等于黄金占比，bond%/cash% 不变。
    total_for_alloc = equity + bond + cash + gold

    allocation = {
        "equity": round(equity / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "bond": round(bond / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "cash": round(cash / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
        "gold": round(gold / total_for_alloc * 100, 1) if total_for_alloc > 0 else 0,
    }

    # 默认目标配置（基于稳健型）+ 黄金独立第 4 档
    # 黄金目标取 domain/rule_engine/glide_path_rules.py:35 GOLD_PCT_DEFAULT = 0.05，
    # 与该表各年龄档 gold 恒为 5 一致。该表 stock_pct 的注释明确写
    # 「股票目标占比（**已扣除黄金**）」，因此从原 equity 50 中扣出 5 给 gold，
    # 四档合计仍为 100。
    target = {"equity": 45, "bond": 30, "cash": 20, "gold": 5}
    deviation = {
        "equity": round(allocation["equity"] - target["equity"], 1),
        "bond": round(allocation["bond"] - target["bond"], 1),
        "cash": round(allocation["cash"] - target["cash"], 1),
        "gold": round(allocation["gold"] - target["gold"], 1),
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

    # 配置偏离检查（含黄金第 4 档）
    max_dev = max(abs(deviation["equity"]), abs(deviation["bond"]),
                  abs(deviation["cash"]), abs(deviation["gold"]))
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
        for asset, label in [("equity", "股票类"), ("bond", "债券类"),
                             ("cash", "现金类"), ("gold", "黄金类")]:
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
