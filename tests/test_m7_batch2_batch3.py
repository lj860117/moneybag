"""
Unit tests for M7+ Batch 2 (Glide Path) + Batch 3 (10 维评分)
===============================================================
纯单元测试，不依赖后端运行。测试 domain/rule_engine/ 下三个新文件。

运行：pytest tests/test_m7_batch2_batch3.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 确保 backend 在 Python 路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


# ============================================================
# Batch 2: glide_path_rules.py 测试
# ============================================================

class TestGlidePath:
    """年龄 Glide Path 配比计算"""

    def test_age_25(self):
        """25 岁 → 股票 70% / 债券 15% / 现金 10% / 黄金 5%"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=25)
        assert result.stock_pct == 0.70
        assert result.bond_pct == 0.15
        assert result.cash_pct == 0.10
        assert result.gold_pct == 0.05
        assert result.age_bucket == 25
        assert result.user_override is False

    def test_age_27_rounds_down_to_25(self):
        """27 岁 → 按 25 岁档位"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=27)
        assert result.age_bucket == 25
        assert result.stock_pct == 0.70

    def test_age_33_rounds_down_to_30(self):
        """33 岁 → 按 30 岁档位"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=33)
        assert result.age_bucket == 30
        assert result.stock_pct == 0.65

    def test_age_55(self):
        """55 岁 → 股票 40% / 债券 40% / 现金 15% / 黄金 5%"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=55)
        assert result.stock_pct == 0.40
        assert result.bond_pct == 0.40
        assert result.cash_pct == 0.15
        assert result.gold_pct == 0.05
        assert result.age_bucket == 55

    def test_age_65(self):
        """65 岁 → 股票 30% / 债券 45% / 现金 20% / 黄金 5%"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=65)
        assert result.stock_pct == 0.30
        assert result.bond_pct == 0.45
        assert result.cash_pct == 0.20
        assert result.gold_pct == 0.05

    def test_all_pct_sum_to_100(self):
        """各年龄档位合计 = 100%"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        for age in range(20, 85, 5):
            result = calculate_target_allocation(age=age)
            total = result.stock_pct + result.bond_pct + result.cash_pct + result.gold_pct
            assert abs(total - 1.0) < 1e-9, f"age={age}: total={total}"

    def test_user_override_within_range(self):
        """用户覆盖在 ±10% 内 → 正常生效"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=30, user_overrides={"stock_pct": 0.60})
        assert result.stock_pct == 0.60
        assert result.user_override is True

    def test_user_override_out_of_range(self):
        """用户覆盖超出 ±10% → 抛 OverrideOutOfRangeError"""
        from domain.rule_engine.glide_path_rules import (
            calculate_target_allocation,
            OverrideOutOfRangeError,
        )
        with pytest.raises(OverrideOutOfRangeError):
            # 30 岁默认 stock=0.65，覆盖 0.80 偏离 15% > 10%
            calculate_target_allocation(age=30, user_overrides={"stock_pct": 0.80})

    def test_user_override_boundary(self):
        """用户覆盖恰好 ±10% → 正常通过"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        # 30 岁默认 stock=0.65，覆盖 0.75 偏离正好 10% → 应通过
        result = calculate_target_allocation(age=30, user_overrides={"stock_pct": 0.75})
        assert result.stock_pct == 0.75

    def test_invalid_age(self):
        """非法年龄 → ValueError"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        with pytest.raises(ValueError):
            calculate_target_allocation(age=-1)
        with pytest.raises(ValueError):
            calculate_target_allocation(age=121)

    def test_young_age_uses_min_bucket(self):
        """18 岁 → 按 20 岁最低档位（不低于 _MIN_BUCKET）"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=18)
        assert result.age_bucket == 20  # 向下取整到 15，但 min 是 20

    def test_very_old_age(self):
        """100 岁 → 按最大档位"""
        from domain.rule_engine.glide_path_rules import calculate_target_allocation
        result = calculate_target_allocation(age=100)
        assert result.age_bucket == 80


class TestStyleDeviation:
    """风格偏离检测"""

    def test_no_deviation(self):
        """无偏离 → 空列表"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        alerts = check_style_deviation(actual_value_pct=0.50, actual_large_cap_pct=0.70)
        assert alerts == []

    def test_value_low_deviation(self):
        """价值 <40% → 标黄"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        alerts = check_style_deviation(actual_value_pct=0.35, actual_large_cap_pct=0.70)
        assert len(alerts) == 1
        assert alerts[0].dimension == "value_style"
        assert alerts[0].level == "yellow"

    def test_value_high_deviation(self):
        """价值 >60% → 标黄"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        alerts = check_style_deviation(actual_value_pct=0.65, actual_large_cap_pct=0.70)
        assert len(alerts) == 1
        assert alerts[0].dimension == "value_style"

    def test_large_cap_low_deviation(self):
        """大盘 <55% → 标黄"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        alerts = check_style_deviation(actual_value_pct=0.50, actual_large_cap_pct=0.50)
        assert len(alerts) == 1
        assert alerts[0].dimension == "large_cap_style"
        assert alerts[0].level == "yellow"

    def test_large_cap_high_deviation(self):
        """大盘 >85% → 标黄"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        alerts = check_style_deviation(actual_value_pct=0.50, actual_large_cap_pct=0.90)
        assert len(alerts) == 1
        assert alerts[0].dimension == "large_cap_style"

    def test_both_deviations(self):
        """双重偏离 → 2 条 alert"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        alerts = check_style_deviation(actual_value_pct=0.35, actual_large_cap_pct=0.50)
        assert len(alerts) == 2

    def test_boundary_no_alert(self):
        """恰好在边界上（偏离 = 阈值）→ 不触发"""
        from domain.rule_engine.glide_path_rules import check_style_deviation
        # 价值 40% 偏离 = 10% = 阈值，不超过不触发
        alerts = check_style_deviation(actual_value_pct=0.40, actual_large_cap_pct=0.70)
        assert len(alerts) == 0


# ============================================================
# Batch 2: deviation_thresholds.py 测试
# ============================================================

class TestDeviationThresholds:
    """动态阈值 + 极端行情保护"""

    def test_low_volatility_tolerance(self):
        """波动率 10% → 容忍 5%"""
        from domain.rule_engine.deviation_thresholds import get_tolerance
        assert get_tolerance(0.10) == 0.05

    def test_mid_volatility_tolerance(self):
        """波动率 20% → 容忍 7%"""
        from domain.rule_engine.deviation_thresholds import get_tolerance
        assert get_tolerance(0.20) == 0.07

    def test_high_volatility_tolerance(self):
        """波动率 30% → 容忍 10%"""
        from domain.rule_engine.deviation_thresholds import get_tolerance
        assert get_tolerance(0.30) == 0.10

    def test_boundary_15_percent(self):
        """波动率 = 15%（边界）→ 中档容忍 7%"""
        from domain.rule_engine.deviation_thresholds import get_tolerance
        assert get_tolerance(0.15) == 0.07

    def test_boundary_25_percent(self):
        """波动率 = 25%（边界）→ 中档容忍 7%"""
        from domain.rule_engine.deviation_thresholds import get_tolerance
        assert get_tolerance(0.25) == 0.07

    def test_extreme_confirmation_triggered(self):
        """波动率 35% → 触发二次确认"""
        from domain.rule_engine.deviation_thresholds import should_trigger_extreme_confirmation
        assert should_trigger_extreme_confirmation(0.35) is True

    def test_extreme_confirmation_not_triggered(self):
        """波动率 25% → 不触发"""
        from domain.rule_engine.deviation_thresholds import should_trigger_extreme_confirmation
        assert should_trigger_extreme_confirmation(0.25) is False

    def test_extreme_confirmation_boundary(self):
        """波动率 = 30%（边界）→ 不触发（需 >30%）"""
        from domain.rule_engine.deviation_thresholds import should_trigger_extreme_confirmation
        assert should_trigger_extreme_confirmation(0.30) is False

    def test_dynamic_filter_within_tolerance(self):
        """偏离 4%，波动率 10% → 容忍 5% → 在范围内 → None"""
        from domain.rule_engine.deviation_thresholds import apply_dynamic_filter
        result = apply_dynamic_filter(deviation=0.04, volatility=0.10)
        assert result is None

    def test_dynamic_filter_mild(self):
        """偏离 6%，波动率 10% → 超出容忍 5% → M3 分级 mild"""
        from domain.rule_engine.deviation_thresholds import apply_dynamic_filter
        result = apply_dynamic_filter(deviation=0.06, volatility=0.10)
        assert result == "mild"

    def test_dynamic_filter_clear(self):
        """偏离 10%，波动率 10% → 超出容忍 → M3 分级 clear"""
        from domain.rule_engine.deviation_thresholds import apply_dynamic_filter
        result = apply_dynamic_filter(deviation=0.10, volatility=0.10)
        assert result == "clear"

    def test_dynamic_filter_strong(self):
        """偏离 20%，波动率 10% → 超出容忍 → M3 分级 strong"""
        from domain.rule_engine.deviation_thresholds import apply_dynamic_filter
        result = apply_dynamic_filter(deviation=0.20, volatility=0.10)
        assert result == "strong"

    def test_dynamic_filter_high_vol_none(self):
        """偏离 8%，波动率 30% → 容忍 10% → 在范围内 → None"""
        from domain.rule_engine.deviation_thresholds import apply_dynamic_filter
        result = apply_dynamic_filter(deviation=0.08, volatility=0.30)
        assert result is None

    def test_dynamic_filter_custom_m3_thresholds(self):
        """自定义 M3 阈值"""
        from domain.rule_engine.deviation_thresholds import apply_dynamic_filter
        custom = {"mild": 0.05, "clear": 0.10, "strong": 0.20}
        # 偏离 12%，波动率 10%（容忍 5%），超出 → 按自定义 clear 档
        result = apply_dynamic_filter(deviation=0.12, volatility=0.10, m3_thresholds=custom)
        assert result == "clear"


# ============================================================
# Batch 3: fund_filter_rules.py 测试
# ============================================================

class TestPurchasabilityCheck:
    """可购买性前置过滤"""

    def _make_candidate(self, **kwargs) -> "FundCandidate":
        from domain.rule_engine.fund_filter_rules import FundCandidate
        defaults = dict(
            fund_code="000001",
            fund_name="测试基金A",
            five_dim_score=80.0,
            fund_type="active",
            category="股票型-偏股混合型",
        )
        defaults.update(kwargs)
        return FundCandidate(**defaults)

    def test_normal_fund_passes(self):
        """正常基金 → 通过"""
        from domain.rule_engine.fund_filter_rules import run_purchasability_check
        fund = self._make_candidate()
        passed, excluded = run_purchasability_check([fund])
        assert len(passed) == 1
        assert len(excluded) == 0

    def test_suspended_fund_excluded(self):
        """暂停申购 → 排除"""
        from domain.rule_engine.fund_filter_rules import run_purchasability_check
        fund = self._make_candidate(is_suspended=True)
        passed, excluded = run_purchasability_check([fund])
        assert len(passed) == 0
        assert len(excluded) == 1
        assert "暂停申购" in excluded[0].reason

    def test_high_min_purchase_excluded(self):
        """最低申购 >1 万 → 排除"""
        from domain.rule_engine.fund_filter_rules import run_purchasability_check
        fund = self._make_candidate(min_purchase_amount=50000)
        passed, excluded = run_purchasability_check([fund])
        assert len(passed) == 0
        assert len(excluded) == 1
        assert ">1万" in excluded[0].reason

    def test_closed_period_excluded(self):
        """封闭期未到期 → 排除"""
        from domain.rule_engine.fund_filter_rules import run_purchasability_check
        fund = self._make_candidate(is_closed_period=True)
        passed, excluded = run_purchasability_check([fund])
        assert len(passed) == 0
        assert len(excluded) == 1
        assert "封闭期" in excluded[0].reason

    def test_permission_required_passes(self):
        """需特定权限 → 不排除（黄灯标记）"""
        from domain.rule_engine.fund_filter_rules import run_purchasability_check
        fund = self._make_candidate(requires_permission="科创板")
        passed, excluded = run_purchasability_check([fund])
        assert len(passed) == 1
        assert len(excluded) == 0

    def test_mixed_batch(self):
        """混合批次：3 只正常 + 2 只排除"""
        from domain.rule_engine.fund_filter_rules import run_purchasability_check
        funds = [
            self._make_candidate(fund_code="001"),
            self._make_candidate(fund_code="002", is_suspended=True),
            self._make_candidate(fund_code="003"),
            self._make_candidate(fund_code="004", is_closed_period=True),
            self._make_candidate(fund_code="005"),
        ]
        passed, excluded = run_purchasability_check(funds)
        assert len(passed) == 3
        assert len(excluded) == 2


class TestTenDimensionFilter:
    """10 维质量筛选"""

    def _make_candidate(self, **kwargs) -> "FundCandidate":
        from domain.rule_engine.fund_filter_rules import FundCandidate
        # 默认：所有维度都绿灯的"完美基金"
        defaults = dict(
            fund_code="000001",
            fund_name="测试基金A",
            five_dim_score=85.0,
            fund_type="active",
            category="股票型-偏股混合型",
            management_fee=0.003,        # 0.3% 费率 → 绿灯
            fund_scale=500_000_000,      # 5 亿 → 绿灯
            institution_ratio=0.40,      # 40% 机构 → 绿灯
            manager_tenure_years=5.0,    # 5 年 → 绿灯
            sharpe_ratio=1.5,
            sharpe_category_percentile=0.20,  # 前 20% → 绿灯
            turnover_rate=1.5,           # 150% → 绿灯
            retail_ratio=0.60,           # 60% 散户 → 绿灯
            max_drawdown=0.20,
            category_avg_drawdown=0.18,  # 回撤 < 1.5 × 平均 → 绿灯
            fund_age_years=8.0,          # 8 年 → 绿灯
        )
        defaults.update(kwargs)
        return FundCandidate(**defaults)

    def test_all_green_passes(self):
        """全绿灯 → passed"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate()
        results = run_10d_filter([fund])
        assert len(results) == 1
        assert results[0].status == "passed"
        assert results[0].red_flags == []
        assert len(results[0].yellow_flags) < 3

    def test_fee_red_excludes(self):
        """费率 >1% → 红灯 → 排除"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate(management_fee=0.015)
        results = run_10d_filter([fund])
        assert results[0].status == "excluded"
        assert "费率" in results[0].red_flags

    def test_scale_red_excludes(self):
        """规模 <5000 万 → 红灯 → 排除"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate(fund_scale=30_000_000)
        results = run_10d_filter([fund])
        assert results[0].status == "excluded"
        assert "规模健康度" in results[0].red_flags

    def test_three_yellow_review(self):
        """3 个黄灯 → review"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate(
            institution_ratio=0.15,           # 黄灯：<20%
            manager_tenure_years=1.5,         # 黄灯：<2 年
            fund_age_years=2.0,               # 黄灯：<3 年
        )
        results = run_10d_filter([fund])
        assert results[0].status == "review"
        assert len(results[0].yellow_flags) >= 3

    def test_two_yellow_passes(self):
        """2 个黄灯 → passed"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate(
            institution_ratio=0.15,           # 黄灯
            fund_age_years=2.0,               # 黄灯
        )
        results = run_10d_filter([fund])
        assert results[0].status == "passed"

    def test_passive_fund_tracking_error_red(self):
        """被动基金宽基跟踪误差 >0.3% → 红灯"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate(
            fund_type="passive",
            index_type="broad",
            tracking_index="000300",
            tracking_error=0.004,  # 0.4% > 0.3%
            management_fee=0.001,
            manager_tenure_years=None,  # 被动基金不看经理
            turnover_rate=None,          # 被动基金不看换手率
        )
        results = run_10d_filter([fund])
        assert results[0].status == "excluded"
        assert "跟踪误差" in results[0].red_flags

    def test_missing_data_treated_as_na(self):
        """数据缺失 → 标 na，不参与红/黄灯判定"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        fund = self._make_candidate(
            institution_ratio=None,
            turnover_rate=None,
            retail_ratio=None,
        )
        results = run_10d_filter([fund])
        details = results[0].dimension_details
        assert details["机构持仓比"].level == "na"
        assert details["换手率"].level == "na"
        assert details["持有人结构"].level == "na"

    def test_top50_cap(self):
        """超过 50 只 → 只取 Top 50"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        funds = [
            self._make_candidate(fund_code=f"F{i:04d}", five_dim_score=80.0 + i * 0.1)
            for i in range(60)
        ]
        results = run_10d_filter(funds)
        assert len(results) == 50

    def test_sorted_by_score_descending(self):
        """结果按 5 维评分降序（同 status 内）"""
        from domain.rule_engine.fund_filter_rules import run_10d_filter
        funds = [
            self._make_candidate(fund_code="A", five_dim_score=90.0),
            self._make_candidate(fund_code="B", five_dim_score=85.0),
            self._make_candidate(fund_code="C", five_dim_score=95.0),
        ]
        results = run_10d_filter(funds)
        scores = [r.five_dim_score for r in results if r.status == "passed"]
        assert scores == sorted(scores, reverse=True)


class TestAntiHomogeneity:
    """防同质化规则"""

    def _make_candidate(self, **kwargs) -> "FundCandidate":
        from domain.rule_engine.fund_filter_rules import FundCandidate
        defaults = dict(
            fund_code="000001",
            fund_name="测试基金",
            five_dim_score=85.0,
            fund_type="passive",
            category="股票型-标准指数型",
            tracking_index="000300",
            index_type="broad",
        )
        defaults.update(kwargs)
        return FundCandidate(**defaults)

    def _make_filter_result(self, fund_code: str, score: float, status: str = "passed") -> "FundFilterResult":
        from domain.rule_engine.fund_filter_rules import FundFilterResult
        return FundFilterResult(
            fund_code=fund_code,
            fund_name=f"基金{fund_code}",
            status=status,
            five_dim_score=score,
        )

    def test_passive_same_index_limit_3(self):
        """同一指数 5 只被动基金 → 保留 3 只，2 只 category_full"""
        from domain.rule_engine.fund_filter_rules import (
            apply_anti_homogeneity_with_candidates,
            FundCandidate,
            FundFilterResult,
        )
        candidates = [
            self._make_candidate(
                fund_code=f"E{i}",
                tracking_index="000300",
                fund_type="passive",
                five_dim_score=90.0 - i,
            )
            for i in range(5)
        ]
        results = [
            self._make_filter_result(f"E{i}", 90.0 - i)
            for i in range(5)
        ]
        output = apply_anti_homogeneity_with_candidates(results, candidates)
        passed_count = sum(1 for r in output if r.status == "passed")
        full_count = sum(1 for r in output if r.status == "category_full")
        assert passed_count == 3
        assert full_count == 2

    def test_active_same_category_limit_4(self):
        """同一分类+风格 6 只主动基金 → 保留 4 只"""
        from domain.rule_engine.fund_filter_rules import (
            apply_anti_homogeneity_with_candidates,
            FundCandidate,
            FundFilterResult,
        )
        candidates = [
            self._make_candidate(
                fund_code=f"A{i}",
                fund_type="active",
                category="混合型-偏股混合型",
                investment_style="成长",
                tracking_index="",
                five_dim_score=90.0 - i,
            )
            for i in range(6)
        ]
        results = [
            self._make_filter_result(f"A{i}", 90.0 - i)
            for i in range(6)
        ]
        output = apply_anti_homogeneity_with_candidates(results, candidates)
        passed_count = sum(1 for r in output if r.status == "passed")
        full_count = sum(1 for r in output if r.status == "category_full")
        assert passed_count == 4
        assert full_count == 2

    def test_different_indices_not_limited(self):
        """不同指数的被动基金 → 各自不受影响"""
        from domain.rule_engine.fund_filter_rules import (
            apply_anti_homogeneity_with_candidates,
            FundCandidate,
        )
        candidates = [
            self._make_candidate(fund_code="E1", tracking_index="000300"),
            self._make_candidate(fund_code="E2", tracking_index="000905"),
            self._make_candidate(fund_code="E3", tracking_index="000001"),
        ]
        results = [self._make_filter_result(f"E{i+1}", 90.0) for i in range(3)]
        output = apply_anti_homogeneity_with_candidates(results, candidates)
        full_count = sum(1 for r in output if r.status == "category_full")
        assert full_count == 0

    def test_excluded_funds_not_affected(self):
        """已排除的基金不参与防同质化"""
        from domain.rule_engine.fund_filter_rules import (
            apply_anti_homogeneity_with_candidates,
        )
        candidates = [
            self._make_candidate(fund_code="E1", tracking_index="000300"),
        ]
        results = [self._make_filter_result("E1", 90.0, status="excluded")]
        output = apply_anti_homogeneity_with_candidates(results, candidates)
        assert output[0].status == "excluded"  # 保持不变


# ============================================================
# defaults.py 新增 dataclass 测试
# ============================================================

class TestDefaults:
    """defaults.py 新增 Batch 2+3 dataclass 完整性"""

    def test_glide_path_defaults_frozen(self):
        """GlidePathDefaults 为 frozen"""
        from domain.rule_engine.defaults import GlidePathDefaults
        assert GlidePathDefaults.GOLD_PCT == 0.05
        assert GlidePathDefaults.USER_OVERRIDE_RANGE == 0.10
        assert GlidePathDefaults.STYLE_VALUE_TARGET == 0.50
        assert GlidePathDefaults.STYLE_LARGE_CAP_TARGET == 0.70
        assert GlidePathDefaults.EXTREME_VOLATILITY_THRESHOLD == 0.30

    def test_deviation_threshold_defaults(self):
        """DeviationThresholdDefaults 值正确"""
        from domain.rule_engine.defaults import DeviationThresholdDefaults
        assert DeviationThresholdDefaults.LOW_VOL_CEILING == 0.15
        assert DeviationThresholdDefaults.MID_VOL_CEILING == 0.25
        assert DeviationThresholdDefaults.LOW_VOL_TOLERANCE == 0.05
        assert DeviationThresholdDefaults.MID_VOL_TOLERANCE == 0.07
        assert DeviationThresholdDefaults.HIGH_VOL_TOLERANCE == 0.10

    def test_fund_filter_defaults(self):
        """FundFilterDefaults 值正确"""
        from domain.rule_engine.defaults import FundFilterDefaults
        assert FundFilterDefaults.FEE_RED == 0.01
        assert FundFilterDefaults.FEE_YELLOW == 0.005
        assert FundFilterDefaults.SCALE_RED == 50_000_000
        assert FundFilterDefaults.SCALE_YELLOW == 200_000_000
        assert FundFilterDefaults.YELLOW_FLAG_THRESHOLD == 3
        assert FundFilterDefaults.PASSIVE_SAME_INDEX_MAX == 3
        assert FundFilterDefaults.ACTIVE_SAME_CATEGORY_MAX == 4

    def test_no_m1_m6_modification(self):
        """M1-M6 已有 dataclass 未被修改"""
        from domain.rule_engine.defaults import (
            AllocationDefaults,
            RiskDefaults,
            ScoringDefaults,
            RebalanceDefaults,
            StaleDataDefaults,
        )
        # 确认关键值未变
        assert AllocationDefaults.AGE_BASE == 30
        assert RiskDefaults.SINGLE_STOCK_MAX == 0.25
        assert ScoringDefaults.CUT_OFF == 60
        assert RebalanceDefaults.CHECK_INTERVAL_DAYS == 90
        assert StaleDataDefaults.INCOME_EXPENSE_MAX_DAYS == 365
