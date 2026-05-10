"""
7-Point Decision Checklist Engine — Pre-trade self-check scoring
=================================================================
Implements the 7-point checklist from 07-decision-guard.md §2-3.

Each item scores 0-10. Total max = 70. Pass threshold = 42 (60%).
Consumes thresholds from defaults.py (invariant #12: thresholds live there).

The 7 items (from design doc 03-rule-engine.md §7 + 07-decision-guard.md §2):
  1. emergency_fund     — 应急金是否 ≥ 6 个月
  2. insurance_complete — 重疾/寿险/医疗/意外 四大险是否齐全
  3. concentration_safe — 加仓后单资产 ≤ 25% 且单行业 ≤ 40%
  4. money_long_term   — 这笔钱未来 3 年内不需要用
  5. reason_rational   — 买入理由不含反向信号（不追高/不跟风）
  6. not_all_in        — 单次加仓不超过可投资金的 20%
  7. cooldown_met      — 距上次调仓 ≥ 30 天（冷静期）

All functions are pure (no I/O, no side effects).
Invariant #10: domain/ has zero imports from infra/.

Design doc: docs/design/07-decision-guard.md §2-3
            docs/design/03-rule-engine.md §7
"""
from __future__ import annotations

from typing import List

from domain.models.checklist import (
    ChecklistItem,
    ChecklistResult,
    CHECKLIST_MAX_SCORE,
    CHECKLIST_PASS_THRESHOLD,
)
from domain.rule_engine.defaults import RiskDefaults


# ============================================================
# Public API
# ============================================================

def run_checklist(
    emergency_months: float = 6.0,
    insurance_count: int = 4,
    single_asset_pct: float = 0.0,
    single_industry_pct: float = 0.0,
    money_needed_within_3y: bool = False,
    red_flag_count: int = 0,
    yellow_flag_count: int = 0,
    trade_pct_of_investable: float = 0.0,
    days_since_last_trade: int = 60,
) -> ChecklistResult:
    """Evaluate the 7-point pre-trade decision checklist.

    Args:
        emergency_months: months of expenses covered by emergency fund
        insurance_count: number of insurance types held (max 4: 重疾/寿险/医疗/意外)
        single_asset_pct: post-trade single asset concentration (0.0-1.0)
        single_industry_pct: post-trade single industry concentration (0.0-1.0)
        money_needed_within_3y: True if this money will be needed in < 3 years
        red_flag_count: number of red-signal buy reasons selected (from evaluate_reasons)
        yellow_flag_count: number of yellow-signal buy reasons selected
        trade_pct_of_investable: this trade amount as % of investable assets (0.0-1.0)
        days_since_last_trade: calendar days since last portfolio adjustment

    Returns:
        ChecklistResult with 7 items scored and overall pass/fail.
    """
    items: List[ChecklistItem] = [
        _check_emergency_fund(emergency_months),
        _check_insurance(insurance_count),
        _check_concentration(single_asset_pct, single_industry_pct),
        _check_money_long_term(money_needed_within_3y),
        _check_reason_rational(red_flag_count, yellow_flag_count),
        _check_not_all_in(trade_pct_of_investable),
        _check_cooldown(days_since_last_trade),
    ]

    total_score = sum(item.score for item in items)
    red_light_count = sum(1 for item in items if item.is_red_light)
    passed = total_score >= CHECKLIST_PASS_THRESHOLD

    # Determine recommendation
    if passed and red_light_count == 0:
        recommendation = "✅ 检查通过，可以执行交易"
    elif passed and red_light_count <= 2:
        recommendation = f"⚠️ 检查通过但有 {red_light_count} 盏红灯，请三思"
    elif not passed and red_light_count >= 3:
        recommendation = f"🚫 检查未通过（{total_score}/{CHECKLIST_MAX_SCORE}），强烈建议不要执行"
    else:
        recommendation = f"⚠️ 检查未通过（{total_score}/{CHECKLIST_MAX_SCORE}），建议暂缓执行"

    blocked = not passed

    return ChecklistResult(
        items=items,
        total_score=total_score,
        max_score=CHECKLIST_MAX_SCORE,
        passed=passed,
        red_light_count=red_light_count,
        recommendation=recommendation,
        blocked=blocked,
    )


# ============================================================
# Individual check functions (each returns ChecklistItem)
# ============================================================

