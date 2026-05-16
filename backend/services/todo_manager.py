"""
钱袋子 — 待办管理器（Phase 3 Batch 1）
========================================
管理用户待办任务。

功能：
- create_todo: 创建待办项
- update_todo: 更新待办项
- delete_todo: 删除待办项
- get_todos: 获取待办列表
- mark_done: 标记待办完成
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional, Any
from services.persistence import load_user, save_user

# ---- MODULE_META ----
MODULE_META = {
    "name": "todo_manager",
    "scope": "private",
    "input": ["user_id", "todo_data"],
    "output": "todos",
    "cost": "io",
    "tags": ["待办", "任务管理", "Phase3"],
    "description": "创建和管理用户待办任务",
    "layer": "service",
    "priority": 2,
}


def create_todo(
    user_id: str,
    title: str,
    rule_triggered: str,
    due_by_days: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """
    创建待办项。
    
    Args:
        user_id: 用户ID
        title: 待办标题
        rule_triggered: 触发规则代码（如 "allocation_deviation_gt_15"）
        due_by_days: 多少天后截止（可选）
        metadata: 扩展元数据（可选）
    
    Returns:
        创建的待办项
    """
    user_data = load_user(user_id)
    
    now = datetime.now()
    due_by = None
    if due_by_days:
        due_by = (now + timedelta(days=due_by_days)).isoformat()
    
    todo = {
        "id": f"todo_{uuid.uuid4().hex[:8]}",
        "title": title,
        "rule_triggered": rule_triggered,
        "created_at": now.isoformat(),
        "due_by": due_by,
        "status": "open",
        "metadata": metadata or {},
    }
    
    if "todos" not in user_data:
        user_data["todos"] = []
    
    user_data["todos"].append(todo)
    save_user(user_data)
    
    return todo


def update_todo(
    user_id: str,
    todo_id: str,
    **kwargs
) -> Optional[dict]:
    """
    更新待办项。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
        **kwargs: 要更新的字段（title, status, due_by, metadata 等）
    
    Returns:
        更新后的待办项，如果不存在返回 None
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    for todo in todos:
        if todo.get("id") == todo_id:
            # 更新允许的字段
            for key in ["title", "status", "due_by", "metadata"]:
                if key in kwargs:
                    todo[key] = kwargs[key]
            
            save_user(user_data)
            return todo
    
    return None


def delete_todo(user_id: str, todo_id: str) -> bool:
    """
    删除待办项。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
    
    Returns:
        是否成功删除
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    original_count = len(todos)
    user_data["todos"] = [t for t in todos if t.get("id") != todo_id]
    
    if len(user_data["todos"]) < original_count:
        save_user(user_data)
        return True
    
    return False


def get_todos(
    user_id: str,
    status_filter: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    获取待办列表。
    
    Args:
        user_id: 用户ID
        status_filter: 状态过滤（"open", "completed", "skipped"，None 表示全部）
        limit: 最多返回条数
    
    Returns:
        待办列表
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    if status_filter:
        todos = [t for t in todos if t.get("status") == status_filter]
    
    # 按创建时间倒序
    todos.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    return todos[:limit]


def get_todo_by_id(user_id: str, todo_id: str) -> Optional[dict]:
    """
    按 ID 获取单个待办项。
    
    Returns:
        待办项，不存在则返回 None
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    for todo in todos:
        if todo.get("id") == todo_id:
            return todo
    
    return None


def mark_done(user_id: str, todo_id: str) -> Optional[dict]:
    """
    标记待办项完成。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
    
    Returns:
        更新后的待办项
    """
    return update_todo(user_id, todo_id, status="completed")


def mark_skipped(user_id: str, todo_id: str) -> Optional[dict]:
    """
    标记待办项为已跳过。
    
    Args:
        user_id: 用户ID
        todo_id: 待办ID
    
    Returns:
        更新后的待办项
    """
    return update_todo(user_id, todo_id, status="skipped")


def get_overdue_todos(user_id: str) -> list[dict]:
    """
    获取所有超期的待办项（due_by 已过期且状态为 open）。
    
    Returns:
        超期待办列表
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    now = datetime.now()
    overdue = []
    
    for todo in todos:
        if todo.get("status") == "open" and todo.get("due_by"):
            try:
                due_time = datetime.fromisoformat(todo["due_by"])
                if due_time < now:
                    overdue.append(todo)
            except (ValueError, TypeError):
                pass
    
    return overdue


def get_open_count(user_id: str) -> int:
    """获取未完成的待办数量"""
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    return len([t for t in todos if t.get("status") == "open"])


def clear_old_todos(user_id: str, keep_days: int = 30) -> int:
    """
    清理已完成/已跳过且超过 N 天的待办项。
    
    Args:
        user_id: 用户ID
        keep_days: 保留天数（默认 30）
    
    Returns:
        删除的数量
    """
    user_data = load_user(user_id)
    todos = user_data.get("todos", [])
    
    cutoff = datetime.now() - timedelta(days=keep_days)
    
    new_todos = []
    for todo in todos:
        status = todo.get("status", "open")
        if status == "open":
            # 开放的待办保留
            new_todos.append(todo)
        else:
            # 已完成/已跳过的，如果超过 keep_days 则删除
            try:
                created_time = datetime.fromisoformat(todo.get("created_at", ""))
                if created_time >= cutoff:
                    new_todos.append(todo)
            except (ValueError, TypeError):
                # 解析失败的保留
                new_todos.append(todo)
    
    deleted_count = len(todos) - len(new_todos)
    user_data["todos"] = new_todos
    
    if deleted_count > 0:
        save_user(user_data)
    
    return deleted_count


__all__ = [
    "create_todo",
    "update_todo",
    "delete_todo",
    "get_todos",
    "get_todo_by_id",
    "mark_done",
    "mark_skipped",
    "get_overdue_todos",
    "get_open_count",
    "clear_old_todos",
]
