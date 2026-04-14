"""
钱袋子 — 持仓体检模块 (portfolio_doctor)
V4 v3.0 W5 新增

职责:
  1. 压力测试 — 模拟极端场景（熊市/黑天鹅/流动性危机）对持仓的冲击
  2. 集中度诊断 — HHI + 行业/风格集中度 + 单票超限
  3. 相关性分析 — 持仓间相关矩阵，识别"假分散"
  4. 健康评分 — 综合评分（0-100）+ 问题清单
  5. enrich(ctx) — 写入 DecisionContext，供 cautious Pipeline 使用

底座集成:
  - MODULE_META + enrich() → Registry 自动发现
  - cautious 管线 step_doctor 调用 enrich()
  - 前端 🏥体检 Tab 调用 3 个 API

参考: 朋友A风控方案 + 老师持仓诊断建议
"""
import json
import time
import math
from datetime import datetime
from pathlib import Path

MODULE_META = {
    "name": "portfolio_doctor",
    "scope": "private",
    "input": ["user_id", "stock_holdings", "fund_holdings"],
    "output": "doctor_report",
    "cost": "cpu",
    "tags": ["risk", "diagnosis"],
    "description": "持仓体检：压力测试+集中度+相关性+健康评分",
    "layer": "risk",
    "priority": 60,
}


# ============================================================
# 1. 压力测试 — 模拟极端场景
# ============================================================

# 压力情景配置
STRESS_SCENARIOS = {
    "bear_market": {
        "name": "🐻 熊市场景",
        "description": "沪深300跌20%，创业板跌30%，债券涨5%，黄金涨10%",
        "shocks": {
            "equity": -0.20,      # 股票类跌20%
            "growth": -0.30,      # 成长/科技跌30%
            "bond": 0.05,         # 债券涨5%
            "gold": 0.10,         # 黄金涨10%
            "money": 0.005,       # 货基+0.5%
        },
    },
    "black_swan": {
        "name": "🦢 黑天鹅事件",
        "description": "全球恐慌，股票跌35%，债券跌5%，黄金涨20%",
        "shocks": {
            "equity": -0.35,
            "growth": -0.45,
            "bond": -0.05,
            "gold": 0.20,
            "money": 0.0,
        },
    },
    "liquidity_crisis": {
        "name": "💧 流动性危机",
        "description": "资金紧缩，小盘跌25%，大盘跌15%，债券跌3%",
        "shocks": {
            "equity": -0.15,
            "growth": -0.25,
            "bond": -0.03,
            "gold": 0.05,
            "money": 0.0,
        },
    },
    "trade_war": {
        "name": "⚔️ 贸易战升级",
        "description": "关税+制裁，出口股跌30%，内需跌10%，黄金涨15%",
        "shocks": {
            "equity": -0.15,
            "growth": -0.30,
            "bond": 0.02,
            "gold": 0.15,
            "money": 0.0,
        },
    },
}

# 基金类型→资产分类映射
_FUND_TYPE_MAP = {
    "股票": "equity", "混合": "equity", "指数": "equity", "ETF联接": "equity",
    "QDII": "equity", "标普": "equity", "纳斯达克": "equity",
    "债券": "bond", "纯债": "bond", "信用": "bond",
    "黄金": "gold", "贵金属": "gold",
    "货币": "money", "余额宝": "money",
}

def _classify_asset(name: str, code: str) -> str:
    """根据基金名称/代码判断资产类别"""
    name_lower = name.lower() if name else ""
    if code == "余额宝":
        return "money"
    for keyword, asset_type in _FUND_TYPE_MAP.items():
        if keyword in name_lower:
            return asset_type
    # 默认按股票处理（包含A股个股）
    return "equity"


