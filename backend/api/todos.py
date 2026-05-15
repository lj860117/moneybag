"""
待办 API（Phase 3 Batch 2）
===========================
获取、创建、更新、删除用户待办项
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.todo_manager import (
    create_todo, get_todos, update_todo, delete_todo,
    get_todo_by_id, mark_done, mark_skipped, get_open_count,
)

router = APIRouter(tags=["待办"])


class TodoCreateRequest(BaseModel):
    """创建待办请求"""
    title: str
    rule_triggered: str
    due_by_days: Optional[int] = None
    metadata: Optional[dict] = None


class TodoUpdateRequest(BaseModel):
    """更新待办请求"""
    title: Optional[str] = None
    status: Optional[str] = None
    due_by: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/api/todos")
async def list_todos(
    userId: str = "default",
    status: Optional[str] = None,
    limit: int = 100,
):
    """
    获取待办列表。
    
    Query Parameters:
    - userId: 用户ID
    - status: 过滤状态（open/completed/skipped），可选
    - limit: 返回最多条数（默认 100）
    """
    todos = get_todos(userId, status_filter=status, limit=limit)
    open_count = get_open_count(userId)
    
    return {
        "total": len(todos),
        "open_count": open_count,
        "todos": todos,
    }


@router.post("/api/todos")
async def create_new_todo(body: TodoCreateRequest, userId: str = "default"):
    """创建新待办项"""
    try:
        todo = create_todo(
            userId,
            body.title,
            body.rule_triggered,
            due_by_days=body.due_by_days,
            metadata=body.metadata,
        )
        return {
            "ok": True,
            "todo": todo,
            "message": f"待办 '{body.title}' 创建成功",
        }
    except Exception as e:
        raise HTTPException(400, f"创建失败: {str(e)}")


@router.get("/api/todos/{todo_id}")
async def get_single_todo(todo_id: str, userId: str = "default"):
    """获取单个待办项详情"""
    todo = get_todo_by_id(userId, todo_id)
    if not todo:
        raise HTTPException(404, "待办项不存在")
    return {"todo": todo}


@router.put("/api/todos/{todo_id}")
async def update_single_todo(
    todo_id: str,
    body: TodoUpdateRequest,
    userId: str = "default",
):
    """更新待办项"""
    update_data = body.dict(exclude_unset=True)
    updated = update_todo(userId, todo_id, **update_data)
    
    if not updated:
        raise HTTPException(404, "待办项不存在")
    
    return {
        "ok": True,
        "todo": updated,
        "message": "待办项已更新",
    }


@router.post("/api/todos/{todo_id}/done")
async def mark_todo_done(todo_id: str, userId: str = "default"):
    """标记待办项完成"""
    todo = mark_done(userId, todo_id)
    if not todo:
        raise HTTPException(404, "待办项不存在")
    
    return {
        "ok": True,
        "todo": todo,
        "message": f"待办 '{todo['title']}' 已完成",
    }


@router.post("/api/todos/{todo_id}/skip")
async def skip_todo(todo_id: str, userId: str = "default"):
    """标记待办项为已跳过"""
    todo = mark_skipped(userId, todo_id)
    if not todo:
        raise HTTPException(404, "待办项不存在")
    
    return {
        "ok": True,
        "todo": todo,
        "message": f"待办 '{todo['title']}' 已跳过",
    }


@router.delete("/api/todos/{todo_id}")
async def delete_single_todo(todo_id: str, userId: str = "default"):
    """删除待办项"""
    ok = delete_todo(userId, todo_id)
    if not ok:
        raise HTTPException(404, "待办项不存在")
    
    return {
        "ok": True,
        "message": "待办项已删除",
    }


__all__ = ["router"]
