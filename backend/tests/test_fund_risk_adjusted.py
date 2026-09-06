"""
基金性价比（风险调整收益）指标单元测试

覆盖 5 项指标 + β + 最大回撤的纯函数，以及 compute_risk_adjusted_metrics
的类型门禁 / 数据不足降级 / 完整契约字段。

纯函数用构造的小样本序列手算期望值验证公式正确性：
  - 日收益：由相邻净值推导，直接手算
  - 最大回撤、β、Calmar、部分 Sharpe/Treynor 使用可精确手算的构造值
  - Sharpe/Sortino/IR 等含 √252 的无理数结果，用 Python statistics 作为
    独立 oracle 复算期望值（公式在手，不调用被测函数自身的内部实现）
"""
import math
import statistics
import sys
from pathlib import Path

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.fund_risk_adjusted import (
    ANNUALIZATION_FACTOR,
    RISK_ADJUSTED_BENCHMARK,
    RISK_ADJUSTED_WINDOW_DAYS,
    RISK_FREE_RATE_ANNUAL,
    compute_beta,
    compute_calmar,
    compute_daily_returns,
    compute_downside_std,
    compute_information_ratio,
    compute_max_drawdown,
    compute_risk_adjusted_metrics,
    compute_sharpe,
    compute_sortino,
    compute_treynor,
)


# ──────────────────────────────────────────────────────────
# 纯函数：精确手算
# ──────────────────────────────────────────────────────────

def test_compute_daily_returns_hand_computed():
    """日收益 = 相邻净值比 - 1，跳过非正净值。"""
    navs = [1.0, 1.10, 0.99, 1.089, None, 2.0]
    result = compute_daily_returns(navs)
    # 1.10/1.0-1 = 0.1；0.99/1.10-1 = -0.1；1.089/0.99-1 = 0.1
    # None 出现在中间会打断相邻链（prev=None 或 cur=None 均跳过），故后面不再产生收益
    assert result == pytest.approx([0.1, -0.1, 0.1])


def test_compute_max_drawdown_hand_computed():
    """最大回撤幅度（正数）：峰值 1.10 到谷 0.99 的 (1.10-0.99)/1.10 = 0.1。"""
    navs = [1.0, 1.10, 0.99, 1.089]
    assert compute_max_drawdown(navs) == pytest.approx(0.1)

    # 单调上涨 → 回撤为 0
    assert compute_max_drawdown([1.0, 1.1, 1.2, 1.3]) == pytest.approx(0.0)

    # 无有效净值 → None
    assert compute_max_drawdown([]) is None
    assert compute_max_drawdown([None, None]) is None


def test_compute_beta_identical_series_is_one():
    """基金收益与基准完全相同时 β 恒为 1（协方差 == 方差）。"""
    returns = [0.02, -0.01, 0.03, 0.01, -0.02]
    assert compute_beta(returns, returns) == pytest.approx(1.0)


def test_compute_information_ratio_identical_series_is_none():
    """基金与基准相同时超额收益恒为 0，σ=0 → IR 返回 None。"""
    returns = [0.02, -0.01, 0.03]
    assert compute_information_ratio(returns, returns) is None


def test_compute_treynor_zero_beta_is_none():
    """β≈0 时 Treynor 返回 None。"""
    assert compute_treynor([0.01, 0.02], beta=0.0) is None
    assert compute_treynor([0.01, 0.02], beta=None) is None


def test_compute_sortino_no_downside_is_none():
    """无下行波动（所有 r_i ≥ MAR=0）→ 下行偏差=0 → Sortino 无定义返回 None。"""
    assert compute_sortino([0.01, 0.02, 0.03]) is None
    # 下行偏差本身应返回 0.0（而非 None），因为确无下行波动
    assert compute_downside_std([0.01, 0.02]) == pytest.approx(0.0)
    # 空序列 → 下行偏差 None
    assert compute_downside_std([]) is None


