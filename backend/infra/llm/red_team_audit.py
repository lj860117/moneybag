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
from typing import Any, Dict, List, Tuple


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
    # 注：北向净流入的口径检查**不在这个列表里** —— 它无法用单条正则表达，
    #     改由 _north_caliber_violation() 按句判定，详见该函数注释。
]

# Compiled patterns (cached for performance)
BANNED_PATTERNS: List[re.Pattern[str]] = [
    re.compile(p) for p in BANNED_PATTERN_STRINGS
]


# ============================================================
# 北向净流入口径检查（按句判定，不用正则先行断言）
# ============================================================
#
# 背景：自 2024-08-19 起沪深交易所停止披露北向【日频净买入】、改为按季度公布，
# Tushare moneyflow_hsgt 的 north_money 现为【当日成交额】。因此任何
# "北向/外资 …净流入|净流出|净买入|净卖出 X 亿" 的**断言**都是凭空编造。
#
# ⚠️ 为什么不用负向先行断言正则（曾经用过，被证明会惩罚诚实，勿改回）：
#   `(北向|外资).{0,15}(净流入…)(?!.{0,25}(不可得|无法|…))` 这种写法
#   **只能向后看**，而诚实表述里限定语经常出现在触发词**前面**，例如：
#       "交易所已停止披露北向净买入总额"          ← 限定语在前
#       "该数据不可得，因此无法给出外资净流入金额"   ← 限定语在前
#       "不要编造北向净买入金额"                  ← 我们自己写进 prompt 的指令
#       "我不能告诉你外资净流入了多少，因为交易所不再公布"  ← 模型正确拒答
#   Python `re` **不支持变长 lookbehind**，所以正则方案在原理上就走不通
#   （实测 11 条诚实表述里误拦 7 条）。
#   一个会把"模型正确拒答"和"我们自己的 prompt 指令"判为违规的守卫，
#   比没有守卫更危险 —— 它在惩罚诚实，而且回归时容易被误读成"守卫生效"。
#
# 现在的做法：按句切分，只要**同一句内任意位置**出现合规线索就放行。
# 按句（而非整段）判定的关键作用：防止在末尾加一句免责声明就把前面的幻觉洗白。

_NORTH_TRIGGER = re.compile(
    r"(北向|外资|陆股通|沪股通|深股通)[^。；\n]{0,15}(净流入|净流出|净买入|净卖出)"
)

# 合规线索：出现在同一句的**任意位置**（前/后都算）即视为诚实表述。
# `不要|勿|别|禁止` 是为了放行我们自己写进 prompt 的禁止指令
# （如"不要编造北向净买入金额"），必须保留。
_NORTH_CLUE = re.compile(
    r"不可得|不可用|未披露|不再披露|停止披露|按季度|季度披露|数据缺失|拿不到|看不到|"
    r"无法|不能|没有这项|不构成|不可信|禁止|不要|勿|别|抱歉|给不了|无从|不予"
)

# 句子切分符**不含逗号**：中文一句话里逗号很多，按逗号切会把
# "该数据不可得，因此无法给出外资净流入金额" 切成两半，后半句失去线索 → 误拦复现。
_SENTENCE_SPLIT = re.compile(r"[。；\n]")


def _north_caliber_violation(text: str) -> Tuple[bool, str]:
    """按句判定北向净流入断言。返回 (是否违规, 违规句)。"""
    if not text:
        return False, ""
    for sent in _SENTENCE_SPLIT.split(text):
        if not _NORTH_TRIGGER.search(sent):
            continue
        if _NORTH_CLUE.search(sent):
            continue  # 同句内有合规线索 → 诚实表述，放行
        return True, sent.strip()
    return False, ""


# ============================================================
# Field-Level Boundaries (from 04-ai-interface.md §4 table)
# ============================================================

# Each field has: allowed_keywords (must have at least one) + banned_keywords
FIELD_BOUNDARIES: Dict[str, Dict[str, Any]] = {
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

    # 北向净流入口径（按句判定，见 _north_caliber_violation 注释）
    north_bad, north_sent = _north_caliber_violation(text)
    if north_bad:
        violations.append(
            f"North caliber violation: '{north_sent[:60]}' "
            f"(rule: 北向净买入自 2024-08-19 起交易所停止日频披露，"
            f"任何净流入/净流出断言均为编造；如为诚实表述请在同一句内说明"
            f"数据不可得)"
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
    max_len: int = int(boundary.get("max_length", 500))
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
