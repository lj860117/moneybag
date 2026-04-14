"""
钱袋子 — 组合优化器 V1
从「拍脑袋分仓」到数学最优组合

支持方法：
  1. 均值-方差优化（MVO, Markowitz）：经典最大夏普比率
  2. 最小方差组合（MinVol）：风险最小化
  3. CVaR优化：控制尾部风险（幻方量化用的方法）
  4. 层次风险平价（HRP）：不需要精确协方差估计，更稳健
  5. 等权重基准：1/N 平均分配

约束：
  - 单只上限: 默认 20%（可配置）
  - 做多 only（不允许做空）
  - 总权重 = 100%

参考：
  - Markowitz, "Portfolio Selection" (1952)
  - Maillard et al., "The Properties of Equally Weighted Risk Contribution Portfolios" (2010)
  - López de Prado, "Building Diversified Portfolios that Outperform Out-of-Sample" (2016)
  - 幻方量化 CVaR 风险预算框架
"""
import time
import math
import traceback
import numpy as np
from scipy import optimize
from concurrent.futures import ThreadPoolExecutor, as_completed

_opt_cache = {}
_OPT_CACHE_TTL = 3600


# ============================================================
# 数据获取
# ============================================================

def _get_returns_matrix(codes: list, days: int = 500) -> tuple:
    """获取多只股票的日收益率矩阵"""
    all_prices = {}

    def _fetch_one(code):
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            if df is not None and len(df) > 60:
                df = df.tail(days)
                return code, df["收盘"].values.astype(np.float64)
        except Exception:
            pass
        return code, None

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(_fetch_one, c) for c in codes]
        for f in as_completed(futures):
            code, prices = f.result()
            if prices is not None and len(prices) > 60:
                all_prices[code] = prices

    if len(all_prices) < 2:
        return None, None, None

    # 对齐长度（取最短）
    min_len = min(len(v) for v in all_prices.values())
    valid_codes = list(all_prices.keys())

    price_matrix = np.column_stack([all_prices[c][-min_len:] for c in valid_codes])
    returns_matrix = np.diff(np.log(price_matrix), axis=0)  # 对数收益率

    return returns_matrix, valid_codes, price_matrix


def _annual_stats(returns_matrix):
    """年化收益率和协方差"""
    mean_ret = np.mean(returns_matrix, axis=0) * 252
    cov_matrix = np.cov(returns_matrix.T) * 252
    return mean_ret, cov_matrix


# ============================================================
# 方法 1: 均值-方差优化（最大夏普比率）
# ============================================================