def test_compute_calmar_hand_computed_exact():
    """Calmar 精确值 = 84.0。

    净值 [1.0, 1.10, 0.99, 1.089] → 日收益 [0.1, -0.1, 0.1]
      μ = 0.1/3 = 1/30 → μ·252 = 8.4
      mdd = (1.10-0.99)/1.10 = 0.1
      Calmar = 8.4 / 0.1 = 84.0
    """
    navs = [1.0, 1.10, 0.99, 1.089]
    returns = [0.1, -0.1, 0.1]
    assert compute_calmar(returns, navs) == pytest.approx(84.0)

    # 无回撤（净值单调）→ mdd=0 → None
    assert compute_calmar([0.01, 0.02], [1.0, 1.1, 1.2]) is None


def test_compute_sharpe_hand_computed_exact_with_custom_params():
    """Sharpe 精确值（自定义 Rf/年化因子以消去无理数）。

    收益 [0.01, 0.03]：μ=0.02，σ=|0.01-0.03|/2=0.01
    Rf=0、年化因子=4 时：(0.02·4 - 0) / (0.01·√4) = 0.08/0.02 = 4.0
    """
    assert compute_sharpe([0.01, 0.03], rf_annual=0.0, annualization_factor=4) == pytest.approx(4.0)


def test_compute_treynor_hand_computed_exact_with_custom_params():
    """Treynor 精确值（自定义参数）。

    收益 [0.01, 0.03]：μ=0.02，β=2.0，Rf=0、年化因子=4：
      (0.02·4 - 0) / 2.0 = 0.04
    """
    assert compute_treynor([0.01, 0.03], beta=2.0, rf_annual=0.0, annualization_factor=4) == pytest.approx(0.04)


# ──────────────────────────────────────────────────────────
# 纯函数：statistics 独立 oracle 复算（默认 252/Rf=2% 口径）
# ──────────────────────────────────────────────────────────

def test_compute_sharpe_matches_reference_formula():
    returns = [0.001, 0.002, -0.001, 0.003, 0.0015, -0.0005]
    mu = statistics.fmean(returns)
    sigma = statistics.pstdev(returns)
    expected = (mu * 252 - RISK_FREE_RATE_ANNUAL) / (sigma * math.sqrt(252))
    assert compute_sharpe(returns) == pytest.approx(expected)


def test_compute_sortino_matches_reference_formula():
    """Sortino = (μ·252 − MAR·252) / (σ_d·√252)，σ_d=√(mean(min(r_i−MAR,0)²))，MAR=0。

    与 empyrical.sortino_ratio(returns, required_return=0) 逐项一致。
    """
    returns = [0.02, -0.01, -0.02, 0.03, 0.02]
    mar_daily = 0.0
    mu = statistics.fmean(returns)
    sigma_d = math.sqrt(sum(min(r - mar_daily, 0.0) ** 2 for r in returns) / len(returns))
    expected = (mu - mar_daily) * 252 / (sigma_d * math.sqrt(252))
    assert compute_sortino(returns) == pytest.approx(expected)
    assert compute_downside_std(returns) == pytest.approx(sigma_d)


def test_compute_downside_std_matches_empyrical_downside_risk():
    """下行偏差 = √(mean(min(r_i, 0)²))，含上行收益也应计入分母（除以 n）。"""
    returns = [0.02, -0.01, -0.02, 0.03, 0.02]
    # 手动复算：仅负收益贡献 (min(r,0)²)，但除以全部 n
    squared = [0.0, (-0.01) ** 2, (-0.02) ** 2, 0.0, 0.0]
    expected = math.sqrt(sum(squared) / len(returns))
    assert compute_downside_std(returns) == pytest.approx(expected)


def test_compute_beta_matches_reference_covariance():
    fund = [0.02, -0.01, 0.03, 0.01, -0.015, 0.004]
    bench = [0.01, -0.01, 0.02, 0.005, -0.01, 0.002]
    fm = statistics.fmean(fund)
    bm = statistics.fmean(bench)
    cov = sum((a - fm) * (b - bm) for a, b in zip(fund, bench)) / len(fund)
    var = sum((b - bm) ** 2 for b in bench) / len(bench)
    assert compute_beta(fund, bench) == pytest.approx(cov / var)


def test_compute_information_ratio_matches_reference_formula():
    fund = [0.02, -0.01, 0.03, 0.01, -0.015, 0.004]
    bench = [0.01, -0.01, 0.02, 0.005, -0.01, 0.002]
    diff = [a - b for a, b in zip(fund, bench)]
    expected = (statistics.fmean(diff) / statistics.pstdev(diff)) * math.sqrt(252)
    assert compute_information_ratio(fund, bench) == pytest.approx(expected)