def _check_emergency_fund(emergency_months: float) -> ChecklistItem:
    """Item 1: 应急金是否 ≥ 6 个月.

    Scoring:
      >= 6 months: 10 (fully passes)
      4-6 months: 7 (acceptable but tight)
      2-4 months: 4 (concerning)
      < 2 months: 1 (red light)
    """
    if emergency_months >= 6.0:
        score = 10
        detail = f"应急金充足（{emergency_months:.0f} 个月）"
    elif emergency_months >= 4.0:
        score = 7
        detail = f"应急金尚可（{emergency_months:.1f} 个月），建议补到 6 个月"
    elif emergency_months >= 2.0:
        score = 4
        detail = f"应急金不足（{emergency_months:.1f} 个月），建议优先补充"
    else:
        score = 1
        detail = f"应急金严重不足（{emergency_months:.1f} 个月），不建议投资"

    is_red = score <= 3
    return ChecklistItem(
        item_id="emergency_fund",
        label_zh="应急金是否充足（≥6个月）",
        score=score,
        passed=score >= 6,
        is_red_light=is_red,
        detail=detail,
    )


def _check_insurance(insurance_count: int) -> ChecklistItem:
    """Item 2: 重疾/寿险/医疗/意外 四大险是否齐全.

    Scoring:
      4/4: 10
      3/4: 7
      2/4: 4
      <=1: 2 (red light)
    """
    if insurance_count >= 4:
        score = 10
        detail = "四大险齐全"
    elif insurance_count == 3:
        score = 7
        detail = f"保险覆盖 {insurance_count}/4，缺 {4 - insurance_count} 项"
    elif insurance_count == 2:
        score = 4
        detail = f"保险覆盖 {insurance_count}/4，建议补全保障"
    else:
        score = 2
        detail = f"保险覆盖 {insurance_count}/4，保障严重不足"

    is_red = score <= 3
    return ChecklistItem(
        item_id="insurance_complete",
        label_zh="四大险是否齐全（重疾/寿险/医疗/意外）",
        score=score,
        passed=score >= 6,
        is_red_light=is_red,
        detail=detail,
    )


def _check_concentration(
    single_asset_pct: float,
    single_industry_pct: float,
) -> ChecklistItem:
    """Item 3: 加仓后单资产 ≤ 25% 且单行业 ≤ 40%.

    Uses RiskDefaults thresholds.
    Scoring:
      Both OK: 10
      One slightly over (within 5%): 6
      One significantly over: 3 (red light)
      Both over: 1 (red light)
    """
    asset_limit = RiskDefaults.SINGLE_STOCK_MAX       # 0.25
    industry_limit = RiskDefaults.SINGLE_INDUSTRY_MAX  # 0.40

    asset_over = single_asset_pct > asset_limit
    industry_over = single_industry_pct > industry_limit

    if not asset_over and not industry_over:
        score = 10
        detail = f"集中度安全（单资产 {single_asset_pct*100:.0f}%，单行业 {single_industry_pct*100:.0f}%）"
    elif asset_over and industry_over:
        score = 1
        detail = (
            f"双重超标！单资产 {single_asset_pct*100:.0f}%>{asset_limit*100:.0f}%，"
            f"单行业 {single_industry_pct*100:.0f}%>{industry_limit*100:.0f}%"
        )
    elif asset_over:
        # How much over?
        over_pct = single_asset_pct - asset_limit
        if over_pct <= 0.05:
            score = 6
            detail = f"单资产略超（{single_asset_pct*100:.0f}%，上限 {asset_limit*100:.0f}%）"
        else:
            score = 3
            detail = f"单资产严重超标（{single_asset_pct*100:.0f}%，上限 {asset_limit*100:.0f}%）"
    else:  # industry_over
        over_pct = single_industry_pct - industry_limit
        if over_pct <= 0.05:
            score = 6
            detail = f"单行业略超（{single_industry_pct*100:.0f}%，上限 {industry_limit*100:.0f}%）"
        else:
            score = 3
            detail = f"单行业严重超标（{single_industry_pct*100:.0f}%，上限 {industry_limit*100:.0f}%）"

    is_red = score <= 3
    return ChecklistItem(
        item_id="concentration_safe",
        label_zh="集中度是否安全（单资产≤25%/单行业≤40%）",
        score=score,
        passed=score >= 6,
        is_red_light=is_red,
        detail=detail,
    )


