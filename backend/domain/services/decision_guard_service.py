"""
Decision Guard Service — Decision quality scoring (Mode B: post-trade review)
===============================================================================
Implements decision quality score calculation from buy reasons and trade context.

Functions:
  - compute_quality_score() — calculate 4-dimension quality score from reasons
  - evaluate_reasons() — classify reasons and detect red/yellow signals
  - get_predefined_reasons() — return all available predefined reasons for UI

All functions are pure (no side effects, no I/O). Storage via caller (use_case layer).

Quality formula: score = f(reason_clarity, info_source, risk_awareness, time_horizon)
  - Each dimension: 0-25 points
  - Total: 0-100
  - Red flags: each -10 from total, capped at floor 10
  - Yellow flags: each -5 from total

Design doc: docs/design/07-decision-guard.md §4-5
Invariant #10: domain/ layer has zero imports from infra/.
Invariant #9: domain/services do not cross-import (only domain/protocols).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from domain.models.decision import (
    BuyReason,
    BuyReasonCategory,
    DecisionQualityScore,
    DecisionReview,
    PredefinedReason,
    PREDEFINED_REASONS,
    REASON_BY_ID,
    SignalLevel,
)


# ============================================================
# Public API
# ============================================================

def get_predefined_reasons() -> List[Dict[str, object]]:
    """Return all predefined reasons for frontend multi-select rendering.

    Returns list of dicts with: id, label_zh, category, signal.
    Used by frontend to render the buy-reason multi-select component.
    """
    return [
        {
            "id": r.id,
            "label_zh": r.label_zh,
            "category": r.category.value,
            "signal": r.signal.value,
        }
        for r in PREDEFINED_REASONS
    ]


def evaluate_reasons(reasons: List[BuyReason]) -> Tuple[int, int, List[str]]:
    """Classify reasons and count red/yellow signals.

    Returns:
        (red_count, yellow_count, signal_messages)
    """
    red_count = 0
    yellow_count = 0
    messages: List[str] = []

    for reason in reasons:
        if reason.signal == SignalLevel.RED.value:
            red_count += 1
            label = _get_reason_label(reason)
            messages.append(f"🚨 红灯：{label}")
        elif reason.signal == SignalLevel.YELLOW.value:
            yellow_count += 1
            label = _get_reason_label(reason)
            messages.append(f"⚠️ 黄灯：{label}")

    return red_count, yellow_count, messages


def compute_quality_score(
    reasons: List[BuyReason],
    has_emergency_fund: bool = True,
    concentration_ok: bool = True,
    money_not_needed_3y: bool = True,
    days_since_last_trade: int = 60,
) -> DecisionQualityScore:
    """Compute decision quality score from reasons and context.

    Four dimensions (each 0-25):
      1. reason_clarity: how specific/clear the reasons are
      2. info_source: quality of information sources (data-driven vs gossip)
      3. risk_awareness: whether user is aware of risk context
      4. time_horizon: appropriate time horizon (not short-term)

    Penalties:
      - Each red flag: -10 from total (floor at 10)
      - Each yellow flag: -5 from total (floor at 10)

    Args:
        reasons: user's selected buy reasons
        has_emergency_fund: whether checklist item 1 passes (>= 6 months)
        concentration_ok: whether checklist item 3 passes (no single asset >25%)
        money_not_needed_3y: whether checklist item 4 passes (money not needed in 3y)
        days_since_last_trade: days since user's last adjustment
    """
    if not reasons:
        return DecisionQualityScore(
            reason_clarity=5,
            info_source=5,
            risk_awareness=5,
            time_horizon=5,
            total=20,
            red_flags=0,
            yellow_flags=0,
            grade="poor",
        )

    # ---- Dimension 1: Reason Clarity (0-25) ----
    reason_clarity = _score_reason_clarity(reasons)

    # ---- Dimension 2: Info Source Quality (0-25) ----
    info_source = _score_info_source(reasons)

    # ---- Dimension 3: Risk Awareness (0-25) ----
    risk_awareness = _score_risk_awareness(
        has_emergency_fund=has_emergency_fund,
        concentration_ok=concentration_ok,
        money_not_needed_3y=money_not_needed_3y,
    )

    # ---- Dimension 4: Time Horizon (0-25) ----
    time_horizon = _score_time_horizon(
        reasons=reasons,
        days_since_last_trade=days_since_last_trade,
    )

    # Count signals
    red_flags, yellow_flags, _ = evaluate_reasons(reasons)

    # Compute raw total
    raw_total = reason_clarity + info_source + risk_awareness + time_horizon

    # Apply penalties
    penalty = red_flags * 10 + yellow_flags * 5
    total = max(10, raw_total - penalty)

    # Grade
    grade = _compute_grade(total)

    return DecisionQualityScore(
        reason_clarity=reason_clarity,
        info_source=info_source,
        risk_awareness=risk_awareness,
        time_horizon=time_horizon,
        total=total,
        red_flags=red_flags,
        yellow_flags=yellow_flags,
        grade=grade,
    )


# ============================================================
# Private scoring helpers
# ============================================================

def _get_reason_label(reason: BuyReason) -> str:
    """Get display label for a reason."""
    if reason.reason_id:
        pr = REASON_BY_ID.get(reason.reason_id)
        if pr:
            return pr.label_zh
        return reason.reason_id
    return reason.custom_text[:30] if reason.custom_text else "未指定"


def _score_reason_clarity(reasons: List[BuyReason]) -> int:
    """Score how clear/specific the user's reasons are (0-25).

    Scoring logic:
      - Base: 10 points for having at least 1 reason
      - +5 for having 2+ distinct categories (shows multi-angle thinking)
      - +5 for having at least 1 green-signal reason (rational reason exists)
      - +5 for providing custom text supplement (extra thought)
    """
    score = 10  # base for having reasons

    # Multi-category bonus
    categories = set(r.category for r in reasons if r.category)
    if len(categories) >= 2:
        score += 5

    # Has a healthy/green reason
    has_green = any(r.signal == SignalLevel.GREEN.value for r in reasons)
    if has_green:
        score += 5

    # Custom text shows extra thought
    has_custom = any(r.custom_text for r in reasons)
    if has_custom:
        score += 5

    return min(25, score)


def _score_info_source(reasons: List[BuyReason]) -> int:
    """Score quality of information sources (0-25).

    Scoring logic:
      - Start at 20
      - Fundamental/valuation reasons: +5 (data-driven)
      - Emotional/FOMO: -8 per instance (gossip-driven)
      - Follow (friend recommend): -10 per instance (no personal analysis)
    """
    score = 20

    for reason in reasons:
        cat = reason.category
        if cat == BuyReasonCategory.FUNDAMENTAL.value:
            score += 5
        elif cat == BuyReasonCategory.EMOTIONAL.value:
            score -= 8
        elif cat == BuyReasonCategory.FOLLOW.value:
            score -= 10

    return max(0, min(25, score))


def _score_risk_awareness(
    has_emergency_fund: bool,
    concentration_ok: bool,
    money_not_needed_3y: bool,
) -> int:
    """Score risk awareness from checklist context (0-25).

    Each risk factor contributes:
      - Emergency fund OK: +8
      - Concentration OK: +8
      - Money not needed in 3y: +9
    """
    score = 0
    if has_emergency_fund:
        score += 8
    if concentration_ok:
        score += 8
    if money_not_needed_3y:
        score += 9
    return min(25, score)


def _score_time_horizon(
    reasons: List[BuyReason],
    days_since_last_trade: int,
) -> int:
    """Score time horizon appropriateness (0-25).

    Scoring logic:
      - Base: 15 if days_since_last_trade >= 30 (not overtrading)
      - Base: 5 if < 30 days (frequent trading = short-term mindset)
      - +5 if has DCA plan reason (systematic, long-term)
      - +5 if has allocation_gap reason (portfolio-level thinking)
      - -5 if has momentum_chase (short-term)
    """
    # Base from trading frequency
    if days_since_last_trade >= 30:
        score = 15
    else:
        score = 5

    # Reason-based adjustments
    for reason in reasons:
        if reason.reason_id == "dca_plan":
            score += 5
        elif reason.reason_id == "allocation_gap":
            score += 5
        elif reason.reason_id == "momentum_chase":
            score -= 5

    return max(0, min(25, score))


def _compute_grade(total: int) -> str:
    """Map total score to grade label."""
    if total >= 80:
        return "excellent"
    elif total >= 60:
        return "good"
    elif total >= 40:
        return "mediocre"
    else:
        return "poor"


__all__ = [
    "get_predefined_reasons",
    "evaluate_reasons",
    "compute_quality_score",
]
