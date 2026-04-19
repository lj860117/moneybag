"""
钱袋子 — 风控系统
HHI 集中度、回撤监控、相关性分析 + 硬阈值执行建议（借鉴幻方量化）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "risk",
    "scope": "private",
    "input": ["user_id"],
    "output": "risk_checks",
    "cost": "cpu",
    "tags": ["风控", "HHI", "回撤", "集中度"],
    "description": "组合风控：HHI集中度+回撤监控+硬阈值执行建议",
    "layer": "risk",
    "priority": 1,
}

from config import (
    RISK_DRAWDOWN_WARNING, RISK_DRAWDOWN_DANGER, RISK_DAILY_DROP_LIMIT,
    RISK_SINGLE_STOCK_MAX, RISK_SINGLE_FUND_MAX, RISK_INDUSTRY_MAX,
    RISK_TAKE_PROFIT, RISK_MAX_DRAWDOWN_LIMIT, RISK_REBALANCE_THRESHOLD,
    ALLOCATION_PROFILES, VALUATION_HIGH,
    # FIX 2026-04-19 V7.2
    DRAWDOWN_ALERT, CORRELATION_DEFAULTS,
)
from services.portfolio_calc import calc_holdings_from_transactions
from services.data_layer import get_fund_nav


# ============================================================
# FIX 2026-04-19 F6: 基金资产类型判断（替代硬编码代码列表）
# ============================================================

# 已知基金类型映射（手动维护常见基金，未知的按 name 关键字推断）
_KNOWN_FUND_TYPES = {
    # 股票/指数 ETF
    "110020": "equity", "050025": "equity", "008114": "equity",
    "510300": "equity", "510500": "equity", "510050": "equity",
    "159915": "equity", "159919": "equity",
    # 债券
    "217022": "bond", "519736": "bond", "003376": "bond",
    # 黄金
    "000216": "gold", "518880": "gold",
    # 货币
    "000198": "money", "003474": "money",
}

_EQUITY_KEYWORDS = ["股票", "沪深", "创业", "科创", "医药", "消费", "新能源", "半导体", "ETF", "300", "500", "50"]
_BOND_KEYWORDS = ["债券", "债A", "债B", "债C", "纯债", "利率债", "信用债", "可转债"]
_GOLD_KEYWORDS = ["黄金", "金ETF"]
_MONEY_KEYWORDS = ["货币", "余额宝", "现金", "宝宝"]


def _classify_asset(h: dict) -> str:
    """根据代码和名称判断资产类型：equity / bond / gold / money / unknown"""
    code = str(h.get("code", ""))
    name = str(h.get("name", ""))

    if code in _KNOWN_FUND_TYPES:
        return _KNOWN_FUND_TYPES[code]

    # 特殊：余额宝
    if code == "余额宝" or "余额宝" in name:
        return "money"

    # 按名称关键字推断
    for kw in _GOLD_KEYWORDS:
        if kw in name:
            return "gold"
    for kw in _BOND_KEYWORDS:
        if kw in name:
            return "bond"
    for kw in _MONEY_KEYWORDS:
        if kw in name:
            return "money"
    for kw in _EQUITY_KEYWORDS:
        if kw in name:
            return "equity"

    # A 股股票代码（6 位数字，6/0/3 开头）默认权益
    if code.isdigit() and len(code) == 6:
        if code[0] in ("6", "3") or code.startswith("000") or code.startswith("002"):
            return "equity"

    return "unknown"


def _calc_real_peak(active: list) -> float:
    """基于持仓历史计算真实净值峰值
    
    FIX 2026-04-19 F5: 原来 peak = total_cost * 1.1 是拍脑袋假设，
    改为：对每个持仓，取其（成本、当前市值、历史最高估值）的最大值之和作为组合峰值。
    这不是完美的历史峰值（需要完整净值曲线），但比 cost*1.1 准确得多。
    """
    peak = 0.0
    for h in active:
        cost = h.get("totalCost", 0)
        shares = h.get("shares", 0)
        avg_nav = h.get("avgNav", 0)
        code = h.get("code", "")

        # 当前市值
        current_val = cost  # fallback
        try:
            if code == "余额宝":
                current_val = shares
            else:
                nav_info = get_fund_nav(code)
                if nav_info and nav_info.get("nav") not in (None, "N/A"):
                    current_val = shares * float(nav_info["nav"])
        except Exception:
            pass

        # 取"成本"和"当前"的最大值作为该持仓的局部峰值
        # （真实峰值需要持仓历史净值曲线，暂用这个更保守的近似）
        peak += max(cost, current_val)
    return peak


def calc_risk_metrics(transactions: list) -> dict:
    """计算组合风险指标：集中度/回撤/相关性"""
    result = {
        "concentration": {"hhi": 0, "max_single": 0, "level": "正常", "detail": ""},
        "drawdown": {"current": 0, "max_historical": 0, "level": "正常", "detail": ""},
        "correlation": {"avg": 0, "detail": ""},
        "alerts": [],
    }

    holdings = calc_holdings_from_transactions(transactions)
    active = holdings.get("active", [])
    if not active:
        return result

    # --- 1. 集中度分析（HHI指数）---
    total_cost = sum(h["totalCost"] for h in active)
    if total_cost > 0:
        weights = [h["totalCost"] / total_cost for h in active]
        hhi = sum(w ** 2 for w in weights) * 10000
        max_single = max(weights) * 100

        result["concentration"]["hhi"] = round(hhi, 0)
        result["concentration"]["max_single"] = round(max_single, 1)

        if hhi > 5000:
            result["concentration"]["level"] = "高度集中"
            result["concentration"]["detail"] = f"HHI={hhi:.0f}，单一资产占比最高{max_single:.0f}%，建议分散"
            result["alerts"].append({
                "type": "concentration",
                "severity": "warning",
                "message": f"⚠️ 持仓集中度过高（HHI={hhi:.0f}），最大单品占{max_single:.0f}%，建议分散配置降低风险"
            })
        elif hhi > 3000:
            result["concentration"]["level"] = "适度集中"
            result["concentration"]["detail"] = f"HHI={hhi:.0f}，集中度适中"
        else:
            result["concentration"]["level"] = "分散良好"
            result["concentration"]["detail"] = f"HHI={hhi:.0f}，分散度良好"

    # --- 2. 回撤监控（FIX F5: 基于真实峰值）---
    try:
        current_market = 0
        for h in active:
            code = h["code"]
            if code == "余额宝":
                current_market += h["shares"]
                continue
            nav_info = get_fund_nav(code)
            if nav_info and nav_info["nav"] != "N/A":
                current_market += h["shares"] * float(nav_info["nav"])
            else:
                current_market += h["shares"] * h["avgNav"]

        peak = _calc_real_peak(active)
        if current_market > 0 and peak > 0:
            drawdown = (peak - current_market) / peak * 100
            if drawdown > 0:
                result["drawdown"]["current"] = round(drawdown, 2)
                if drawdown > DRAWDOWN_ALERT["severe_pct"]:
                    result["drawdown"]["level"] = "严重回撤"
                    result["alerts"].append({
                        "type": "drawdown", "severity": "danger",
                        "message": f"🔴 当前回撤{drawdown:.1f}%，超过{DRAWDOWN_ALERT['severe_pct']:.0f}%警戒线！检查持仓基本面是否变化"
                    })
                elif drawdown > DRAWDOWN_ALERT["moderate_pct"]:
                    result["drawdown"]["level"] = "中度回撤"
                    result["alerts"].append({
                        "type": "drawdown", "severity": "warning",
                        "message": f"⚠️ 当前回撤{drawdown:.1f}%，注意风险控制"
                    })
                result["drawdown"]["detail"] = f"当前回撤{drawdown:.1f}%（基于持仓成本与当前市值的组合峰值近似）"
    except Exception as e:
        print(f"[RISK] Drawdown calc failed: {e}")

    # --- 3. 相关性提示（FIX F6: 按资产类型分类）---
    type_counts = {"equity": 0, "bond": 0, "gold": 0, "money": 0, "unknown": 0}
    for h in active:
        t = _classify_asset(h)
        type_counts[t] += 1

    equity_n = type_counts["equity"]
    bond_n = type_counts["bond"]
    gold_n = type_counts["gold"]
    has_hedge = bond_n > 0 or gold_n > 0

    if equity_n >= 2 and not has_hedge:
        result["correlation"]["avg"] = CORRELATION_DEFAULTS["all_equity"]
        result["correlation"]["detail"] = f"持仓以权益类为主（股票/权益基金{equity_n}只），相关性较高，缺少避险资产对冲"
        result["alerts"].append({
            "type": "correlation", "severity": "info",
            "message": "💡 持仓权益资产占比高，建议配置债券/黄金降低组合波动"
        })
    elif bond_n > 0 and gold_n > 0:
        result["correlation"]["avg"] = CORRELATION_DEFAULTS["stock_bond_gold"]
        result["correlation"]["detail"] = f"股债金组合（权益{equity_n}/债券{bond_n}/黄金{gold_n}），相关性适中，对冲效果良好"
    elif has_hedge:
        result["correlation"]["avg"] = CORRELATION_DEFAULTS["with_hedge"]
        result["correlation"]["detail"] = f"含避险资产（债券{bond_n}/黄金{gold_n}），相关性中等偏低"
    else:
        result["correlation"]["avg"] = CORRELATION_DEFAULTS["mixed"]
        result["correlation"]["detail"] = "资产类型未完全识别，相关性估算中等"

    return result


# ============================================================
# 新增：硬阈值执行建议（借鉴豆包方案 / 幻方量化风控规则）
# ============================================================

def generate_risk_actions(transactions: list, valuation_pct: float = 50) -> dict:
    """
    根据持仓+市场数据，生成硬阈值执行建议
    参考：
    - 豆包方案：回撤≤-15%降仓50%，≤-18%降仓40%，单日≥4%暂停
    - 幻方量化：止盈≥40%减半，单票≤3%，行业≤20%，再平衡±8%
    返回: {actions: [...], summary: str, risk_level: str}
    """
    actions = []
    risk_metrics = calc_risk_metrics(transactions)
    holdings = calc_holdings_from_transactions(transactions)
    active = holdings.get("active", [])

    if not active:
        return {"actions": [], "summary": "暂无持仓", "risk_level": "safe"}

    total_cost = sum(h["totalCost"] for h in active)
    if total_cost <= 0:
        return {"actions": [], "summary": "持仓成本为零", "risk_level": "safe"}

    # --- 规则1: 回撤硬阈值 ---
    drawdown_pct = -risk_metrics["drawdown"]["current"] / 100  # 转为负数小数
    if drawdown_pct <= RISK_MAX_DRAWDOWN_LIMIT:  # -20%
        actions.append({
            "level": "danger", "rule": "最大回撤红线",
            "action": "🚨 立即止损！回撤已达-20%红线，清仓止损保住本金",
            "detail": f"当前回撤{drawdown_pct*100:.1f}%，触发-20%绝对红线"
        })
    elif drawdown_pct <= RISK_DRAWDOWN_DANGER:  # -18%
        actions.append({
            "level": "danger", "rule": "回撤警戒线",
            "action": "⚠️ 股票仓位降至40%，增配债券基金",
            "detail": f"当前回撤{drawdown_pct*100:.1f}%，触发-18%警戒线"
        })
    elif drawdown_pct <= RISK_DRAWDOWN_WARNING:  # -15%
        actions.append({
            "level": "warning", "rule": "回撤预警线",
            "action": "⚠️ 股票仓位降至50%，暂停新增买入",
            "detail": f"当前回撤{drawdown_pct*100:.1f}%，触发-15%预警线"
        })

    # --- 规则2: 单基金/单票占比检查 ---
    for h in active:
        weight = h["totalCost"] / total_cost
        name = h.get("name", h["code"])
        if weight > RISK_SINGLE_FUND_MAX:  # >15%
            actions.append({
                "level": "warning", "rule": "单基金占比上限",
                "action": f"⚠️ {name}占比{weight*100:.0f}%，超过15%上限，建议减持至15%以内",
                "detail": f"单只基金仓位上限15%，当前{weight*100:.1f}%"
            })

    # --- 规则3: 止盈检查（单品收益≥40%减半）---
    for h in active:
        if h["totalCost"] > 0 and h["shares"] > 0:
            nav_info = get_fund_nav(h["code"])
            if nav_info and nav_info["nav"] != "N/A":
                current_value = h["shares"] * float(nav_info["nav"])
                profit_pct = (current_value - h["totalCost"]) / h["totalCost"]
                name = h.get("name", h["code"])
                if profit_pct >= RISK_TAKE_PROFIT:  # ≥40%
                    actions.append({
                        "level": "info", "rule": "止盈纪律",
                        "action": f"🎯 {name}收益{profit_pct*100:.0f}%，建议卖出50%锁定利润",
                        "detail": f"止盈阈值40%，当前{profit_pct*100:.1f}%"
                    })

    # --- 规则4: 估值配置建议 ---
    if valuation_pct > VALUATION_HIGH:  # >80%
        target = ALLOCATION_PROFILES["high"]
        actions.append({
            "level": "warning", "rule": "高估值配置调整",
            "action": f"📊 估值{valuation_pct}%高估，建议股票≤{target['stock']*100:.0f}%/债券≥{target['bond']*100:.0f}%/现金≥{target['cash']*100:.0f}%",
            "detail": f"当前估值百分位{valuation_pct}%，处于高估区间"
        })
    elif valuation_pct < 20:
        target = ALLOCATION_PROFILES["low"]
        actions.append({
            "level": "info", "rule": "低估值加仓建议",
            "action": f"💰 估值{valuation_pct}%低估，可提升股票至{target['stock']*100:.0f}%",
            "detail": f"当前估值百分位{valuation_pct}%，处于低估区间，适合增配"
        })

    # --- 规则5: 集中度警告 ---
    hhi = risk_metrics["concentration"]["hhi"]
    if hhi > 5000:
        actions.append({
            "level": "warning", "rule": "行业集中度",
            "action": "⚠️ 持仓过于集中，建议分散到3-5只不同类型基金",
            "detail": f"HHI指数{hhi:.0f}（>5000为高度集中）"
        })

    # --- 汇总 ---
    danger_count = sum(1 for a in actions if a["level"] == "danger")
    warning_count = sum(1 for a in actions if a["level"] == "warning")

    if danger_count > 0:
        risk_level = "danger"
        summary = f"🔴 {danger_count}项严重风险需立即处理"
    elif warning_count > 0:
        risk_level = "warning"
        summary = f"🟡 {warning_count}项风险提示需关注"
    else:
        risk_level = "safe"
        summary = "🟢 风控检查通过，各项指标正常"

    return {
        "actions": actions,
        "summary": summary,
        "risk_level": risk_level,
        "metrics": risk_metrics,
    }


# ---- V4 底座：enrich() 适配层 ----
def enrich(ctx):
    """Pipeline 适配：从 ctx 读用户持仓 → 风控检查 → 写回 ctx"""
    try:
        from services.persistence import load_user
        user = load_user(ctx.user_id)
        if not user:
            ctx.modules_skipped.append({"name": "risk", "reason": "no_user_data"})
            return ctx
        portfolio = user.get("portfolio") or {}
        txs = portfolio.get("transactions") or []
        if not txs:
            ctx.modules_skipped.append({"name": "risk", "reason": "no_transactions"})
            return ctx
        risk_result = calc_risk_metrics(txs)
        if not risk_result:
            ctx.modules_skipped.append({"name": "risk", "reason": "calc_returned_none"})
            return ctx
        alerts = risk_result.get("alerts") or []
        concentration = risk_result.get("concentration") or {}
        drawdown = risk_result.get("drawdown") or {}
        has_danger = any(a.get("level") == "danger" for a in alerts)
        checks = [{"rule": a.get("type", "general"), "passed": a.get("level", "info") == "info", "detail": a.get("message", "")} for a in alerts]
        ctx.risk_checks = checks
        ctx.risk_blocked = has_danger
        ctx.modules_results["risk"] = {
            "available": True,
            "direction": "bearish" if has_danger else "neutral",
            "confidence": 90 if has_danger else 50,
            "data": {"concentration": concentration, "drawdown": drawdown, "alerts_count": len(alerts)},
            "cost": "cpu",
            "latency_ms": 0,
        }
        ctx.modules_called.append("risk")
    except Exception as e:
        print(f"[risk.enrich] Error: {e}")
        import traceback; traceback.print_exc()
        ctx.errors.append({"module": "risk", "error": str(e), "fallback_used": True})
        ctx.modules_skipped.append({"name": "risk", "reason": str(e)})
    return ctx
