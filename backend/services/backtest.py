"""
钱袋子 — 回测引擎
策略回测 + 高级指标（IR、Calmar、Sortino等）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "backtest",
    "scope": "public",
    "input": ['strategy', 'years'],
    "output": "backtest_comparison",
    "cost": "cpu",
    "tags": ['策略回测', '定投对比', 'IR', 'Calmar'],
    "description": "策略回测：智能定投vs固定定投+高级指标(IR/Calmar/Sortino)",
    "layer": "analysis",
    "priority": 5,
}
import math
from services.data_layer import get_valuation_percentile

def run_backtest(strategy: str = "smart_dca", years: int = 3, monthly_amount: float = 1000) -> dict:
    """简易策略回测 — 用沪深300历史数据"""
    result = {
        "strategy": strategy,
        "years": years,
        "monthlyAmount": monthly_amount,
        "totalInvested": 0,
        "finalValue": 0,
        "totalReturn": 0,
        "totalReturnPct": 0,
        "annualizedReturn": 0,
        "maxDrawdown": 0,
        "months": [],
        "comparison": {},
    }

    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is None or len(df) < years * 250:
            return {**result, "error": "历史数据不足"}

        # 取最近 N 年数据，按月采样
        total_days = years * 250
        daily = df.tail(total_days)
        closes = daily["close"].values
        dates = daily.index if hasattr(daily.index, '__len__') else list(range(len(daily)))

        # 每月取一个数据点（约每 20 个交易日）
        monthly_prices = []
        monthly_dates = []
        for i in range(0, len(closes), 20):
            monthly_prices.append(float(closes[i]))
            if hasattr(dates[i], 'strftime'):
                monthly_dates.append(dates[i].strftime("%Y-%m"))
            else:
                monthly_dates.append(f"M{i//20+1}")

        if len(monthly_prices) < 12:
            return {**result, "error": "月度数据不足"}

        # 计算近3年估值百分位序列（用价格百分位近似）
        all_closes = [float(c) for c in df.tail(total_days + 250)["close"].values]

        # --- 固定定投策略 ---
        fix_shares = 0
        fix_invested = 0
        fix_curve = []
        for i, price in enumerate(monthly_prices):
            shares_bought = monthly_amount / price
            fix_shares += shares_bought
            fix_invested += monthly_amount
            fix_curve.append({
                "month": monthly_dates[i] if i < len(monthly_dates) else f"M{i+1}",
                "invested": round(fix_invested, 2),
                "value": round(fix_shares * price, 2),
            })

        fix_final = fix_shares * monthly_prices[-1]
        fix_return = fix_final - fix_invested
        fix_return_pct = (fix_return / fix_invested * 100) if fix_invested > 0 else 0

        # --- 智能定投策略 ---
        smart_shares = 0
        smart_invested = 0
        smart_curve = []
        for i, price in enumerate(monthly_prices):
            # 计算当月的价格百分位（用前 N 个月的价格范围）
            lookback = all_closes[:len(all_closes) - len(monthly_prices) + i + 1]
            if len(lookback) > 60:
                lookback_recent = lookback[-750:]  # 近3年
                pct = sum(1 for p in lookback_recent if p <= price) / len(lookback_recent) * 100
            else:
                pct = 50

            # 根据估值调整定投金额
            dca = calc_smart_dca(monthly_amount, pct)
            actual_amount = dca["smartAmount"]

            shares_bought = actual_amount / price
            smart_shares += shares_bought
            smart_invested += actual_amount
            smart_curve.append({
                "month": monthly_dates[i] if i < len(monthly_dates) else f"M{i+1}",
                "invested": round(smart_invested, 2),
                "value": round(smart_shares * price, 2),
                "multiplier": dca["multiplier"],
            })

        smart_final = smart_shares * monthly_prices[-1]
        smart_return = smart_final - smart_invested
        smart_return_pct = (smart_return / smart_invested * 100) if smart_invested > 0 else 0

        # --- 计算最大回撤 ---
        def calc_max_drawdown(curve):
            peak = 0
            max_dd = 0
            for pt in curve:
                v = pt["value"]
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
            return round(max_dd, 2)

        # --- 年化收益率 ---
        n_years = len(monthly_prices) / 12
        fix_annual = ((1 + fix_return_pct / 100) ** (1 / n_years) - 1) * 100 if n_years > 0 and fix_return_pct > -100 else 0
        smart_annual = ((1 + smart_return_pct / 100) ** (1 / n_years) - 1) * 100 if n_years > 0 and smart_return_pct > -100 else 0

        # --- V4.5 增强回测指标（借鉴幻方量化科学化回测）---
        def calc_advanced_metrics(curve, annual_return):
            """计算信息比率IR、卡玛比率、胜率、盈亏比、Sortino比率"""
            metrics = {"ir": 0, "calmar": 0, "winRate": 0, "profitLossRatio": 0, "sortino": 0}
            if len(curve) < 6:
                return metrics

            # 月度收益率序列
            monthly_returns = []
            for i in range(1, len(curve)):
                prev_val = curve[i-1]["value"]
                curr_val = curve[i]["value"]
                if prev_val > 0:
                    monthly_returns.append((curr_val - prev_val) / prev_val * 100)

            if not monthly_returns:
                return metrics

            # 基准月度收益（沪深300指数的月度涨跌）
            benchmark_returns = []
            for i in range(1, len(monthly_prices)):
                if monthly_prices[i-1] > 0:
                    benchmark_returns.append((monthly_prices[i] - monthly_prices[i-1]) / monthly_prices[i-1] * 100)
            # 对齐长度
            min_len = min(len(monthly_returns), len(benchmark_returns))
            monthly_returns = monthly_returns[:min_len]
            benchmark_returns = benchmark_returns[:min_len]

            # 1. 信息比率 IR = (策略收益 - 基准收益) / 跟踪误差
            if min_len > 3:
                excess = [monthly_returns[i] - benchmark_returns[i] for i in range(min_len)]
                avg_excess = sum(excess) / len(excess)
                tracking_error = (sum((e - avg_excess) ** 2 for e in excess) / len(excess)) ** 0.5
                metrics["ir"] = round(avg_excess / tracking_error * (12 ** 0.5), 2) if tracking_error > 0.01 else 0

            # 2. 卡玛比率 = 年化收益率 / 最大回撤
            max_dd = calc_max_drawdown(curve)
            metrics["calmar"] = round(annual_return / max_dd, 2) if max_dd > 0.1 else 99.99

            # 3. 胜率 = 正收益月数 / 总月数
            wins = sum(1 for r in monthly_returns if r > 0)
            metrics["winRate"] = round(wins / len(monthly_returns) * 100, 1) if monthly_returns else 0

            # 4. 盈亏比 = 平均盈利 / 平均亏损
            gains = [r for r in monthly_returns if r > 0]
            losses = [abs(r) for r in monthly_returns if r < 0]
            avg_gain = sum(gains) / len(gains) if gains else 0
            avg_loss = sum(losses) / len(losses) if losses else 0.01
            metrics["profitLossRatio"] = round(avg_gain / avg_loss, 2) if avg_loss > 0.01 else 99.99

            # 5. Sortino 比率 = (收益-无风险利率) / 下行标准差
            risk_free_monthly = 0.15  # 假设年化1.8%/12
            downside = [r for r in monthly_returns if r < risk_free_monthly]
            if downside:
                downside_std = (sum((r - risk_free_monthly) ** 2 for r in downside) / len(downside)) ** 0.5
                avg_return = sum(monthly_returns) / len(monthly_returns)
                metrics["sortino"] = round((avg_return - risk_free_monthly) / downside_std * (12 ** 0.5), 2) if downside_std > 0.01 else 0

            return metrics

        fix_metrics = calc_advanced_metrics(fix_curve, fix_annual)
        smart_metrics = calc_advanced_metrics(smart_curve, smart_annual)

        result.update({
            "totalInvested": round(smart_invested, 2),
            "finalValue": round(smart_final, 2),
            "totalReturn": round(smart_return, 2),
            "totalReturnPct": round(smart_return_pct, 2),
            "annualizedReturn": round(smart_annual, 2),
            "maxDrawdown": calc_max_drawdown(smart_curve),
            "advancedMetrics": smart_metrics,
            "months": smart_curve,
            "comparison": {
                "fixedDca": {
                    "invested": round(fix_invested, 2),
                    "finalValue": round(fix_final, 2),
                    "totalReturn": round(fix_return, 2),
                    "totalReturnPct": round(fix_return_pct, 2),
                    "annualizedReturn": round(fix_annual, 2),
                    "maxDrawdown": calc_max_drawdown(fix_curve),
                    "advancedMetrics": fix_metrics,
                    "months": fix_curve,
                },
                "smartDca": {
                    "invested": round(smart_invested, 2),
                    "finalValue": round(smart_final, 2),
                    "totalReturn": round(smart_return, 2),
                    "totalReturnPct": round(smart_return_pct, 2),
                    "annualizedReturn": round(smart_annual, 2),
                    "maxDrawdown": calc_max_drawdown(smart_curve),
                    "advancedMetrics": smart_metrics,
                    "months": smart_curve,
                },
                "advantage": round(smart_return_pct - fix_return_pct, 2),
            },
        })

    except Exception as e:
        print(f"[BACKTEST] Failed: {e}")
        import traceback; traceback.print_exc()
        result["error"] = str(e)

    return result


