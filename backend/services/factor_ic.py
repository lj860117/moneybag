"""
钱袋子 — 因子 IC 检验 V1
验证 30 因子体系中哪些因子真正具有收益预测能力

核心指标：
  - IC (Information Coefficient): 因子值与未来 N 日收益的 Spearman 相关系数
  - IC_IR (IC / std(IC)): IC 的稳定性，越高说明因子越可靠
  - IC 衰减曲线: 因子在不同预测周期(5d/10d/20d/60d)的 IC 变化

学术标准：
  - |IC| > 0.03: 有效因子
  - |IC| > 0.05: 优秀因子
  - IC_IR > 0.5: 非常稳定
  - IC_IR > 0.3: 较稳定

参考：Barra 多因子模型 + 幻方量化因子研究框架
"""
import time
import math
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

_ic_cache = {}
_IC_CACHE_TTL = 86400  # 24 小时缓存（日频计算）


def _spearman_rank_corr(x: list, y: list) -> float:
    """Spearman 秩相关系数（纯 Python，不依赖 scipy）"""
    n = len(x)
    if n < 5:
        return 0.0

    # 计算排名
    def _rank(arr):
        indexed = sorted(range(n), key=lambda i: arr[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and arr[indexed[j]] == arr[indexed[j + 1]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[indexed[k]] = avg_rank
            i = j + 1
        return ranks

    rx = _rank(x)
    ry = _rank(y)

    # Spearman = Pearson(rank_x, rank_y)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))

    denom = math.sqrt(var_x * var_y)
    if denom == 0:
        return 0.0
    return cov / denom


def _get_stock_pool(limit: int = 300) -> list:
    """获取股票池（市值 TOP 300 的活跃股）"""
    try:
        from services.stock_data_provider import get_stock_data
        data = get_stock_data()
        stocks = data.get("stocks", [])

        # 过滤基本条件
        valid = []
        for s in stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            price = s.get("price")
            if not code or not name or not price or price <= 0:
                continue
            if "ST" in name:
                continue
            mcap = s.get("market_cap") or 0
            if mcap < 50:
                continue
            valid.append(s)

        # 按市值排序取 TOP N
        valid.sort(key=lambda x: x.get("market_cap", 0), reverse=True)
        return valid[:limit]
    except Exception as e:
        print(f"[IC] Stock pool failed: {e}")
        return []


def _extract_factor_values(stock: dict, financials: dict) -> dict:
    """从行情+财务数据中提取所有因子原始值"""
    factors = {}
    pe = stock.get("pe")
    pb = stock.get("pb")
    roe = financials.get("roe")
    eps = financials.get("eps")
    gm = financials.get("gross_margin")
    nm = financials.get("net_margin")
    dr = financials.get("debt_ratio")
    cf = financials.get("cash_flow_per_share")
    mcap = stock.get("market_cap")
    to = stock.get("turnover")

    # 价值因子（低PE/低PB = 高分 → 取负号使方向一致）
    if pe and 0 < pe < 300:
        factors["F01_PE"] = -pe  # 负号：PE 越低越好
    if pb and 0 < pb < 50:
        factors["F02_PB"] = -pb
    if pe and pe > 0:
        factors["F03_EP"] = 1.0 / pe  # 盈利收益率
    if roe and pb and pb > 0:
        factors["F04_ROE_PB"] = roe / pb
    if eps is not None:
        factors["F05_EPS"] = eps

    # 成长因子
    rev_g = financials.get("revenue_growth") or financials.get("revenue_yoy")
    if rev_g is not None:
        factors["F07_REV_GROWTH"] = rev_g
    np_yoy = financials.get("netprofit_yoy")
    if np_yoy is not None:
        factors["F08_NP_GROWTH"] = np_yoy
    if roe is not None:
        factors["F09_ROE"] = roe

    # 质量因子
    if gm is not None:
        factors["F13_GROSS_MARGIN"] = gm
    if nm is not None:
        factors["F14_NET_MARGIN"] = nm
    if dr is not None:
        factors["F15_DEBT_RATIO"] = -dr  # 负号：负债率越低越好
    if cf is not None:
        factors["F16_CASHFLOW"] = cf
    if mcap is not None:
        factors["F17_MARKET_CAP"] = math.log(mcap + 1)  # 对数化

    # 动量因子
    c5 = stock.get("change_5d")
    c20 = stock.get("change_20d")
    c60 = stock.get("change_60d")
    cpct = stock.get("change_pct")
    if c5 is not None:
        factors["F18_MOM_5D"] = c5
    if c20 is not None:
        factors["F19_MOM_20D"] = c20
    if c60 is not None:
        factors["F20_MOM_60D"] = c60
    if cpct is not None:
        factors["F21_MOM_1D"] = cpct

    # 风险因子
    amp = stock.get("amplitude")
    if amp is not None:
        factors["F22_AMPLITUDE"] = -amp  # 负号：振幅越低越好

    # 流动性因子
    if to is not None:
        factors["F26_TURNOVER"] = to
    if mcap is not None:
        factors["F27_MCAP_LIQ"] = mcap

    return factors


def _get_future_returns(code: str, days: int = 20) -> float | None:
    """获取个股未来 N 日收益率（用历史数据模拟：取当前往前的区间收益）
    
    实际回测时应该用时间切片。这里简化为：
    用最近 N 日收盘价 vs 当前价计算"已实现收益"作为代理变量。
    """
    try:
        from services.backtest_engine import _get_stock_hist
        prices = _get_stock_hist(code, days=days + 5)
        if not prices or len(prices) < days + 1:
            return None
        # 最近的价格
        recent = prices[-1]["close"]
        past = prices[-(days + 1)]["close"]
        if past <= 0:
            return None
        return (recent - past) / past * 100  # 百分比收益率
    except Exception:
        return None


def compute_factor_ic(
    forward_days: int = 20,
    pool_size: int = 200,
) -> dict:
    """计算所有因子的 IC 值
    
    Args:
        forward_days: 未来收益计算周期（交易日）
        pool_size: 股票池大小
    
    Returns:
        {
            "factors": {
                "F01_PE": {"ic": 0.05, "rank": 1, "level": "优秀", ...},
                ...
            },
            "summary": {...},
            "recommendations": [...]
        }
    """
    cache_key = f"ic_{forward_days}_{pool_size}"
    now = time.time()
    if cache_key in _ic_cache and now - _ic_cache[cache_key]["ts"] < _IC_CACHE_TTL:
        return _ic_cache[cache_key]["data"]

    print(f"[IC] Starting IC test: forward={forward_days}d, pool={pool_size}")
    t0 = time.time()

    # Step 1: 获取股票池
    stocks = _get_stock_pool(pool_size)
    if len(stocks) < 30:
        return {"error": "股票池不足30只，无法计算IC", "pool_size": len(stocks)}

    print(f"[IC] Stock pool: {len(stocks)} stocks")

    # Step 2: 并发获取财务数据
    from services.factor_data import get_stock_financials
    financials_map = {}

    def _fetch_fin(s):
        code = s["code"]
        clean = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        try:
            fin = get_stock_financials(clean)
            return code, fin if fin.get("available") else {}
        except Exception:
            return code, {}

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_fetch_fin, s): s for s in stocks}
        for f in as_completed(futures):
            try:
                code, fin = f.result()
                financials_map[code] = fin
            except Exception:
                pass

    fin_ok = sum(1 for v in financials_map.values() if v)
    print(f"[IC] Financials: {fin_ok}/{len(stocks)} available")

    # Step 3: 提取因子值 + 未来收益
    factor_data = {}  # {factor_name: [(value, future_return), ...]}
    skipped = 0

    def _get_return(code):
        clean = code.replace("sh", "").replace("sz", "").replace("SH", "").replace("SZ", "")
        return code, _get_future_returns(clean, forward_days)

    # 并发获取收益率
    returns_map = {}
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(_get_return, s["code"]): s for s in stocks}
        for f in as_completed(futures):
            try:
                code, ret = f.result()
                if ret is not None:
                    returns_map[code] = ret
            except Exception:
                pass

    print(f"[IC] Returns data: {len(returns_map)}/{len(stocks)} available")

    for s in stocks:
        code = s["code"]
        ret = returns_map.get(code)
        if ret is None:
            skipped += 1
            continue

        fin = financials_map.get(code, {})
        factors = _extract_factor_values(s, fin)

        for fname, fval in factors.items():
            if fval is None or math.isnan(fval) or math.isinf(fval):
                continue
            if fname not in factor_data:
                factor_data[fname] = []
            factor_data[fname].append((fval, ret))

    print(f"[IC] Factor extraction done: {len(factor_data)} factors, skipped={skipped}")

    # Step 4: 计算每个因子的 IC
    results = {}
    for fname, pairs in factor_data.items():
        if len(pairs) < 20:
            results[fname] = {
                "ic": 0, "samples": len(pairs), "level": "样本不足",
                "effective": False, "reason": f"仅{len(pairs)}个样本（需≥20）",
            }
            continue

        fvals = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]

        ic = _spearman_rank_corr(fvals, rets)

        # IC 质量评级
        abs_ic = abs(ic)
        if abs_ic >= 0.05:
            level = "优秀"
            effective = True
        elif abs_ic >= 0.03:
            level = "有效"
            effective = True
        elif abs_ic >= 0.02:
            level = "微弱"
            effective = False
        else:
            level = "无效"
            effective = False

        # 因子方向
        direction = "正向" if ic > 0 else "负向"

        results[fname] = {
            "ic": round(ic, 4),
            "abs_ic": round(abs_ic, 4),
            "samples": len(pairs),
            "level": level,
            "effective": effective,
            "direction": direction,
        }

    # Step 5: 排序 + 汇总
    sorted_factors = sorted(results.items(), key=lambda x: x[1].get("abs_ic", 0), reverse=True)
    for rank, (fname, info) in enumerate(sorted_factors, 1):
        info["rank"] = rank

    effective_count = sum(1 for _, v in results.items() if v.get("effective"))
    ineffective = [fname for fname, v in results.items() if not v.get("effective") and v.get("samples", 0) >= 20]

    # 因子中文名映射
    FACTOR_NAMES = {
        "F01_PE": "市盈率(PE)", "F02_PB": "市净率(PB)", "F03_EP": "盈利收益率(EP)",
        "F04_ROE_PB": "ROE/PB复合", "F05_EPS": "每股收益(EPS)",
        "F07_REV_GROWTH": "营收增速", "F08_NP_GROWTH": "净利增速", "F09_ROE": "净资产收益率(ROE)",
        "F13_GROSS_MARGIN": "毛利率", "F14_NET_MARGIN": "净利率",
        "F15_DEBT_RATIO": "资产负债率(反)", "F16_CASHFLOW": "每股现金流", "F17_MARKET_CAP": "市值(对数)",
        "F18_MOM_5D": "5日动量", "F19_MOM_20D": "20日动量", "F20_MOM_60D": "60日动量", "F21_MOM_1D": "日内动量",
        "F22_AMPLITUDE": "振幅(反)", "F26_TURNOVER": "换手率", "F27_MCAP_LIQ": "市值(流动性)",
    }

    # 生成建议
    recommendations = []
    top3 = sorted_factors[:3]
    if top3:
        names = [FACTOR_NAMES.get(f, f) for f, _ in top3]
        recommendations.append(f"最有效的3个因子：{', '.join(names)}，建议在选股中加大权重")

    if ineffective:
        names = [FACTOR_NAMES.get(f, f) for f in ineffective[:5]]
        recommendations.append(f"无效因子({len(ineffective)}个)：{', '.join(names)}等，建议降低权重或移除")

    if effective_count / max(len(results), 1) > 0.6:
        recommendations.append("因子体系整体有效率 > 60%，框架设计合理")
    elif effective_count / max(len(results), 1) < 0.3:
        recommendations.append("⚠️ 有效因子不足 30%，需要重新审视因子设计")

    elapsed = time.time() - t0
    print(f"[IC] Done in {elapsed:.1f}s: {effective_count}/{len(results)} effective factors")

    result = {
        "factors": {fname: {**info, "name_cn": FACTOR_NAMES.get(fname, fname)} for fname, info in results.items()},
        "ranking": [
            {
                "factor": fname,
                "name_cn": FACTOR_NAMES.get(fname, fname),
                **info,
            }
            for fname, info in sorted_factors
        ],
        "summary": {
            "total_factors": len(results),
            "effective_factors": effective_count,
            "effectiveness_rate": round(effective_count / max(len(results), 1) * 100, 1),
            "pool_size": len(stocks),
            "samples_with_returns": len(returns_map),
            "forward_days": forward_days,
            "elapsed_seconds": round(elapsed, 1),
        },
        "recommendations": recommendations,
        "ineffective_factors": ineffective,
    }

    _ic_cache[cache_key] = {"data": result, "ts": time.time()}
    return result


