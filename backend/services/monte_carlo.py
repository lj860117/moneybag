"""
钱袋子 — 蒙特卡洛模拟 V1
用概率分布替代单点预测，给出投资结果的概率区间

核心能力：
  1. 基于历史收益分布参数（均值、标准差），模拟 N 条未来路径
  2. 输出概率分布：P10 / P25 / P50 / P75 / P90（从最差到最好）
  3. 盈利概率（终值 > 本金的概率）
  4. 最大回撤分布
  5. 纪律策略模拟（止损止盈对概率的影响）

参考：
  - 幻方量化 CVaR 模型简化版
  - AQR Capital Management Monte Carlo 方法论
  - CFA Level 2 Monte Carlo Simulation 章节
"""
import math
import time
import random
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "monte_carlo",
    "scope": "public",
    "input": ["stock_code", "simulations", "horizon"],
    "output": "probability_distribution",
    "cost": "cpu",
    "tags": ["风险", "蒙特卡洛", "VaR", "概率"],
    "description": "GBM 5000次路径模拟，P10-P90+VaR/CVaR+盈利概率",
    "layer": "analysis",
    "priority": 4,
}

_mc_cache = {}
_MC_CACHE_TTL = 3600  # 1 小时


def _get_historical_params(code: str, days: int = 750) -> dict | None:
    """从历史数据提取收益分布参数"""
    try:
        from services.backtest_engine import _get_stock_hist
        prices = _get_stock_hist(code, days=days)
        if not prices or len(prices) < 60:
            return None

        closes = [p["close"] for p in prices]
        daily_returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                daily_returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

        if len(daily_returns) < 30:
            return None

        mean_r = sum(daily_returns) / len(daily_returns)
        var_r = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
        std_r = math.sqrt(var_r)

        # 偏度（skewness）
        if std_r > 0:
            skew = sum((r - mean_r) ** 3 for r in daily_returns) / (len(daily_returns) * std_r ** 3)
        else:
            skew = 0

        # 最大回撤
        peak = closes[0]
        max_dd = 0
        for c in closes:
            if c > peak:
                peak = c
            dd = (peak - c) / peak
            if dd > max_dd:
                max_dd = dd

        return {
            "mean_daily": mean_r,
            "std_daily": std_r,
            "annual_return": mean_r * 250,
            "annual_vol": std_r * math.sqrt(250),
            "skewness": round(skew, 2),
            "max_drawdown": round(max_dd, 4),
            "trading_days": len(daily_returns),
            "last_price": closes[-1],
        }
    except Exception as e:
        print(f"[MC] Params failed for {code}: {e}")
        return None


