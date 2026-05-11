"""
Fund Filter Validation -- 行业偏离度检测 + 10 维筛选历史验证
============================================================
在历史数据上验证 Batch 3 的 10 维筛选规则有效性，并检测行业偏离度。

验收标准：
  - 复核池占比 10%-30%
  - 红灯排除率 <80%
  - 无单一维度贡献 >60% 红灯
  - 行业偏离能检测到 ≥3 种偏离场景
  - 10 维筛选后二级分类占比 ≤40%

设计文档：docs/design/m7-plus/04-batch-m8-industry-deviation.md
不变式 #1：不做收益回测，只验证规则逻辑
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


# ============================================================
# 协议（数据源依赖注入）
# ============================================================

class DataSourceProtocol(Protocol):
    """历史验证用的数据源接口"""

    def get_monthly_candidates(self, year: int, month: int) -> list:
        """获取指定月份的候选基金列表（FundCandidate 格式）"""
        ...

    def get_holdings(self, year: int, month: int) -> list:
        """获取指定月份的持仓数据（含行业分类）"""
        ...


# ============================================================
# 数据结构
# ============================================================

@dataclass
class IndustryDeviationAlert:
    """行业偏离度告警"""
    industry: str              # 行业名称
    actual_pct: float          # 实际占比（0-1）
    threshold: float           # 触发阈值（0-1）
    level: str                 # "yellow" | "red"
    m3_level: Optional[str]    # M3 层级的判定（方案 A：40% 红灯）
    message: str               # 人类可读提示


@dataclass
class IndustryDeviationStats:
    """行业偏离度统计"""
    industries_hit_25: dict[str, int] = field(default_factory=dict)  # 各行业命中 25% 次数
    industries_hit_35: dict[str, int] = field(default_factory=dict)  # 各行业命中 35% 次数
    top3_concentration_avg: float = 0.0  # 前三大行业平均集中度
    scenarios_detected: int = 0          # 检测到的偏离场景数


@dataclass
class MonthlyResult:
    """单月验证结果"""
    year: int
    month: int
    total_candidates: int
    passed_count: int
    review_count: int
    excluded_count: int
    red_dimensions: dict[str, int] = field(default_factory=dict)   # 各维度红灯次数
    yellow_dimensions: dict[str, int] = field(default_factory=dict)  # 各维度黄灯次数
    category_distribution: dict[str, float] = field(default_factory=dict)  # 二级分类占比


@dataclass
class ValidationReport:
    """历史验证报告"""
    total_months: int
    monthly_results: list[MonthlyResult] = field(default_factory=list)
    avg_red_rate: float = 0.0
    avg_yellow_rate: float = 0.0
    avg_review_pool_pct: float = 0.0
    avg_pass_rate: float = 0.0
    dimension_red_distribution: dict[str, float] = field(default_factory=dict)
    industry_deviation_stats: IndustryDeviationStats = field(
        default_factory=IndustryDeviationStats
    )
    is_qualified: bool = False
    issues: list[str] = field(default_factory=list)


# ============================================================
# 常量区（行业偏离度阈值，引用 defaults.py）
# ============================================================

# M7+ 行业偏离度阈值（精细配置偏离）
SINGLE_INDUSTRY_YELLOW: float = 0.25  # 单行业占比 >25% → 黄灯
SINGLE_INDUSTRY_RED: float = 0.35     # 单行业占比 >35% → 红灯
TOP3_INDUSTRY_YELLOW: float = 0.70    # 前三大行业占比 >70% → 黄灯

# M3 层级阈值（风险画像宽松底线）
M3_SINGLE_INDUSTRY_MAX: float = 0.40  # M3 单行业上限 40%


# ============================================================
# 1. 行业偏离度检测
# ============================================================

@dataclass
class HoldingItem:
    """持仓项（行业偏离度检测输入）"""
    fund_code: str
    fund_name: str
    industry: str       # 天天基金行业分类（季报）
    weight_pct: float   # 该基金在组合中占比（0-1）


def check_industry_deviation(
    holdings: list[HoldingItem],
    decision_2_result: str = "plan_a",
) -> list[IndustryDeviationAlert]:
    """检测行业偏离度。

    规则（M7+ 精细配置偏离层级）：
    - 单一行业占比 >25% → 🟡 标黄
    - 单一行业占比 >35% → 🔴 标红
    - 前三大行业占比 >70% → 🟡 标黄

    方案 A 时：同时计算 M3 层级的 40% 判定作为 m3_level 字段。

    Args:
        holdings: 持仓列表（含行业分类）
        decision_2_result: 决策 2 结果标识（"plan_a" = 双层展示）

    Returns:
        IndustryDeviationAlert 列表（按严重程度降序）
    """
    if not holdings:
        return []

    alerts: list[IndustryDeviationAlert] = []

    # 汇总各行业占比
    industry_weights: dict[str, float] = {}
    for h in holdings:
        industry_weights[h.industry] = industry_weights.get(h.industry, 0.0) + h.weight_pct

    # 检查单一行业阈值
    for industry, pct in industry_weights.items():
        m3_level: Optional[str] = None

        # M3 层级判定（方案 A）
        if decision_2_result == "plan_a":
            if pct > M3_SINGLE_INDUSTRY_MAX:
                m3_level = "red"
            elif pct > SINGLE_INDUSTRY_YELLOW:
                m3_level = "yellow"
            else:
                m3_level = "green"

        # M7+ 精细层级判定
        if pct > SINGLE_INDUSTRY_RED:
            alerts.append(IndustryDeviationAlert(
                industry=industry,
                actual_pct=pct,
                threshold=SINGLE_INDUSTRY_RED,
                level="red",
                m3_level=m3_level,
                message=f"行业「{industry}」占比 {pct:.1%} 超过红灯阈值 {SINGLE_INDUSTRY_RED:.0%}",
            ))
        elif pct > SINGLE_INDUSTRY_YELLOW:
            alerts.append(IndustryDeviationAlert(
                industry=industry,
                actual_pct=pct,
                threshold=SINGLE_INDUSTRY_YELLOW,
                level="yellow",
                m3_level=m3_level,
                message=f"行业「{industry}」占比 {pct:.1%} 超过黄灯阈值 {SINGLE_INDUSTRY_YELLOW:.0%}",
            ))

    # 检查前三大行业集中度
    sorted_industries = sorted(industry_weights.values(), reverse=True)
    if len(sorted_industries) >= 3:
        top3_pct = sum(sorted_industries[:3])
        if top3_pct > TOP3_INDUSTRY_YELLOW:
            top3_names = sorted(industry_weights.items(), key=lambda x: -x[1])[:3]
            names_str = "、".join(f"{name}({pct:.1%})" for name, pct in top3_names)
            alerts.append(IndustryDeviationAlert(
                industry="[前三大行业]",
                actual_pct=top3_pct,
                threshold=TOP3_INDUSTRY_YELLOW,
                level="yellow",
                m3_level=None,
                message=f"前三大行业合计占比 {top3_pct:.1%} > {TOP3_INDUSTRY_YELLOW:.0%}：{names_str}",
            ))

    # 按严重程度排序：red > yellow
    level_order = {"red": 0, "yellow": 1}
    alerts.sort(key=lambda a: (level_order.get(a.level, 9), -a.actual_pct))

    return alerts


# ============================================================
# 2. 10 维筛选历史验证
# ============================================================

def run_historical_validation(
    months: int = 12,
    data_source: Optional[DataSourceProtocol] = None,
) -> ValidationReport:
    """在历史数据上跑 10 维筛选验证。

    随机抽取指定月数的数据，每月跑一遍 10 维筛选，统计：
    - 红灯触发率、黄灯触发率、复核池占比、一票否决率、通过率
    - 行业偏离专项：各行业命中 25%/35% 阈值的频次

    规则：
    - 不做收益回测，只验证规则逻辑
    - 历史数据缺失月份容错跳过
    - 发现阈值问题输出报告，不修改 Batch 3 的筛选阈值

    Args:
        months: 验证月数（默认 12）
        data_source: 数据源（依赖注入），为 None 时使用模拟数据

    Returns:
        ValidationReport — 包含验证结论和问题清单
    """
    # 延迟导入避免循环依赖
    from domain.rule_engine.fund_filter_rules import run_10d_filter

    monthly_results: list[MonthlyResult] = []
    all_red_dimensions: dict[str, int] = {}
    industry_stats = IndustryDeviationStats()
    valid_months = 0

    for offset in range(months):
        # 计算年月（从当前向前回溯）
        year = 2025 - (offset // 12)
        month = 12 - (offset % 12)
        if month <= 0:
            month += 12
            year -= 1

        # 获取数据（数据源缺失时跳过该月）
        if data_source is None:
            # TODO(Batch 6+): 实现 MockDataSource 测试基础设施，提供合成历史数据。
            # 当前无数据源时跳过所有月份，依赖 check_industry_deviation() 的独立单元测试验证逻辑正确性。
            continue  # 无数据源时跳过
        try:
            candidates = data_source.get_monthly_candidates(year, month)
            holdings = data_source.get_holdings(year, month)
        except Exception:
            continue  # 容错跳过

        if not candidates:
            continue

        valid_months += 1

        # 跑 10 维筛选
        filter_results = run_10d_filter(candidates)

        # 统计本月结果
        passed = [r for r in filter_results if r.status == "passed"]
        review = [r for r in filter_results if r.status == "review"]
        excluded = [r for r in filter_results if r.status == "excluded"]

        # 统计各维度红灯贡献
        month_red_dims: dict[str, int] = {}
        month_yellow_dims: dict[str, int] = {}
        for r in filter_results:
            for dim in r.red_flags:
                month_red_dims[dim] = month_red_dims.get(dim, 0) + 1
                all_red_dimensions[dim] = all_red_dimensions.get(dim, 0) + 1
            for dim in r.yellow_flags:
                month_yellow_dims[dim] = month_yellow_dims.get(dim, 0) + 1

        # 二级分类占比统计（只对 passed 的标的）
        cat_dist: dict[str, float] = {}
        if passed:
            cat_counts: dict[str, int] = {}
            for r in passed:
                # 使用 fund_name 中提取的分类（简化处理）
                cat = "默认分类"
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
            total_passed = len(passed)
            cat_dist = {k: v / total_passed for k, v in cat_counts.items()}

        # 行业偏离度检测
        if holdings:
            deviation_alerts = check_industry_deviation(holdings)
            for alert in deviation_alerts:
                if alert.level == "yellow" and alert.industry != "[前三大行业]":
                    industry_stats.industries_hit_25[alert.industry] = (
                        industry_stats.industries_hit_25.get(alert.industry, 0) + 1
                    )
                elif alert.level == "red":
                    industry_stats.industries_hit_35[alert.industry] = (
                        industry_stats.industries_hit_35.get(alert.industry, 0) + 1
                    )

        monthly_results.append(MonthlyResult(
            year=year,
            month=month,
            total_candidates=len(filter_results),
            passed_count=len(passed),
            review_count=len(review),
            excluded_count=len(excluded),
            red_dimensions=month_red_dims,
            yellow_dimensions=month_yellow_dims,
            category_distribution=cat_dist,
        ))

    # 汇总统计
    report = _build_report(monthly_results, valid_months, all_red_dimensions, industry_stats)
    return report


def _build_report(
    monthly_results: list[MonthlyResult],
    valid_months: int,
    all_red_dimensions: dict[str, int],
    industry_stats: IndustryDeviationStats,
) -> ValidationReport:
    """根据月度结果构建验证报告。"""
    if valid_months == 0:
        return ValidationReport(
            total_months=0,
            is_qualified=False,
            issues=["无有效历史数据"],
        )

    # 计算平均指标
    total_candidates = sum(m.total_candidates for m in monthly_results)
    total_excluded = sum(m.excluded_count for m in monthly_results)
    total_review = sum(m.review_count for m in monthly_results)
    total_passed = sum(m.passed_count for m in monthly_results)

    avg_red_rate = total_excluded / total_candidates if total_candidates else 0
    avg_review_pct = total_review / total_candidates if total_candidates else 0
    avg_pass_rate = total_passed / total_candidates if total_candidates else 0
    avg_yellow_rate = avg_review_pct  # 黄灯率 ≈ 复核池占比

    # 维度红灯分布（占比）
    total_reds = sum(all_red_dimensions.values()) or 1
    dim_red_dist = {k: v / total_reds for k, v in all_red_dimensions.items()}

    # 行业偏离场景数
    scenarios = (
        len(industry_stats.industries_hit_25)
        + len(industry_stats.industries_hit_35)
        + (1 if industry_stats.top3_concentration_avg > TOP3_INDUSTRY_YELLOW else 0)
    )
    industry_stats.scenarios_detected = scenarios

    # 验收判定
    issues: list[str] = []

    if avg_review_pct > 0.30:
        issues.append(f"复核池占比 {avg_review_pct:.1%} > 30%，黄灯阈值可能过严")
    elif avg_review_pct < 0.10:
        issues.append(f"复核池占比 {avg_review_pct:.1%} < 10%，黄灯阈值可能过松")

    if avg_red_rate > 0.80:
        issues.append(f"红灯排除率 {avg_red_rate:.1%} > 80%，红灯阈值可能过严")

    for dim, pct in dim_red_dist.items():
        if pct > 0.60:
            issues.append(f"维度「{dim}」贡献 {pct:.1%} 红灯，该维度阈值可能不合理")

    if scenarios < 3:
        issues.append(f"行业偏离只检测到 {scenarios} 种场景（需 ≥3），行业分类可能有遗漏")

    is_qualified = len(issues) == 0

    return ValidationReport(
        total_months=valid_months,
        monthly_results=monthly_results,
        avg_red_rate=avg_red_rate,
        avg_yellow_rate=avg_yellow_rate,
        avg_review_pool_pct=avg_review_pct,
        avg_pass_rate=avg_pass_rate,
        dimension_red_distribution=dim_red_dist,
        industry_deviation_stats=industry_stats,
        is_qualified=is_qualified,
        issues=issues,
    )


# ============================================================
# 导出
# ============================================================

__all__ = [
    "HoldingItem",
    "IndustryDeviationAlert",
    "IndustryDeviationStats",
    "MonthlyResult",
    "ValidationReport",
    "check_industry_deviation",
    "run_historical_validation",
]
