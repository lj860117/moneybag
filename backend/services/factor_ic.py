"""
钱袋子 — 因子 IC 检验 V2（真实面板时间切片，无未来函数）
验证 30 因子体系中哪些因子真正具有收益预测能力

════════════════════════════════════════════════════════════════
V1（已废弃）的致命缺陷 —— 未来函数 / 循环论证
────────────────────────────────────────────────────────────────
V1 的 `_get_future_returns()` 名字叫"未来收益"，实际返回的是
**过去 N 日已实现收益**（prices[-1] vs prices[-(N+1)]）。于是：

    IC = corr( 今日因子值 , 过去20日涨幅 )

对动量类因子（F18/F19/F20/F21，本身就是涨幅）而言，等式两边是同一个量，
IC 必然接近 ±1 —— 纯循环论证，不含任何预测信息。据此做的"因子有效性排序"
"IC 衰减曲线""LLM 因子加权"全部无效。

────────────────────────────────────────────────────────────────
V2 的做法：面板时间切片（panel time-slice）
────────────────────────────────────────────────────────────────
  1. 取若干个历史截面日 T（默认过去约 1 年，每 20 交易日一个，最多 12 个）；
  2. 每个 T 只用**T 日收盘时已经可得的信息**构造因子：
       - 估值/流动性：T 日的 daily_basic 截面（pe / pb / 总市值 / 换手率）
       - 动量      ：T 日及之前的收盘价序列
       - 财务      ：ann_date <= T 的最近一期财报（point-in-time，无前视）
  3. 收益端用**真实的 T → T+forward_days 前瞻收益**（同一序列 T 之后第 N 个
     交易日的收盘价），不再用"过去 N 日已实现收益"冒充；
  4. 每个截面算一个 Spearman IC，得到 IC 序列后输出 IC 均值 / ICIR /
     IC>0 占比 / t 统计量。

核心指标：
  - IC (Information Coefficient): 因子值与未来 N 日收益的 Spearman 相关系数
  - IC_IR (IC均值 / IC标准差): IC 的稳定性
  - IC>0 占比: 方向一致性
  - t 统计量: IC 均值是否显著不为 0（|t| >= 2 视为显著）
  - IC 衰减曲线: 因子在不同预测周期(5d/10d/20d/60d)的 IC 变化

学术标准：
  - |IC| > 0.03: 有效因子
  - |IC| > 0.05: 优秀因子
  - IC_IR > 0.5: 非常稳定
  - IC_IR > 0.3: 较稳定

⚠️ 已知残留局限（无法在现有数据条件下消除，已在输出 limitations 中声明）：
  - 股票池用「当前」市值 TOP N 构建，存在幸存者偏差（历史上当时的小票不在池内）；
  - F22_AMPLITUDE 需要日内最高/最低价，而 `_get_stock_hist` 只返回收盘价，
    面板模式无法回溯，故标为不可用。

参考：Barra 多因子模型 + 幻方量化因子研究框架
"""
from __future__ import annotations

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "factor_ic",
    "scope": "public",
    "input": ['pool_size', 'forward_days'],
    "output": "ic_ranking",
    "cost": "cpu",
    "tags": ['IC检验', 'Barra', '因子有效性', '时间切片'],
    "description": "30因子面板时间切片Spearman IC检验（无未来函数）+IC衰减分析",
    "layer": "analysis",
    "priority": 5,
}
import time
import math
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from infra.cache import MemoryCache

_IC_CACHE_TTL = 86400  # 24 小时缓存（日频计算）
_ic_cache = MemoryCache(default_ttl=_IC_CACHE_TTL)

