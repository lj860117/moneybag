"""
P0-3 回归测试：因子 IC 不得含未来函数 / 循环论证

背景（V1 缺陷）：
`_get_future_returns()` 名为"未来收益"，实际返回 prices[-1] vs prices[-(N+1)]
——即**过去 N 日已实现收益**。于是 IC = corr(今日因子值, 过去20日涨幅)。
对动量因子（本身就是涨幅）来说等式两边是同一个量，IC 必然 ≈ ±1，
纯循环论证，据此做的因子排序/加权全部无效。

本测试用**合成价格面板**锁死正确行为：
  1. 构造 b_{i+1} = -k * b_i 的区块收益结构（前 20 日涨得多 → 后 20 日跌得多）
  2. 截面日恰好落在区块边界，因此 mom20(T) ∝ b_i、前瞻收益 ∝ b_{i+1}
  3. 正确实现会得到 **强负 IC**；若退回 V1 的"已实现收益"口径，
     mom20 与收益同源，会得到 **强正 IC**（≈ +1）——测试即失败
"""
import math
import random
from datetime import date, timedelta

import pytest

from services import factor_ic


BLOCK = 20
N_BLOCKS = 15
N_BARS = BLOCK * N_BLOCKS


def _make_dates(n: int) -> list:
    """生成 n 个连续的工作日日期（跳过周末）"""
    out = []
    d = date(2024, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _make_closes(rng: random.Random, k: float = 0.8, jitter: float = 0.25) -> list:
    """区块收益满足 b_{i+1} = -k*b_i + noise，块内均摊到每日

    这样在区块边界 T 上：20日动量 ∝ b_i，前瞻20日收益 ∝ b_{i+1} = -k*b_i，
    二者必然负相关。
    """
    blocks = []
    prev = rng.gauss(0.0, 1.0)
    for _ in range(N_BLOCKS):
        blocks.append(prev)
        prev = -k * prev + rng.gauss(0.0, jitter)
    rets = []
    for b in blocks:
        rets.extend([b / BLOCK] * BLOCK)
    closes = []
    p = 100.0
    for r in rets:
        p *= math.exp(r / 100.0)
        closes.append(p)
    return closes


@pytest.fixture
def synthetic_market(monkeypatch):
    """把行情/股票池/数据源替换成合成数据，Tushare 关闭（只留动量因子）"""
    rng = random.Random(20260912)
    n_stocks = 40
    codes = [f"{600000 + i}" for i in range(n_stocks)]
    dates = _make_dates(N_BARS)
    hist = {c: _make_closes(rng) for c in codes}

    pool = [
        {
            "code": c,
            "name": f"合成{i}",
            "price": hist[c][-1],
            "market_cap": 100.0 + i,
        }
        for i, c in enumerate(codes)
    ]

    import services.stock_data_provider as sdp
    import services.backtest_engine as be
    import services.tushare_data as tsd

    monkeypatch.setattr(sdp, "get_stock_data", lambda: {"stocks": pool})
    monkeypatch.setattr(
        be, "_get_stock_hist", lambda code, period="daily", days=750: [
            {"date": d, "close": c}
            for d, c in zip(dates, hist[code][-days:])
        ]
    )
    # Tushare 关闭 → 估值/财务因子应退化为「无历史面板」，而不是编造数字
    monkeypatch.setattr(tsd, "is_configured", lambda: False)

    factor_ic._ic_cache.delete(f"ic_v2_20_{n_stocks}")
    return {"codes": codes, "hist": hist, "pool_size": n_stocks}


def test_momentum_ic_is_forward_looking_not_self_correlation(synthetic_market):
    """核心回归：动量因子的 IC 必须反映「对前瞻收益的预测」，且方向为负

    V1（未来函数/循环论证）下这里会得到 ≈ +1.0，测试即红。
    """
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True
    )
    assert "error" not in result, result.get("error")

    mom20 = result["factors"]["F19_MOM_20D"]
    assert mom20["n_periods"] >= 3, mom20
    assert mom20["ic"] is not None
    # 构造的是"动量反转"市场，前瞻 IC 必须为强负
    assert mom20["ic"] < -0.3, (
        f"MOM_20D 前瞻 IC 应为强负，实际 {mom20['ic']}；"
        "若为强正说明又退回了「因子 vs 已实现收益」的循环论证"
    )
    # 方向一致性也应很低（几乎每个截面都是负 IC）
    assert mom20["ic_positive_rate"] <= 0.2, mom20


def test_ic_output_has_stability_stats(synthetic_market):
    """必须输出 ICIR / IC>0 占比 / t 统计量，供判断 IC 是否只是噪声"""
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True
    )
    info = result["factors"]["F19_MOM_20D"]
    for key in ("ic_mean", "ic_std", "icir", "ic_positive_rate", "t_stat",
                "n_periods", "ic_series"):
        assert key in info, f"缺少统计字段 {key}"
    assert isinstance(info["ic_series"], list) and len(info["ic_series"]) >= 3
    assert info["significant"] is True  # 强负 IC + 多截面 → 必然显著


def test_factors_without_historical_panel_are_honest(synthetic_market):
    """没有 point-in-time 历史的因子必须留空 + 标注，不得编造 IC"""
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True
    )
    # Tushare 关闭 → 估值/财务类因子无历史面板
    for fname in ("F01_PE", "F02_PB", "F09_ROE", "F13_GROSS_MARGIN"):
        info = result["factors"][fname]
        assert info["ic"] is None, f"{fname} 不应编造 IC：{info}"
        assert info["invalid_reason"] == "no_historical_panel", info
        assert info["effective"] is False
    assert set(result["no_panel_factors"]) >= {"F01_PE", "F09_ROE"}
    assert result["method"] == "panel_timeslice"


def test_no_trailing_return_helper_exists():
    """V1 的 `_get_future_returns`（实为已实现收益）必须已删除，防止回潮"""
    assert not hasattr(factor_ic, "_get_future_returns"), (
        "factor_ic._get_future_returns 已复活；它返回的是过去 N 日已实现收益，"
        "会造成 IC 循环论证"
    )


def test_cross_sections_are_spaced_and_forward_complete(synthetic_market):
    """截面日必须落在「前面有 60 根算动量、后面还有 forward_days 根做收益」的区间"""
    result = factor_ic.compute_factor_ic(
        forward_days=20, pool_size=synthetic_market["pool_size"], force=True
    )
    dates = result["summary"]["cross_section_dates"]
    assert len(dates) >= 3
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates), "截面日不应重复"

    # 前瞻窗口不能越界：最后一个截面日之后至少还要剩 20 个交易日
    all_dates = _make_dates(N_BARS)
    assert all_dates.index(dates[-1]) <= N_BARS - 1 - 20
    # 动量回看窗口也要够：第一个截面日之前至少要有 60 个交易日
    assert all_dates.index(dates[0]) >= 60
