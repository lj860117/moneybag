"""
Rule Engine — Domain business logic for allocation, risk, and decisions
========================================================================
Contains allocation matrices, risk thresholds, and decision-guard rules.

Reference: docs/design/03-rule-engine.md

Invariant #11: All interfaces are Protocols in domain/protocols/; no imports from infra/.
"""
from domain.rule_engine.defaults import (
    AllocationDefaults,
    RiskDefaults,
    ScoringDefaults,
    RebalanceDefaults,
    StaleDataDefaults,
    GlidePathDefaults,
    DeviationThresholdDefaults,
    FundFilterDefaults,
    IndustryDeviationDefaults,
    BehaviorDefaults,
)
from domain.rule_engine.checklist import run_checklist
from domain.rule_engine.glide_path_rules import (
    calculate_target_allocation,
    check_style_deviation,
    AllocationTarget,
    DeviationAlert,
    OverrideOutOfRangeError,
)
from domain.rule_engine.deviation_thresholds import (
    get_tolerance,
    should_trigger_extreme_confirmation,
    apply_dynamic_filter,
)
from domain.rule_engine.fund_filter_rules import (
    run_purchasability_check,
    run_10d_filter,
    apply_anti_homogeneity,
    apply_anti_homogeneity_with_candidates,
    FundCandidate,
    FundFilterResult,
    DimensionResult,
    ExcludedFund,
)

__all__ = [
    # M1-M6 defaults
    "AllocationDefaults",
    "RiskDefaults",
    "ScoringDefaults",
    "RebalanceDefaults",
    "StaleDataDefaults",
    # M7+ Batch 2 defaults
    "GlidePathDefaults",
    "DeviationThresholdDefaults",
    # M7+ Batch 3 defaults
    "FundFilterDefaults",
    # M7+ Batch 4 defaults
    "IndustryDeviationDefaults",
    # M7+ Batch 5 defaults
    "BehaviorDefaults",
    # M1-M6 checklist
    "run_checklist",
    # M7+ Batch 2: Glide Path
    "calculate_target_allocation",
    "check_style_deviation",
    "AllocationTarget",
    "DeviationAlert",
    "OverrideOutOfRangeError",
    # M7+ Batch 2: 动态阈值
    "get_tolerance",
    "should_trigger_extreme_confirmation",
    "apply_dynamic_filter",
    # M7+ Batch 3: 10 维筛选
    "run_purchasability_check",
    "run_10d_filter",
    "apply_anti_homogeneity",
    "apply_anti_homogeneity_with_candidates",
    "FundCandidate",
    "FundFilterResult",
    "DimensionResult",
    "ExcludedFund",
]
