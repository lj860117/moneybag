"""
QA 独立回归测试 — 基金性价比指标（严过关）

针对工程师测试未覆盖的场景做「独立对拍」验证，重点：
  1. 基金/基准交易日历不一致时，β 与信息比率必须按【共同交易日】对齐后计算，
     而不是用"基金原始日收益[:n]"去对"按日期对齐后的基准收益[:n]"。
  2. 指标返回精度（round 到 2 位）与契约字段。

背景：工程师的 test_full_contract_populated 用相同日期序列构造 fund/bench，
      掩盖了"对齐后的基金收益被丢弃"这一缺陷——当指数缺某天（或基金多某天）
      时，β/IR 会因错位配对而算错（β 甚至可能符号反转）。
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.fund_risk_adjusted import (
    compute_beta,
    compute_information_ratio,
    compute_risk_adjusted_metrics,
)


def _fund_navs():
    """基金净值：5 个交易日，含 0103（指数缺失这一天）。"""
    return [
        {"nav_date": "20240101", "adj_nav": 1.00, "unit_nav": 1.00},
        {"nav_date": "20240102", "adj_nav": 1.02, "unit_nav": 1.02},
        {"nav_date": "20240103", "adj_nav": 1.03, "unit_nav": 1.03},
        {"nav_date": "20240104", "adj_nav": 1.05, "unit_nav": 1.05},
        {"nav_date": "20240105", "adj_nav": 1.06, "unit_nav": 1.06},
    ]


def _bench_closes():
    """指数收盘：缺 0103（模拟数据源缺口 / 交易日历不一致）。"""
    return [
        {"trade_date": "20240101", "close": 100.0},
        {"trade_date": "20240102", "close": 101.0},
        {"trade_date": "20240104", "close": 103.0},
        {"trade_date": "20240105", "close": 104.0},
    ]


def test_beta_and_ir_aligned_to_common_trading_days(monkeypatch):
    """β/信息比率必须基于共同交易日对齐后的收益序列。

    若实现用"基金原始日收益"去对"按日期对齐后的基准收益"，则 0103 的基金
    单日收益会错配到 0104 的基准两日收益上，β 与 IR 均算错。
    """
    monkeypatch.setattr("services.tushare_data.is_configured", lambda: True)
    monkeypatch.setattr(
        "services.tushare_data.get_fund_nav",
        lambda code, days=60: {"available": True, "navs": _fund_navs()},
    )
    monkeypatch.setattr(
        "services.tushare_data.get_index_daily",
        lambda ts_code="000300.SH", days=120: _bench_closes(),
    )

    result = compute_risk_adjusted_metrics("110020", name="易方达沪深300ETF联接A", fund_type="股票型")

    # 正确口径：只保留共同交易日 {0101,0102,0104,0105} 后计算的收益对
    aligned_fund = [1.02 / 1.00 - 1, 1.05 / 1.02 - 1, 1.06 / 1.05 - 1]
    aligned_bench = [101.0 / 100.0 - 1, 103.0 / 101.0 - 1, 104.0 / 103.0 - 1]

    expected_beta = compute_beta(aligned_fund, aligned_bench)
    expected_ir = compute_information_ratio(aligned_fund, aligned_bench)

    assert result["beta"] == pytest.approx(round(expected_beta, 2), abs=0.005), (
        f"β 对齐错误：期望 {round(expected_beta, 2)}，实际 {result['beta']}"
    )
    assert result["information_ratio"] == pytest.approx(round(expected_ir, 2), abs=0.005), (
        f"IR 对齐错误：期望 {round(expected_ir, 2)}，实际 {result['information_ratio']}"
    )


def _fund_navs_with_drawdown():
    """基金净值（含回撤 + 负收益，保证 Sharpe/Sortino/Calmar 均可计算）。"""
    return [
        {"nav_date": "20240101", "adj_nav": 1.00, "unit_nav": 1.00},
        {"nav_date": "20240102", "adj_nav": 1.05, "unit_nav": 1.05},
        {"nav_date": "20240103", "adj_nav": 0.99, "unit_nav": 0.99},
        {"nav_date": "20240104", "adj_nav": 1.04, "unit_nav": 1.04},
        {"nav_date": "20240105", "adj_nav": 1.02, "unit_nav": 1.02},
    ]


def test_sharpe_sortino_calmar_unaffected_by_benchmark_gap(monkeypatch):
    """Sharpe/Sortino/Calmar 不依赖基准，基准缺日不影响它们；仅 β/IR/Treynor 受影响。"""
    monkeypatch.setattr("services.tushare_data.is_configured", lambda: True)
    monkeypatch.setattr(
        "services.tushare_data.get_fund_nav",
        lambda code, days=60: {"available": True, "navs": _fund_navs_with_drawdown()},
    )
    monkeypatch.setattr(
        "services.tushare_data.get_index_daily",
        lambda ts_code="000300.SH", days=120: _bench_closes(),
    )

    result = compute_risk_adjusted_metrics("110020", name="易方达沪深300ETF联接A", fund_type="股票型")

    # 基金原始收益序列含波动与回撤，这三项应可计算（不被基准缺日拖累）
    assert result["sharpe_ratio"] is not None
    assert result["sortino_ratio"] is not None
    assert result["calmar_ratio"] is not None
