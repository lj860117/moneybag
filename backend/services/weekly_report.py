"""
钱袋子 — 周报生成器
W9 闭环：汇总一周的判断记录+持仓变化+市场回顾→生成周报

用于：
  1. 企微推送周报（每周日晚8点）
  2. 前端"周报"页面展示
  3. Claude 复盘参考
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from config import DATA_DIR


MODULE_META = {
    "name": "weekly_report",
    "scope": "private",
    "input": ["user_id"],
    "output": "weekly_report",
    "cost": "cpu",
    "tags": ["output", "report"],
    "description": "汇总一周判断+持仓+市场→结构化周报",
    "layer": "output",
    "priority": 90,
}


def generate(user_id: str, weeks_ago: int = 0) -> dict:
    """生成周报
    
    Args:
        user_id: 用户ID
        weeks_ago: 0=本周, 1=上周, ...
    
    Returns:
        {period, summary, judgments, portfolio_changes, market_review, recommendations}
    """
    now = datetime.now()
    # 计算周区间（周一到周日）
    week_start = now - timedelta(days=now.weekday() + 7 * weeks_ago)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    period = f"{week_start.strftime('%m/%d')} - {week_end.strftime('%m/%d')}"
    
    # 1. 汇总判断记录
    judgments_summary = _summarize_judgments(user_id, week_start, week_end)
    
    # 2. 持仓变化
    portfolio_changes = _summarize_portfolio_changes(user_id, week_start, week_end)
    
    # 3. 市场回顾
    market_review = _summarize_market(week_start, week_end)
    
    # 4. 下周建议
    recommendations = _generate_recommendations(judgments_summary, portfolio_changes, market_review)
    
    report = {
        "user_id": user_id,
        "period": period,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "generated_at": now.isoformat(),
        "summary": _build_summary(judgments_summary, portfolio_changes),
        "narrative": generate_narrative({
            "period": period,
            "portfolio_changes": portfolio_changes,
            "market_review": market_review,
            "recommendations": recommendations,
            "judgments": judgments_summary,
        }),
        "judgments": judgments_summary,
        "portfolio_changes": portfolio_changes,
        "market_review": market_review,
        "recommendations": recommendations,
    }
    
    # 保存
    report_dir = DATA_DIR / user_id / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    fp = report_dir / f"week_{week_start.strftime('%Y%m%d')}.json"
    fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return report


def _summarize_judgments(user_id: str, start: datetime, end: datetime) -> dict:
    """汇总一周的判断记录"""
    try:
        from services.judgment_tracker import scorecard
        card = scorecard(user_id)
        # 筛选本周的
        recent = [j for j in card.get("recent", []) 
                  if start.isoformat() <= j.get("time", "") <= end.isoformat()]
        
        total = len(recent)
        verified = [j for j in recent if j.get("verified")]
        correct = [j for j in verified if j.get("correct")]
        
        return {
            "total_judgments": total,
            "verified": len(verified),
            "correct": len(correct),
            "accuracy": round(len(correct) / len(verified) * 100) if verified else 0,
            "details": recent[:10],  # 最多展示10条
        }
    except Exception as e:
        print(f"[WEEKLY] judgment summary failed: {e}")
        return {"total_judgments": 0, "verified": 0, "correct": 0, "accuracy": 0, "details": []}


def _summarize_portfolio_changes(user_id: str, start: datetime, end: datetime) -> dict:
    """汇总持仓变化"""
    try:
        from services.persistence import load_user
        user = load_user(user_id)
        txns = user.get("portfolio", {}).get("transactions", [])
        
        # 本周交易
        week_txns = [t for t in txns 
                     if start.isoformat() <= t.get("date", "") <= end.isoformat()]
        
        buys = [t for t in week_txns if t.get("type") == "BUY"]
        sells = [t for t in week_txns if t.get("type") == "SELL"]
        
        total_bought = sum(t.get("amount", 0) for t in buys)
        total_sold = sum(t.get("amount", 0) for t in sells)
        
        return {
            "total_transactions": len(week_txns),
            "buys": len(buys),
            "sells": len(sells),
            "total_bought": round(total_bought, 2),
            "total_sold": round(total_sold, 2),
            "net_flow": round(total_bought - total_sold, 2),
        }
    except Exception as e:
        print(f"[WEEKLY] portfolio summary failed: {e}")
        return {"total_transactions": 0, "buys": 0, "sells": 0, "total_bought": 0, "total_sold": 0, "net_flow": 0}


def _summarize_market(start: datetime, end: datetime) -> dict:
    """本周市场回顾"""
    try:
        from services.regime_engine import classify
        regime = classify()
        return {
            "regime": regime.get("regime", "unknown"),
            "regime_description": regime.get("description", ""),
            "confidence": regime.get("confidence", 0),
        }
    except Exception as e:
        print(f"[WEEKLY] market summary failed: {e}")
        return {"regime": "unknown", "regime_description": "", "confidence": 0}


def _generate_recommendations(judgments: dict, portfolio: dict, market: dict) -> list:
    """基于本周数据生成下周建议"""
    recs = []
    
    # 判断准确率低
    if judgments.get("verified", 0) >= 3 and judgments.get("accuracy", 0) < 50:
        recs.append("📉 本周判断准确率较低，建议下周减少操作，以观望为主")
    
    # 交易过于频繁
    if portfolio.get("total_transactions", 0) > 5:
        recs.append("⚠️ 本周交易较频繁，频繁交易不利于长期收益，建议控制操作次数")
    
    # 净买入过多
    if portfolio.get("net_flow", 0) > 50000:
        recs.append("💰 本周净买入较多，注意保持现金储备应对突发")
    
    # 市场状态建议
    regime = market.get("regime", "")
    if regime == "high_vol_bear":
        recs.append("🐻 市场处于高波熊市，建议防守为主，控制仓位")
    elif regime == "trending_bull":
        recs.append("🐂 市场处于趋势牛，可以适当跟随趋势，注意止盈")
    
    if not recs:
        recs.append("✅ 本周表现正常，继续保持纪律投资")
    
    return recs


def _build_summary(judgments: dict, portfolio: dict) -> str:
    """一句话总结"""
    parts = []
    if judgments.get("total_judgments", 0) > 0:
        parts.append(f"分析{judgments['total_judgments']}次")
        if judgments.get("verified", 0) > 0:
            parts.append(f"验证{judgments['verified']}次")
            parts.append(f"准确率{judgments['accuracy']}%")
    if portfolio.get("total_transactions", 0) > 0:
        parts.append(f"交易{portfolio['total_transactions']}笔")
    return "｜".join(parts) if parts else "本周暂无活动"


def generate_narrative(report: dict) -> str:
    """把结构化周报转为家庭财务复盘的人话版本（不调 LLM，纯模板）

    输出风格：像给家人写的周记，不用专业术语。
    """
    period = report.get("period", "本周")
    portfolio = report.get("portfolio_changes", {})
    market = report.get("market_review", {})
    recommendations = report.get("recommendations", [])
    judgments = report.get("judgments", {})

    lines = [f"📋 本周家庭财务复盘（{period}）", ""]

    # 1. 投资操作
    lines.append("📌 投资操作")
    txn_count = portfolio.get("total_transactions", 0)
    if txn_count == 0:
        lines.append("   本周没有新增交易，执行纪律良好。👍")
    else:
        buys = portfolio.get("buys", 0)
        sells = portfolio.get("sells", 0)
        bought = portfolio.get("total_bought", 0)
        sold = portfolio.get("total_sold", 0)
        parts = []
        if buys > 0:
            parts.append(f"买入 {buys} 笔（¥{bought:,.0f}）")
        if sells > 0:
            parts.append(f"卖出 {sells} 笔（¥{sold:,.0f}）")
        lines.append(f"   {', '.join(parts)}。")
        if txn_count > 5:
            lines.append("   ⚠️ 交易次数偏多，频繁操作可能影响长期收益。")
    lines.append("")

    # 2. 市场状态
    lines.append("📊 市场状态")
    regime_desc = market.get("regime_description", "")
    if regime_desc:
        lines.append(f"   {regime_desc}")
    else:
        regime_map = {
            "trending_bull": "市场处于上升趋势，整体偏强。",
            "oscillating": "市场横盘震荡，方向不明。",
            "high_vol_bear": "市场波动加大，偏弱，注意防守。",
            "rotation": "市场热点轮动，板块分化明显。",
        }
        regime = market.get("regime", "unknown")
        lines.append(f"   {regime_map.get(regime, '市场状态正常。')}")
    lines.append("")

    # 3. 判断复盘
    total_j = judgments.get("total_judgments", 0)
    if total_j > 0:
        lines.append("🎯 判断复盘")
        accuracy = judgments.get("accuracy", 0)
        verified = judgments.get("verified", 0)
        if verified > 0:
            lines.append(f"   本周做了 {total_j} 次判断，验证了 {verified} 次，准确率 {accuracy}%。")
            if accuracy >= 70:
                lines.append("   判断质量不错，继续保持。✅")
            elif accuracy < 50:
                lines.append("   准确率偏低，下周建议减少操作频率，多观察少动手。")
        else:
            lines.append(f"   本周做了 {total_j} 次判断，暂未验证。")
        lines.append("")

    # 4. 下周提醒
    lines.append("💡 下周提醒")
    if recommendations:
        for r in recommendations[:3]:
            # 去掉开头的 emoji（如果有的话）
            text = r.lstrip("📉⚠️💰🐻🐂✅ ")
            lines.append(f"   • {text}")
    else:
        lines.append("   • 按计划执行，无需特别调整。")

    return "\n".join(lines)


def enrich(ctx):
    """Pipeline enrich — 生成周报写入 ctx"""
    try:
        report = generate(ctx.user_id)
        ctx.weekly_report = report
    except Exception as e:
        print(f"[WEEKLY] enrich failed: {e}")
        ctx.weekly_report = None
    return ctx


def get_history(user_id: str, limit: int = 4) -> list:
    """获取历史周报列表"""
    report_dir = DATA_DIR / user_id / "reports"
    if not report_dir.exists():
        return []
    
    files = sorted(report_dir.glob("week_*.json"), reverse=True)[:limit]
    reports = []
    for fp in files:
        try:
            reports.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            continue
    return reports