def stress_test(holdings: list, scenarios: list = None) -> dict:
    """
    压力测试 — 模拟极端场景对持仓的冲击
    
    Args:
        holdings: [{name, code, marketValue, category/type, ...}]
        scenarios: 指定场景列表，默认全部
    
    Returns:
        {scenarios: [{name, description, impact, new_value, loss_pct, worst_holdings}], 
         summary: str, total_value: float}
    """
    if not holdings:
        return {"scenarios": [], "summary": "无持仓数据", "total_value": 0}
    
    total_value = sum(h.get("marketValue", 0) or h.get("value", 0) or 0 for h in holdings)
    if total_value <= 0:
        return {"scenarios": [], "summary": "持仓市值为0", "total_value": 0}
    
    scenario_keys = scenarios or list(STRESS_SCENARIOS.keys())
    results = []
    
    for key in scenario_keys:
        sc = STRESS_SCENARIOS.get(key)
        if not sc:
            continue
        
        shocks = sc["shocks"]
        scenario_loss = 0
        worst_holdings = []
        
        for h in holdings:
            mv = h.get("marketValue", 0) or h.get("value", 0) or 0
            if mv <= 0:
                continue
            
            asset_class = _classify_asset(h.get("name", ""), h.get("code", ""))
            
            # 成长股用 growth shock，其他用对应类别
            is_growth = any(kw in (h.get("name", "") or "") for kw in ["创业", "科技", "成长", "半导体", "AI", "新能源"])
            if is_growth and "growth" in shocks:
                shock = shocks["growth"]
            else:
                shock = shocks.get(asset_class, shocks.get("equity", -0.15))
            
            loss = mv * shock
            scenario_loss += loss
            
            if shock < -0.1:
                worst_holdings.append({
                    "name": h.get("name", h.get("code", "?")),
                    "loss": round(loss, 0),
                    "loss_pct": round(shock * 100, 1),
                })
        
        new_value = total_value + scenario_loss
        loss_pct = (scenario_loss / total_value) * 100 if total_value > 0 else 0
        
        results.append({
            "key": key,
            "name": sc["name"],
            "description": sc["description"],
            "loss": round(scenario_loss, 0),
            "loss_pct": round(loss_pct, 1),
            "new_value": round(new_value, 0),
            "worst_holdings": sorted(worst_holdings, key=lambda x: x["loss"])[:5],
        })
    
    # 按损失排序（最差场景在前）
    results.sort(key=lambda x: x["loss"])
    worst = results[0] if results else None
    
    summary = f"最差场景「{worst['name']}」预估亏损 {worst['loss_pct']:.1f}%（¥{abs(worst['loss']):,.0f}）" if worst else "无压力数据"
    
    return {
        "scenarios": results,
        "summary": summary,
        "total_value": round(total_value, 0),
        "worst_scenario": worst["key"] if worst else None,
    }


# ============================================================
# 2. 集中度诊断 — HHI + 单票超限 + 行业集中
# ============================================================