# ── 面板参数 ──
_PANEL_LOOKBACK_DAYS = 800     # 行情历史长度（自然日，约 550 个交易日）
_CROSS_SECTION_STEP = 20       # 相邻截面日间隔（交易日）
_MAX_CROSS_SECTIONS = 12       # 最多截面数（约覆盖 1 年）
_MIN_COVERAGE_RATIO = 0.6      # 截面日至少覆盖池内多少比例的个股
_MIN_CROSS_STOCKS = 30         # 单个截面少于该数量的个股则丢弃该截面
_MIN_HISTORY_BARS = 120        # 个股最少需要的K线数（60 动量 + 60 前瞻）
_MOMENTUM_MAX_LOOKBACK = 60    # 最长动量回看窗口（决定截面日的最早位置）
_MIN_IC_PERIODS = 3            # 少于 3 个截面的 IC 不参与统计


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


def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list) -> float:
    """样本标准差（n-1）"""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _get_stock_pool(limit: int = 300) -> list:
    """获取股票池（市值 TOP 300 的活跃股）

    ⚠️ 池子用「当前」市值构建，存在幸存者偏差 —— 见模块 docstring。
    """
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


def _clean_code(code: str) -> str:
    return (code or "").replace("sh", "").replace("sz", "").replace(
        "SH", "").replace("SZ", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 面板数据装载
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _load_price_panel(codes: list, days: int = _PANEL_LOOKBACK_DAYS) -> dict:
    """并发拉取个股日线，返回 {code: {"dates": [...], "closes": [...], "pos": {date: idx}}}

    只保留 K 线数量足够的个股（需要 60 根算动量 + 60 根做前瞻收益）。
    """
    panel = {}

    def _one(code):
        try:
            from services.backtest_engine import _get_stock_hist
            prices = _get_stock_hist(code, days=days)
        except Exception:
            prices = []
        rows = []
        for p in prices or []:
            d = p.get("date")
            c = p.get("close")
            if not d or c is None:
                continue
            try:
                c = float(c)
            except (TypeError, ValueError):
                continue
            if c > 0:
                rows.append((str(d)[:10], c))
        return code, (rows if len(rows) >= _MIN_HISTORY_BARS else None)

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = [pool.submit(_one, c) for c in codes]
        for f in as_completed(futures):
            try:
                code, rows = f.result()
                if rows:
                    panel[code] = {
                        "dates": [r[0] for r in rows],
                        "closes": [r[1] for r in rows],
                        "pos": {r[0]: i for i, r in enumerate(rows)},
                    }
            except Exception:
                pass
    return panel


def _build_trading_axis(panel: dict) -> list:
    """用池内个股日期并集构建交易日历轴，只保留覆盖率足够的日期"""
    cnt = Counter()
    for rec in panel.values():
        for d in rec["dates"]:
            cnt[d] += 1
    n = len(panel)
    if n == 0:
        return []
    need = max(_MIN_CROSS_STOCKS, int(n * _MIN_COVERAGE_RATIO))
    return sorted(d for d, c in cnt.items() if c >= need)


def _pick_cross_section_dates(axis: list, forward_days: int) -> list:
    """在交易日历轴上挑选截面日：保证 T 之前有足够动量回看、T 之后有足够前瞻窗口"""
    lo = _MOMENTUM_MAX_LOOKBACK
    hi = len(axis) - 1 - forward_days
    if hi < lo:
        return []
    idxs = list(range(hi, lo - 1, -_CROSS_SECTION_STEP))
    idxs = idxs[:_MAX_CROSS_SECTIONS]
    idxs.reverse()
    return [axis[i] for i in idxs]


def _load_valuation_snapshots(dates: list, codes: set) -> dict:
    """{date: {code: {pe, pb, total_mv, turnover}}} —— T 日的估值截面

    走 Tushare daily_basic 按 trade_date 拉全市场，天然是 T 日的横截面快照，
    不含任何 T 之后的信息。
    """
    snapshots = {}
    try:
        from services.tushare_data import is_configured, get_valuation_batch_map
        if not is_configured():
            print("[IC] Tushare 未配置，估值类因子（PE/PB/EP/市值/换手）无历史截面")
            return {}
    except Exception:
        return {}

    for d in dates:
        try:
            raw = get_valuation_batch_map(trade_date=d.replace("-", "")) or {}
        except Exception as e:
            print(f"[IC] valuation snapshot {d} failed: {e}")
            raw = {}
        if raw:
            snapshots[d] = {c: v for c, v in raw.items() if c in codes}
    return snapshots


def _load_financial_history(codes: list) -> dict:
    """{code: [(ann_date, {...财务字段...}), ...]} 按 ann_date 升序

    拉一个冗余的 3 年窗口，保证窗口起点之前最近一期的公告也在里面。
    """
    hist = {}
    start = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
    try:
        from services.tushare_data import is_configured, get_financials_history
        if not is_configured():
            print("[IC] Tushare 未配置，财务类因子（ROE/增速/ margins 等）无 point-in-time 序列")
            return {}
    except Exception:
        return {}

    def _one(code):
        try:
            return code, get_financials_history(code, start_date=start) or []
        except Exception:
            return code, []

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = [pool.submit(_one, c) for c in codes]
        for f in as_completed(futures):
            try:
                code, rows = f.result()
                if rows:
                    hist[code] = [(r["ann_date"], r) for r in rows]
            except Exception:
                pass
    return hist


def _yyyymmdd(value) -> str:
    """统一日期比较口径：Tushare 返回 20240105，行情轴是 2024-01-05

    两者直接字符串比较会出错：'-' (0x2D) < '0' (0x30)，导致
    "20240105" > "2024-01-05" 恒成立 —— 财务因子会全部被判成"还没公告"。
    """
    return str(value).replace("-", "").replace("/", "")[:8]


def _fin_asof(hist_rows: list, date_str: str) -> dict | None:
    """取 ann_date <= date_str 的最近一期财报（point-in-time，杜绝前视偏差）"""
    if not hist_rows:
        return None
    target = _yyyymmdd(date_str)
    best = None
    for ann_date, payload in hist_rows:
        if _yyyymmdd(ann_date) <= target:
            best = payload
        else:
            break  # 已按 ann_date 升序，后面的都太新
    return best


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 因子构造（只使用 T 日及之前可知的信息）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _panel_factors(closes: list, pos: int, valuation: dict | None, fin: dict | None) -> dict:
    """在截面日 T（closes 下标 pos）计算全部可回溯因子值

    因子 ID 与符号约定与 V1 保持一致（低 PE/PB 取负号使"越大越好"方向一致），
    因此下游的因子加权逻辑无需改动。
    """
    factors = {}

    # ── 估值 / 流动性：T 日 daily_basic 截面 ──
    if valuation:
        pe = valuation.get("pe")
        pb = valuation.get("pb")
        mv = valuation.get("total_mv")
        to = valuation.get("turnover")
        if pe and 0 < pe < 300:
            factors["F01_PE"] = -pe            # 负号：PE 越低越好
            factors["F03_EP"] = 1.0 / pe       # 盈利收益率
        if pb and 0 < pb < 50:
            factors["F02_PB"] = -pb
        if mv and mv > 0:
            factors["F17_MARKET_CAP"] = math.log(mv + 1)   # 对数化
            factors["F27_MCAP_LIQ"] = mv
        if to is not None:
            factors["F26_TURNOVER"] = to

    # ── 动量：T 日及之前的收盘价 ──
    c0 = closes[pos]
    for name, lag in (("F21_MOM_1D", 1), ("F18_MOM_5D", 5),
                      ("F19_MOM_20D", 20), ("F20_MOM_60D", 60)):
        if pos >= lag:
            prev = closes[pos - lag]
            if prev > 0:
                factors[name] = (c0 - prev) / prev * 100

    # ── 财务：ann_date <= T 的最近一期 ──
    if fin:
        roe = fin.get("roe")
        eps = fin.get("eps")
        pb_now = valuation.get("pb") if valuation else None
        if roe is not None and pb_now and pb_now > 0:
            factors["F04_ROE_PB"] = roe / pb_now
        if eps is not None:
            factors["F05_EPS"] = eps
        rg = fin.get("revenue_yoy")
        if rg is not None:
            factors["F07_REV_GROWTH"] = rg
        ny = fin.get("netprofit_yoy")
        if ny is not None:
            factors["F08_NP_GROWTH"] = ny
        if roe is not None:
            factors["F09_ROE"] = roe
        gm = fin.get("gross_margin")
        if gm is not None:
            factors["F13_GROSS_MARGIN"] = gm
        nm = fin.get("net_margin")
        if nm is not None:
            factors["F14_NET_MARGIN"] = nm
        dr = fin.get("debt_ratio")
        if dr is not None:
            factors["F15_DEBT_RATIO"] = -dr    # 负号：负债率越低越好
        cf = fin.get("cash_flow_per_share")
        if cf is not None:
            factors["F16_CASHFLOW"] = cf

    return factors


# 因子中文名映射（同时作为「全部因子」的权威清单 —— 未覆盖的因子会被标为不可用）
FACTOR_NAMES = {
    "F01_PE": "市盈率(PE)", "F02_PB": "市净率(PB)", "F03_EP": "盈利收益率(EP)",
    "F04_ROE_PB": "ROE/PB复合", "F05_EPS": "每股收益(EPS)",
    "F07_REV_GROWTH": "营收增速", "F08_NP_GROWTH": "净利增速", "F09_ROE": "净资产收益率(ROE)",
    "F13_GROSS_MARGIN": "毛利率", "F14_NET_MARGIN": "净利率",
    "F15_DEBT_RATIO": "资产负债率(反)", "F16_CASHFLOW": "每股现金流", "F17_MARKET_CAP": "市值(对数)",
    "F18_MOM_5D": "5日动量", "F19_MOM_20D": "20日动量", "F20_MOM_60D": "60日动量", "F21_MOM_1D": "日内动量",
    "F22_AMPLITUDE": "振幅(反)", "F26_TURNOVER": "换手率", "F27_MCAP_LIQ": "市值(流动性)",
}


def compute_factor_ic(
    forward_days: int = 20,
    pool_size: int = 200,
    force: bool = False,
) -> dict:
    """计算所有因子的 IC 值（面板时间切片版）

    对每个历史截面日 T：
        因子值 = f(T 日及之前可知的信息)
        收益率 = (close[T+forward_days] - close[T]) / close[T]   ← 真实前瞻收益
    再对所有截面的 Spearman IC 求均值 / 标准差 / IR / t 统计量。

    Args:
        forward_days: 前瞻收益周期（交易日）
        pool_size: 股票池大小
        force: True 则跳过缓存强制重新计算

    Returns:
        {
            "factors": {...},
            "ranking": [...],
            "summary": {...},
            "recommendations": [...],
            "method": "panel_timeslice",
            ...
        }
    """
    cache_key = f"ic_v2_{forward_days}_{pool_size}"
    if not force:
        cached = _ic_cache.get(cache_key)
        if cached is not None:
            return cached
    else:
        _ic_cache.delete(cache_key)
        print(f"[IC] Force refresh: cache cleared for {cache_key}")

    print(f"[IC] Starting panel IC test: forward={forward_days}d, pool={pool_size}")
    t0 = time.time()

    # Step 1: 获取股票池
    stocks = _get_stock_pool(pool_size)
    if len(stocks) < 30:
        return {"error": "股票池不足30只，无法计算IC", "pool_size": len(stocks)}

    print(f"[IC] Stock pool: {len(stocks)} stocks")

    # Step 2: 装载价格面板（每只股票一条长序列，后续所有截面共用）
    codes = [_clean_code(s["code"]) for s in stocks]
    panel = _load_price_panel(codes)
    print(f"[IC] Price panel: {len(panel)}/{len(codes)} stocks "
          f"(need >= {_MIN_HISTORY_BARS} bars)")
    if len(panel) < 30:
        return {"error": f"行情历史不足（仅{len(panel)}只满足K线长度要求），无法计算IC",
                "pool_size": len(stocks)}

    # Step 3: 交易日历轴 + 截面日
    axis = _build_trading_axis(panel)
    cross_dates = _pick_cross_section_dates(axis, forward_days)
    if not cross_dates:
        return {"error": f"可用交易日历轴不足（{len(axis)}个交易日），无法构造前瞻{forward_days}日的截面",
                "pool_size": len(stocks)}
    print(f"[IC] Trading axis: {len(axis)} days, cross-sections: {len(cross_dates)} "
          f"({cross_dates[0]} ~ {cross_dates[-1]})")

    # Step 4: 截面估值快照 + point-in-time 财务序列
    panel_codes = set(panel.keys())
    val_snaps = _load_valuation_snapshots(cross_dates, panel_codes)
    fin_hist = _load_financial_history(list(panel.keys()))
    print(f"[IC] Valuation snapshots: {len(val_snaps)}/{len(cross_dates)}, "
          f"financial history: {len(fin_hist)}/{len(panel)}")

    # Step 5: 逐截面计算 Spearman IC
    per_date_ic: list[tuple[str, dict, dict]] = []   # [(date, {fname: ic}, {fname: n}), ...]
    total_pairs: dict[str, int] = {}
    covered_stocks = set()

    for d in cross_dates:
        val_map = val_snaps.get(d, {})
        pairs: dict[str, list] = {}

        for code, rec in panel.items():
            pos = rec["pos"].get(d)
            if pos is None:
                continue
            end_pos = pos + forward_days
            if end_pos >= len(rec["closes"]):
                continue

            c0 = rec["closes"][pos]
            c1 = rec["closes"][end_pos]
            if c0 <= 0:
                continue
            fwd_ret = (c1 - c0) / c0 * 100

            covered_stocks.add(code)
            factors = _panel_factors(
                rec["closes"], pos,
                val_map.get(code),
                _fin_asof(fin_hist.get(code), d),
            )
            for fname, fval in factors.items():
                if fval is None or (isinstance(fval, float) and
                                    (math.isnan(fval) or math.isinf(fval))):
                    continue
                pairs.setdefault(fname, []).append((fval, fwd_ret))

        if not pairs:
            continue

        ic_row = {}
        count_row = {}
        for fname, ps in pairs.items():
            n = len(ps)
            total_pairs[fname] = total_pairs.get(fname, 0) + n
            count_row[fname] = n
            if n >= _MIN_CROSS_STOCKS:
                ic_row[fname] = _spearman_rank_corr([p[0] for p in ps], [p[1] for p in ps])
        if ic_row:
            per_date_ic.append((d, ic_row, count_row))

    if not per_date_ic:
        return {"error": f"所有截面日均样本不足（需>={_MIN_CROSS_STOCKS}只），无法计算IC",
                "pool_size": len(stocks)}

    print(f"[IC] Cross-sections used: {len(per_date_ic)}, stocks covered: {len(covered_stocks)}")

    # Step 6: 汇总 IC 序列
    results = {}
    for fname in FACTOR_NAMES:
        series = [row[fname] for _, row, _ in per_date_ic if fname in row]
        samples = total_pairs.get(fname, 0)

        if len(series) < _MIN_IC_PERIODS or samples < 20:
            reason = ("no_historical_panel" if not series else "data_insufficient")
            results[fname] = {
                "ic": None, "abs_ic": 0.0, "samples": samples,
                "level": "无历史面板" if reason == "no_historical_panel" else "样本不足",
                "effective": False,
                "direction": "",
                "invalid_reason": reason,
                "ic_mean": None, "ic_std": None, "icir": None,
                "ic_positive_rate": None, "t_stat": None,
                "n_periods": len(series), "ic_series": [],
                "significant": False,
                "reason": (
                    "该因子无 point-in-time 历史序列（当前数据源只提供最新快照），"
                    "时间切片模式下无法计算，强行用当期值会引入前视偏差，故留空"
                    if reason == "no_historical_panel" else
                    f"仅{len(series)}个有效截面（需>={_MIN_IC_PERIODS}），样本不足"
                ),
            }
            continue

        ic_mean = _mean(series)
        ic_std = _std(series)
        icir = (ic_mean / ic_std) if ic_std > 0 else 0.0
        pos_rate = sum(1 for v in series if v > 0) / len(series)
        n = len(series)
        t_stat = (ic_mean / (ic_std / math.sqrt(n))) if ic_std > 0 else 0.0
        abs_ic = abs(ic_mean)

        # IC 质量评级（沿用 Barra 阈值，作用在 IC 均值上）
        if abs_ic >= 0.05:
            level, effective, invalid_reason = "优秀", True, None
        elif abs_ic >= 0.03:
            level, effective, invalid_reason = "有效", True, None
        elif abs_ic >= 0.02:
            level, effective, invalid_reason = "微弱", False, "ic_low"
        else:
            level, effective, invalid_reason = "无效", False, "ic_low"

        # 是否统计显著：|t| >= 2。不显著说明 IC 均值很可能是噪声，
        # 不能拿来排序或加权（V1 的循环论证正是缺少这道闸门）。
        significant = abs(t_stat) >= 2.0
        if effective and not significant:
            effective = False
            invalid_reason = "not_significant"
            level = f"{level}(不显著)"

        results[fname] = {
            "ic": round(ic_mean, 4),
            "abs_ic": round(abs_ic, 4),
            "ic_mean": round(ic_mean, 4),
            "ic_std": round(ic_std, 4),
            "icir": round(icir, 3),
            "ic_positive_rate": round(pos_rate, 3),
            "t_stat": round(t_stat, 2),
            "n_periods": n,
            "ic_series": [round(v, 4) for v in series],
            "samples": samples,
            "level": level,
            "effective": effective,
            "significant": significant,
            "direction": "正向" if ic_mean > 0 else "负向",
            "invalid_reason": invalid_reason,
        }

    # Step 7: 排序 + 汇总
    sorted_factors = sorted(results.items(), key=lambda x: x[1].get("abs_ic", 0), reverse=True)
    for rank, (fname, info) in enumerate(sorted_factors, 1):
        info["rank"] = rank

    effective_count = sum(1 for _, v in results.items() if v.get("effective"))
    ineffective_ic = [fname for fname, v in results.items()
                      if not v.get("effective") and v.get("invalid_reason") in ("ic_low", "not_significant")]
    ineffective_data = [fname for fname, v in results.items()
                        if not v.get("effective") and v.get("invalid_reason") == "data_insufficient"]
    no_panel = [fname for fname, v in results.items()
                if v.get("invalid_reason") == "no_historical_panel"]

    # 生成建议（措辞必须反映"是否显著"，避免把噪声当 alpha）
    recommendations = []
    top3 = [(f, v) for f, v in sorted_factors if v.get("effective")][:3]
    if top3:
        names = [FACTOR_NAMES.get(f, f) for f, _ in top3]
        recommendations.append(
            f"统计显著的有效因子TOP{len(top3)}：{', '.join(names)}"
            f"（|IC|>=0.03 且 |t|>=2），可在选股中加大权重")
    else:
        recommendations.append(
            "⚠️ 本期没有因子同时通过 |IC|>=0.03 与 |t|>=2 两道检验，"
            "建议本期不要依据 IC 结果调整因子权重")

    if ineffective_ic:
        names = [FACTOR_NAMES.get(f, f) for f in ineffective_ic[:5]]
        recommendations.append(f"未通过检验的因子({len(ineffective_ic)}个)：{', '.join(names)}等，建议降低权重或移除")

    if no_panel:
        names = [FACTOR_NAMES.get(f, f) for f in no_panel[:5]]
        recommendations.append(f"无历史面板因子({len(no_panel)}个)：{', '.join(names)}等 —— "
                               "当前数据源只提供最新快照，无法做时间切片，故不给出 IC")

    if ineffective_data:
        names = [FACTOR_NAMES.get(f, f) for f in ineffective_data[:5]]
        recommendations.append(f"样本不足因子({len(ineffective_data)}个)：{', '.join(names)}等，非因子本身失效")

    scored = [v for v in results.values() if v.get("n_periods", 0) >= _MIN_IC_PERIODS]
    if scored:
        rate = effective_count / len(scored)
        recommendations.append(
            f"可检验因子 {len(scored)} 个，其中 {effective_count} 个统计显著"
            f"（{round(rate * 100, 1)}%）")

    elapsed = time.time() - t0
    print(f"[IC] Done in {elapsed:.1f}s: {effective_count} effective, "
          f"ic_low/not_sig={len(ineffective_ic)}, no_panel={len(no_panel)}, "
          f"data_insufficient={len(ineffective_data)}")

    result = {
        "factors": {fname: {**info, "name_cn": FACTOR_NAMES.get(fname, fname)}
                    for fname, info in results.items()},
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
            "samples_with_returns": len(covered_stocks),
            "forward_days": forward_days,
            "elapsed_seconds": round(elapsed, 1),
            "ineffective_ic_count": len(ineffective_ic),
            "ineffective_data_count": len(ineffective_data),
            # V2 新增
            "cross_sections": len(per_date_ic),
            "cross_section_dates": [d for d, _, _ in per_date_ic],
            "panel_stocks": len(panel),
            "scorable_factors": len(scored),
        },
        "recommendations": recommendations,
        "ineffective_factors": ineffective_ic,
        "insufficient_data_factors": ineffective_data,
        "no_panel_factors": no_panel,
        # V2 新增：方法与口径声明
        "method": "panel_timeslice",
        "method_cn": "面板时间切片（T日因子值 vs T→T+N 真实前瞻收益）",
        "forward_return_definition": (
            f"(close[T+{forward_days}] - close[T]) / close[T]，"
            "只用 T 日收盘前已可得的信息构造因子，无未来函数"),
        "warnings": [
            "IC 由多个历史截面的 Spearman IC 汇总而来，截面数量有限（默认≤12），"
            "单期数值波动大，请以 ICIR 与 t 统计量为准，不要只看 IC 均值。",
        ],
        "limitations": [
            "股票池按「当前」市值 TOP N 构建，存在幸存者偏差（当时的小票不在池内）。",
            "F22_AMPLITUDE 需要日内最高/最低价，而现有行情接口只提供收盘价序列，"
            "时间切片模式下无法回溯，标为不可用。",
            "财务因子按 ann_date <= T 取最近一期（point-in-time），"
            "若 Tushare fina_indicator 历史序列不可得，这些因子会退化为「无历史面板」。",
        ],
    }

    _ic_cache.set(cache_key, result)
    return result


