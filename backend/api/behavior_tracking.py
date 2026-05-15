"""
行为跟踪 API（Phase 3 Batch 2）
==============================
记录和查询用户交易行为事件
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Any

from services.behavior_recorder import (
    record_behavior_event, get_behavior_events, get_recent_event,
    get_events_by_pattern, get_event_count_today,
)

router = APIRouter(tags=["行为跟踪"])


class TradeDetails(BaseModel):
    """交易详情"""
    code: str
    direction: str  # "buy" or "sell"
    amount: float
    price: Optional[float] = None


class BehaviorEventRequest(BaseModel):
    """记录行为事件请求"""
    trade_details: TradeDetails
    patterns_detected: Optional[list[str]] = None
    market_context: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None


@router.get("/api/behavior/events")
async def list_behavior_events(
    userId: str = "default",
    limit: int = 50,
    offset: int = 0,
    pattern: Optional[str] = None,
):
    """
    获取行为事件列表。
    
    Query Parameters:
    - userId: 用户ID
    - limit: 返回最多条数（默认 50）
    - offset: 偏移量（默认 0）
    - pattern: 按行为模式过滤（可选，如 "chasing_high"）
    """
    if pattern:
        events = get_events_by_pattern(userId, pattern, limit=limit)
    else:
        events = get_behavior_events(userId, limit=limit, offset=offset)
    
    today_count = get_event_count_today(userId)
    
    return {
        "total": len(events),
        "today_count": today_count,
        "events": events,
    }


@router.post("/api/behavior/record")
async def record_event(body: BehaviorEventRequest, userId: str = "default"):
    """
    记录一条行为事件（交易）。
    
    通常由前端交易确认时调用。
    """
    try:
        trade_dict = body.trade_details.dict()
        event = record_behavior_event(
            userId,
            trade_dict,
            patterns_detected=body.patterns_detected,
            market_context=body.market_context,
            metadata=body.metadata,
        )
        
        return {
            "ok": True,
            "event": event,
            "message": f"交易事件已记录：{body.trade_details.code}",
        }
    except Exception as e:
        raise HTTPException(400, f"记录失败: {str(e)}")


@router.get("/api/behavior/recent")
async def get_recent_behavior(userId: str = "default"):
    """获取最近的行为事件"""
    event = get_recent_event(userId)
    if not event:
        return {
            "event": None,
            "message": "还没有记录行为事件",
        }
    
    return {
        "event": event,
    }


@router.get("/api/behavior/stats")
async def get_behavior_stats(userId: str = "default"):
    """获取行为统计"""
    today_count = get_event_count_today(userId)
    events = get_behavior_events(userId, limit=1000)
    
    # 统计各种模式
    pattern_counts = {}
    for event in events:
        for pattern in event.get("patterns_detected", []):
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
    
    return {
        "today_event_count": today_count,
        "total_events_tracked": len(events),
        "pattern_distribution": pattern_counts,
    }


__all__ = ["router"]
