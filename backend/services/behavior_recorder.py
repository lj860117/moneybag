"""
钱袋子 — 行为事件记录器（Phase 3 Batch 1）
==============================================
记录用户交易行为和检测到的行为模式。

功能：
- record_behavior_event: 记录单次交易事件
- get_behavior_events: 获取用户行为事件历史
- clear_old_events: 清理超过 500 条的事件
- get_recent_event: 获取最近的事件
"""
from datetime import datetime
from typing import Optional, Any
from services.persistence import load_user, save_user

# ---- MODULE_META ----
MODULE_META = {
    "name": "behavior_recorder",
    "scope": "private",
    "input": ["user_id", "trade_details"],
    "output": "behavior_events",
    "cost": "io",
    "tags": ["行为监控", "交易事件", "Phase3"],
    "description": "记录和查询用户交易行为事件",
    "layer": "service",
    "priority": 2,
}


def record_behavior_event(
    user_id: str,
    trade_details: dict,
    patterns_detected: Optional[list[str]] = None,
    market_context: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """
    记录单次行为事件（通常是交易）。
    
    Args:
        user_id: 用户ID
        trade_details: 交易详情 {code, direction, amount, price}
        patterns_detected: 检测到的行为模式列表（可选）
        market_context: 市场背景数据（可选）
        metadata: 扩展元数据（可选）
    
    Returns:
        记录的事件数据
    """
    user_data = load_user(user_id)
    
    event = {
        "timestamp": datetime.now().isoformat(),
        "event_type": "trade_executed",
        "trade_details": trade_details or {},
        "patterns_detected": patterns_detected or [],
        "market_context": market_context or {},
        "metadata": metadata or {},
    }
    
    # Phase 3: 添加到事件列表
    if "behavior_events" not in user_data:
        user_data["behavior_events"] = []
    
    user_data["behavior_events"].append(event)
    
    # 清理超过 500 条的旧事件
    if len(user_data["behavior_events"]) > 500:
        user_data["behavior_events"] = user_data["behavior_events"][-500:]
    
    save_user(user_data)
    return event


def get_behavior_events(
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    获取用户行为事件历史。
    
    Args:
        user_id: 用户ID
        limit: 返回最多条数（默认 50）
        offset: 偏移量（默认 0）
    
    Returns:
        行为事件列表（按时间倒序）
    """
    user_data = load_user(user_id)
    events = user_data.get("behavior_events", [])
    
    # 按时间倒序返回
    events_sorted = sorted(events, key=lambda x: x.get("timestamp", ""), reverse=True)
    
    # 分页
    return events_sorted[offset : offset + limit]


def get_recent_event(user_id: str) -> Optional[dict]:
    """
    获取最近的一条事件。
    
    Returns:
        最近的事件，如果没有则返回 None
    """
    user_data = load_user(user_id)
    events = user_data.get("behavior_events", [])
    
    if not events:
        return None
    
    # 返回最后一条（假设列表是按时间追加的）
    return events[-1]


def get_events_by_pattern(
    user_id: str,
    pattern: str,
    limit: int = 50,
) -> list[dict]:
    """
    按行为模式过滤事件。
    
    Args:
        user_id: 用户ID
        pattern: 行为模式类型（如 "chasing_high", "fomo"）
        limit: 最多返回条数
    
    Returns:
        包含该模式的事件列表
    """
    user_data = load_user(user_id)
    events = user_data.get("behavior_events", [])
    
    # 过滤包含该模式的事件
    filtered = [e for e in events if pattern in e.get("patterns_detected", [])]
    
    # 按时间倒序
    filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return filtered[:limit]


def get_event_count_today(user_id: str) -> int:
    """
    获取今天记录的事件数量。
    
    Returns:
        今天的事件数
    """
    user_data = load_user(user_id)
    events = user_data.get("behavior_events", [])
    
    today = datetime.now().date()
    count = 0
    
    for event in events:
        try:
            event_date = datetime.fromisoformat(event.get("timestamp", "")).date()
            if event_date == today:
                count += 1
        except (ValueError, TypeError):
            pass
    
    return count


def clear_old_events(user_id: str, keep_days: int = 90) -> int:
    """
    清理超过 N 天的旧事件。
    
    Args:
        user_id: 用户ID
        keep_days: 保留天数（默认 90）
    
    Returns:
        删除的事件数
    """
    from datetime import timedelta
    
    user_data = load_user(user_id)
    events = user_data.get("behavior_events", [])
    
    cutoff = datetime.now() - timedelta(days=keep_days)
    
    new_events = []
    for event in events:
        try:
            event_time = datetime.fromisoformat(event.get("timestamp", ""))
            if event_time >= cutoff:
                new_events.append(event)
        except (ValueError, TypeError):
            # 保留解析失败的事件
            new_events.append(event)
    
    deleted_count = len(events) - len(new_events)
    user_data["behavior_events"] = new_events
    
    if deleted_count > 0:
        save_user(user_data)
    
    return deleted_count


__all__ = [
    "record_behavior_event",
    "get_behavior_events",
    "get_recent_event",
    "get_events_by_pattern",
    "get_event_count_today",
    "clear_old_events",
]
