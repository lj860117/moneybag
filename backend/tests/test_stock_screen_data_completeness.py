"""
P1-2 回归测试：选股评分必须带出数据完整度

背景（P1-2 缺陷）：
    `_score_momentum` 对缺失因子不增不减，于是「4 个动量因子全部缺失」与
    「数据齐全、综合判断为中性」都会落在 50 附近。输出里两者长得一模一样，
    下游与用户会把「根本没数据」误读成「判断为中性」——
    与 P1-1「未判定不得显示为中性」是同一条数据诚实准则。

本测试锁死：覆盖率必须能区分这两种情况。
"""
import pytest

from services.stock_screen import _momentum_coverage, _score_momentum


ALL_MISSING = {"change_5d": None, "change_20d": None,
               "change_60d": None, "change_pct": None}


# ── 1. 覆盖率本身 ────────────────────────────────────────────────────

def test_coverage_all_missing():
    assert _momentum_coverage(ALL_MISSING) == "0/4"


def test_coverage_all_present():
    s = {"change_5d": 1.0, "change_20d": 2.0, "change_60d": 3.0, "change_pct": 0.5}
    assert _momentum_coverage(s) == "4/4"


def test_coverage_partial():
    """原始缺陷场景：change_20d / change_60d 为 null，只有 5 日与今日有值"""
    s = {"change_5d": -6.0, "change_20d": None, "change_60d": None, "change_pct": 1.0}
    assert _momentum_coverage(s) == "2/4"


def test_coverage_only_one():
    assert _momentum_coverage({"change_pct": 1.0}) == "1/4"


def test_coverage_empty_dict_is_zero():
    assert _momentum_coverage({}) == "0/4"
    assert _momentum_coverage(None) == "0/4"


# ── 2. 核心：没数据 ≠ 判断为中性 ─────────────────────────────────────

def test_missing_data_is_flagged_as_no_data_not_neutral():
    """核心：全缺失时分数停在基准 50，看起来像「中性判断」，必须靠覆盖率揭示为无数据

    注意 `_score_momentum` 的每个分支都带增减，因此**不存在**「数据齐全恰好回到
    50」的输入；50 这个基准分只有「一个因子都没有」时才会原样出现。
    危险正在于此：用户看到 50 会当成均衡中性，实际是根本没有数据。
    """
    assert _score_momentum(ALL_MISSING) == 50, "全缺失应停在基准分，不增不减"
    assert _momentum_coverage(ALL_MISSING) == "0/4"

    # 但只要有一个因子有值，分数就会偏离 50 —— 覆盖率也随之变化
    one_factor = dict(ALL_MISSING, change_pct=1.0)
    assert _score_momentum(one_factor) != 50
    assert _momentum_coverage(one_factor) == "1/4"


def test_zero_is_not_treated_as_real_value():
    """负面控制：0.0 是真实数据（当日平盘），必须计入覆盖，不得当缺失跳过"""
    s = {"change_5d": 0.0, "change_20d": 0.0, "change_60d": 0.0, "change_pct": 0.0}
    assert _momentum_coverage(s) == "4/4"


def test_missing_does_not_fake_zero_return():
    """缺失不得被当成「涨幅 0%」参与评分：全缺失应停在基准分，而非按 0 涨幅加分"""
    assert _score_momentum(ALL_MISSING) == 50
    # 若把 None 当 0.0，5日/20日/60日/今日 都会落进各自的加分区间，分数会明显偏高
    fake_zero = {"change_5d": 0.0, "change_20d": 0.0,
                 "change_60d": 0.0, "change_pct": 0.0}
    assert _score_momentum(ALL_MISSING) != _score_momentum(fake_zero)
