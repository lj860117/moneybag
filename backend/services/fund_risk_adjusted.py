"""
钱袋子 — 基金性价比（风险调整收益）指标服务

统一计算 5 项风险调整收益指标 + β，供前端「性价比」展示与 LLM 评分富化使用。

口径（v9.9.x，与旧 fund_detail.py 内联 1 年/Rf=1.5% 口径不同）：
  - 近 3 年窗口（window_days = RISK_ADJUSTED_WINDOW_DAYS = 1095 自然日）
  - 日频，年化因子 252
  - 无风险利率 Rf = RISK_FREE_RATE_ANNUAL = 2%（从 config 读取，可配置）
  - 基准：股票型/混合型 → 沪深300（000300.SH）；其他类型不做计算

公式（设 r_i 为日收益，μ 为日均收益，σ 为日收益标准差，σ_d 为下行标准差
      b_i 为基准日收益，β 为对基准回归 beta，mdd 为最大回撤幅度，
      MAR 为最低可接受收益（日频，默认 0））：
  - Sharpe            = (μ·252 − Rf) / (σ·√252)
  - Sortino           = (μ·252 − MAR·252) / (σ_d·√252)，σ_d = √(mean(min(r_i − MAR, 0)²))
                        与 empyrical.sortino_ratio(returns, required_return=0) 一致（MAR=0 时）
  - Calmar            = (μ·252) / mdd
  - Information Ratio = mean(r_i − b_i) / std(r_i − b_i) · √252
  - Treynor           = (μ·252 − Rf) / β

设计约束：
  - 不修改 fund_history_returns.py，独立新服务。
  - 复用 services.tushare_data 的 get_fund_nav / get_index_daily，不新造数据拉取逻辑。
  - 数据缺失/不足时对应指标置 None，绝不抛异常（fail-open，保证接口可用）。
  - 纯计算函数与数据获取解耦，便于单测。
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from config import (
    DATA_DIR,
    RISK_FREE_RATE_ANNUAL,
    RISK_ADJUSTED_WINDOW_DAYS,
    ANNUALIZATION_FACTOR,
    RISK_ADJUSTED_BENCHMARK,
    RISK_ADJUSTED_MAR_ANNUAL,
    RISK_ADJUSTED_CACHE_TTL,
)

# 浮点判零阈值（避免 β≈0 / 方差≈0 时的除零）
_EPSILON = 1e-9

# 数据充足性阈值（与旧口径一致：>=60 个日收益点才视为"充足"）
_FULL_MIN_RETURNS = 60
# 最少日收益点数（<2 无法计算标准差/beta，直接判"数据不足"）
_MIN_RETURNS = 2

# 分类器 type → 中文标签兜底映射
_TYPE_LABEL_MAP = {
    "equity": "股票型",
    "mixed": "混合型",
    "bond": "债券型",
    "money": "货币型",
    "gold": "黄金型",
    "unknown": "未知",
}

# QDII/海外基金名称关键字（即使 classify_fund 误判为 mixed，也显式排除）
_QDII_NAME_KEYWORDS = (
    "QDII", "海外", "美元", "港币", "纳斯达克", "纳指", "标普", "日经", "环球", "全球",
)


# ══════════════════════════════════════════════════════════
# 共享性价比缓存 + 后台预热队列（T01/T03）
# ══════════════════════════════════════════════════════════
# 单一真值来源：选基列表注入只读这里，详情计算完成 + 预热 worker 写这里。
# 正缓存（available=True）与负缓存（available=False，债/QDII/货币）都落盘，
# 使列表注入能区分「没算过 → 入队补算」vs「算过但不可用 → 不入队不注入」。
_RA_CACHE_DIR = DATA_DIR / "_cache" / "fund_risk_adjusted"
try:
    _RA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# 内存缓存：{code: {"v": metrics, "t": epoch}}
_RA_CACHE: dict = {}
# 单把锁保护内存 + 文件读写（写不频繁、对象小，无需读写锁）
_RA_CACHE_LOCK = threading.Lock()

# 预热队列 + 守卫（防止并发 drain 打爆 Tushare）
_PENDING_WARMUP: set = set()
_WARMUP_LOCK = threading.Lock()
_WARMUP_RUNNING = False
# 预热并发上限 / 单只超时（秒）——超时只代表「不等」，底层线程自行结束
_WARMUP_MAX_WORKERS = 2
_WARMUP_SINGLE_TIMEOUT = 25


def _risk_adjusted_cache_path(code: str) -> Path:
    """返回 code 对应的共享缓存文件路径。"""
    return _RA_CACHE_DIR / f"{code}.json"


def get_risk_adjusted_cache(code: str) -> Optional[dict]:
    """读取共享性价比缓存（内存 → 文件 → 回填内存）。

    TTL 由 config.RISK_ADJUSTED_CACHE_TTL 控制（默认 24h）。
    命中返回 metrics 契约 dict；未命中/过期返回 None。
    负缓存（available=False）也会返回其 dict，调用方据此区分
    「没算过」vs「算过但不可用」。网络计算绝不放在锁内。
    """
    now = time.time()
    with _RA_CACHE_LOCK:
        entry = _RA_CACHE.get(code)
        if entry is not None and now - entry["t"] < RISK_ADJUSTED_CACHE_TTL:
            return entry["v"]

        # 内存未命中/过期 → 读文件并回填内存
        path = _risk_adjusted_cache_path(code)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                t = data.get("t", 0)
                if now - t < RISK_ADJUSTED_CACHE_TTL and "v" in data:
                    _RA_CACHE[code] = data
                    return data["v"]
            except Exception:
                pass
    return None


def set_risk_adjusted_cache(code: str, metrics: dict) -> None:
    """写入共享性价比缓存（内存 + 文件原子替换）。

    幂等 last-write-wins：同一 code 两次写内容等价（同口径同数据）。
    先写 `{code}.json.tmp` 再 os.replace 原子替换，避免并发读到半截文件。
    负缓存（available=False）同样落盘。
    """
    rec = {"v": metrics, "t": time.time()}
    with _RA_CACHE_LOCK:
        _RA_CACHE[code] = rec
        path = _risk_adjusted_cache_path(code)
        tmp_path = path.with_name(f"{code}.json.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, path)
        except Exception:
            # 文件写失败不影响内存命中；清理可能残留的 .tmp
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass


def enqueue_risk_adjusted_warmup(codes) -> None:
    """将未命中缓存的 code 加入预热队列，并按需启动后台 worker。

    去重由 set 保证；「过滤已缓存」在入队处粗过滤（含负缓存：算过即跳过），
    worker 弹出时再做一次精确过滤（避免入队后又被详情回填/预热补算）。
    """
    global _WARMUP_RUNNING
    if not codes:
        return
    code_list = [c for c in codes if c]
    if not code_list:
        return

    # 粗过滤：只在当前无有效缓存时才入队（负缓存也算已缓存，跳过）
    to_add = [c for c in code_list if get_risk_adjusted_cache(c) is None]
    if not to_add:
        return

    with _WARMUP_LOCK:
        _PENDING_WARMUP.update(to_add)
        if _WARMUP_RUNNING:
            return
        _WARMUP_RUNNING = True

    threading.Thread(target=_warm_risk_adjusted_worker, daemon=True).start()


def _warm_risk_adjusted_worker() -> None:
    """后台预热 worker：循环取队列直到空，算完落正/负缓存。

    串行取批（每批最多 _WARMUP_MAX_WORKERS 只）+ 限并发计算。
    _WARMUP_RUNNING 只在「队列已确认为空」时于锁内复位一次，避免与
    并发入队（spawn 新 worker）的守卫状态互相覆盖。
    """
    global _WARMUP_RUNNING
    while True:
        with _WARMUP_LOCK:
            batch: List[str] = []
            while _PENDING_WARMUP and len(batch) < _WARMUP_MAX_WORKERS:
                batch.append(_PENDING_WARMUP.pop())
            if not batch:
                _WARMUP_RUNNING = False
                return

        # 锁外精确过滤已缓存（避免阻塞入队，且不把网络计算放锁内）
        batch = [c for c in batch if get_risk_adjusted_cache(c) is None]
        if not batch:
            continue
        try:
            _compute_batch(batch)
        except Exception:
            # _compute_batch 内部已吞单只异常；这里兜底防整批级异常中断循环，
            # 保证 worker 能继续 drain 剩余队列、最终复位守卫。
            pass


def _compute_batch(codes: List[str]) -> None:
    """限并发补算一批基金性价比指标并落缓存（含负缓存）。

    ThreadPoolExecutor(max_workers=_WARMUP_MAX_WORKERS)；单只
    future.result(timeout=_WARMUP_SINGLE_TIMEOUT)。超时只代表「不等」，
    底层线程自行结束后落缓存（shutdown(wait=False) 不阻塞 worker）。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _one(code: str) -> None:
        try:
            metrics = compute_risk_adjusted_metrics(code)
            set_risk_adjusted_cache(code, metrics)
        except Exception:
            # 单只失败不拖垮整批；失败不落缓存，下次请求会重试
            pass

    ex = ThreadPoolExecutor(max_workers=_WARMUP_MAX_WORKERS)
    try:
        futures = [ex.submit(_one, c) for c in codes]
        for fut in futures:
            try:
                fut.result(timeout=_WARMUP_SINGLE_TIMEOUT)
            except Exception:
                pass
    finally:
        ex.shutdown(wait=False)