def _simulate_path(
    mean: float,
    std: float,
    days: int,
    initial: float = 10000,
    stop_loss: float = -0.08,
    take_profit: float = 0.20,
    apply_discipline: bool = True,
) -> dict:
    """模拟一条价格路径
    
    Returns:
        {
            "final_value": float,
            "return_pct": float,
            "max_drawdown": float,
            "stopped_loss": bool,
            "took_profit": bool,
            "days_held": int,
        }
    """
    value = initial
    peak = initial
    max_dd = 0
    stopped_loss = False
    took_profit = False
    days_held = days

    for d in range(days):
        # 几何布朗运动
        daily_r = random.gauss(mean, std)
        value *= (1 + daily_r)

        if value <= 0:
            value = 0
            days_held = d + 1
            break

        # 更新最大回撤
        if value > peak:
            peak = value
        dd = (peak - value) / peak
        if dd > max_dd:
            max_dd = dd

        # 纪律规则
        if apply_discipline:
            current_return = (value - initial) / initial

            # 止损：亏损达到阈值，强制卖出
            if current_return <= stop_loss and not stopped_loss:
                stopped_loss = True
                days_held = d + 1
                break

            # 止盈：盈利达到阈值，卖出一半（简化为锁定一半利润）
            if current_return >= take_profit and not took_profit:
                took_profit = True
                # 锁定一半利润
                profit = value - initial
                value = initial + profit * 0.5
                peak = value  # 重置峰值

    return_pct = (value - initial) / initial
    return {
        "final_value": round(value, 2),
        "return_pct": round(return_pct * 100, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "stopped_loss": stopped_loss,
        "took_profit": took_profit,
        "days_held": days_held,
    }


def monte_carlo_single(
    code: str,
    simulations: int = 5000,
    horizon_days: int = 250,
    initial_investment: float = 10000,
    apply_discipline: bool = True,
    stop_loss: float = -0.08,
    take_profit: float = 0.20,
) -> dict:
    """单只股票蒙特卡洛模拟
    
    Args:
        code: 股票代码
        simulations: 模拟次数（5000 次足够稳定）
        horizon_days: 投资期限（交易日，250=1年）
        initial_investment: 初始投资金额
        apply_discipline: 是否应用纪律规则
        stop_loss: 止损线
        take_profit: 止盈线
    
    Returns:
        {
            "percentiles": {P10, P25, P50, P75, P90},
            "profit_probability": float,  # 盈利概率
            "expected_return": float,
            "var_95": float,  # 95% VaR（最差5%的收益）
            "cvar_95": float,  # 条件 VaR
            ...
        }
    """
    cache_key = f"mc_{code}_{simulations}_{horizon_days}_{apply_discipline}"
    now = time.time()
    if cache_key in _mc_cache and now - _mc_cache[cache_key]["ts"] < _MC_CACHE_TTL:
        return _mc_cache[cache_key]["data"]

    print(f"[MC] Single: {code}, {simulations} sims, {horizon_days}d, discipline={apply_discipline}")
    t0 = time.time()

    # 获取历史分布参数
    clean_code = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
    params = _get_historical_params(clean_code)
    if not params:
        return {"error": f"无法获取 {code} 的历史数据", "code": code}

    mean = params["mean_daily"]
    std = params["std_daily"]

    # 批量模拟
    results = []
    for _ in range(simulations):
        path = _simulate_path(
            mean=mean, std=std, days=horizon_days,
            initial=initial_investment,
            stop_loss=stop_loss, take_profit=take_profit,
            apply_discipline=apply_discipline,
        )
        results.append(path)

    # 统计分析
    returns = sorted([r["return_pct"] for r in results])
    drawdowns = sorted([r["max_drawdown"] for r in results])
    final_values = [r["final_value"] for r in results]

    n = len(returns)

    def _percentile(arr, pct):
        idx = int(pct / 100 * len(arr))
        idx = max(0, min(idx, len(arr) - 1))
        return arr[idx]

    # 核心概率指标
    profit_count = sum(1 for r in returns if r > 0)
    profit_probability = round(profit_count / n * 100, 1)

    loss_10_count = sum(1 for r in returns if r < -10)
    loss_10_probability = round(loss_10_count / n * 100, 1)

    gain_20_count = sum(1 for r in returns if r > 20)
    gain_20_probability = round(gain_20_count / n * 100, 1)

    stop_loss_count = sum(1 for r in results if r["stopped_loss"])
    take_profit_count = sum(1 for r in results if r["took_profit"])

    # VaR 和 CVaR
    var_95 = _percentile(returns, 5)  # 最差 5%
    cvar_95_returns = [r for r in returns if r <= var_95]
    cvar_95 = round(sum(cvar_95_returns) / max(len(cvar_95_returns), 1), 2)

    elapsed = time.time() - t0

    result = {
        "code": code,
        "simulations": simulations,
        "horizon_days": horizon_days,
        "horizon_years": round(horizon_days / 250, 1),
        "initial_investment": initial_investment,
        "discipline_applied": apply_discipline,
        "historical_params": {
            "annual_return": round(params["annual_return"] * 100, 2),
            "annual_volatility": round(params["annual_vol"] * 100, 2),
            "skewness": params["skewness"],
            "historical_max_dd": round(params["max_drawdown"] * 100, 2),
        },
        "percentiles": {
            "P10": round(_percentile(returns, 10), 2),
            "P25": round(_percentile(returns, 25), 2),
            "P50": round(_percentile(returns, 50), 2),
            "P75": round(_percentile(returns, 75), 2),
            "P90": round(_percentile(returns, 90), 2),
        },
        "value_percentiles": {
            "P10": round(_percentile(sorted(final_values), 10), 0),
            "P25": round(_percentile(sorted(final_values), 25), 0),
            "P50": round(_percentile(sorted(final_values), 50), 0),
            "P75": round(_percentile(sorted(final_values), 75), 0),
            "P90": round(_percentile(sorted(final_values), 90), 0),
        },
        "probabilities": {
            "profit": profit_probability,
            "loss_over_10pct": loss_10_probability,
            "gain_over_20pct": gain_20_probability,
        },
        "risk_metrics": {
            "var_95": round(var_95, 2),
            "cvar_95": cvar_95,
            "max_drawdown_P50": round(_percentile(drawdowns, 50), 2),
            "max_drawdown_P90": round(_percentile(drawdowns, 90), 2),
        },
        "discipline_stats": {
            "stop_loss_triggered": round(stop_loss_count / n * 100, 1),
            "take_profit_triggered": round(take_profit_count / n * 100, 1),
            "stop_loss_threshold": f"{stop_loss*100}%",
            "take_profit_threshold": f"{take_profit*100}%",
        },
        "expected_return": round(sum(returns) / n, 2),
        "return_std": round(
            math.sqrt(sum((r - sum(returns) / n) ** 2 for r in returns) / n), 2
        ),
        "elapsed_seconds": round(elapsed, 1),
    }

    _mc_cache[cache_key] = {"data": result, "ts": time.time()}
    print(f"[MC] Done: profit_prob={profit_probability}%, P50={result['percentiles']['P50']}%, {elapsed:.1f}s")
    return result


def monte_carlo_portfolio(
    holdings: list,
    simulations: int = 3000,
    horizon_days: int = 250,
    initial_investment: float = 100000,
    apply_discipline: bool = True,
) -> dict:
    """组合蒙特卡洛模拟
    
    holdings: [{"code": "600519", "weight": 0.2}, ...]
    """
    cache_key = f"mc_port_{'_'.join(h['code'] for h in holdings)}_{simulations}_{horizon_days}_{apply_discipline}"
    now = time.time()
    if cache_key in _mc_cache and now - _mc_cache[cache_key]["ts"] < _MC_CACHE_TTL:
        return _mc_cache[cache_key]["data"]

    print(f"[MC] Portfolio: {len(holdings)} holdings, {simulations} sims")
    t0 = time.time()

    # 获取每只股票的历史参数
    params_map = {}
    for h in holdings:
        code = h["code"]
        clean = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        p = _get_historical_params(clean)
        if p:
            params_map[code] = p

    if len(params_map) < 2:
        return {"error": "组合中有效数据不足2只股票", "available": len(params_map)}

    # 归一化权重
    total_weight = sum(h.get("weight", 1.0 / len(holdings)) for h in holdings if h["code"] in params_map)
    weights = {}
    for h in holdings:
        code = h["code"]
        if code in params_map:
            weights[code] = h.get("weight", 1.0 / len(holdings)) / total_weight if total_weight > 0 else 0

    # 模拟
    portfolio_returns = []
    portfolio_drawdowns = []

    from config import STOCK_STOP_LOSS, STOCK_TAKE_PROFIT

    for _ in range(simulations):
        port_value = initial_investment
        port_peak = initial_investment
        port_max_dd = 0

        # 每只股票独立模拟，按权重加权
        stock_finals = {}
        for code, p in params_map.items():
            w = weights.get(code, 0)
            if w <= 0:
                continue
            alloc = initial_investment * w
            path = _simulate_path(
                mean=p["mean_daily"], std=p["std_daily"],
                days=horizon_days, initial=alloc,
                stop_loss=STOCK_STOP_LOSS if apply_discipline else -1,
                take_profit=STOCK_TAKE_PROFIT if apply_discipline else 10,
                apply_discipline=apply_discipline,
            )
            stock_finals[code] = path["final_value"]

        port_value = sum(stock_finals.values())
        port_return = (port_value - initial_investment) / initial_investment * 100
        portfolio_returns.append(port_return)

    portfolio_returns.sort()
    n = len(portfolio_returns)

    def _pct(arr, p):
        idx = max(0, min(int(p / 100 * len(arr)), len(arr) - 1))
        return arr[idx]

    profit_prob = round(sum(1 for r in portfolio_returns if r > 0) / n * 100, 1)
    var_95 = _pct(portfolio_returns, 5)
    cvar_list = [r for r in portfolio_returns if r <= var_95]
    cvar_95 = round(sum(cvar_list) / max(len(cvar_list), 1), 2)

    elapsed = time.time() - t0

    result = {
        "holdings": [
            {
                "code": h["code"],
                "weight": round(weights.get(h["code"], 0) * 100, 1),
                "has_data": h["code"] in params_map,
                "annual_return": round(params_map[h["code"]]["annual_return"] * 100, 2) if h["code"] in params_map else None,
                "annual_vol": round(params_map[h["code"]]["annual_vol"] * 100, 2) if h["code"] in params_map else None,
            }
            for h in holdings
        ],
        "simulations": simulations,
        "horizon_days": horizon_days,
        "horizon_years": round(horizon_days / 250, 1),
        "initial_investment": initial_investment,
        "discipline_applied": apply_discipline,
        "percentiles": {
            "P10": round(_pct(portfolio_returns, 10), 2),
            "P25": round(_pct(portfolio_returns, 25), 2),
            "P50": round(_pct(portfolio_returns, 50), 2),
            "P75": round(_pct(portfolio_returns, 75), 2),
            "P90": round(_pct(portfolio_returns, 90), 2),
        },
        "probabilities": {
            "profit": profit_prob,
            "loss_over_10pct": round(sum(1 for r in portfolio_returns if r < -10) / n * 100, 1),
            "gain_over_20pct": round(sum(1 for r in portfolio_returns if r > 20) / n * 100, 1),
        },
        "risk_metrics": {
            "var_95": round(var_95, 2),
            "cvar_95": cvar_95,
        },
        "expected_return": round(sum(portfolio_returns) / n, 2),
        "return_std": round(
            math.sqrt(sum((r - sum(portfolio_returns) / n) ** 2 for r in portfolio_returns) / n), 2
        ),
        "elapsed_seconds": round(elapsed, 1),
    }

    _mc_cache[cache_key] = {"data": result, "ts": time.time()}
    print(f"[MC] Portfolio done: profit_prob={profit_prob}%, P50={result['percentiles']['P50']}%, {elapsed:.1f}s")
    return result


def monte_carlo_compare(
    code: str,
    simulations: int = 3000,
    horizon_days: int = 250,
) -> dict:
    """对比：有纪律 vs 无纪律的蒙特卡洛结果
    
    直观展示纪律投资的价值
    """
    cache_key = f"mc_cmp_{code}_{simulations}_{horizon_days}"
    now = time.time()
    if cache_key in _mc_cache and now - _mc_cache[cache_key]["ts"] < _MC_CACHE_TTL:
        return _mc_cache[cache_key]["data"]

    print(f"[MC_CMP] Comparing discipline vs no-discipline for {code}")

    # 有纪律
    with_discipline = monte_carlo_single(
        code, simulations=simulations, horizon_days=horizon_days,
        apply_discipline=True,
    )

    # 无纪律
    without_discipline = monte_carlo_single(
        code, simulations=simulations, horizon_days=horizon_days,
        apply_discipline=False,
    )

    if "error" in with_discipline or "error" in without_discipline:
        return {
            "error": with_discipline.get("error") or without_discipline.get("error"),
            "code": code,
        }

    # 计算差值
    result = {
        "code": code,
        "with_discipline": with_discipline,
        "without_discipline": without_discipline,
        "improvement": {
            "profit_probability": round(
                with_discipline["probabilities"]["profit"] - without_discipline["probabilities"]["profit"], 1
            ),
            "loss_reduction": round(
                without_discipline["probabilities"]["loss_over_10pct"] - with_discipline["probabilities"]["loss_over_10pct"], 1
            ),
            "var_improvement": round(
                with_discipline["risk_metrics"]["var_95"] - without_discipline["risk_metrics"]["var_95"], 2
            ),
            "median_return_diff": round(
                with_discipline["percentiles"]["P50"] - without_discipline["percentiles"]["P50"], 2
            ),
        },
        "conclusion": "",
    }

    # 生成结论
    imp = result["improvement"]
    parts = []
    if imp["profit_probability"] > 0:
        parts.append(f"盈利概率提升 {imp['profit_probability']}个百分点")
    if imp["loss_reduction"] > 0:
        parts.append(f"大亏概率降低 {imp['loss_reduction']}个百分点")
    if imp["var_improvement"] > 0:
        parts.append(f"最差情景改善 {imp['var_improvement']}%")

    if parts:
        result["conclusion"] = f"纪律投资效果：{', '.join(parts)}。止损线(-8%)有效截断尾部风险，止盈(+20%)锁定收益。"
    else:
        result["conclusion"] = "在当前市场状态下，纪律规则对该股票影响有限，可能因为近期波动不大。"

    _mc_cache[cache_key] = {"data": result, "ts": time.time()}
    return result


# ---- V4 底座：enrich() 适配层 ----
def enrich(ctx):
    """Pipeline 适配：从 ctx 读第一只股票 → 蒙特卡洛模拟 → 写回 ctx"""
    try:
        codes = [h.get("code", "") for h in (ctx.stock_holdings or []) if h.get("code")]
        if not codes:
            ctx.modules_skipped.append({"name": "monte_carlo", "reason": "no_stock_holdings"})
            return ctx
        result = monte_carlo_single(codes[0], simulations=2000, horizon_days=60)
        if "error" in result:
            raise Exception(result["error"])
        probs = result.get("probabilities", {})
        profit_prob = probs.get("profit", 50)
        direction = "bullish" if profit_prob > 60 else ("bearish" if profit_prob < 40 else "neutral")
        ctx.modules_results["monte_carlo"] = {
            "available": True,
            "direction": direction,
            "confidence": round(abs(profit_prob - 50) + 50, 1),
            "data": {"code": codes[0], "profit_prob": profit_prob, "percentiles": result.get("percentiles", {}), "risk_metrics": result.get("risk_metrics", {})},
            "cost": "cpu",
            "latency_ms": 0,
        }
        ctx.modules_called.append("monte_carlo")
    except Exception as e:
        print(f"[monte_carlo.enrich] Error: {e}")
        ctx.errors.append({"module": "monte_carlo", "error": str(e), "fallback_used": True})
        ctx.modules_skipped.append({"name": "monte_carlo", "reason": str(e)})
    return ctx
