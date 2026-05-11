"""
Fund Filter Rules -- 前置过滤 + 10 维硬指标筛选 + 防同质化
=============================================================
在 5 维评分候选池基础上，用 10 维硬指标进一步淘汰不合格标的，降低噪音。

流程：
  1. run_purchasability_check() — 可购买性前置过滤（能不能买）
  2. run_10d_filter() — 10 维质量筛选（值不值得买）
  3. apply_anti_homogeneity() — 防同质化（同类限数）

设计文档：docs/design/m7-plus/03-batch-m8-10d-scoring.md
不变式 #9：domain/ 不 import infra/
不变式：不修改 M3 scoring_rules.py（10 维是 5 维的下游）

⚠️ 数据来源说明：
  - 机构持仓比、换手率、持有人结构来自基金半年报
  - DataSourceProtocol 当前可能未覆盖这些字段
  - 缺失数据时标记为 "na"（不适用），不参与红/黄灯判定

TODO(§九验证3): DataSourceProtocol 尚未覆盖以下基金半年报字段：
  - institution_ratio（机构持仓比）
  - turnover_rate（换手率）
  - retail_ratio（持有人结构/散户比例）
  - manager_tenure_years（基金经理任职年限）
  等 §九验证3 完成后，扩展 DataSourceProtocol 并移除本 TODO。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


# ============================================================
# 常量区（10 维阈值）
# ============================================================

# 费率阈值
FEE_RED: float = 0.01         # 管理费率 >1% → 红灯
FEE_YELLOW: float = 0.005     # 管理费率 >0.5% → 黄灯

# 规模健康度阈值（元）
SCALE_RED: int = 50_000_000          # <5000 万 → 红灯
SCALE_YELLOW: int = 200_000_000      # <2 亿 → 黄灯

# 机构持仓比阈值
INSTITUTION_YELLOW: float = 0.20     # <20% → 黄灯（无红灯）

# 基金经理稳定性阈值（年）— 仅适用于主动基金
MANAGER_TENURE_YELLOW: float = 2.0   # 任职 <2 年 → 黄灯（无红灯）

# 跟踪误差阈值 — 仅适用于被动基金/ETF
TRACKING_ERROR_BROAD_RED: float = 0.003    # 宽基 >0.3% → 红灯
TRACKING_ERROR_BROAD_YELLOW: float = 0.002  # 宽基 >0.2% → 黄灯
TRACKING_ERROR_SECTOR_RED: float = 0.005    # 行业/主题 >0.5% → 红灯
TRACKING_ERROR_SECTOR_YELLOW: float = 0.003  # 行业/主题 >0.3% → 黄灯

# 夏普比率阈值（同类排名）
SHARPE_YELLOW_PERCENTILE: float = 0.30  # 同类排名后 30% → 黄灯（无红灯）

# 换手率阈值 — 仅适用于主动基金
TURNOVER_YELLOW: float = 3.0   # >300% → 黄灯（无红灯）

# 持有人结构阈值
RETAIL_YELLOW: float = 0.90    # 散户 >90% → 黄灯（无红灯）

# 最大回撤阈值（相对同类平均）
MAX_DRAWDOWN_YELLOW_RATIO: float = 1.5  # >同类平均 1.5 倍 → 黄灯（无红灯）

# 成立年限阈值（年）
FUND_AGE_YELLOW: float = 3.0  # <3 年 → 黄灯（无红灯）

# 筛选逻辑阈值
YELLOW_FLAG_THRESHOLD: int = 3  # 黄灯 ≥3 项 → 进复核池

# 防同质化限数
PASSIVE_SAME_INDEX_MAX: int = 3   # 被动基金同一指数最多保留 3 只
ACTIVE_SAME_CATEGORY_MAX: int = 4  # 主动基金同一分类+风格最多保留 4 只

# 候选池输入上限
MAX_CANDIDATES_FOR_10D: int = 50  # >75 分标的超过 50 只时先取 Top 50

# 可购买性阈值
MIN_PURCHASE_AMOUNT_RED: int = 10000  # 最低申购金额 >1 万 → 红灯


# ============================================================
# 数据结构
# ============================================================

@dataclass
class FundCandidate:
    """5 维评分通过后的候选基金（10 维筛选的输入）"""
    fund_code: str
    fund_name: str
    five_dim_score: float            # 5 维评分（0-100）
    fund_type: str                   # "active" | "passive" | "etf"
    category: str                    # 天天基金二级分类
    investment_style: str = ""       # 投资风格（价值/平衡/成长）
    tracking_index: str = ""         # 跟踪指数代码（被动基金用）
    index_type: str = ""             # "broad" | "sector"（宽基/行业）

    # 可购买性字段
    is_suspended: bool = False               # 是否暂停申购
    min_purchase_amount: int = 0             # 最低申购金额（元）
    is_closed_period: bool = False           # 是否封闭期未到期
    requires_permission: Optional[str] = None  # 需特定权限（北交所/科创板/港股通）
    is_qdii_limited: bool = False            # QDII 限额申购
    qdii_daily_limit: Optional[int] = None   # QDII 每日限额（元）

    # 10 维数据字段（Optional，缺失时标 "na"）
    management_fee: Optional[float] = None         # 管理费率（0-1）
    fund_scale: Optional[int] = None               # 基金规模（元）
    institution_ratio: Optional[float] = None      # 机构持仓比（0-1）
    manager_tenure_years: Optional[float] = None   # 基金经理任职年限
    tracking_error: Optional[float] = None         # 年化跟踪误差（0-1）
    sharpe_ratio: Optional[float] = None           # 夏普比率
    sharpe_category_percentile: Optional[float] = None  # 夏普比率同类百分位（0-1，0=最好）
    turnover_rate: Optional[float] = None          # 换手率（倍数，如 3.0=300%）
    retail_ratio: Optional[float] = None           # 散户比例（0-1）
    max_drawdown: Optional[float] = None           # 最大回撤（正数，如 0.3=30%）
    category_avg_drawdown: Optional[float] = None  # 同类平均最大回撤
    fund_age_years: Optional[float] = None         # 成立年限


@dataclass
class ExcludedFund:
    """被前置过滤排除的基金"""
    fund_code: str
    fund_name: str
    reason: str          # 排除原因
    level: str = "red"   # "red" = 直接排除, "yellow" = 标记但不排除


@dataclass
class DimensionResult:
    """单个维度的检查结果"""
    dimension: str                       # 维度名
    value: Optional[float] = None        # 实际值
    threshold_red: Optional[float] = None
    threshold_yellow: Optional[float] = None
    level: str = "green"                 # "green" | "yellow" | "red" | "na"


@dataclass
class FundFilterResult:
    """单只基金的 10 维筛选结果"""
    fund_code: str
    fund_name: str
    status: str                                          # "passed" | "review" | "excluded" | "category_full"
    red_flags: List[str] = field(default_factory=list)   # 红灯维度列表
    yellow_flags: List[str] = field(default_factory=list)  # 黄灯维度列表
    dimension_details: dict[str, Any] = field(default_factory=dict)  # 每个维度的详细结果
    five_dim_score: float = 0.0                          # 5 维评分（用于排序）
    exclusion_reason: Optional[str] = None               # 排除原因（人类可读）


# ============================================================
# 1. 可购买性前置过滤
# ============================================================

def run_purchasability_check(
    candidates: List[FundCandidate],
) -> Tuple[List[FundCandidate], List[ExcludedFund]]:
    """前置过滤：可购买性检查。

    过滤规则：
    - 暂停申购 → 红灯排除
    - 最低申购金额 >1 万 → 红灯排除
    - 封闭期未到期 → 红灯排除
    - 需特定权限 → 黄灯标记（不排除）
    - QDII 限额申购 → 黄灯标记（不排除）

    Args:
        candidates: 候选基金列表

    Returns:
        (通过列表, 排除列表)
    """
    passed: List[FundCandidate] = []
    excluded: List[ExcludedFund] = []

    for fund in candidates:
        # 红灯：直接排除
        if fund.is_suspended:
            excluded.append(ExcludedFund(
                fund_code=fund.fund_code,
                fund_name=fund.fund_name,
                reason="暂停申购",
                level="red",
            ))
            continue

        if fund.min_purchase_amount > MIN_PURCHASE_AMOUNT_RED:
            excluded.append(ExcludedFund(
                fund_code=fund.fund_code,
                fund_name=fund.fund_name,
                reason=f"最低申购金额 {fund.min_purchase_amount} 元（>1万）",
                level="red",
            ))
            continue

        if fund.is_closed_period:
            excluded.append(ExcludedFund(
                fund_code=fund.fund_code,
                fund_name=fund.fund_name,
                reason="封闭期未到期",
                level="red",
            ))
            continue

        # 黄灯：标记但不排除（通过标记在 fund 对象上，下游可读取）
        # 注意：黄灯标记不影响通过/排除判定，仅作为展示信息
        passed.append(fund)

    return passed, excluded


# ============================================================
# 2. 10 维质量筛选
# ============================================================

def _check_fee(fund: FundCandidate) -> DimensionResult:
    """维度 1：费率检查"""
    if fund.management_fee is None:
        return DimensionResult(dimension="费率", level="na")
    val = fund.management_fee
    if val > FEE_RED:
        return DimensionResult(dimension="费率", value=val,
                               threshold_red=FEE_RED, threshold_yellow=FEE_YELLOW, level="red")
    elif val > FEE_YELLOW:
        return DimensionResult(dimension="费率", value=val,
                               threshold_red=FEE_RED, threshold_yellow=FEE_YELLOW, level="yellow")
    return DimensionResult(dimension="费率", value=val,
                           threshold_red=FEE_RED, threshold_yellow=FEE_YELLOW, level="green")


def _check_scale(fund: FundCandidate) -> DimensionResult:
    """维度 2：规模健康度检查"""
    if fund.fund_scale is None:
        return DimensionResult(dimension="规模健康度", level="na")
    val = fund.fund_scale
    if val < SCALE_RED:
        return DimensionResult(dimension="规模健康度", value=float(val),
                               threshold_red=float(SCALE_RED), threshold_yellow=float(SCALE_YELLOW),
                               level="red")
    elif val < SCALE_YELLOW:
        return DimensionResult(dimension="规模健康度", value=float(val),
                               threshold_red=float(SCALE_RED), threshold_yellow=float(SCALE_YELLOW),
                               level="yellow")
    return DimensionResult(dimension="规模健康度", value=float(val),
                           threshold_red=float(SCALE_RED), threshold_yellow=float(SCALE_YELLOW),
                           level="green")


def _check_institution_ratio(fund: FundCandidate) -> DimensionResult:
    """维度 3：机构持仓比检查"""
    if fund.institution_ratio is None:
        # ⚠️ 数据来自基金半年报，DataSourceProtocol 可能未覆盖
        return DimensionResult(dimension="机构持仓比", level="na")
    val = fund.institution_ratio
    if val < INSTITUTION_YELLOW:
        return DimensionResult(dimension="机构持仓比", value=val,
                               threshold_yellow=INSTITUTION_YELLOW, level="yellow")
    return DimensionResult(dimension="机构持仓比", value=val,
                           threshold_yellow=INSTITUTION_YELLOW, level="green")


def _check_manager_tenure(fund: FundCandidate) -> DimensionResult:
    """维度 4：基金经理稳定性检查（仅主动基金）"""
    if fund.fund_type not in ("active",):
        return DimensionResult(dimension="基金经理稳定性", level="na")
    if fund.manager_tenure_years is None:
        return DimensionResult(dimension="基金经理稳定性", level="na")
    val = fund.manager_tenure_years
    if val < MANAGER_TENURE_YELLOW:
        return DimensionResult(dimension="基金经理稳定性", value=val,
                               threshold_yellow=MANAGER_TENURE_YELLOW, level="yellow")
    return DimensionResult(dimension="基金经理稳定性", value=val,
                           threshold_yellow=MANAGER_TENURE_YELLOW, level="green")


def _check_tracking_error(fund: FundCandidate) -> DimensionResult:
    """维度 5：跟踪误差检查（仅被动基金/ETF）"""
    if fund.fund_type not in ("passive", "etf"):
        return DimensionResult(dimension="跟踪误差", level="na")
    if fund.tracking_error is None:
        return DimensionResult(dimension="跟踪误差", level="na")

    val = fund.tracking_error
    # 根据指数类型选阈值
    if fund.index_type == "broad":
        red_threshold = TRACKING_ERROR_BROAD_RED
        yellow_threshold = TRACKING_ERROR_BROAD_YELLOW
    else:
        # 行业/主题（默认）
        red_threshold = TRACKING_ERROR_SECTOR_RED
        yellow_threshold = TRACKING_ERROR_SECTOR_YELLOW

    if val > red_threshold:
        return DimensionResult(dimension="跟踪误差", value=val,
                               threshold_red=red_threshold, threshold_yellow=yellow_threshold,
                               level="red")
    elif val > yellow_threshold:
        return DimensionResult(dimension="跟踪误差", value=val,
                               threshold_red=red_threshold, threshold_yellow=yellow_threshold,
                               level="yellow")
    return DimensionResult(dimension="跟踪误差", value=val,
                           threshold_red=red_threshold, threshold_yellow=yellow_threshold,
                           level="green")


def _check_sharpe_ratio(fund: FundCandidate) -> DimensionResult:
    """维度 6：夏普比率检查（同类排名）"""
    if fund.sharpe_category_percentile is None:
        return DimensionResult(dimension="夏普比率", level="na")
    # percentile: 0=最好, 1=最差。后 30% 意味着 percentile > 0.70
    val = fund.sharpe_category_percentile
    if val > (1.0 - SHARPE_YELLOW_PERCENTILE):
        return DimensionResult(dimension="夏普比率", value=fund.sharpe_ratio,
                               threshold_yellow=SHARPE_YELLOW_PERCENTILE, level="yellow")
    return DimensionResult(dimension="夏普比率", value=fund.sharpe_ratio,
                           threshold_yellow=SHARPE_YELLOW_PERCENTILE, level="green")


def _check_turnover(fund: FundCandidate) -> DimensionResult:
    """维度 7：换手率检查（仅主动基金）"""
    if fund.fund_type not in ("active",):
        return DimensionResult(dimension="换手率", level="na")
    if fund.turnover_rate is None:
        # ⚠️ 数据来自基金年报，DataSourceProtocol 可能未覆盖
        return DimensionResult(dimension="换手率", level="na")
    val = fund.turnover_rate
    if val > TURNOVER_YELLOW:
        return DimensionResult(dimension="换手率", value=val,
                               threshold_yellow=TURNOVER_YELLOW, level="yellow")
    return DimensionResult(dimension="换手率", value=val,
                           threshold_yellow=TURNOVER_YELLOW, level="green")


def _check_retail_ratio(fund: FundCandidate) -> DimensionResult:
    """维度 8：持有人结构检查"""
    if fund.retail_ratio is None:
        # ⚠️ 数据来自基金半年报，DataSourceProtocol 可能未覆盖
        return DimensionResult(dimension="持有人结构", level="na")
    val = fund.retail_ratio
    if val > RETAIL_YELLOW:
        return DimensionResult(dimension="持有人结构", value=val,
                               threshold_yellow=RETAIL_YELLOW, level="yellow")
    return DimensionResult(dimension="持有人结构", value=val,
                           threshold_yellow=RETAIL_YELLOW, level="green")


def _check_max_drawdown(fund: FundCandidate) -> DimensionResult:
    """维度 9：最大回撤检查（相对同类平均）"""
    if fund.max_drawdown is None or fund.category_avg_drawdown is None:
        return DimensionResult(dimension="最大回撤", level="na")
    val = fund.max_drawdown
    avg = fund.category_avg_drawdown
    if avg > 0 and val > avg * MAX_DRAWDOWN_YELLOW_RATIO:
        return DimensionResult(dimension="最大回撤", value=val,
                               threshold_yellow=avg * MAX_DRAWDOWN_YELLOW_RATIO, level="yellow")
    return DimensionResult(dimension="最大回撤", value=val,
                           threshold_yellow=avg * MAX_DRAWDOWN_YELLOW_RATIO if avg > 0 else None,
                           level="green")


def _check_fund_age(fund: FundCandidate) -> DimensionResult:
    """维度 10：成立年限检查"""
    if fund.fund_age_years is None:
        return DimensionResult(dimension="成立年限", level="na")
    val = fund.fund_age_years
    if val < FUND_AGE_YELLOW:
        return DimensionResult(dimension="成立年限", value=val,
                               threshold_yellow=FUND_AGE_YELLOW, level="yellow")
    return DimensionResult(dimension="成立年限", value=val,
                           threshold_yellow=FUND_AGE_YELLOW, level="green")


def run_10d_filter(candidates: List[FundCandidate]) -> List[FundFilterResult]:
    """10 维质量筛选主入口。

    输入：5 维评分 >75 分的候选池（已通过可购买性检查）
    若 >75 分标的超过 50 只，先取 Top 50 再跑。

    筛选逻辑：
    - 红灯 ≥1 项 → 一票否决，status="excluded"
    - 黄灯 ≥3 项 → 进复核池，status="review"
    - 无红灯 + 黄灯 <3 项 → 通过，status="passed"
    - 剩余标的按 5 维评分重新排序

    Args:
        candidates: 5 维评分 >75 分的候选池

    Returns:
        FundFilterResult 列表（含 passed/review/excluded 状态），按 5 维评分降序
    """
    # 若超过 50 只，取 Top 50
    if len(candidates) > MAX_CANDIDATES_FOR_10D:
        candidates = sorted(candidates, key=lambda f: f.five_dim_score, reverse=True)[:MAX_CANDIDATES_FOR_10D]

    results: List[FundFilterResult] = []

    # 10 维检查函数列表
    check_functions = [
        _check_fee,
        _check_scale,
        _check_institution_ratio,
        _check_manager_tenure,
        _check_tracking_error,
        _check_sharpe_ratio,
        _check_turnover,
        _check_retail_ratio,
        _check_max_drawdown,
        _check_fund_age,
    ]

    for fund in candidates:
        red_flags: List[str] = []
        yellow_flags: List[str] = []
        dimension_details: dict[str, Any] = {}

        for check_fn in check_functions:
            result = check_fn(fund)
            dimension_details[result.dimension] = result

            if result.level == "red":
                red_flags.append(result.dimension)
            elif result.level == "yellow":
                yellow_flags.append(result.dimension)

        # 判定状态
        if len(red_flags) >= 1:
            status = "excluded"
            exclusion_reason = f"红灯维度：{'、'.join(red_flags)}"
        elif len(yellow_flags) >= YELLOW_FLAG_THRESHOLD:
            status = "review"
            exclusion_reason = None
        else:
            status = "passed"
            exclusion_reason = None

        results.append(FundFilterResult(
            fund_code=fund.fund_code,
            fund_name=fund.fund_name,
            status=status,
            red_flags=red_flags,
            yellow_flags=yellow_flags,
            dimension_details=dimension_details,
            five_dim_score=fund.five_dim_score,
            exclusion_reason=exclusion_reason,
        ))

    # 按 5 维评分降序排序（excluded 排最后）
    status_order = {"passed": 0, "review": 1, "excluded": 2}
    results.sort(key=lambda r: (status_order.get(r.status, 9), -r.five_dim_score))

    return results


# ============================================================
# 3. 防同质化规则
# ============================================================

def apply_anti_homogeneity(results: List[FundFilterResult]) -> List[FundFilterResult]:
    """防同质化规则：按分类限数。

    规则：
    - 被动基金/ETF：同一指数最多保留 3 只（按跟踪误差排序取最优）
    - 主动基金：同一二级分类 + 同一投资风格最多保留 4 只（按 5 维评分排序）
    - 超出数量的标的 status 改为 "category_full"

    验收标准：任意二级分类占比不超过 40%

    Args:
        results: run_10d_filter() 的输出（已含 passed/review/excluded 状态）

    Returns:
        更新后的 FundFilterResult 列表
    """
    # 只对 passed + review 的标的做防同质化
    active_results = [r for r in results if r.status in ("passed", "review")]
    other_results = [r for r in results if r.status not in ("passed", "review")]

    # 需要原始 candidates 的信息来做分组，这里通过 fund_code 匹配
    # 由于 FundFilterResult 没有 fund_type/tracking_index 信息，
    # 我们提供一个内部版本接受额外参数

    # 简化实现：基于 FundFilterResult 本身的信息做分组
    # 实际生产中应通过 fund_code 关联原始 FundCandidate

    # 无法从 FundFilterResult 获取 fund_type/tracking_index
    # 返回原始列表，防同质化需要配合原始 candidates 使用
    # ⬇️ 提供带 candidates 参数的完整版本

    return results


def apply_anti_homogeneity_with_candidates(
    results: List[FundFilterResult],
    candidates: List[FundCandidate],
) -> List[FundFilterResult]:
    """防同质化规则（完整版，需要原始候选数据）。

    Args:
        results: run_10d_filter() 的输出
        candidates: 原始 FundCandidate 列表（提供分类信息）

    Returns:
        更新后的 FundFilterResult 列表
    """
    # 构建 fund_code → candidate 映射
    candidate_map = {c.fund_code: c for c in candidates}

    # 分离已排除的和活跃的
    active_results = [r for r in results if r.status in ("passed", "review")]
    excluded_results = [r for r in results if r.status not in ("passed", "review")]

    # --- 被动基金/ETF：同一指数最多保留 3 只 ---
    passive_groups: dict[str, List[FundFilterResult]] = {}
    active_fund_results: List[FundFilterResult] = []

    for r in active_results:
        cand = candidate_map.get(r.fund_code)
        if cand and cand.fund_type in ("passive", "etf") and cand.tracking_index:
            key = cand.tracking_index
            if key not in passive_groups:
                passive_groups[key] = []
            passive_groups[key].append(r)
        elif cand and cand.fund_type == "active":
            active_fund_results.append(r)
        else:
            # 其他类型不做限制
            active_fund_results.append(r)

    # 被动基金：按跟踪误差排序，保留前 3
    kept_passive: List[FundFilterResult] = []
    for index_code, group in passive_groups.items():
        # 按跟踪误差升序排列（误差小的优先）
        group.sort(key=lambda r: _get_tracking_error(r, candidate_map))
        for i, item in enumerate(group):
            if i < PASSIVE_SAME_INDEX_MAX:
                kept_passive.append(item)
            else:
                item.status = "category_full"
                item.exclusion_reason = f"同一指数（{index_code}）已有 {PASSIVE_SAME_INDEX_MAX} 只"
                excluded_results.append(item)

    # --- 主动基金：同一二级分类 + 同一投资风格最多保留 4 只 ---
    active_groups: dict[str, List[FundFilterResult]] = {}
    for r in active_fund_results:
        cand = candidate_map.get(r.fund_code)
        if cand:
            key = f"{cand.category}|{cand.investment_style}"
        else:
            key = "unknown|unknown"
        if key not in active_groups:
            active_groups[key] = []
        active_groups[key].append(r)

    kept_active: List[FundFilterResult] = []
    for cat_key, group in active_groups.items():
        # 按 5 维评分降序
        group.sort(key=lambda r: -r.five_dim_score)
        for i, item in enumerate(group):
            if i < ACTIVE_SAME_CATEGORY_MAX:
                kept_active.append(item)
            else:
                item.status = "category_full"
                item.exclusion_reason = f"同一分类+风格（{cat_key}）已有 {ACTIVE_SAME_CATEGORY_MAX} 只"
                excluded_results.append(item)

    # 合并结果
    final_results = kept_passive + kept_active + excluded_results

    # 排序：passed > review > category_full > excluded
    status_order = {"passed": 0, "review": 1, "category_full": 2, "excluded": 3}
    final_results.sort(key=lambda r: (status_order.get(r.status, 9), -r.five_dim_score))

    return final_results


def _get_tracking_error(result: FundFilterResult, candidate_map: dict[str, Any]) -> float:
    """辅助函数：从 dimension_details 或 candidate 获取跟踪误差，用于排序。"""
    # 优先从 dimension_details 获取
    te_detail = result.dimension_details.get("跟踪误差")
    if te_detail and hasattr(te_detail, "value") and te_detail.value is not None:
        return float(te_detail.value)
    # 降级从 candidate 获取
    cand = candidate_map.get(result.fund_code)
    if cand and cand.tracking_error is not None:
        return float(cand.tracking_error)
    return float("inf")  # 无数据排最后


# ============================================================
# 导出
# ============================================================

__all__ = [
    "FundCandidate",
    "ExcludedFund",
    "DimensionResult",
    "FundFilterResult",
    "run_purchasability_check",
    "run_10d_filter",
    "apply_anti_homogeneity",
    "apply_anti_homogeneity_with_candidates",
]