def test_compute_treynor_matches_reference_formula():
    returns = [0.001, 0.002, -0.001, 0.003, 0.0015, -0.0005]
    beta = 0.85
    mu = statistics.fmean(returns)
    expected = (mu * 252 - RISK_FREE_RATE_ANNUAL) / beta
    assert compute_treynor(returns, beta) == pytest.approx(expected)


# ──────────────────────────────────────────────────────────
# compute_risk_adjusted_metrics：类型门禁 / 降级 / 契约
# ──────────────────────────────────────────────────────────

_CONTRACT_KEYS = {
    "code", "available", "degraded", "fund_type",
    "sharpe_ratio", "sortino_ratio", "calmar_ratio",
    "information_ratio", "treynor_ratio", "beta",
    "window_days", "nav_points", "benchmark",
    "rf_annual", "mar_annual", "annualization_factor",
    "sortino_reason", "data_quality", "source",
}


def test_not_eligible_bond_fund_returns_available_false():
    result = compute_risk_adjusted_metrics("000001", name="华夏纯债债券A", fund_type="债券型")
    assert result["available"] is False
    assert result["degraded"] is False
    assert result["fund_type"] == "债券型"
    assert result["data_quality"] == "insufficient"
    for k in ("sharpe_ratio", "sortino_ratio", "calmar_ratio", "information_ratio", "treynor_ratio", "beta"):
        assert result[k] is None
    assert _CONTRACT_KEYS <= set(result.keys())


def test_not_eligible_secondary_bond_fund_misclassified_as_mixed():
    """二级债基（如"债券型-混合二级"）含"混合"字样，但本质是债基，必须排除。"""
    result = compute_risk_adjusted_metrics("000003", name="XX双债增强", fund_type="债券型-混合二级")
    assert result["available"] is False
    assert result["fund_type"] == "债券型-混合二级"


def test_not_eligible_bond_fund_by_name_when_type_empty():
    """ft 缺失时，名称含"债"也应排除（与 QDII 名称关键字同理）。"""
    result = compute_risk_adjusted_metrics("000006", name="XX纯债债券A", fund_type="")
    assert result["available"] is False


def test_not_eligible_qdii_by_name_keyword():
    """QDII 即使 classify_fund 会判为 mixed，也必须显式排除。"""
    result = compute_risk_adjusted_metrics("000001", name="嘉实全球互联网股票(QDII)", fund_type="")
    assert result["available"] is False


def test_not_eligible_when_tushare_not_configured(monkeypatch):
    monkeypatch.setattr("services.tushare_data.is_configured", lambda: False)
    result = compute_risk_adjusted_metrics("000001", name="华夏成长混合", fund_type="混合型")
    assert result["available"] is True  # 类型可计算，但数据源不可用
    assert result["degraded"] is True
    assert result["data_quality"] == "insufficient"
    assert result["nav_points"] == 0


def test_insufficient_nav_points(monkeypatch):
    monkeypatch.setattr("services.tushare_data.is_configured", lambda: True)
    monkeypatch.setattr("services.tushare_data.get_fund_nav", lambda code, days=60: {"available": True, "navs": []})
    monkeypatch.setattr("services.tushare_data.get_index_daily", lambda ts_code="000300.SH", days=120: [])
    result = compute_risk_adjusted_metrics("000001", name="华夏成长混合", fund_type="混合型")
    assert result["available"] is True
    assert result["degraded"] is True
    assert result["data_quality"] == "insufficient"
    assert result["nav_points"] == 0
    assert result["sharpe_ratio"] is None


def _make_fund_navs(n: int):
    """构造确定性的净值序列（含波动与回撤，保证各指标可计算）。"""
    navs = []
    for i in range(n):
        nav = 1.0 + 0.0004 * i + 0.006 * math.sin(i / 4.0)
        navs.append({
            "nav_date": f"2024{i % 100:02d}{i % 28 + 1:02d}",
            "adj_nav": round(nav, 6),
            "unit_nav": round(nav * 0.99, 6),
        })
    return navs