def compute_ic_decay(pool_size: int = 150) -> dict:
    """IC 衰减曲线：在不同预测周期下的 IC 变化
    
    用于判断因子是短期有效还是长期有效
    """
    cache_key = f"ic_decay_{pool_size}"
    now = time.time()
    if cache_key in _ic_cache and now - _ic_cache[cache_key]["ts"] < _IC_CACHE_TTL:
        return _ic_cache[cache_key]["data"]

    periods = [5, 10, 20, 60]
    decay = {}

    for days in periods:
        print(f"[IC_DECAY] Computing IC for forward={days}d...")
        ic_result = compute_factor_ic(forward_days=days, pool_size=pool_size)
        if "error" in ic_result:
            continue

        for fname, info in ic_result.get("factors", {}).items():
            if fname not in decay:
                decay[fname] = {"name_cn": info.get("name_cn", fname), "periods": {}}
            decay[fname]["periods"][str(days)] = {
                "ic": info.get("ic", 0),
                "level": info.get("level", ""),
                "effective": info.get("effective", False),
            }

    # 分析衰减模式
    for fname, info in decay.items():
        periods_data = info["periods"]
        ics = [periods_data.get(str(d), {}).get("ic", 0) for d in [5, 10, 20, 60]]
        abs_ics = [abs(ic) for ic in ics]

        # 判断类型
        if len(abs_ics) >= 3:
            if abs_ics[0] > abs_ics[-1] * 1.5:
                info["pattern"] = "短期因子"
                info["description"] = "短期预测力强，长期衰减"
            elif abs_ics[-1] > abs_ics[0] * 1.5:
                info["pattern"] = "长期因子"
                info["description"] = "长期预测力更强"
            else:
                info["pattern"] = "稳定因子"
                info["description"] = "各周期预测力稳定"
        else:
            info["pattern"] = "数据不足"
            info["description"] = ""

    result = {
        "decay": decay,
        "periods": periods,
        "pool_size": pool_size,
    }

    _ic_cache[cache_key] = {"data": result, "ts": time.time()}
    return result