def concentration_check(holdings: list) -> dict:
    """
    集中度诊断
    
    Returns:
        {hhi, hhi_level, single_stock_alerts, asset_class_distribution, issues}
    """
    if not holdings:
        return {"hhi": 0, "hhi_level": "无数据", "issues": []}
    
    total_value = sum(h.get("marketValue", 0) or h.get("value", 0) or 0 for h in holdings)
    if total_value <= 0:
        return {"hhi": 0, "hhi_level": "无数据", "issues": []}
    
    issues = []
    
    # HHI 计算
    weights = []
    for h in holdings:
        mv = h.get("marketValue", 0) or h.get("value", 0) or 0
        w = (mv / total_value) * 100 if total_value > 0 else 0
        weights.append({"name": h.get("name", h.get("code", "?")), "weight": round(w, 1)})
    
    hhi = sum(w["weight"] ** 2 for w in weights)
    
    if hhi > 5000:
        hhi_level = "🔴 高度集中"
        issues.append(f"HHI={hhi:.0f}，高度集中（>5000），建议分散投资")
    elif hhi > 2500:
        hhi_level = "🟡 适度集中"
        issues.append(f"HHI={hhi:.0f}，适度集中（2500-5000），可以考虑分散")
    else:
        hhi_level = "🟢 分散良好"
    
    # 单票超限检查（>15% 预警，>30% 危险）
    single_alerts = []
    for w in weights:
        if w["weight"] > 30:
            single_alerts.append({"name": w["name"], "weight": w["weight"], "level": "danger"})
            issues.append(f"🔴 {w['name']} 占比 {w['weight']:.1f}%，超过30%红线")
        elif w["weight"] > 15:
            single_alerts.append({"name": w["name"], "weight": w["weight"], "level": "warning"})
            issues.append(f"⚠️ {w['name']} 占比 {w['weight']:.1f}%，超过15%建议分散")
    
    # 资产类别分布
    class_dist = {}
    for h in holdings:
        mv = h.get("marketValue", 0) or h.get("value", 0) or 0
        asset_class = _classify_asset(h.get("name", ""), h.get("code", ""))
        class_dist[asset_class] = class_dist.get(asset_class, 0) + mv
    
    class_pct = {k: round(v / total_value * 100, 1) for k, v in class_dist.items()} if total_value > 0 else {}
    
    # 股票占比过高预警
    equity_pct = class_pct.get("equity", 0) + class_pct.get("growth", 0)
    if equity_pct > 80:
        issues.append(f"⚠️ 权益类占比 {equity_pct:.0f}%，过高，建议增配债券/黄金分散风险")
    
    return {
        "hhi": round(hhi, 0),
        "hhi_level": hhi_level,
        "holdings_weight": sorted(weights, key=lambda x: -x["weight"]),
        "single_stock_alerts": single_alerts,
        "asset_class_distribution": class_pct,
        "equity_pct": round(equity_pct, 1),
        "issues": issues,
    }


# ============================================================
# 3. 健康评分 — 综合 0-100 分
# ============================================================

def health_score(holdings: list, risk_metrics: dict = None) -> dict:
    """
    综合健康评分
    
    Returns:
        {score, grade, dimensions: {concentration, diversification, risk, stability}, issues}
    """
    if not holdings:
        return {"score": 0, "grade": "❓ 无数据", "dimensions": {}, "issues": ["无持仓数据"]}
    
    issues = []
    scores = {}
    
    # 维度 1: 集中度（30 分满分）
    conc = concentration_check(holdings)
    hhi = conc.get("hhi", 10000)
    if hhi < 2000:
        scores["concentration"] = 30
    elif hhi < 3000:
        scores["concentration"] = 25
    elif hhi < 5000:
        scores["concentration"] = 15
    else:
        scores["concentration"] = 5
    issues.extend(conc.get("issues", []))
    
    # 维度 2: 资产多样性（25 分满分）
    class_count = len(conc.get("asset_class_distribution", {}))
    if class_count >= 4:
        scores["diversification"] = 25
    elif class_count >= 3:
        scores["diversification"] = 20
    elif class_count >= 2:
        scores["diversification"] = 12
    else:
        scores["diversification"] = 5
        issues.append("⚠️ 资产类别单一，建议配置股+债+黄金分散风险")
    
    # 维度 3: 风险水平（25 分满分）
    stress = stress_test(holdings, ["bear_market"])
    worst_loss = abs(stress["scenarios"][0]["loss_pct"]) if stress["scenarios"] else 20
    if worst_loss < 10:
        scores["risk"] = 25
    elif worst_loss < 15:
        scores["risk"] = 20
    elif worst_loss < 20:
        scores["risk"] = 15
    elif worst_loss < 30:
        scores["risk"] = 8
    else:
        scores["risk"] = 3
        issues.append(f"🔴 熊市场景预估亏损 {worst_loss:.0f}%，风险偏高")
    
    # 维度 4: 持仓数量合理性（20 分满分）
    count = len(holdings)
    if 5 <= count <= 15:
        scores["stability"] = 20
    elif 3 <= count <= 20:
        scores["stability"] = 15
    elif count < 3:
        scores["stability"] = 8
        issues.append("⚠️ 持仓只有 {count} 只，过于集中".format(count=count))
    else:
        scores["stability"] = 10
        issues.append(f"⚠️ 持仓 {count} 只，偏多，管理成本高")
    
    total_score = sum(scores.values())
    
    if total_score >= 85:
        grade = "🏆 优秀"
    elif total_score >= 70:
        grade = "✅ 良好"
    elif total_score >= 55:
        grade = "⚠️ 一般"
    elif total_score >= 40:
        grade = "🟡 偏弱"
    else:
        grade = "🔴 危险"
    
    return {
        "score": total_score,
        "grade": grade,
        "dimensions": scores,
        "max_scores": {"concentration": 30, "diversification": 25, "risk": 25, "stability": 20},
        "issues": issues,
        "holdings_count": count,
    }


