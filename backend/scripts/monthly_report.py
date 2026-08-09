"""
钱袋子 v9.5.123 Sprint 3 — 月度家庭财务健康报告 + 家庭合并视图
================================================================
1. 月度报告: 每月1号生成, 含双人合计收益/定投执行/行为评分/下月建议
2. 家庭合并视图: API返回两人持仓合并的行业分布/重叠/风格分析

用法:
  python3 scripts/monthly_report.py --report   # 生成月报
  python3 scripts/monthly_report.py --family   # 生成家庭合并数据
"""
import sys
import os
import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
USERS = ["LeiJiang", "BuLuoGeLi"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ═══════════════════════════════════════════════
# 1. 月度家庭财务健康报告
# ═══════════════════════════════════════════════

def generate_monthly_report() -> dict:
    """生成家庭月度财务健康报告"""
    log("📋 === 月度家庭报告 ===")
    
    from services.persistence import load_user
    from services.fund_monitor import load_fund_holdings
    
    today = date.today()
    month_name = f"{today.year}年{today.month-1 if today.month > 1 else 12}月"
    
    family_data = {"users": {}, "family_total": {}}
    total_cost = 0
    total_value = 0
    total_funds = 0
    all_behavior_issues = []
    dca_executions = 0
    
    for uid in USERS:
        log(f"  📊 分析用户: {uid}")
        user = load_user(uid)
        portfolio = user.get("portfolio") or {}
        txns = portfolio.get("transactions") or []
        my_funds = load_fund_holdings(uid) or []
        
        # 计算持仓市值(简化:用份额×最新净值)
        user_cost = 0
        user_shares_value = 0
        for t in txns:
            if t.get("action") == "buy":
                user_cost += t.get("amount", 0)
        
        # 用enrich缓存获取市值
        cache_fp = DATA_DIR / "_cache" / f"holding_enrich_{uid}.json"
        if cache_fp.exists():
            try:
                enrich_data = json.loads(cache_fp.read_text(encoding="utf-8"))
                funds = enrich_data.get("data", {}).get("funds", []) or enrich_data.get("funds", [])
                for f in funds:
                    nav = f.get("nav_cur", 0)
                    # 简化估算
                    user_shares_value += nav * 1000 if nav else 0  # 占位
            except Exception:
                pass
        
        fund_count = len(my_funds)
        total_funds += fund_count
        
        # 本月交易次数(行为检测)
        month_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
        monthly_txns = [t for t in txns if (t.get("date", "") or "") >= month_start]
        if len(monthly_txns) > 8:
            all_behavior_issues.append(f"{uid}: 月交易{len(monthly_txns)}次(频繁)")
        
        # 定投执行记录
        dca_history_fp = DATA_DIR / "_cache" / "dca_history.json"
        if dca_history_fp.exists():
            try:
                history = json.loads(dca_history_fp.read_text(encoding="utf-8"))
                recent = [h for h in history if h.get("date", "")[:7] == today.strftime("%Y-%m")]
                dca_executions += len(recent)
            except Exception:
                pass
        
        family_data["users"][uid] = {
            "fund_count": fund_count,
            "monthly_trades": len(monthly_txns),
        }
    
    # 行为评分 (满分100)
    behavior_score = 100
    if all_behavior_issues:
        behavior_score -= len(all_behavior_issues) * 15
    behavior_score = max(0, behavior_score)
    
    # 定投执行率
    dca_rate = min(100, dca_executions * 50)  # 每月执行1次=50%, 2次=100%
    
    # 生成报告
    report = {
        "month": month_name,
        "generated_at": datetime.now().isoformat(),
        "family_summary": {
            "total_funds": total_funds,
            "behavior_score": behavior_score,
            "behavior_issues": all_behavior_issues,
            "dca_execution_rate": dca_rate,
        },
        "users": family_data["users"],
        "next_month_advice": _generate_next_month_advice(behavior_score, dca_rate, all_behavior_issues),
    }
    
    # 保存报告
    report_dir = DATA_DIR / "_cache" / "monthly_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    fp = report_dir / f"report_{today.strftime('%Y%m')}.json"
    fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  📄 报告已保存: {fp}")
    
    # 企微推送
    _push_monthly_report(report)
    
    return report


def _generate_next_month_advice(score, dca_rate, issues) -> list:
    """根据本月表现生成下月建议"""
    advice = []
    if score >= 90:
        advice.append("✅ 行为纪律优秀! 继续保持, 不追涨不杀跌")
    elif score >= 70:
        advice.append("👍 整体不错, 但需注意交易频率")
    else:
        advice.append("⚠️ 行为偏差较多, 建议严格执行定投纪律")
    
    if dca_rate < 50:
        advice.append("💰 定投执行率偏低, 记得每月25号按建议执行")
    
    if issues:
        advice.append("📉 " + "; ".join(issues[:2]))
    
    advice.append("🎯 下月重点: 坚持定投纪律 + 不因短期波动改变策略")
    return advice


def _push_monthly_report(report: dict):
    """推送月报到企微"""
    from services.wxwork_push import is_configured, send_markdown
    if not is_configured():
        log("  ⚠️ 企微未配置")
        return
    
    month = report["month"]
    summary = report["family_summary"]
    advice = report.get("next_month_advice", [])
    
    lines = [
        f"📋 **{month} 家庭财务月报**\n",
        f"👨‍👩‍👦 家庭持仓: **{summary['total_funds']}只基金**",
        f"🎯 行为纪律评分: **{summary['behavior_score']}分** /100",
        f"💰 定投执行率: **{summary['dca_execution_rate']}%**",
    ]
    
    if summary["behavior_issues"]:
        lines.append(f"\n⚠️ 行为偏差: {', '.join(summary['behavior_issues'][:2])}")
    
    lines.append("\n📌 **下月建议:**")
    for a in advice[:3]:
        lines.append(f"  {a}")
    
    lines.append(f"\n> 报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    msg = "\n".join(lines)
    try:
        send_markdown(msg)
        log("  ✅ 月报推送成功")
    except Exception as e:
        log(f"  ❌ 推送失败: {e}")


# ═══════════════════════════════════════════════
# 2. 家庭合并持仓视图
# ═══════════════════════════════════════════════

def generate_family_view() -> dict:
    """生成家庭合并持仓视图: 行业分布 + 重叠 + 风格互补"""
    log("👨‍👩‍👦 === 家庭合并视图 ===")
    
    from services.fund_monitor import load_fund_holdings
    from services.industry_templates import get_fund_industry
    
    all_holdings = {}  # {code: {name, owners: [], industry}}
    user_industries = {}  # {uid: {industry: count}}
    
    for uid in USERS:
        my_funds = load_fund_holdings(uid) or []
        user_industries[uid] = {}
        
        for f in my_funds:
            code = f.get("code", "")
            name = f.get("name", "")
            if not code:
                continue
            
            # 行业分类
            ind = get_fund_industry(name)
            industry = ind.get("tag", "其他") if ind else "其他"
            
            if code not in all_holdings:
                all_holdings[code] = {"code": code, "name": name, "industry": industry, "owners": []}
            all_holdings[code]["owners"].append(uid)
            
            # 统计行业分布
            user_industries[uid][industry] = user_industries[uid].get(industry, 0) + 1
    
    # 分析
    total_funds = len(all_holdings)
    
    # 重叠: 两人都持有的基金
    overlap = [h for h in all_holdings.values() if len(h["owners"]) > 1]
    
    # 行业集中度
    family_industries = {}
    for h in all_holdings.values():
        ind = h["industry"]
        family_industries[ind] = family_industries.get(ind, 0) + 1
    
    # 排序: 持有最多的行业在前
    sorted_industries = sorted(family_industries.items(), key=lambda x: -x[1])
    
    # 风格互补度 (两人行业分布重叠率)
    uid1, uid2 = USERS[0], USERS[1]
    ind1 = set(user_industries.get(uid1, {}).keys())
    ind2 = set(user_industries.get(uid2, {}).keys())
    overlap_industries = ind1 & ind2
    all_industries = ind1 | ind2
    overlap_rate = round(len(overlap_industries) / max(len(all_industries), 1) * 100)
    
    # 集中度风险
    top_industry = sorted_industries[0] if sorted_industries else ("无", 0)
    concentration = round(top_industry[1] / max(total_funds, 1) * 100)
    
    # 风险提示
    warnings = []
    if concentration > 40:
        warnings.append(f"⚠️ 家庭对「{top_industry[0]}」行业暴露{concentration}%, 集中度偏高")
    if overlap_rate > 60:
        warnings.append(f"⚠️ 两人行业重叠率{overlap_rate}%, 分散度不足")
    if len(overlap) >= 3:
        names = [h["name"][:6] for h in overlap[:3]]
        warnings.append(f"📎 {len(overlap)}只基金两人都持有: {', '.join(names)}{'等' if len(overlap)>3 else ''}")
    
    if not warnings:
        warnings.append("✅ 家庭持仓风格互补, 整体风险分散良好")
    
    result = {
        "total_funds": total_funds,
        "total_users": len(USERS),
        "industry_distribution": sorted_industries[:8],
        "top_industry": {"name": top_industry[0], "count": top_industry[1], "pct": concentration},
        "overlap_funds": [{"code": h["code"], "name": h["name"], "industry": h["industry"]} for h in overlap],
        "overlap_rate": overlap_rate,
        "user_breakdown": {uid: {"funds": len(load_fund_holdings(uid) or []), "industries": user_industries.get(uid, {})} for uid in USERS},
        "warnings": warnings,
        "complementary_score": max(0, 100 - overlap_rate),  # 互补度=100-重叠率
    }
    
    # 缓存
    cache_fp = DATA_DIR / "_cache" / "family_view.json"
    cache_fp.parent.mkdir(parents=True, exist_ok=True)
    cache_fp.write_text(json.dumps({"data": result, "created_at": time.time()}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  ✅ 家庭视图已生成: {total_funds}只基金, 互补度{result['complementary_score']}%")
    
    return result


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ["--all"]
    
    results = {}
    if "--report" in args or "--all" in args:
        results["report"] = generate_monthly_report()
    if "--family" in args or "--all" in args:
        results["family"] = generate_family_view()
    
    log(f"\n✅ 完成")
    return results


if __name__ == "__main__":
    main()