def _make_bench_closes(n: int):
    closes = []
    for i in range(n):
        close = 1.0 + 0.0003 * i + 0.005 * math.sin(i / 5.0) + 0.1
        closes.append({"trade_date": f"2024{i % 100:02d}{i % 28 + 1:02d}", "close": close})
    return closes


def test_full_contract_populated(monkeypatch):
    monkeypatch.setattr("services.tushare_data.is_configured", lambda: True)
    monkeypatch.setattr("services.tushare_data.get_fund_nav", lambda code, days=60: {"available": True, "navs": _make_fund_navs(200)})
    monkeypatch.setattr("services.tushare_data.get_index_daily", lambda ts_code="000300.SH", days=120: _make_bench_closes(200))

    result = compute_risk_adjusted_metrics("110020", name="易方达沪深300ETF联接A", fund_type="股票型")

    assert result["available"] is True
    assert result["fund_type"] == "股票型"
    assert result["window_days"] == RISK_ADJUSTED_WINDOW_DAYS
    assert result["annualization_factor"] == ANNUALIZATION_FACTOR
    assert result["benchmark"] == RISK_ADJUSTED_BENCHMARK
    assert result["rf_annual"] == RISK_FREE_RATE_ANNUAL
    assert result["nav_points"] == 200
    assert result["source"] == "tushare"
    # 构造序列保证核心指标可计算
    assert result["sharpe_ratio"] is not None
    assert result["sortino_ratio"] is not None
    assert result["calmar_ratio"] is not None
    assert result["beta"] is not None
    assert result["treynor_ratio"] is not None
    assert result["information_ratio"] is not None
    assert result["data_quality"] == "full"
    assert result["degraded"] is False
    assert _CONTRACT_KEYS <= set(result.keys())


def _make_all_positive_fund_navs(n: int):
    """净值严格单调上涨（每期正收益），用于构造「无下行波动」场景。"""
    navs = []
    nav = 1.0
    for i in range(n):
        nav *= 1.0 + 0.001 + 0.0002 * math.sin(i / 3.0)
        navs.append({
            "nav_date": f"2024{i % 100:02d}{i % 28 + 1:02d}",
            "adj_nav": round(nav, 8),
            "unit_nav": round(nav * 0.99, 8),
        })
    return navs


def test_no_downside_sortino_returns_none_with_reason(monkeypatch):
    """近3年无下行波动（所有 r_i ≥ MAR=0）→ Sortino=None + sortino_reason=no_downside + data_quality=no_downside。"""
    monkeypatch.setattr("services.tushare_data.is_configured", lambda: True)
    monkeypatch.setattr(
        "services.tushare_data.get_fund_nav",
        lambda code, days=60: {"available": True, "navs": _make_all_positive_fund_navs(80)},
    )
    monkeypatch.setattr(
        "services.tushare_data.get_index_daily",
        lambda ts_code="000300.SH", days=120: _make_bench_closes(80),
    )

    result = compute_risk_adjusted_metrics("110020", name="XX稳健增长", fund_type="股票型")

    assert result["available"] is True
    assert result["sortino_ratio"] is None          # 无下行 → 不返回 0/∞
    assert result["sortino_reason"] == "no_downside"
    assert result["data_quality"] == "no_downside"
    assert result["degraded"] is False


if __name__ == "__main__":
    # 便于本地直接运行
    import traceback

    tests = [
        test_compute_daily_returns_hand_computed,
        test_compute_max_drawdown_hand_computed,
        test_compute_beta_identical_series_is_one,
        test_compute_information_ratio_identical_series_is_none,
        test_compute_treynor_zero_beta_is_none,
        test_compute_sortino_no_downside_is_none,
        test_compute_calmar_hand_computed_exact,
        test_compute_sharpe_hand_computed_exact_with_custom_params,
        test_compute_treynor_hand_computed_exact_with_custom_params,
        test_compute_sharpe_matches_reference_formula,
        test_compute_sortino_matches_reference_formula,
        test_compute_beta_matches_reference_covariance,
        test_compute_information_ratio_matches_reference_formula,
        test_compute_treynor_matches_reference_formula,
        test_not_eligible_bond_fund_returns_available_false,
        test_not_eligible_qdii_by_name_keyword,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"✓ {t.__name__}")
        except Exception:
            print(f"✗ {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} 通过")