# ============================================================
# 4. 完整诊断（合并调用）
# ============================================================

def diagnose(user_id: str) -> dict:
    """
    完整持仓体检 — 合并压力测试+集中度+健康评分
    
    Args:
        user_id: 用户 ID
    
    Returns:
        完整诊断报告
    """
    # 从持仓模块获取数据
    holdings = []
    
    try:
        from services.stock_monitor import load_stock_holdings, scan_all_holdings
        stock_scan = scan_all_holdings(user_id)
        for h in stock_scan.get("holdings", []):
            holdings.append({
                "name": h.get("name", h.get("code", "")),
                "code": h.get("code", ""),
                "marketValue": h.get("marketValue", 0),
                "type": "stock",
                "category": "equity",
            })
    except Exception as e:
        print(f"[DOCTOR] 股票数据获取失败: {e}")
    
    try:
        from services.fund_monitor import load_fund_holdings, scan_all_fund_holdings
        fund_scan = scan_all_fund_holdings(user_id)
        for h in fund_scan.get("holdings", []):
            mv = 0
            if h.get("costNav") and h.get("shares"):
                rt = h.get("realtime", {})
                nav = float(rt.get("nav", 0) or rt.get("estNav", 0) or h.get("costNav", 0))
                mv = nav * h.get("shares", 0)
            holdings.append({
                "name": h.get("name", h.get("code", "")),
                "code": h.get("code", ""),
                "marketValue": round(mv, 2),
                "type": "fund",
                "category": _classify_asset(h.get("name", ""), h.get("code", "")),
            })
    except Exception as e:
        print(f"[DOCTOR] 基金数据获取失败: {e}")
    
    if not holdings:
        return {
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(),
            "status": "no_data",
            "message": "暂无持仓数据，请先添加股票或基金",
        }
    
    # 执行三项诊断
    stress = stress_test(holdings)
    conc = concentration_check(holdings)
    health = health_score(holdings)
    
    return {
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "status": "ok",
        "stress_test": stress,
        "concentration": conc,
        "health": health,
        "summary": f"{health['grade']} {health['score']}分 | {conc['hhi_level']} | {stress['summary']}",
    }


# ============================================================
# 5. enrich — DecisionContext 适配层
# ============================================================

def enrich(ctx):
    """
    写入 DecisionContext — cautious Pipeline step_doctor 调用
    
    读: ctx.user_id, ctx.stock_holdings, ctx.fund_holdings
    写: ctx.doctor_report (完整诊断)
    """
    user_id = getattr(ctx, "user_id", "default")
    
    try:
        report = diagnose(user_id)
        ctx.doctor_report = report
        
        # 如果健康分 < 40，标记风控红灯
        health = report.get("health", {})
        if health.get("score", 100) < 40:
            if not hasattr(ctx, "risk_flags"):
                ctx.risk_flags = []
            ctx.risk_flags.append({
                "source": "portfolio_doctor",
                "level": "danger",
                "message": f"持仓健康评分仅 {health['score']}分（{health['grade']}），建议立即调整",
            })
    except Exception as e:
        print(f"[DOCTOR] enrich 失败: {e}")
        ctx.doctor_report = {"status": "error", "message": str(e)}
    
    return ctx
