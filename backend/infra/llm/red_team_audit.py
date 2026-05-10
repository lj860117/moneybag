"""
Red Team Audit — Static LLM output boundary enforcement
=========================================================
Validates LLM outputs against banned patterns and field-level boundaries.
Called by the LLM gateway after every response, and by CI for static analysis.

Functions:
  - audit_response(text) → (passed: bool, violations: list[str])
  - audit_field(field_name, value) → (passed: bool, violations: list[str])
  - get_banned_patterns() → list of compiled regex patterns

Target: >99% interception rate for prohibited content.

Design doc: docs/design/04-ai-interface.md §4 (字段级硬边界)
Invariant #3: All LLM calls through infra/llm/gateway.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple


# ============================================================
# Banned Patterns (from 04-ai-interface.md §4)
# ============================================================

# Raw pattern strings (shared with CI for validation)
BANNED_PATTERN_STRINGS: List[str] = [
    # Direct buy/sell recommendations
    r"建议(你|您)?(现在|立即|马上)?(买|卖|加仓|减仓|调仓|清仓)",
    r"(应该|可以|需要)(买入|卖出|加仓|减仓|清仓)",
    r"我(的建议|建议)是.{0,10}(买|卖|仓)",
    # Price predictions
    r"(预计|预测|将会|即将)(涨|跌|反弹|下跌|上涨|暴跌|涨到|跌到)",
    r"(未来|下周|下月|明天|近期|一个月)(会|将)\s*(涨|跌|上涨|下跌|反弹|涨到|跌到)",
    # Specific position sizing
    r"(具体|精确)?\s*(仓位|比例|金额).{0,5}[:：]?\s*\d+",
    r"\d+%\s*(仓位|的仓|比例)",
    # Guarantees / promises
    r"(保本|保息|稳赚|包赚|必赚|零风险)",
    r"(一定|肯定|绝对)(能|会|不会)(赚|亏|涨|跌)",
    # Specific stock recommendations
    r"(推荐|建议)(买入?|关注)\s*[一-鿿]{2,6}",
    r"(可以|建议|推荐)\s*(买入?|加仓)\s*[A-Z0-9]{5,6}",
]

# Compiled patterns (cached for performance)
BANNED_PATTERNS: List[re.Pattern[str]] = [
    re.compile(p) for p in BANNED_PATTERN_STRINGS
]


# ============================================================
# Field-Level Boundaries (from 04-ai-interface.md §4 table)
# ============================================================

# Each field has: allowed_keywords (must have at least one) + banned_keywords
FIELD_BOUNDARIES: Dict[str, Dict[str, List[str]]] = {
    "market_environment": {
        "banned": ["看好", "看空", "建议加仓", "建议减仓", "强烈推荐"],
        "max_length": 200,
    },
    "portfolio_health_issue": {
        "banned": ["将会涨", "将会跌", "预计反弹", "明天会", "下周会"],
        "max_length": 150,
    },
    "risk_inventory_risk": {
        "banned": ["我认为", "我判断", "我预测", "个人观点"],
        "max_length": 100,
    },
    "direction_notes": {
        "banned": ["推荐买入", "建议满仓", "全仓", "具体标的", "仓位比例"],
        "max_length": 200,
    },
}


# ============================================================
# Public API
# ============================================================

def audit_response(text: str) -> Tuple[bool, List[str]]:
    """Audit an LLM response text against all banned patterns.

    Args:
        text: The LLM response text to audit.

    Returns:
        (passed, violations) where:
          - passed: True if no violations found
          - violations: list of matched pattern descriptions
    """
    if not text:
        return True, []

    violations: List[str] = []

    for i, pattern in enumerate(BANNED_PATTERNS):
        match = pattern.search(text)
        if match:
            violations.append(
                f"Pattern[{i}] matched: '{match.group()}' "
                f"(rule: {BANNED_PATTERN_STRINGS[i][:50]})"
            )

    passed = len(violations) == 0
    return passed, violations


def audit_field(field_name: str, value: str) -> Tuple[bool, List[str]]:
    """Audit a specific output field against its boundary rules.

    Args:
        field_name: one of the keys in FIELD_BOUNDARIES
        value: the field value to check

    Returns:
        (passed, violations)
    """
    if not value:
        return True, []

    boundary = FIELD_BOUNDARIES.get(field_name)
    if boundary is None:
        # Unknown field — pass by default (no boundary defined)
        return True, []

    violations: List[str] = []

    # Check banned keywords
    banned_words = boundary.get("banned", [])
    for word in banned_words:
        if word in value:
            violations.append(f"Field '{field_name}' contains banned word: '{word}'")

    # Check max length
    max_len = boundary.get("max_length", 500)
    if len(value) > max_len:
        violations.append(
            f"Field '{field_name}' exceeds max length: {len(value)} > {max_len}"
        )

    # Also run general banned patterns
    for i, pattern in enumerate(BANNED_PATTERNS):
        match = pattern.search(value)
        if match:
            violations.append(
                f"Field '{field_name}' matched banned pattern: '{match.group()}'"
            )

    passed = len(violations) == 0
    return passed, violations


def get_banned_patterns() -> List[str]:
    """Return raw banned pattern strings (for CI validation and display)."""
    return list(BANNED_PATTERN_STRINGS)


def get_field_boundaries() -> Dict[str, Dict[str, object]]:
    """Return field boundary definitions (for documentation/CI)."""
    return dict(FIELD_BOUNDARIES)


def compute_interception_rate(test_cases: List[Tuple[str, bool]]) -> float:
    """Compute interception rate from test cases.

    Args:
        test_cases: list of (text, should_be_blocked) tuples

    Returns:
        Rate of correct interceptions (0.0-1.0).
        should_be_blocked=True and we blocked it → correct.
        should_be_blocked=False and we passed it → correct.
    """
    if not test_cases:
        return 1.0

    correct = 0
    for text, should_block in test_cases:
        passed, _ = audit_response(text)
        blocked = not passed
        if blocked == should_block:
            correct += 1

    return correct / len(test_cases)


__all__ = [
    "BANNED_PATTERNS",
    "BANNED_PATTERN_STRINGS",
    "FIELD_BOUNDARIES",
    "audit_response",
    "audit_field",
    "get_banned_patterns",
    "get_field_boundaries",
    "compute_interception_rate",
]
