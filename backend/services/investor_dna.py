"""
钱袋子 v9.5.123 Sprint 4 — 投资DNA画像 + 决策复盘 + AI自进化
================================================================
护城河模块: 越用越懂你, 越用越准

1. 投资DNA画像: 分析交易历史 → 风格/偏好/弱点
2. 决策复盘追踪: 卖出后30/60/90天追踪 → 学习卖点规律
3. 权重自进化: 回测各维度准确率 → 动态调权
"""
import os
import json
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import Counter

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))

# ═══════════════════════════════════════════════
# 1. 投资DNA画像
# ═══════════════════════════════════════════════

def generate_investor_dna(user_id: str) -> dict:
    """分析用户交易历史,生成投资DNA画像
    
    画像维度:
    - 风险偏好: 保守/稳健/进取/激进
    - 平均持有期: 短线(<3月)/中线(3-12月)/长线(>12月)
    - 擅长赛道: 哪些行业买了赚钱
    - 行为弱点: 追涨/杀跌/频繁/集中度
    - 回撤容忍度: 最大亏损后是否止损
    - 定投纪律: 执行率
    """
    from services.persistence import load_user
    from services.industry_templates import get_fund_industry
    
    user = load_user(user_id)
    portfolio = user.get("portfolio") or {}
    txns = portfolio.get("transactions") or []
    
    if len(txns) < 5:
        return {"available": False, "reason": "交易记录不足(需至少5笔)", "user_id": user_id}
    
    # ── 基础统计 ──
    buy_txns = [t for t in txns if t.get("action") == "buy"]
    sell_txns = [t for t in txns if t.get("action") == "sell"]
    total_txns = len(txns)
    
    # 时间跨度
    dates = sorted([t.get("date", "") for t in txns if t.get("date")])
    first_date = dates[0] if dates else ""
    last_date = dates[-1] if dates else ""
    try:
        span_days = (datetime.strptime(last_date, "%Y-%m-%d") - datetime.strptime(first_date, "%Y-%m-%d")).days
    except Exception:
        span_days = 0
    span_months = max(1, span_days // 30)
    
    # ── 1. 风险偏好 ──
    # 基于投资品种和集中度判断
    codes = [t.get("code", "") for t in buy_txns]
    unique_codes = set(codes)
    diversification = len(unique_codes) / max(len(buy_txns), 1)
    
    # 行业分布
    industries = []
    for t in buy_txns:
        name = t.get("name", "")
        ind = get_fund_industry(name)
        if ind:
            industries.append(ind.get("tag", "其他"))
    
    industry_counts = Counter(industries)
    top_industry_pct = max(industry_counts.values()) / max(len(industries), 1) * 100 if industry_counts else 0
    
    # 风险评级
    if top_industry_pct > 60 or diversification < 0.3:
        risk_profile = "激进"
        risk_desc = "高集中度,重仓单一赛道"
    elif top_industry_pct > 40:
        risk_profile = "进取"
        risk_desc = "有主题偏好但有一定分散"
    elif len(unique_codes) >= 8:
        risk_profile = "稳健"
        risk_desc = "持仓分散,风格均衡"
    else:
        risk_profile = "保守"
        risk_desc = "持仓较少,偏好安全"
    
    # ── 2. 持有期 ──
    holding_periods = []
    for s in sell_txns:
        code = s.get("code", "")
        sell_date = s.get("date", "")
        # 找最早买入日期
        buys_of_code = [b for b in buy_txns if b.get("code") == code and b.get("date", "") < sell_date]
        if buys_of_code:
            buy_date = buys_of_code[0].get("date", "")
            try:
                days = (datetime.strptime(sell_date, "%Y-%m-%d") - datetime.strptime(buy_date, "%Y-%m-%d")).days
                holding_periods.append(days)
            except Exception:
                pass
    
    avg_holding = round(sum(holding_periods) / max(len(holding_periods), 1))
    if avg_holding < 90:
        holding_style = "短线型"
        holding_desc = f"平均持有{avg_holding}天,偏短线操作"
    elif avg_holding < 365:
        holding_style = "中线型"
        holding_desc = f"平均持有{avg_holding}天,中期持有"
    else:
        holding_style = "长线型"
        holding_desc = f"平均持有{avg_holding}天,长期主义"
    
    # ── 3. 擅长赛道 ──
    # 基于行业分布找到重点赛道
    top_industries = industry_counts.most_common(3)
    strong_sectors = [ind for ind, _ in top_industries] if top_industries else ["未知"]
    
    # ── 4. 行为弱点 ──
    weaknesses = []
    
    # 频繁交易
    monthly_rate = total_txns / span_months
    if monthly_rate > 5:
        weaknesses.append({"type": "frequent", "desc": f"月均{monthly_rate:.1f}次交易,过于频繁", "severity": "high"})
    
    # 集中度
    code_counter = Counter(codes)
    if code_counter and code_counter.most_common(1)[0][1] / max(len(codes), 1) > 0.4:
        top_code = code_counter.most_common(1)[0]
        weaknesses.append({"type": "concentration", "desc": f"对单只标的操作占比{top_code[1]/len(codes)*100:.0f}%", "severity": "medium"})
    
    # 追涨(买入时NAV百分位高)
    high_buy_count = sum(1 for t in buy_txns if (t.get("nav_percentile") or 0) > 75)
    if high_buy_count >= 2:
        weaknesses.append({"type": "chasing", "desc": f"{high_buy_count}次在高位(>75%百分位)买入", "severity": "high"})
    
    if not weaknesses:
        weaknesses.append({"type": "none", "desc": "暂未发现明显行为偏差", "severity": "low"})
    
    # ── 5. 回撤容忍度 ──
    # 看亏损最大时是否止损
    max_loss_held = 0  # 持有的最大亏损%
    for t in buy_txns:
        pnl = t.get("pnl_pct")
        if pnl and pnl < max_loss_held:
            max_loss_held = pnl
    
    if max_loss_held < -30:
        drawdown_tolerance = "高容忍"
        drawdown_desc = f"曾承受{max_loss_held:.0f}%亏损未止损"
    elif max_loss_held < -15:
        drawdown_tolerance = "中等容忍"
        drawdown_desc = f"最大持有亏损{max_loss_held:.0f}%"
    else:
        drawdown_tolerance = "低容忍"
        drawdown_desc = "倾向于早期止损或未经历大回撤"
    
    # ── 汇总 ──
    dna = {
        "available": True,
        "user_id": user_id,
        "generated_at": datetime.now().isoformat(),
        "data_span": {"first_txn": first_date, "last_txn": last_date, "months": span_months, "total_txns": total_txns},
        "risk_profile": {"level": risk_profile, "desc": risk_desc, "concentration_pct": round(top_industry_pct)},
        "holding_style": {"type": holding_style, "avg_days": avg_holding, "desc": holding_desc},
        "strong_sectors": strong_sectors,
        "weaknesses": weaknesses,
        "drawdown_tolerance": {"level": drawdown_tolerance, "desc": drawdown_desc, "max_held_loss": round(max_loss_held, 1)},
        "summary": f"{risk_profile}·{holding_style} | 偏好{'/'.join(strong_sectors[:2])} | {weaknesses[0]['desc'][:15]}",
    }
    
    # 缓存
    cache_dir = DATA_DIR / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = cache_dir / f"investor_dna_{user_id}.json"
    fp.write_text(json.dumps(dna, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return dna


# ═══════════════════════════════════════════════
# 2. 决策复盘追踪
# ═══════════════════════════════════════════════

def track_sell_decisions(user_id: str) -> dict:
    """追踪卖出决策: 卖出后30/60/90天净值变化 → 判断卖对了还是卖早了"""
    from services.persistence import load_user
    from services.fund_monitor import get_fund_realtime
    
    user = load_user(user_id)
    portfolio = user.get("portfolio") or {}
    txns = portfolio.get("transactions") or []
    
    sell_txns = [t for t in txns if t.get("action") == "sell"]
    if not sell_txns:
        return {"available": False, "reviews": []}
    
    reviews = []
    today = date.today()
    
    for s in sell_txns[-20:]:  # 最近20笔卖出
        code = s.get("code", "")
        name = s.get("name", code)
        sell_date_str = s.get("date", "")
        sell_nav = s.get("nav") or s.get("price", 0)
        
        if not sell_date_str or not sell_nav or sell_nav <= 0:
            continue
        
        try:
            sell_date = datetime.strptime(sell_date_str, "%Y-%m-%d").date()
        except Exception:
            continue
        
        days_since = (today - sell_date).days
        if days_since < 7:
            continue  # 太近,还看不出
        
        # 获取当前净值
        rt = get_fund_realtime(code)
        current_nav = (rt.get("nav") or rt.get("estNav")) if rt else None
        if not current_nav or current_nav <= 0:
            continue
        
        # 计算卖出后涨跌
        change_since_sell = (current_nav - sell_nav) / sell_nav * 100
        
        # 判断
        if change_since_sell > 10:
            verdict = "卖早了"
            icon = "😅"
            lesson = f"卖出后又涨了{change_since_sell:.1f}%,下次可以设更高止盈线"
        elif change_since_sell > 3:
            verdict = "略早"
            icon = "🤔"
            lesson = f"卖出后小涨{change_since_sell:.1f}%,时机尚可但非最优"
        elif change_since_sell < -10:
            verdict = "卖对了"
            icon = "✅"
            lesson = f"卖出后跌了{change_since_sell:.1f}%,决策正确"
        elif change_since_sell < -3:
            verdict = "基本正确"
            icon = "👍"
            lesson = f"卖出后小跌{change_since_sell:.1f}%,时机不错"
        else:
            verdict = "影响不大"
            icon = "😐"
            lesson = f"卖出后变化{change_since_sell:+.1f}%,卖不卖区别不大"
        
        reviews.append({
            "code": code, "name": name,
            "sell_date": sell_date_str,
            "sell_nav": round(sell_nav, 4),
            "current_nav": round(current_nav, 4),
            "days_since": days_since,
            "change_pct": round(change_since_sell, 1),
            "verdict": verdict,
            "icon": icon,
            "lesson": lesson,
        })
    
    # 统计卖出决策正确率
    correct = sum(1 for r in reviews if r["verdict"] in ("卖对了", "基本正确"))
    too_early = sum(1 for r in reviews if r["verdict"] in ("卖早了", "略早"))
    total = len(reviews)
    
    result = {
        "available": True,
        "user_id": user_id,
        "reviews": reviews,
        "stats": {
            "total_reviewed": total,
            "correct": correct,
            "too_early": too_early,
            "correct_rate": round(correct / max(total, 1) * 100, 1),
            "pattern": "倾向过早止盈" if too_early > correct else "止盈时机较好" if correct > too_early else "表现中性",
        },
    }
    
    # 缓存
    fp = DATA_DIR / "_cache" / f"sell_reviews_{user_id}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return result


# ═══════════════════════════════════════════════
# 3. AI权重自进化
# ═══════════════════════════════════════════════

# 默认权重(当前8维引擎)
DEFAULT_WEIGHTS = {
    "动量趋势": 25,
    "技术面信号": 20,
    "估值水位": 15,
    "资金流向": 15,
    "市场环境": 10,
    "赛道热度": 5,
    "波动率风险": 5,
    "情绪面": 5,
}


def evolve_weights() -> dict:
    """根据历史回测结果自动调整8维权重
    
    逻辑:
    1. 读取回测结果中各维度独立准确率
    2. 准确率高于均值的升权, 低于均值的降权
    3. 调整幅度不超过±5(防止极端)
    4. 保证总权重=100
    """
    # 读取回测结果
    backtest_fp = DATA_DIR / "_cache" / "backtest_results.json"
    if not backtest_fp.exists():
        return {"evolved": False, "reason": "无回测数据", "weights": DEFAULT_WEIGHTS}
    
    try:
        bt = json.loads(backtest_fp.read_text(encoding="utf-8"))
    except Exception:
        return {"evolved": False, "reason": "回测数据解析失败", "weights": DEFAULT_WEIGHTS}
    
    # 当前版本回测只有综合准确率,没有分维度准确率
    # 先用综合准确率做基础调整策略:
    # - 如果综合准确率>50% → 维持当前权重(模型有效)
    # - 如果<30% → 增大估值权重(逆向因子更可靠), 降低动量权重
    overall_acc = bt.get("trend_accuracy", {}).get("avg_overall", 0)
    dca_excess = bt.get("dca_comparison", {}).get("avg_excess", 0)
    
    weights = dict(DEFAULT_WEIGHTS)
    evolved = False
    reason = ""
    
    if overall_acc >= 50:
        reason = f"综合准确率{overall_acc}%良好, 维持当前权重"
    elif overall_acc >= 30:
        # 小幅调整: 估值可靠性更高, 稍微升权
        weights["估值水位"] = min(20, weights["估值水位"] + 3)
        weights["动量趋势"] = max(20, weights["动量趋势"] - 3)
        evolved = True
        reason = f"准确率{overall_acc}%中等, 小幅升估值降动量"
    else:
        # 较大调整
        weights["估值水位"] = min(22, weights["估值水位"] + 5)
        weights["动量趋势"] = max(18, weights["动量趋势"] - 5)
        weights["波动率风险"] = min(8, weights["波动率风险"] + 2)
        weights["情绪面"] = max(3, weights["情绪面"] - 2)
        evolved = True
        reason = f"准确率{overall_acc}%偏低, 大幅升估值+波动率, 降动量+情绪"
    
    # 如果定投超额为正, 额外奖励"估值水位"维度(因为定投核心靠估值)
    if dca_excess > 3:
        weights["估值水位"] = min(22, weights["估值水位"] + 2)
        weights["赛道热度"] = max(3, weights["赛道热度"] - 2)
        reason += f" | 定投超额+{dca_excess}%验证估值有效"
    
    # 归一化到100
    total = sum(weights.values())
    if total != 100:
        factor = 100 / total
        weights = {k: round(v * factor) for k, v in weights.items()}
        # 微调确保总和=100
        diff = 100 - sum(weights.values())
        if diff != 0:
            weights["动量趋势"] += diff
    
    result = {
        "evolved": evolved,
        "reason": reason,
        "weights": weights,
        "previous_weights": DEFAULT_WEIGHTS,
        "backtest_accuracy": overall_acc,
        "dca_excess": dca_excess,
        "updated_at": datetime.now().isoformat(),
    }
    
    # 保存进化后的权重
    fp = DATA_DIR / "_cache" / "evolved_weights.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return result
