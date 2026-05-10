"""
Run Checklist Use Case — Mode A pre-trade checklist orchestration
==================================================================
Orchestrates domain rule_engine (checklist) and domain services (decision_guard_service)
to evaluate a 7-point pre-trade checklist.

Dependency rule: use_cases/ -> domain/ -> infra/ (never backward to api/).

Design doc: docs/design/07-decision-guard.md §3
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from domain.models.checklist import ChecklistResult
from domain.models.decision import BuyReason
from domain.rule_engine.checklist import run_checklist
from domain.services.decision_guard_service import evaluate_reasons


def run_pre_trade_checklist(
    reason_ids: List[str],
    custom_reason_text: str = "",
    emergency_months: float = 6.0,
    insurance_count: int = 4,
    single_asset_pct: float = 0.0,
    single_industry_pct: float = 0.0,
    money_needed_within_3y: bool = False,
    trade_pct_of_investable: float = 0.0,
    days_since_last_trade: int = 60,
) -> Tuple[ChecklistResult, List[str]]:
    """Run the 7-point pre-trade decision checklist.

    Orchestration steps:
      1. Parse buy reasons from IDs + custom text
      2. Evaluate reasons to get red/yellow flag counts (from M3 W1)
      3. Run 7-point checklist with all context
      4. Generate warnings for failed items
      5. Return (result, warnings)

    Args:
        reason_ids: predefined reason IDs the user selected
        custom_reason_text: optional free-text reason
        emergency_months: months of emergency fund coverage
        insurance_count: number of insurance types (0-4)
        single_asset_pct: post-trade single asset concentration (0.0-1.0)
        single_industry_pct: post-trade single industry concentration (0.0-1.0)
        money_needed_within_3y: True if money needed within 3 years
        trade_pct_of_investable: trade amount as fraction of investable assets
        days_since_last_trade: days since last portfolio adjustment

    Returns:
        Tuple of (ChecklistResult, warning_messages).
        If result.blocked is True, the trade should not proceed.
    """
    # 1. Parse reasons
    reasons: List[BuyReason] = []
    for rid in reason_ids:
        reasons.append(BuyReason.from_predefined(rid))
    if custom_reason_text.strip():
        reasons.append(BuyReason.from_custom(custom_reason_text.strip()))

    # 2. Evaluate reasons → red/yellow counts (consumes M3 W1 service)
    red_count, yellow_count, signal_messages = evaluate_reasons(reasons)

    # 3. Run checklist
    result = run_checklist(
        emergency_months=emergency_months,
        insurance_count=insurance_count,
        single_asset_pct=single_asset_pct,
        single_industry_pct=single_industry_pct,
        money_needed_within_3y=money_needed_within_3y,
        red_flag_count=red_count,
        yellow_flag_count=yellow_count,
        trade_pct_of_investable=trade_pct_of_investable,
        days_since_last_trade=days_since_last_trade,
    )

    # 4. Generate warnings
    warnings: List[str] = []

    # Add signal messages from reason evaluation
    warnings.extend(signal_messages)

    # Add per-item warnings for failed items
    for item in result.items:
        if item.is_red_light:
            warnings.append(f"🚨 {item.label_zh}：{item.detail}")
        elif not item.passed:
            warnings.append(f"⚠️ {item.label_zh}：{item.detail}")

    # Add overall recommendation
    if result.blocked:
        warnings.append(f"🚫 总分 {result.total_score}/{result.max_score}，未达 60% 通过线，建议暂缓交易")

    return result, warnings


def get_checklist_items_description() -> List[Dict[str, str]]:
    """Return descriptions of all 7 checklist items for UI display.

    Used by frontend to show what will be checked before the user fills in data.
    """
    return [
        {
            "item_id": "emergency_fund",
            "label_zh": "应急金是否充足（≥6个月）",
            "description": "确保应急储备金覆盖至少 6 个月生活支出",
        },
        {
            "item_id": "insurance_complete",
            "label_zh": "四大险是否齐全（重疾/寿险/医疗/意外）",
            "description": "重疾险、定期寿险、百万医疗、意外险四大保障",
        },
        {
            "item_id": "concentration_safe",
            "label_zh": "集中度是否安全（单资产≤25%/单行业≤40%）",
            "description": "加仓后单只股票不超过总仓位 25%，单行业不超 40%",
        },
        {
            "item_id": "money_long_term",
            "label_zh": "这笔钱 3 年内不需要用",
            "description": "投入股市的钱应是 3 年内不会动用的闲钱",
        },
        {
            "item_id": "reason_rational",
            "label_zh": "买入理由是否理性（不追高/不跟风）",
            "description": "买入理由不含'涨得好/热搜/朋友推荐/怕错过'等冲动信号",
        },
        {
            "item_id": "not_all_in",
            "label_zh": "单次加仓不超过可投资金 20%",
            "description": "分批建仓，单次操作金额不超过可投资金的 20%",
        },
        {
            "item_id": "cooldown_met",
            "label_zh": "冷静期是否满足（≥30天）",
            "description": "距离上次调仓/加仓至少 30 天，避免频繁操作",
        },
    ]


__all__ = [
    "run_pre_trade_checklist",
    "get_checklist_items_description",
]
