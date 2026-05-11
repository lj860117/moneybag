"""行为风控 API -- 开关状态 / 活跃干预查询 | Batch 6 前端集成"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from domain.rule_engine.behavior_intervention_rules import (
    is_guard_enabled, set_guard_enabled, get_active_interventions,
    override_intervention, ActiveIntervention,
)

router = APIRouter(tags=["行为风控"])


class GuardToggleBody(BaseModel):
    enabled: bool
    reason: str = ""


@router.get("/api/behavior/guard-status")
async def guard_status(userId: str = "default"):
    """行为风控总开关状态 + 活跃干预数量"""
    enabled = is_guard_enabled(userId)
    active = get_active_interventions(userId)
    return {
        "enabled": enabled,
        "active_count": len(active),
        "status_icon": "on" if enabled else "off",
        "tip": "行为风控已启用，交易前自动检测偏差" if enabled else "行为风控已关闭，所有交易不受行为约束",
    }


@router.post("/api/behavior/guard-toggle")
async def guard_toggle(body: GuardToggleBody, userId: str = "default"):
    """切换行为风控总开关"""
    set_guard_enabled(userId, body.enabled, body.reason or "用户手动切换")
    return {
        "ok": True,
        "enabled": body.enabled,
        "message": "行为风控已启用" if body.enabled else "行为风控已关闭",
    }


@router.get("/api/behavior/active-interventions")
async def active_interventions(userId: str = "default"):
    """查询当前生效的干预列表"""
    active = get_active_interventions(userId)
    return {
        "total": len(active),
        "interventions": [_serialize(inv) for inv in active],
    }


@router.post("/api/behavior/override/{index}")
async def override(index: int, userId: str = "default"):
    """用户二次确认后覆盖指定干预"""
    ok = override_intervention(userId, index)
    if not ok:
        raise HTTPException(404, "干预不存在或已过期")
    return {"ok": True, "message": "已覆盖该干预"}


def _serialize(inv: ActiveIntervention) -> dict:
    return {
        "pattern": inv.trigger_evidence.pattern_type,
        "description": inv.trigger_evidence.description,
        "action": inv.rule.action_type,
        "message": f"[仅提醒] {inv.trigger_evidence.description}",
        "triggered_at": inv.triggered_at.isoformat(),
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
        "status": inv.status,
        "is_degraded": True,  # §九验证1 = not_supported
    }
