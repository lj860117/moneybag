"""
钱袋子 — 周报生成服务（简化版，直接使用持仓数据）
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from config import DATA_DIR

MODULE_META = {
    "name": "weekly_report",
    "scope": "user",
    "description": "周报生成 - 专业的周度投资报告",
}


def generate(user_id: str, weeks_ago: int = 0) -> dict:
    """生成周报（简化版 - 直接使用持仓数据）"""
    from datetime import datetime, timedelta
    
    # 计算时间范围
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday() + 7 * weeks_ago)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    
    period_str = f"{week_start.strftime('%m/%d')} - {week_end.strftime('%m/%d')}"
    
    # 读取上周快照（用于周环比）
    last_week_snapshot = _load_last_week_snapshot(user_id, week_start)
    
    # 直接读取持仓数据
    try:
        from services.fund_monitor import load_fund_holdings
        from services.tushare_data import get_fund_nav
        
        funds = load_fund_holdings(user_id) or []
        print(f"[WEEKLY] 读取到 {len(funds)} 只基金持仓")
        
        # 计算净资产和收益
        total_market_value = 0
        total_cost = 0
        holdings_perf = []
        
        for fund in funds:
            code = fund.get("code", "")
            name = fund.get("name", code)
            shares = fund.get("shares", 0)
            cost_nav = fund.get("costNav", 0)  # 持仓成本净值
            
            if shares <= 0:
                continue
            
            # 获取最新净值
            try:
                nav_result = get_fund_nav(code, days=5)
                if not nav_result or not nav_result.get("available"):
                    print(f"[WEEKLY] {name} 净值数据不可用")
                    continue
                
                current_nav = nav_result.get("unit_nav", 0)
                if current_nav <= 0:
                    print(f"[WEEKLY] {name} 净值异常: {current_nav}")
                    continue
                
                market_value = shares * current_nav
                total_market_value += market_value
                
                # 成本
                cost_total = shares * cost_nav if cost_nav > 0 else 0
                total_cost += cost_total
                
                # 收益
                profit_amount = market_value - cost_total if cost_total > 0 else 0
                profit_pct = (profit_amount / cost_total * 100) if cost_total > 0 else 0
                
                holdings_perf.append({
                    "code": code,
                    "name": name,
                    "shares": shares,
                    "cost": cost_nav,
                    "current_nav": current_nav,
                    "market_value": round(market_value, 2),
                    "profit_amount": round(profit_amount, 2),
                    "profit_pct": round(profit_pct, 2),
                })
                
                # 计算风险指标（只计算重要基金）
                if market_value > total_market_value * 0.15:  # 持仓占比>15%
                    risk_metrics = _calculate_risk_metrics(code, days=90)
                    if risk_metrics:
                        holdings_perf[-1]["max_drawdown"] = risk_metrics.get("max_drawdown", 0)
                        holdings_perf[-1]["volatility"] = risk_metrics.get("volatility", 0)
                        print(f"[WEEKLY] {name} 风险指标: 最大回撤={risk_metrics.get('max_drawdown', 0):.2f}%, 波动率={risk_metrics.get('volatility', 0):.2f}%")
                
                print(f"[WEEKLY] {name}: 市值={market_value:.2f}, 收益={profit_amount:+.2f} ({profit_pct:+.2f}%)")
                
            except Exception as e:
                print(f"[WEEKLY] {name} 处理失败: {e}")
                continue
        
        # 排序
        holdings_perf.sort(key=lambda x: x.get("profit_amount", 0), reverse=True)
        
        # 计算总收益
        profit_amount = total_market_value - total_cost if total_cost > 0 else 0
        profit_pct = (profit_amount / total_cost * 100) if total_cost > 0 else 0
        
        print(f"[WEEKLY] 总市值: {total_market_value:.2f}, 总成本: {total_cost:.2f}")
        print(f"[WEEKLY] 总收益: {profit_amount:+.2f} ({profit_pct:+.2f}%)")
        print(f"[WEEKLY] 个基表现: {len(holdings_perf)} 只")
        
        # 计算周环比
        week_over_week = _calculate_week_over_week(last_week_snapshot, total_market_value, profit_amount)
        
        # 生成文本格式
        narrative = _format_narrative_simple(
            user_id, period_str,
            total_market_value, total_cost, profit_amount, profit_pct,
            holdings_perf, week_over_week, week_start, week_end
        )
        
        return {
            "user_id": user_id,
            "period": period_str,
            "sections": {
                "performance": {
                    "net_worth": total_market_value,
                    "cost_basis": total_cost,
                    "profit_amount": round(profit_amount, 2),
                    "profit_pct": round(profit_pct, 2),
                    "holdings_performance": holdings_perf,
                }
            },
            "narrative": narrative,
        }
        
    except Exception as e:
        print(f"[WEEKLY] 生成失败: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def _format_narrative_simple(user_id, period_str, net_worth, cost_basis, profit_amount, profit_pct, holdings_perf, week_over_week=None, week_start=None, week_end=None) -> str:
    """简化版格式化"""
    lines = []
    lines.append(f"📋 钱袋子周报")
    lines.append("")
    lines.append(f"📋 本周家庭财务复盘（{period_str}）")
    lines.append("")
    
    # 本周表现
    lines.append("💰 本周表现")
    lines.append(f"   净资产: ¥{net_worth:,.0f}")
    
    # 周环比
    if week_over_week and week_over_week.get("available"):
        wow_net = week_over_week.get("net_worth_change", 0)
        wow_profit = week_over_week.get("profit_change", 0)
        sign_net = "+" if wow_net >= 0 else ""
        sign_profit = "+" if wow_profit >= 0 else ""
        lines.append(f"   📊 周环比: 净资产{sign_net}¥{wow_net:,.0f}｜收益{sign_profit}¥{wow_profit:,.0f}")
    
    if cost_basis > 0:
        sign = "+" if profit_amount >= 0 else ""
        lines.append(f"   投资收益: {sign}¥{profit_amount:,.0f} ({sign}{profit_pct:.1f}%)｜成本 ¥{cost_basis:,.0f}")
    
    # 个基表现
    if holdings_perf:
        lines.append("")
        lines.append("   个基表现:")
        
        winners = [h for h in holdings_perf if h.get("profit_amount", 0) > 0]
        losers = [h for h in holdings_perf if h.get("profit_amount", 0) < 0]
        
        if winners:
            best = winners[0]
            sign = "+" if best.get("profit_amount", 0) >= 0 else ""
            lines.append(f"   📈 本周赢家：{best.get('name')} {sign}{best.get('profit_amount', 0):.0f}元 ({sign}{best.get('profit_pct', 0):.1f}%)")
        
        if losers:
            worst = losers[-1]
            sign = "+" if worst.get("profit_amount", 0) >= 0 else ""
            lines.append(f"   📉 本周输家：{worst.get('name')} {sign}{worst.get('profit_amount', 0):.0f}元 ({sign}{worst.get('profit_pct', 0):.1f}%)")
    
    # 资产配置分析
    asset_alloc = _analyze_asset_allocation(holdings_perf)
    if asset_alloc:
        lines.append("")
        lines.append("📊 资产配置")
        for industry, data in sorted(asset_alloc.items(), key=lambda x: x[1]["value"], reverse=True):
            lines.append(f"   {industry}: ¥{data['value']:,.0f} ({data['pct']:.1f}%)")
    
    # 市场回顾
    market_perf = _get_market_performance()
    if market_perf:
        lines.append("")
        lines.append("📈 市场回顾")
        for market, data in market_perf.items():
            change_pct = data.get("change_pct", 0)
            sign = "+" if change_pct >= 0 else ""
            lines.append(f"   {market}: {sign}{change_pct:.2f}%")
    
    lines.append("")
    lines.append("📌 投资操作")
    
    # 读取本周交易记录
    try:
        from services.persistence import load_user
        user = load_user(user_id)
        portfolio = user.get("portfolio") or {}
        txns = portfolio.get("transactions") or []
        
        # 过滤本周交易
        week_txns = _filter_this_week_txns(txns, week_start, week_end)
        
        if week_txns:
            for txn in week_txns[:5]:  # 最多显示5条
                txn_type = txn.get("type", "").lower()
                code = txn.get("code", "")
                name = txn.get("name", code)
                shares = txn.get("shares", 0)
                amount = txn.get("amount", 0)
                
                if txn_type == "buy":
                    lines.append(f"   📥 {name} 买入 {shares} 份，¥{amount:,.0f}")
                elif txn_type == "sell":
                    lines.append(f"   📤 {name} 卖出 {shares} 份，¥{amount:,.0f}")
                else:
                    lines.append(f"   • {name}: {txn_type} {shares} 份")
            
            if len(week_txns) > 5:
                lines.append(f"   ... 还有 {len(week_txns) - 5} 笔交易")
        else:
            lines.append("   本周没有新增交易，执行纪律良好。👍")
    except Exception as e:
        print(f"[WEEKLY] 读取交易记录失败: {e}")
        import traceback
        traceback.print_exc()
        lines.append("   本周没有新增交易，执行纪律良好。👍")
    
    lines.append("")
    lines.append("💡 下周行动建议")
    lines.append("   • 市场震荡，适合高抛低吸，控制仓位")
    lines.append("")
    
    # 风险预警
    risk_alerts = _generate_risk_alerts(user_id, holdings_perf)
    if risk_alerts:
        lines.append("⚠️ 风险预警")
        for alert in risk_alerts:
            lines.append(f"   {alert}")
        lines.append("")
    
    # 风险分析（最大回撤/波动率）
    risk_analysis = _generate_risk_analysis(holdings_perf)
    if risk_analysis:
        lines.append("📊 风险分析")
        for line in risk_analysis:
            lines.append(f"   {line}")
        lines.append("")
    
    # 重要事件（简化）
    try:
        from services.financial_calendar import get_calendar_events
        events = get_calendar_events(days_ahead=7, countries=["中国", "美国"])
        if events:
            lines.append("📅 下周重要事件")
            for i, event in enumerate(events[:5], 1):
                date = event.get("date", "")
                event_name = event.get("event", "")
                lines.append(f"   {i}. {date} {event_name}")
            lines.append("")
    except Exception as e:
        print(f"[WEEKLY] 财经日历加载失败: {e}")
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"⏰ {now_str}")
    
    return "\n".join(lines)


def get_history(user_id: str, limit: int = 4) -> list:
    """获取历史周报列表"""
    return []

def _load_last_week_snapshot(user_id: str, current_week_start: datetime) -> dict:
    """读取上周的周快照"""
    from pathlib import Path
    from config import DATA_DIR
    
    try:
        # 上周的日期
        last_week_date = (current_week_start - timedelta(days=7)).strftime("%Y%m%d")
        snapshot_file = Path(DATA_DIR) / user_id / "snapshots" / f"weekly_{last_week_date}.json"
        
        if not snapshot_file.exists():
            print(f"[WEEKLY] 上周快照不存在: {snapshot_file}")
            return {}
        
        with open(snapshot_file, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        
        print(f"[WEEKLY] 读取上周快照: {snapshot_file.name}")
        return snapshot
        
    except Exception as e:
        print(f"[WEEKLY] 读取上周快照失败: {e}")
        return {}


def _calculate_week_over_week(last_week_snapshot: dict, current_net_worth: float, current_profit: float) -> dict:
    """计算周环比"""
    if not last_week_snapshot:
        return {"available": False}
    
    try:
        last_net_worth = last_week_snapshot.get("net_worth", 0)
        last_cost = last_week_snapshot.get("cost_basis", 0)
        
        if last_net_worth <= 0:
            return {"available": False}
        
        # 上周收益
        last_profit = last_net_worth - last_cost if last_cost > 0 else 0
        
        # 计算变化
        net_worth_change = current_net_worth - last_net_worth
        profit_change = current_profit - last_profit
        
        return {
            "available": True,
            "last_net_worth": last_net_worth,
            "last_profit": last_profit,
            "net_worth_change": round(net_worth_change, 2),
            "profit_change": round(profit_change, 2),
        }
        
    except Exception as e:
        print(f"[WEEKLY] 周环比计算失败: {e}")
        return {"available": False}

def _filter_this_week_txns(txns: list, week_start: datetime, week_end: datetime) -> list:
    """过滤本周的交易记录"""
    try:
        week_start_str = week_start.strftime("%Y-%m-%d")
        week_end_str = week_end.strftime("%Y-%m-%d")
        
        filtered = []
        for txn in txns:
            txn_date = txn.get("date", "")
            if not txn_date:
                continue
            
            # 标准化日期格式
            if " " in txn_date:
                txn_date = txn_date.split(" ")[0]
            
            if week_start_str <= txn_date <= week_end_str:
                filtered.append(txn)
        
        print(f"[WEEKLY] 本周交易: {len(filtered)} 笔")
        return filtered
        
    except Exception as e:
        print(f"[WEEKLY] 过滤交易记录失败: {e}")
        return []

def _generate_risk_alerts(user_id: str, holdings_perf: list) -> list:
    """生成风险预警"""
    alerts = []
    
    try:
        # 1. 检查亏损基金
        losers = [h for h in holdings_perf if h.get("profit_amount", 0) < 0]
        if losers:
            total_loss = sum(h.get("profit_amount", 0) for h in losers)
            alerts.append(f"⚠️ {len(losers)} 只基金亏损，合计 ¥{total_loss:,.0f}")
        
        # 2. 检查集中度（持仓>40%）
        total_value = sum(h.get("market_value", 0) for h in holdings_perf)
        if total_value > 0:
            for h in holdings_perf:
                pct = h.get("market_value", 0) / total_value * 100
                if pct > 40:
                    alerts.append(f"⚠️ {h.get('name')} 占比 {pct:.1f}%，过度集中")
        
        # 3. 检查单一行业暴露（如果有行业数据）
        # TODO: 后续添加行业分析
        
        print(f"[WEEKLY] 风险预警: {len(alerts)} 条")
        
    except Exception as e:
        print(f"[WEEKLY] 风险预警生成失败: {e}")
    
    return alerts

def _analyze_asset_allocation(holdings_perf: list) -> dict:
    """分析资产配置（简化版 - 基于基金名称判断）"""
    try:
        # 行业关键词映射
        industry_keywords = {
            "科技": ["科技", "创新", "半导体", "先进制造", "全球科技"],
            "消费": ["消费", "白酒", "食品", "家电"],
            "医药": ["医药", "医疗", "生物", "健康"],
            "金融": ["金融", "银行", "证券", "保险"],
            "新能源": ["新能源", "汽车", "智能汽车"],
            "港股": ["港股", "香港"],
            "QDII": ["QDII", "全球", "国际"],
        }
        
        industry_alloc = {}
        total_value = sum(h.get("market_value", 0) for h in holdings_perf)
        
        if total_value <= 0:
            return {}
        
        # 统计行业分布
        for h in holdings_perf:
            name = h.get("name", "")
            value = h.get("market_value", 0)
            
            # 判断行业
            matched = False
            for industry, keywords in industry_keywords.items():
                if any(kw in name for kw in keywords):
                    if industry not in industry_alloc:
                        industry_alloc[industry] = {"value": 0, "count": 0}
                    industry_alloc[industry]["value"] += value
                    industry_alloc[industry]["count"] += 1
                    matched = True
                    break
            
            if not matched:
                if "其他" not in industry_alloc:
                    industry_alloc["其他"] = {"value": 0, "count": 0}
                industry_alloc["其他"]["value"] += value
                industry_alloc["其他"]["count"] += 1
        
        # 计算百分比
        for industry in industry_alloc:
            industry_alloc[industry]["pct"] = round(industry_alloc[industry]["value"] / total_value * 100, 1)
        
        print(f"[WEEKLY] 资产配置分析: {len(industry_alloc)} 个行业")
        return industry_alloc
        
    except Exception as e:
        print(f"[WEEKLY] 资产配置分析失败: {e}")
        return {}

def _get_market_performance() -> dict:
    """获取本周市场表现（简化版）"""
    try:
        from services.tushare_data import _call_tushare
        from datetime import datetime, timedelta
        
        markets = {
            "沪深300": "000300.SH",
            "中证500": "000905.SH",
        }
        
        performance = {}
        
        # 计算日期范围（最近10天）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        
        for name, code in markets.items():
            try:
                # 使用 index_daily API（指数日线）
                rows = _call_tushare(
                    "index_daily",
                    {"ts_code": code, "start_date": start_date, "end_date": end_date},
                    "ts_code,trade_date,close"
                )
                
                if not rows or len(rows) < 2:
                    print(f"[WEEKLY] {name} 数据不足")
                    continue
                
                # 按日期排序
                rows = sorted(rows, key=lambda x: x.get("trade_date", ""))
                
                latest_close = float(rows[-1].get("close", 0))
                week_ago_close = float(rows[0].get("close", 0))
                
                if week_ago_close > 0:
                    change_pct = round((latest_close - week_ago_close) / week_ago_close * 100, 2)
                    performance[name] = {
                        "current": latest_close,
                        "change_pct": change_pct,
                    }
                    print(f"[WEEKLY] {name}: {change_pct:+.2f}%")
            except Exception as e:
                print(f"[WEEKLY] {name} 获取失败: {e}")
                continue
        
        return performance
        
    except Exception as e:
        print(f"[WEEKLY] 市场表现获取失败: {e}")
        return {}

def _generate_action_suggestions(market_perf: dict, holdings_perf: list, risk_alerts: list) -> list:
    """生成下周行动建议"""
    suggestions = []
    
    try:
        # 1. 基于市场表现给建议
        if market_perf:
            hs300_change = market_perf.get("沪深300", {}).get("change_pct", 0)
            
            if hs300_change < -3:
                suggestions.append("📉 市场大跌，可能是加仓良机，分批建仓")
            elif hs300_change < -1:
                suggestions.append("📉 市场调整，适合高抛低吸，控制仓位")
            elif hs300_change > 3:
                suggestions.append("📈 市场大涨，注意止盈，避免追高")
            elif hs300_change > 1:
                suggestions.append("📈 市场上涨，持有为主，可适当减仓")
            else:
                suggestions.append("➡️ 市场震荡，保持观望，等待方向明确")
        
        # 2. 基于持仓表现给建议
        losers = [h for h in holdings_perf if h.get("profit_amount", 0) < 0]
        if len(losers) >= 3:
            suggestions.append("⚠️ 多只基金亏损，建议复盘策略，考虑止损或补仓")
        
        # 3. 基于风险预警给建议
        if any("过度集中" in alert for alert in risk_alerts):
            suggestions.append("⚠️ 持仓过度集中，建议分散投资，降低风险")
        
        # 4. 默认建议
        if not suggestions:
            suggestions.append("💡 市场平稳，继续执行定投计划，保持纪律")
        
        print(f"[WEEKLY] 行动建议: {len(suggestions)} 条")
        
    except Exception as e:
        print(f"[WEEKLY] 行动建议生成失败: {e}")
        suggestions = ["💡 保持投资纪律，长期持有"]
    
    return suggestions
    

def _calculate_risk_metrics(code: str, days: int = 90) -> dict:
    """计算风险指标（最大回撤、波动率）"""
    try:
        from services.tushare_data import get_fund_nav
        
        # 获取历史净值
        nav_result = get_fund_nav(code, days=days)
        if not nav_result or not nav_result.get("available"):
            return {}
        
        # 从 navs 中提取 unit_nav 字段
        rows = nav_result.get("navs", [])
        if len(rows) < 10:  # 至少需要10个数据点
            return {}
        
        # 提取净值列表（按日期排序）
        nav_list = []
        for r in rows:
            nav = r.get("unit_nav")
            if nav is not None:
                try:
                    nav_list.append(float(nav))
                except (ValueError, TypeError):
                    continue
        
        if len(nav_list) < 10:
            return {}
        
        # 计算最大回撤
        max_dd = _calc_max_drawdown(nav_list)
        
        # 计算波动率（年化）
        volatility = _calc_volatility(nav_list)
        
        return {
            "max_drawdown": round(max_dd, 2),
            "volatility": round(volatility, 2),
        }
        
    except Exception as e:
        print(f"[WEEKLY] 风险指标计算失败 {code}: {e}")
        return {}


def _calc_max_drawdown(nav_list: list) -> float:
    """计算最大回撤"""
    try:
        max_nav = nav_list[0]
        max_dd = 0.0
        
        for nav in nav_list:
            if nav > max_nav:
                max_nav = nav
            
            drawdown = (max_nav - nav) / max_nav * 100
            if drawdown > max_dd:
                max_dd = drawdown
        
        return max_dd
        
    except Exception:
        return 0.0


def _calc_volatility(nav_list: list) -> float:
    """计算波动率（年化）"""
    try:
        import math
        
        # 计算日收益率
        returns = []
        for i in range(1, len(nav_list)):
            ret = (nav_list[i] - nav_list[i-1]) / nav_list[i-1]
            returns.append(ret)
        
        if len(returns) < 2:
            return 0.0
        
        # 计算标准差（日波动率）
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        daily_vol = math.sqrt(variance)
        
        # 年化波动率（假设252个交易日）
        annual_vol = daily_vol * math.sqrt(252) * 100
        
        return annual_vol
        
    except Exception:
        return 0.0



def _generate_risk_analysis(holdings_perf: list) -> list:
    """生成风险分析（最大回撤、波动率）"""
    try:
        # 过滤出有风险指标的基金
        funds_with_risk = [h for h in holdings_perf if h.get("max_drawdown")]
        
        if not funds_with_risk:
            return []
        
        # 按最大回撤排序（从高到低）
        funds_with_risk.sort(key=lambda x: x.get("max_drawdown", 0), reverse=True)
        
        analysis = []
        
        # 显示前3名（最大回撤最高）
        for fund in funds_with_risk[:3]:
            name = fund.get("name", "")
            max_dd = fund.get("max_drawdown", 0)
            volatility = fund.get("volatility", 0)
            
            analysis.append(f"{name}: 最大回撤 {max_dd:.2f}%, 波动率 {volatility:.2f}%")
        
        print(f"[WEEKLY] 风险分析: {len(analysis)} 条")
        return analysis
        
    except Exception as e:
        print(f"[WEEKLY] 风险分析生成失败: {e}")
        return []


def _calculate_historical_comparison(user_id: str, current_profit: float) -> dict:
    """计算历史对比（本周 vs 上周 vs 上月同期）"""
    try:
        from datetime import datetime, timedelta
        from services.fund_monitor import load_fund_holdings
        from services.tushare_data import get_fund_nav
        
        # 获取上周同一天的净值
        today = datetime.now()
        last_week_date = (today - timedelta(days=7)).strftime("%Y%m%d")
        last_month_date = (today - timedelta(days=30)).strftime("%Y%m%d")
        
        funds = load_fund_holdings(user_id) or []
        
        last_week_value = 0
        last_month_value = 0
        current_value = 0
        
        for fund in funds:
            code = fund.get("code", "")
            shares = fund.get("shares", 0)
            
            if shares <= 0:
                continue
            
            # 当前市值
            nav_result = get_fund_nav(code, days=1)
            if nav_result and nav_result.get("available"):
                current_nav = nav_result.get("unit_nav", 0)
                current_value += shares * current_nav
            
            # 上周同期市值（简化：用当前净值代替，实际应该获取历史净值）
            # TODO: 获取历史净值
            last_week_value += shares * current_nav  # 简化
        
        # 计算变化
        weekly_change = current_value - last_week_value
        weekly_pct = (weekly_change / last_week_value * 100) if last_week_value > 0 else 0
        
        return {
            "current_value": round(current_value, 2),
            "weekly_change": round(weekly_change, 2),
            "weekly_pct": round(weekly_pct, 2),
            "status": "📈" if weekly_change >= 0 else "📉"
        }
        
    except Exception as e:
        print(f"[WEEKLY] 历史对比计算失败: {e}")
        import traceback
        traceback.print_exc()
        return {}