def _max_sharpe(mean_ret, cov_matrix, rf=0.02, max_weight=0.20):
    """最大夏普比率组合"""
    n = len(mean_ret)

    def neg_sharpe(w):
        port_ret = np.dot(w, mean_ret)
        port_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
        return -(port_ret - rf) / (port_vol + 1e-10)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(0, max_weight) for _ in range(n)]
    x0 = np.array([1.0 / n] * n)

    result = optimize.minimize(neg_sharpe, x0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else x0


# ============================================================
# 方法 2: 最小方差组合
# ============================================================

def _min_variance(cov_matrix, max_weight=0.20):
    """最小方差组合"""
    n = cov_matrix.shape[0]

    def portfolio_vol(w):
        return np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(0, max_weight) for _ in range(n)]
    x0 = np.array([1.0 / n] * n)

    result = optimize.minimize(portfolio_vol, x0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else x0


# ============================================================
# 方法 3: CVaR 优化（幻方量化方法）
# ============================================================

def _min_cvar(returns_matrix, alpha=0.05, max_weight=0.20):
    """最小 CVaR 组合（条件在险价值）"""
    T, n = returns_matrix.shape

    def portfolio_cvar(w):
        port_returns = returns_matrix @ w
        var_threshold = np.percentile(port_returns, alpha * 100)
        tail_returns = port_returns[port_returns <= var_threshold]
        if len(tail_returns) == 0:
            return 0
        return -np.mean(tail_returns)  # 最小化负尾部均值

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(0, max_weight) for _ in range(n)]
    x0 = np.array([1.0 / n] * n)

    result = optimize.minimize(portfolio_cvar, x0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else x0


# ============================================================
# 方法 4: 层次风险平价（HRP）
# ============================================================

def _hrp(returns_matrix, cov_matrix):
    """
    层次风险平价（Hierarchical Risk Parity）
    López de Prado 2016 算法简化版
    """
    n = cov_matrix.shape[0]

    # Step 1: 相关距离矩阵
    corr = np.corrcoef(returns_matrix.T)
    corr = np.nan_to_num(corr, nan=0.0)
    dist = np.sqrt(0.5 * (1 - corr))

    # Step 2: 层次聚类（单链接）
    from scipy.cluster.hierarchy import linkage, leaves_list
    condensed_dist = []
    for i in range(n):
        for j in range(i + 1, n):
            condensed_dist.append(dist[i, j])
    condensed_dist = np.array(condensed_dist)

    if len(condensed_dist) == 0:
        return np.array([1.0 / n] * n)

    Z = linkage(condensed_dist, method="single")
    order = leaves_list(Z)

    # Step 3: 递归二分 + 反方差分配
    def _get_cluster_var(cov, indices):
        sub_cov = cov[np.ix_(indices, indices)]
        inv_diag = 1.0 / np.diag(sub_cov)
        inv_diag /= inv_diag.sum()
        return np.dot(inv_diag, np.dot(sub_cov, inv_diag))

    def _recursive_bisection(cov, order_list):
        weights = np.zeros(n)
        if len(order_list) == 1:
            weights[order_list[0]] = 1.0
            return weights

        mid = len(order_list) // 2
        left = order_list[:mid]
        right = order_list[mid:]

        var_left = _get_cluster_var(cov, left)
        var_right = _get_cluster_var(cov, right)

        alpha = 1 - var_left / (var_left + var_right + 1e-10)

        w_left = _recursive_bisection(cov, left)
        w_right = _recursive_bisection(cov, right)

        weights = alpha * w_left + (1 - alpha) * w_right
        return weights

    weights = _recursive_bisection(cov_matrix, list(order))

    # 归一化
    total = weights.sum()
    if total > 0:
        weights /= total

    return weights


# ============================================================
# 组合评估
# ============================================================

def _evaluate_portfolio(weights, mean_ret, cov_matrix, returns_matrix, rf=0.02):
    """评估组合表现"""
    port_ret = float(np.dot(weights, mean_ret))
    port_vol = float(np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights))))
    sharpe = (port_ret - rf) / (port_vol + 1e-10)

    # VaR & CVaR
    port_returns = returns_matrix @ weights
    var_95 = float(np.percentile(port_returns, 5))
    cvar_95 = float(np.mean(port_returns[port_returns <= var_95])) if np.any(port_returns <= var_95) else var_95

    # 最大回撤
    cum_returns = np.cumprod(1 + port_returns)
    running_max = np.maximum.accumulate(cum_returns)
    drawdowns = (cum_returns - running_max) / running_max
    max_dd = float(np.min(drawdowns))

    return {
        "annual_return": round(port_ret * 100, 2),
        "annual_volatility": round(port_vol * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "var_95_daily": round(var_95 * 100, 2),
        "cvar_95_daily": round(cvar_95 * 100, 2),
        "max_drawdown": round(max_dd * 100, 2),
    }


# ============================================================
# 公开 API
# ============================================================

def optimize_portfolio(user_id: str, method: str = "all", max_weight: float = 0.20) -> dict:
    """
    对用户持仓进行组合优化

    method: "sharpe" | "minvol" | "cvar" | "hrp" | "equal" | "all"
    """
    cache_key = f"opt_{user_id}_{method}_{max_weight}"
    now = time.time()
    if cache_key in _opt_cache and now - _opt_cache[cache_key]["ts"] < _OPT_CACHE_TTL:
        return _opt_cache[cache_key]["data"]

    try:
        from services.stock_monitor import get_stock_holdings
        holdings = get_stock_holdings(user_id)

        if not holdings or len(holdings) < 2:
            return {"error": "至少需要2只持仓才能优化"}

        codes = [h.get("code", "") for h in holdings if h.get("code")]
        names = {h.get("code", ""): h.get("name", "") for h in holdings}

        returns_matrix, valid_codes, price_matrix = _get_returns_matrix(codes)
        if returns_matrix is None:
            return {"error": "数据获取失败"}

        mean_ret, cov_matrix = _annual_stats(returns_matrix)
        n = len(valid_codes)

        results = {}

        methods_to_run = ["sharpe", "minvol", "cvar", "hrp", "equal"] if method == "all" else [method]

        for m in methods_to_run:
            if m == "sharpe":
                w = _max_sharpe(mean_ret, cov_matrix, max_weight=max_weight)
            elif m == "minvol":
                w = _min_variance(cov_matrix, max_weight=max_weight)
            elif m == "cvar":
                w = _min_cvar(returns_matrix, max_weight=max_weight)
            elif m == "hrp":
                w = _hrp(returns_matrix, cov_matrix)
            elif m == "equal":
                w = np.array([1.0 / n] * n)
            else:
                continue

            metrics = _evaluate_portfolio(w, mean_ret, cov_matrix, returns_matrix)
            allocations = []
            for i, code in enumerate(valid_codes):
                if w[i] > 0.001:  # 只显示权重 > 0.1% 的
                    allocations.append({
                        "code": code,
                        "name": names.get(code, ""),
                        "weight": round(float(w[i]) * 100, 1),
                    })
            allocations.sort(key=lambda x: -x["weight"])

            results[m] = {
                "name": {
                    "sharpe": "📈 最大夏普比率",
                    "minvol": "🛡️ 最小方差",
                    "cvar": "⚡ CVaR优化（幻方方法）",
                    "hrp": "🌳 层次风险平价",
                    "equal": "⚖️ 等权重基准",
                }.get(m, m),
                "allocations": allocations,
                "metrics": metrics,
            }

        # 推荐
        if len(results) > 1:
            best_method = max(
                [(k, v["metrics"]["sharpe_ratio"]) for k, v in results.items()],
                key=lambda x: x[1]
            )[0]
            recommendation = f"推荐使用「{results[best_method]['name']}」，夏普比率最高({results[best_method]['metrics']['sharpe_ratio']})"
        else:
            recommendation = ""
            best_method = list(results.keys())[0]

        # 当前持仓 vs 最优的差异
        current_weights = {}
        total_val = sum(h.get("market_value", h.get("cost", 0)) for h in holdings)
        if total_val > 0:
            for h in holdings:
                code = h.get("code", "")
                current_weights[code] = h.get("market_value", h.get("cost", 0)) / total_val

        adjustments = []
        if best_method in results:
            for alloc in results[best_method]["allocations"]:
                code = alloc["code"]
                current = round(current_weights.get(code, 0) * 100, 1)
                optimal = alloc["weight"]
                diff = round(optimal - current, 1)
                if abs(diff) > 1:
                    adjustments.append({
                        "code": code,
                        "name": alloc["name"],
                        "current": current,
                        "optimal": optimal,
                        "action": f"{'加仓' if diff > 0 else '减仓'} {abs(diff)}%",
                    })

        output = {
            "methods": results,
            "recommendation": recommendation,
            "adjustments": adjustments,
            "stock_count": n,
            "data_days": returns_matrix.shape[0],
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        _opt_cache[cache_key] = {"data": output, "ts": now}
        return output

    except Exception as e:
        traceback.print_exc()
        return {"error": f"组合优化失败: {str(e)}"}