def _to_float(value) -> Optional[float]:
    """安全转 float，非法/NaN/Inf 返回 None。"""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (ValueError, TypeError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _fmt(value: Optional[float], digits: int = 2) -> Optional[float]:
    """对外展示精度：四舍五入到指定小数位；None 透传。"""
    if value is None:
        return None
    return round(value, digits)


def compute_daily_returns(navs: Sequence[Optional[float]]) -> List[float]:
    """由相邻净值计算日收益率序列（跳过无效/非正净值，不中断）。"""
    returns: List[float] = []
    for i in range(1, len(navs)):
        prev, cur = navs[i - 1], navs[i]
        if prev is None or cur is None or prev <= 0:
            continue
        returns.append(cur / prev - 1.0)
    return returns


def _mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _population_std(values: Sequence[float]) -> Optional[float]:
    """总体标准差（与 Sharpe 口径一致：除以 n）。"""
    if len(values) < _MIN_RETURNS:
        return None
    mean = _mean(values)
    if mean is None:
        return None
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def compute_sharpe(
    daily_returns: Sequence[float],
    rf_annual: float = RISK_FREE_RATE_ANNUAL,
    annualization_factor: int = ANNUALIZATION_FACTOR,
) -> Optional[float]:
    """夏普比率 = (μ·252 − Rf) / (σ·√252)。"""
    if len(daily_returns) < _MIN_RETURNS:
        return None
    mu = _mean(daily_returns)
    sigma = _population_std(daily_returns)
    if mu is None or sigma is None or sigma <= _EPSILON:
        return None
    return (mu * annualization_factor - rf_annual) / (sigma * math.sqrt(annualization_factor))


def compute_downside_std(daily_returns: Sequence[float], mar_daily: float = 0.0) -> Optional[float]:
    """下行偏差 = √(mean(min(r_i − MAR, 0)²))，日频（未年化）。

    与 empyrical.downside_risk 的日频部分一致（不含 √252 年化缩放）。
    无下行波动（所有 r_i ≥ MAR）时返回 0.0（而非 None）；空序列返回 None。
    """
    if not daily_returns:
        return None
    squared = [min(r - mar_daily, 0.0) ** 2 for r in daily_returns]
    return math.sqrt(sum(squared) / len(squared))


def compute_sortino(
    daily_returns: Sequence[float],
    mar_daily: float = 0.0,
    annualization_factor: int = ANNUALIZATION_FACTOR,
) -> Optional[float]:
    """索提诺比率 = (年化收益率 − 年化 MAR) / 年化下行标准差
                  = (μ·252 − MAR·252) / (σ_d·√252)

    与 empyrical.sortino_ratio(returns, required_return=0) 一致（MAR=0 时）。
    无下行波动（σ_d=0，所有 r_i ≥ MAR）时无定义，返回 None（不返回 0/∞）。
    """
    if len(daily_returns) < _MIN_RETURNS:
        return None
    mu = _mean(daily_returns)
    sigma_d = compute_downside_std(daily_returns, mar_daily)
    if mu is None or sigma_d is None or sigma_d <= _EPSILON:
        return None
    return (mu - mar_daily) * annualization_factor / (sigma_d * math.sqrt(annualization_factor))


def compute_max_drawdown(navs: Sequence[Optional[float]]) -> Optional[float]:
    """最大回撤幅度（正数，比例形式：0.35 表示 35%）。无有效净值返回 None。"""
    peak: Optional[float] = None
    mdd = 0.0
    for n in navs:
        if n is None or n <= 0:
            continue
        if peak is None or n > peak:
            peak = n
        if peak is not None and peak > 0:
            dd = (peak - n) / peak
            if dd > mdd:
                mdd = dd
    if peak is None:
        return None
    return mdd


def compute_calmar(
    daily_returns: Sequence[float],
    navs: Sequence[Optional[float]],
    annualization_factor: int = ANNUALIZATION_FACTOR,
) -> Optional[float]:
    """卡玛比率 = (μ·252) / mdd（mdd 为最大回撤幅度；为 0 或缺失时返回 None）。"""
    if len(daily_returns) < _MIN_RETURNS:
        return None
    mu = _mean(daily_returns)
    mdd = compute_max_drawdown(navs)
    if mu is None or mdd is None or mdd <= _EPSILON:
        return None
    return (mu * annualization_factor) / mdd


def compute_beta(fund_returns: Sequence[float], bench_returns: Sequence[float]) -> Optional[float]:
    """β = Cov(fund, bench) / Var(bench)（总体协方差/方差）。"""
    n = min(len(fund_returns), len(bench_returns))
    if n < _MIN_RETURNS:
        return None
    fr = fund_returns[:n]
    br = bench_returns[:n]
    fmean = _mean(fr)
    bmean = _mean(br)
    if fmean is None or bmean is None:
        return None
    cov = sum((fr[i] - fmean) * (br[i] - bmean) for i in range(n)) / n
    var = sum((br[i] - bmean) ** 2 for i in range(n)) / n
    if var <= _EPSILON:
        return None
    return cov / var


def compute_information_ratio(
    fund_returns: Sequence[float],
    bench_returns: Sequence[float],
    annualization_factor: int = ANNUALIZATION_FACTOR,
) -> Optional[float]:
    """信息比率 = mean(r_i − b_i) / std(r_i − b_i) · √252。"""
    n = min(len(fund_returns), len(bench_returns))
    if n < _MIN_RETURNS:
        return None
    diff = [fund_returns[i] - bench_returns[i] for i in range(n)]
    mean_diff = _mean(diff)
    std_diff = _population_std(diff)
    if mean_diff is None or std_diff is None or std_diff <= _EPSILON:
        return None
    return (mean_diff / std_diff) * math.sqrt(annualization_factor)


def compute_treynor(
    daily_returns: Sequence[float],
    beta: Optional[float],
    rf_annual: float = RISK_FREE_RATE_ANNUAL,
    annualization_factor: int = ANNUALIZATION_FACTOR,
) -> Optional[float]:
    """特雷诺比率 = (μ·252 − Rf) / β；β≈0 时返回 None。"""
    if beta is None or abs(beta) <= _EPSILON:
        return None
    if len(daily_returns) < _MIN_RETURNS:
        return None
    mu = _mean(daily_returns)
    if mu is None:
        return None
    return (mu * annualization_factor - rf_annual) / beta


def _align_return_pairs(
    fund_points: Sequence[dict],
    bench_points: Sequence[dict],
) -> Tuple[List[float], List[float]]:
    """将基金与基准的日收益对齐到共同交易日。

    优先按日期对齐（get_fund_nav 的 nav_date / get_index_daily 的 trade_date）；
    无日期时退化为按位置对齐（截断到较短一方）。
    返回 (fund_returns, bench_returns)，两者等长。
    """
    if not fund_points or not bench_points:
        return [], []

    has_dates = bool(
        (fund_points[0].get("date") if fund_points else "")
        and (bench_points[0].get("date") if bench_points else "")
    )

    if not has_dates:
        fr = compute_daily_returns([p.get("nav") for p in fund_points])
        br = compute_daily_returns([p.get("close") for p in bench_points])
        n = min(len(fr), len(br))
        return fr[:n], br[:n]

    bench_by_date = {p.get("date"): p.get("close") for p in bench_points if p.get("date")}
    common_fund_navs: List[Optional[float]] = []
    common_bench_navs: List[Optional[float]] = []
    for p in fund_points:
        d = p.get("date")
        if d and d in bench_by_date:
            common_fund_navs.append(p.get("nav"))
            common_bench_navs.append(bench_by_date[d])

    return compute_daily_returns(common_fund_navs), compute_daily_returns(common_bench_navs)


def _resolve_fund_type(code: str, name: str, fund_type: str) -> Tuple[str, bool]:
    """确定基金类型中文标签 + 是否支持性价比计算（仅股票型/混合型）。

    Returns:
        (label, eligible)。label 为中文类型标签；eligible=True 表示可计算。
    """
    ft = (fund_type or "").strip()
    nm = name or ""
    nm_upper = nm.upper()

    # QDII/海外基金显式排除（即使 classify_fund 会误判为 mixed）
    if "QDII" in ft.upper() or "QDII" in nm_upper:
        return (ft or "QDII"), False
    if any(k in nm for k in _QDII_NAME_KEYWORDS):
        return (ft or "QDII"), False

    # 债券基金显式排除（名称含"债"，即使 ft 缺失/classify_fund 误判）
    if "债" in ft or "债" in nm:
        return (ft or "债券型"), False

    # 优先用传入的中文/英文基金类型
    if ft:
        # 债券型（含「债券型-混合二级」等二级债基/可转债）显式排除：
        # 必须先于"混合"匹配，否则二级债基会被误判为混合型、错误地拿沪深300当基准。
        if "债" in ft or "bond" in ft.lower():
            return ft, False
        if "混合" in ft or "mixed" in ft.lower():
            return "混合型", True
        if "股票" in ft or "equity" in ft.lower() or "stock" in ft.lower():
            return "股票型", True
        # 货币/FOF/QDII/其他 → 不计算
        return ft, False

    # fallback：用 classify_fund 按代码+名称分类
    try:
        from services.fund_classifier import classify_fund
        cls_type = classify_fund(code=code, name=nm).get("type", "unknown")
    except Exception:
        cls_type = "unknown"

    if cls_type == "equity":
        return "股票型", True
    if cls_type == "mixed":
        return "混合型", True
    return _TYPE_LABEL_MAP.get(cls_type, cls_type), False


def compute_risk_adjusted_metrics(code: str, name: str = "", fund_type: str = "") -> dict:
    """计算基金 5 项风险调整收益指标 + β。

    Args:
        code: 基金代码（如 000001）
        name: 基金名称（可选，用于类型识别兜底）
        fund_type: 基金类型（可选，优先使用；如"股票型"/"混合型-偏股"/"QDII"）

    Returns:
        契约 dict（见模块 docstring / 团队约定），字段缺失时置 None、degraded=true，
        绝不抛异常。
    """
    rf_annual = RISK_FREE_RATE_ANNUAL
    annualization_factor = ANNUALIZATION_FACTOR
    window_days = RISK_ADJUSTED_WINDOW_DAYS
    benchmark = RISK_ADJUSTED_BENCHMARK
    mar_annual = RISK_ADJUSTED_MAR_ANNUAL
    mar_daily = mar_annual / annualization_factor if annualization_factor else 0.0

    result = {
        "code": code,
        "available": False,
        "degraded": False,
        "fund_type": "",
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "information_ratio": None,
        "treynor_ratio": None,
        "beta": None,
        "window_days": window_days,
        "nav_points": 0,
        "benchmark": benchmark,
        "rf_annual": rf_annual,
        "mar_annual": mar_annual,
        "annualization_factor": annualization_factor,
        "sortino_reason": None,
        "data_quality": "insufficient",
        "source": "tushare",
    }

    # 1. 类型识别：仅股票型/混合型可计算
    label, eligible = _resolve_fund_type(code, name, fund_type)
    result["fund_type"] = label
    if not eligible:
        result["reason"] = "仅股票型/混合型基金支持性价比计算"
        return result

    result["available"] = True

    # 2. 拉取基金净值（近 3 年）
    try:
        from services.tushare_data import get_fund_nav, get_index_daily, is_configured
    except Exception as e:  # pragma: no cover - import 失败兜底
        result["degraded"] = True
        result["data_quality"] = "insufficient"
        result["reason"] = f"数据源不可用: {e}"
        return result

    if not is_configured():
        result["degraded"] = True
        result["data_quality"] = "insufficient"
        result["reason"] = "tushare 未配置"
        return result

    try:
        nav_data = get_fund_nav(code, days=window_days) or {}
    except Exception as e:
        result["degraded"] = True
        result["data_quality"] = "insufficient"
        result["reason"] = f"基金净值拉取失败: {e}"
        return result

    navs_raw = nav_data.get("navs") or []

    # 3. 提取净值序列（优先复权净值 adj_nav，回退单位净值 unit_nav）
    fund_points: List[dict] = []
    for n in navs_raw:
        adj = _to_float(n.get("adj_nav"))
        unit = _to_float(n.get("unit_nav"))
        nav = adj if (adj is not None and adj > 0) else unit
        if nav is not None and nav > 0:
            fund_points.append({"date": n.get("nav_date", ""), "nav": nav})

    result["nav_points"] = len(fund_points)
    fund_navs: List[Optional[float]] = [p["nav"] for p in fund_points]
    fund_returns = compute_daily_returns(fund_navs)

    if len(fund_returns) < _MIN_RETURNS:
        result["degraded"] = True
        result["data_quality"] = "insufficient"
        result["reason"] = "净值点不足，无法计算日收益"
        return result

    # 4. 拉取基准（沪深300）并对齐
    bench_returns: List[float] = []
    try:
        bench_rows = get_index_daily(benchmark, days=window_days) or []
        bench_points: List[dict] = []
        for r in bench_rows:
            close = _to_float(r.get("close"))
            if close is not None and close > 0:
                bench_points.append({"date": r.get("trade_date", ""), "close": close})
        aligned_fund_returns, bench_returns = _align_return_pairs(fund_points, bench_points)
    except Exception:
        aligned_fund_returns = []
        bench_returns = []

    # 5. 计算 5 项指标 + β
    sharpe = compute_sharpe(fund_returns, rf_annual, annualization_factor)
    sortino = compute_sortino(fund_returns, mar_daily, annualization_factor)
    calmar = compute_calmar(fund_returns, fund_navs, annualization_factor)
    # β / 信息比率必须用「按共同交易日对齐后的」基金收益，否则基金与基准
    # 日期错位会算错（特雷诺依赖 β，随之修复）。
    beta = (
        compute_beta(aligned_fund_returns, bench_returns)
        if len(bench_returns) >= _MIN_RETURNS and len(aligned_fund_returns) >= _MIN_RETURNS
        else None
    )
    information_ratio = (
        compute_information_ratio(aligned_fund_returns, bench_returns, annualization_factor)
        if len(bench_returns) >= _MIN_RETURNS and len(aligned_fund_returns) >= _MIN_RETURNS
        else None
    )
    treynor = compute_treynor(fund_returns, beta, rf_annual, annualization_factor)

    result["sharpe_ratio"] = _fmt(sharpe)
    result["sortino_ratio"] = _fmt(sortino)
    result["calmar_ratio"] = _fmt(calmar)
    result["information_ratio"] = _fmt(information_ratio)
    result["treynor_ratio"] = _fmt(treynor)
    result["beta"] = _fmt(beta)

    # 6. Sortino 缺失原因判定（无下行波动 vs 数据不足），供前端「暂无数据」展示
    if sortino is None:
        if len(fund_returns) < _MIN_RETURNS:
            result["sortino_reason"] = "insufficient"
        else:
            downs = compute_downside_std(fund_returns, mar_daily)
            if downs is not None and downs <= _EPSILON:
                result["sortino_reason"] = "no_downside"

    # 7. 数据质量判定
    computed = [sharpe, sortino, calmar, information_ratio, treynor]
    n_computed = sum(1 for x in computed if x is not None)
    if result["sortino_reason"] == "no_downside" and len(fund_returns) >= _FULL_MIN_RETURNS:
        # 数据充足，但 Sortino 因「近3年无下行波动」无定义 → 特殊标记，非数据降级
        result["data_quality"] = "no_downside"
        result["degraded"] = False
    elif len(fund_returns) >= _FULL_MIN_RETURNS and n_computed == len(computed):
        result["data_quality"] = "full"
        result["degraded"] = False
    elif n_computed > 0:
        result["data_quality"] = "partial"
        result["degraded"] = True
    else:
        result["data_quality"] = "insufficient"
        result["degraded"] = True

    return result
