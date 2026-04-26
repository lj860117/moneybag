"""
钱袋子 — Agent 记忆系统（重导出 shim）

M2 W1 拆分：所有实现已迁移到:
  - domain/services/user_preference_service.py  (用户偏好/画像/铁律/情绪/生活事件/待审洞察)
  - domain/rule_engine/decision_archive.py      (决策档案/规则/上下文/自动提取)

本文件仅做重导出，保持所有 `from services.agent_memory import X` 不报错。
绞杀者模式：调用方逐步改为直接 import 新模块后，本文件将删除。

build_memory_summary: 已降级为 stub（返回空串）。
  设计决策: LLM 记忆注入是过拟合温床，不再往 prompt 塞历史。
  详见 docs/design/02-code-audit.md §4.2
"""

# ---- V4 底座：MODULE_META（保留向后兼容）----
MODULE_META = {
    "name": "agent_memory",
    "scope": "private",
    "input": ["user_id"],
    "output": "memory_summary",
    "cost": "cpu",
    "tags": ["记忆", "偏好", "决策日志", "规则"],
    "description": "Agent记忆系统：偏好/决策日志/自定义规则/上下文接力（重导出 shim）",
    "layer": "data",
    "priority": 1,
}

# ============================================================
# Re-exports from domain/services/user_preference_service.py
# ============================================================
from domain.services.user_preference_service import (  # noqa: F401
    # Family config
    FAMILY_ADMIN,
    _route_to_admin,
    _user_memory_dir,
    # Preferences
    get_preferences,
    save_preferences,
    # Profile
    DEFAULT_PROFILE,
    get_profile,
    save_profile,
    # Ironies
    get_ironies,
    add_irony,
    remove_irony,
    # Emotion
    tag_emotion,
    record_emotion,
    get_emotion_summary,
    # Life events
    get_life_events,
    save_life_events,
    add_life_event,
    remove_life_event,
    get_upcoming_events,
    # Pending insights
    get_pending_insights,
    add_pending_insight,
    approve_insight,
    reject_insight,
)

# ============================================================
# Re-exports from domain/rule_engine/decision_archive.py
# ============================================================
from domain.rule_engine.decision_archive import (  # noqa: F401
    # Decisions
    get_decisions,
    get_archived_decisions,
    get_archive_summaries,
    add_decision,
    archive_old_decisions,
    summarize_archive_month,
    track_decision_result,
    # Rules
    get_rules,
    add_rule,
    remove_rule,
    check_rules,
    # Context
    get_context,
    save_context,
    # Auto-extract
    add_to_extract_queue,
    get_extract_queue,
    clear_extract_queue,
    auto_extract_insight,
    batch_extract_for_user,
)


# ============================================================
# LLM Memory: build_memory_summary → STUB (design decision)
# ============================================================
# 原函数 160+ 行，把用户画像/偏好/铁律/情绪/决策/归档摘要全部注入 LLM prompt。
# 设计决策（02-code-audit.md §4.2）：LLM 记忆注入是过拟合温床，删除。
# 人可以记住自己犯过的错，但别让 LLM 读它再犯一次。

def build_memory_summary(user_id: str) -> str:
    """[DEPRECATED] LLM memory injection removed (overfitting risk).

    Returns empty string. Callers should stop injecting this into prompts.
    See docs/design/02-code-audit.md §4.2 for rationale.
    """
    return ""
