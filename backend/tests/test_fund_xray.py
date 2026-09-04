"""P0-1 组合穿透体检（compute_exposure）独立回归。

核心验的是二次加权公式与覆盖率三分解，用合成数据手算期望值：
  exposure[stock] = Σ_fund ( weight_mv[fund] × stk_mkv_ratio[fund][stock] / 100 )
  penetrated + blind + residual == 100.00 恒成立。
全部离线，不依赖 is_qdii、不依赖网络。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.fund_signal.portfolio import FundPosition
from services.fund_signal import xray


def _pf(code, name, weight_mv, is_qdii=False):
    return FundPosition(
        code=code, name=name, shares=1.0, cost_nav=1.0, unit_nav=1.0,
        adj_nav=1.0, is_qdii=is_qdii, market_value=1.0, weight_mv=weight_mv,
        nav_date="20260930", nav_history=[],
    )


def _holding(symbol, ratio):
    return {"symbol": symbol, "stk_mkv_ratio": ratio}


# 合成持仓：F1 50% + F2(盲区) 25% + F3 25%
POSITIONS = [
    _pf("013107", "华夏先进制造", 50.0),
    _pf("006555", "浦银全球智能科技QDII", 25.0, is_qdii=True),
    _pf("007356", "汇添富科技创新", 25.0),
]

PORTFOLIOS = {
    "013107": {"ok": True, "end_date": "20260630", "ann_date": "20260721",
               "holdings": [_holding("002371.SZ", 40.0), _holding("300502.SZ", 30.0)]},
    "006555": {"ok": False},   # QDII 盲区（正常路径，不是故障）
    "007356": {"ok": True, "end_date": "20260630", "ann_date": "20260721",
               "holdings": [_holding("002371.SZ", 40.0), _holding("688037.SZ", 10.0)]},
}

SW_MAP = {
    "002371.SZ": {"l1": "电子", "l2": "半导体", "l3": "半导体设备", "name": "北方华创"},
    "300502.SZ": {"l1": "通信", "l2": "通信设备", "l3": "通信设备", "name": "新易盛"},
    "688037.SZ": {"l1": "电子", "l2": "半导体", "l3": "半导体设备", "name": "芯源微"},
}


def test_exposure_second_order_weighting_and_coverage_decomposition():
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")

    # 二次加权：北方华创 = 50×40/100 + 25×40/100 = 30.0
    stocks = {s.symbol: s for s in result.stocks}
    assert abs(stocks["002371.SZ"].exposure_pct - 30.0) < 0.01
    assert abs(stocks["300502.SZ"].exposure_pct - 15.0) < 0.01
    assert abs(stocks["688037.SZ"].exposure_pct - 2.5) < 0.01

    # fund_count：北方华创被 2 只基金重仓
    assert stocks["002371.SZ"].fund_count == 2
    assert stocks["002371.SZ"].name == "北方华创"

    # 覆盖率三分解恒 = 100.00
    cov = result.coverage
    assert abs(cov.penetrated_pct - 47.5) < 0.01, cov.penetrated_pct
    assert abs(cov.blind_pct - 25.0) < 0.01, cov.blind_pct
    assert abs(cov.residual_pct - 27.5) < 0.01, cov.residual_pct
    assert abs(cov.penetrated_pct + cov.blind_pct + cov.residual_pct - 100.0) < 0.01

    # 行业聚合：半导体 = 北方华创 30 + 芯源微 2.5 = 32.5
    inds = {i.industry: i for i in result.industries}
    assert abs(inds["半导体"].exposure_pct - 32.5) < 0.01
    assert abs(inds["通信设备"].exposure_pct - 15.0) < 0.01

    # 盲区基金点名
    assert [f["code"] for f in cov.blind_funds] == ["006555"]

    # 触发规则：R1(半导体>25) + R2(个股>3)
    assert "R1_industry_concentration" in result.triggered_rules
    assert "R2_stock_concentration" in result.triggered_rules
    assert "R3_overlap" not in result.triggered_rules  # 最大 fund_count=2 < 3


def test_blind_pct_does_not_depend_on_is_qdii():
    """覆盖率主判定靠运行时 ok=False，不靠 FundPosition.is_qdii。"""
    positions = [_pf(p.code, p.name, p.weight_mv, is_qdii=False) for p in POSITIONS]
    result = xray.compute_exposure(positions, PORTFOLIOS, SW_MAP, "sw_l2")
    assert abs(result.coverage.blind_pct - 25.0) < 0.01


def test_industry_map_empty_degrades_to_stocks_only():
    """行业映射不可用 → 只报个股暴露、不报行业，不得整体失败。"""
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, {}, "none")
    assert result.industries == []
    assert len(result.stocks) == 3
    assert result.coverage.industry_source == "none"


def test_industry_source_is_recorded():
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "tushare_industry")
    assert result.coverage.industry_source == "tushare_industry"


def test_all_funds_blind_yields_full_blind_coverage():
    """全部基金都无穿透数据 → blind=100，penetrated=residual=0。"""
    portfolios = {c: {"ok": False} for c in PORTFOLIOS}
    result = xray.compute_exposure(POSITIONS, portfolios, SW_MAP, "sw_l2")
    cov = result.coverage
    assert abs(cov.blind_pct - 100.0) < 0.01
    assert abs(cov.penetrated_pct - 0.0) < 0.01
    assert abs(cov.residual_pct - 0.0) < 0.01


# ============================================================
# decide_emit：P0-1 首次基线无条件推，之后只推新报告期 + 命中门槛
# ============================================================

def test_decide_emit_cold_start_is_true():
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")
    assert xray.decide_emit(result, PORTFOLIOS, {}) is True


def test_decide_emit_same_period_no_rerun():
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")
    state = {"baseline_sent": True, "last_end_dates": {
        "013107": "2026-06-30", "007356": "2026-06-30"}}
    assert xray.decide_emit(result, PORTFOLIOS, state) is False


def test_decide_emit_new_period_with_rule_is_true():
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")
    state = {"baseline_sent": True, "last_end_dates": {
        "013107": "2026-03-31", "007356": "2026-03-31"}}  # 新的 06-30 报告期
    assert xray.decide_emit(result, PORTFOLIOS, state) is True


# ------------------------------------------------------------------
# 2026-09-05 线上实测回归：last_end_dates 的日期格式必须两边都规范化
# ------------------------------------------------------------------
# 线上 bug：fund_signal/__init__.py 把 pf["end_date"]【原样】写进 state
# （Tushare 原始格式 "20260630"），而 decide_emit 拿它跟 _norm_date() 之后
# 的 "2026-06-30" 直接比字符串 → 永远不等 → new_period 恒为 True →
# P0-1 每次 match() 都重推，冷启动「只推一次」语义失效。
#
# 上方 test_decide_emit_same_period_no_rerun 用的是【已规范化】的
# "2026-06-30" 写 state，与线上写入形状不一致，所以测不出这个 bug。
# 下面两个用例刻意复刻线上写入形状（YYYYMMDD）。

def test_decide_emit_same_period_no_rerun_when_state_stores_raw_yyyymmdd():
    """线上真实形状：state 里存 Tushare 原始 YYYYMMDD → 同报告期不得重推。"""
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")
    state = {"baseline_sent": True, "last_end_dates": {
        "013107": "20260630", "007356": "20260630"}}   # ← 原样写入，未规范化
    assert xray.decide_emit(result, PORTFOLIOS, state) is False


def test_decide_emit_mixed_date_formats_in_state_still_matches():
    """新旧格式混存（历史状态 + 新写入）时也不能误判为新报告期。"""
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")
    state = {"baseline_sent": True, "last_end_dates": {
        "013107": "20260630",          # 旧：YYYYMMDD
        "007356": "2026-06-30"}}       # 新：已规范化
    assert xray.decide_emit(result, PORTFOLIOS, state) is False


def test_decide_emit_blank_state_date_counts_as_new_period():
    """state 里是空串（QDII 盲区基金写进去的 ""）→ 视为未见过的报告期。"""
    result = xray.compute_exposure(POSITIONS, PORTFOLIOS, SW_MAP, "sw_l2")
    state = {"baseline_sent": True, "last_end_dates": {
        "013107": "20260630", "007356": ""}}
    assert xray.decide_emit(result, PORTFOLIOS, state) is True