def _check_money_long_term(money_needed_within_3y: bool) -> ChecklistItem:
    """Item 4: 这笔钱未来 3 年内是否要用.

    Binary:
      Not needed: 10
      Needed: 2 (red light)
    """
    if not money_needed_within_3y:
        return ChecklistItem(
            item_id="money_long_term",
            label_zh="这笔钱 3 年内不需要用",
            score=10,
            passed=True,
            is_red_light=False,
            detail="确认这笔钱不会在 3 年内动用",
        )
    else:
        return ChecklistItem(
            item_id="money_long_term",
            label_zh="这笔钱 3 年内不需要用",
            score=2,
            passed=False,
            is_red_light=True,
            detail="⚠️ 这笔钱 3 年内可能需要用，不适合投入高风险资产",
        )


def _check_reason_rational(red_flag_count: int, yellow_flag_count: int) -> ChecklistItem:
    """Item 5: 买入理由是否理性（不追高/不跟风）.

    Consumes red/yellow flag counts from M3 W1 evaluate_reasons().
    Scoring:
      0 red + 0 yellow: 10
      0 red + 1-2 yellow: 7
      1 red: 4
      2+ red: 1 (red light)
    """
    if red_flag_count == 0 and yellow_flag_count == 0:
        score = 10
        detail = "买入理由理性，无冲动信号"
    elif red_flag_count == 0 and yellow_flag_count <= 2:
        score = 7
        detail = f"有 {yellow_flag_count} 个黄灯信号，请留意"
    elif red_flag_count == 1:
        score = 4
        detail = f"有 1 个红灯信号（追高/跟风），三思"
    else:
        score = 1
        detail = f"有 {red_flag_count} 个红灯信号，极大概率是冲动决策"

    is_red = score <= 3
    return ChecklistItem(
        item_id="reason_rational",
        label_zh="买入理由是否理性（不追高/不跟风）",
        score=score,
        passed=score >= 6,
        is_red_light=is_red,
        detail=detail,
    )


def _check_not_all_in(trade_pct_of_investable: float) -> ChecklistItem:
    """Item 6: 单次加仓不超过可投资金的 20%.

    Scoring:
      <= 10%: 10 (very conservative)
      10-20%: 8 (acceptable)
      20-30%: 5 (borderline)
      30-50%: 3 (red light territory)
      > 50%: 1 (red light)
    """
    pct = trade_pct_of_investable

    if pct <= 0.10:
        score = 10
        detail = f"单次仓位 {pct*100:.0f}%，非常稳健"
    elif pct <= 0.20:
        score = 8
        detail = f"单次仓位 {pct*100:.0f}%，可接受"
    elif pct <= 0.30:
        score = 5
        detail = f"单次仓位 {pct*100:.0f}%，偏高，建议分批"
    elif pct <= 0.50:
        score = 3
        detail = f"单次仓位 {pct*100:.0f}%，过高！建议分 2-3 批"
    else:
        score = 1
        detail = f"单次仓位 {pct*100:.0f}%，接近 All-in！强烈建议分批"

    is_red = score <= 3
    return ChecklistItem(
        item_id="not_all_in",
        label_zh="单次加仓不超过可投资金 20%",
        score=score,
        passed=score >= 6,
        is_red_light=is_red,
        detail=detail,
    )


def _check_cooldown(days_since_last_trade: int) -> ChecklistItem:
    """Item 7: 距上次调仓 ≥ 30 天（冷静期）.

    Scoring:
      >= 30 days: 10
      15-29 days: 7
      7-14 days: 4
      < 7 days: 2 (red light)
    """
    if days_since_last_trade >= 30:
        score = 10
        detail = f"距上次调仓 {days_since_last_trade} 天，冷静期充足"
    elif days_since_last_trade >= 15:
        score = 7
        detail = f"距上次调仓 {days_since_last_trade} 天，尚可"
    elif days_since_last_trade >= 7:
        score = 4
        detail = f"距上次调仓仅 {days_since_last_trade} 天，操作过于频繁"
    else:
        score = 2
        detail = f"距上次调仓仅 {days_since_last_trade} 天，极度频繁交易！"

    is_red = score <= 3
    return ChecklistItem(
        item_id="cooldown_met",
        label_zh="冷静期是否满足（≥30天）",
        score=score,
        passed=score >= 6,
        is_red_light=is_red,
        detail=detail,
    )


__all__ = [
    "run_checklist",
]