def compute_ic_decay(pool_size: int = 150) -> dict:
    """IC 衰减曲线：在不同前瞻周期下的 IC 变化

    用于判断因子是短期有效还是长期有效。
    V2：每个周期的 IC 都是真实前瞻 IC（V1 是"过去N日已实现收益"的相关性，无预测含义）。
    """
    cache_key = f"ic_decay_v2_{pool_size}"
    cached = _ic_cache.get(cache_key)
    if cached is not None:
        return cached

    periods = [5, 10, 20, 60]
    decay = {}

    for days in periods:
        print(f"[IC_DECAY] Computing panel IC for forward={days}d...")
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
                "t_stat": info.get("t_stat"),
                "icir": info.get("icir"),
            }

    # 分析衰减模式
    for fname, info in decay.items():
        periods_data = info["periods"]
        ics = [periods_data.get(str(d), {}).get("ic", 0) for d in [5, 10, 20, 60]]
        abs_ics = [abs(ic) if ic is not None else 0 for ic in ics]

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
        "method": "panel_timeslice",
        "method_cn": "面板时间切片（无未来函数）",
        "note": "各周期 IC 均为 T 日因子值 vs T→T+N 真实前瞻收益的相关性，具备预测力含义",
    }

    _ic_cache.set(cache_key, result)
    return result
