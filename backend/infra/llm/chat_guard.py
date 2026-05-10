"""
Chat Guard — Anchor-required chat enforcement (no free-form chat)
==================================================================
Enforces the "受限追问" (restricted follow-up) model from 04-ai-interface.md §5.

Rules:
  1. Every chat must have an anchor_id (a data point: report/alert/holding)
  2. Maximum 5 rounds per anchor
  3. "我该怎么办" type questions get FALLBACK_RESPONSE (fixed phrasing)
  4. No-anchor /api/chat is deprecated (M3 下线)

Design doc: docs/design/04-ai-interface.md §5
Invariant #3: All LLM calls through infra/llm/gateway.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


# ============================================================
# Constants
# ============================================================

MAX_ROUNDS_PER_ANCHOR: int = 5

# Patterns that trigger the fixed response (user asking for action advice)
ACTION_SEEKING_PATTERNS: list[str] = [
    r"(我|那)?该怎么办",
    r"(我|那)?应该(怎么|如何)(做|操作|处理)",
    r"你(建议|觉得|推荐)我(怎么|如何)",
    r"给我(一个|个)(方案|建议|操作)",
    r"(具体|直接)告诉我(怎么|该)",
    r"(买|卖|加仓|减仓)哪个",
    r"推荐(一下|几个|一个)",
]

_ACTION_PATTERNS_COMPILED = [re.compile(p) for p in ACTION_SEEKING_PATTERNS]

# Fixed response for action-seeking questions (design doc §5.1)
FALLBACK_RESPONSE: str = (
    "我只能帮你解释数据说明了什么问题，具体怎么操作需要你自己决定。\n"
    "关于这个问题，你可以参考：\n"
    "- 7 点决策检查清单（/api/decisions/checklist）\n"
    "- 相关金融常识（知识库）\n"
    "- 三视角独立评估（M4 上线）\n\n"
    "投资决策的责任始终在你自己。"
)

# Response for exceeding round limit
ROUND_LIMIT_RESPONSE: str = (
    "本轮追问已达上限（{max_rounds} 轮），建议回到主页查看完整报告，"
    "或者从新的数据锚点重新开始对话。"
)

# Response for no-anchor chat attempt (deprecated endpoint)
NO_ANCHOR_RESPONSE: str = (
    "自由对话入口已关闭（M3 下线）。请从具体的报告、提醒或持仓页面进入对话。\n"
    "这样我能更准确地帮你解读数据。"
)


# ============================================================
# Public API
# ============================================================

def validate_chat_request(
    anchor_id: Optional[str],
    round_num: int = 1,
) -> Tuple[bool, str]:
    """Validate a chat request before processing.

    Args:
        anchor_id: the data anchor (report/alert/holding) this chat is about.
                   None or empty means no anchor (deprecated free-form chat).
        round_num: which round of this anchor conversation (1-based).

    Returns:
        (allowed, error_message):
          - allowed=True: proceed with LLM call
          - allowed=False: return error_message to user directly
    """
    # Rule 1: Must have anchor
    if not anchor_id or not anchor_id.strip():
        return False, NO_ANCHOR_RESPONSE

    # Rule 2: Round limit
    if round_num > MAX_ROUNDS_PER_ANCHOR:
        return False, ROUND_LIMIT_RESPONSE.format(max_rounds=MAX_ROUNDS_PER_ANCHOR)

    return True, ""


def check_action_seeking(user_message: str) -> Tuple[bool, str]:
    """Check if user is seeking action advice (triggers fixed response).

    Args:
        user_message: the user's chat message

    Returns:
        (is_action_seeking, fallback_response):
          - True: return FALLBACK_RESPONSE instead of calling LLM
          - False: proceed normally
    """
    if not user_message:
        return False, ""

    for pattern in _ACTION_PATTERNS_COMPILED:
        if pattern.search(user_message):
            return True, FALLBACK_RESPONSE

    return False, ""


def is_anchor_valid(anchor_id: str) -> bool:
    """Check if an anchor ID follows valid format.

    Valid formats:
      - report_{date}_{user}  (e.g., report_20260510_leijiang)
      - alert_{type}_{id}     (e.g., alert_deviation_001)
      - holding_{code}        (e.g., holding_600519)
      - checklist_{id}        (e.g., checklist_rev_123)

    Returns True if format is valid (existence check is caller's job).
    """
    if not anchor_id:
        return False

    valid_prefixes = ("report_", "alert_", "holding_", "checklist_", "review_")
    return any(anchor_id.startswith(p) for p in valid_prefixes)


__all__ = [
    "MAX_ROUNDS_PER_ANCHOR",
    "FALLBACK_RESPONSE",
    "NO_ANCHOR_RESPONSE",
    "ROUND_LIMIT_RESPONSE",
    "validate_chat_request",
    "check_action_seeking",
    "is_anchor_valid",
]
