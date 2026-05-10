"""
Agent 决策引擎路由
==================
/api/agent/memory/{user_id}          — 获取用户记忆摘要
/api/agent/preferences               — 保存用户偏好
/api/agent/rules                     — 添加自定义预警规则
/api/agent/rules/{user_id}/{rule_id} — 删除规则
/api/agent/profile/{user_id}         — 读用户画像
/api/agent/profile                   — 保存用户画像
/api/agent/ironies/{user_id}         — 读铁律
/api/agent/ironies                   — 添加铁律
/api/agent/ironies/{user_id}/{id}    — 删除铁律
/api/agent/emotion/{user_id}         — 读情绪摘要
/api/agent/life-events/{user_id}     — 读生活事件
/api/agent/life-events               — 添加生活事件
/api/agent/life-events/{user_id}/{id}— 删除生活事件
/api/agent/pending-insights/{user_id}— 待审记忆队列
/api/agent/insight/approve           — 批准待审记忆
/api/agent/insight/reject            — 拒绝待审记忆
/api/agent/analyze                   — 手动触发分析
/api/agent/signals/{user_id}         — 获取最新信号

P3 高耦合路由 — 依赖 agent_memory, agent_engine, shared_helpers
"""
import json

from fastapi import APIRouter, HTTPException

from config import DATA_DIR
from services.agent_memory import (
    get_preferences, save_preferences, get_decisions, add_decision,
    get_rules, add_rule, remove_rule, get_context, build_memory_summary,
    # 画像/情绪/铁律
    get_profile, save_profile, get_emotion_summary, record_emotion,
    get_ironies, add_irony, remove_irony,
    # 生活事件
    get_life_events, save_life_events, add_life_event, remove_life_event,
    get_upcoming_events,
    # 自动记忆积累（待审队列）
    get_pending_insights, approve_insight, reject_insight,
)
from services.agent_engine import run_analysis_cycle, save_signal_file
from api.shared_helpers import _build_market_context, _build_portfolio_context

router = APIRouter()


@router.get("/api/agent/memory/{user_id}")
def get_agent_memory(user_id: str):
    """获取用户记忆摘要（含画像/情绪/铁律）"""
    return {
        "preferences": get_preferences(user_id),
        "profile": get_profile(user_id),
        "emotion": get_emotion_summary(user_id),
        "ironies": get_ironies(user_id),
        "decisions": get_decisions(user_id, limit=10),
        "rules": get_rules(user_id),
        "context": get_context(user_id),
        "summary": build_memory_summary(user_id),
    }


@router.post("/api/agent/preferences")
def save_agent_preferences(req: dict):
    """保存用户偏好"""
    user_id = req.pop("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return save_preferences(user_id, req)


@router.post("/api/agent/rules")
def add_agent_rule(req: dict):
    """添加自定义预警规则"""
    user_id = req.pop("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return add_rule(user_id, req)


@router.delete("/api/agent/rules/{user_id}/{rule_id}")
def delete_agent_rule(user_id: str, rule_id: str):
    """删除自定义规则"""
    return {"ok": remove_rule(user_id, rule_id)}


# ========== 画像 / 铁律 / 情绪 ==========

@router.get("/api/agent/profile/{user_id}")
def api_get_profile(user_id: str):
    """读用户画像"""
    return get_profile(user_id)


@router.post("/api/agent/profile")
def api_save_profile(req: dict):
    """保存用户画像（增量合并）"""
    user_id = req.pop("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return save_profile(user_id, req)


@router.get("/api/agent/ironies/{user_id}")
def api_get_ironies(user_id: str):
    """读用户铁律列表"""
    return {"ironies": get_ironies(user_id)}


@router.post("/api/agent/ironies")
def api_add_irony(req: dict):
    """添加铁律（user 告诉过 AI 的不可违反事实）"""
    user_id = req.get("userId", "")
    text = req.get("text", "").strip()
    source = req.get("source", "manual")
    if not user_id or not text:
        raise HTTPException(400, "userId and text required")
    return add_irony(user_id, text, source=source)


@router.delete("/api/agent/ironies/{user_id}/{iron_id}")
def api_remove_irony(user_id: str, iron_id: str):
    """删除铁律"""
    return {"ok": remove_irony(user_id, iron_id)}


@router.get("/api/agent/emotion/{user_id}")
def api_get_emotion(user_id: str):
    """读用户情绪摘要"""
    return get_emotion_summary(user_id) or {"dominant": None, "sample_size": 0}


@router.get("/api/agent/life-events/{user_id}")
def api_get_life_events(user_id: str):
    """读生活事件列表 + 未来 30 天即将到的"""
    return {
        "events": get_life_events(user_id),
        "upcoming_30d": get_upcoming_events(user_id, days_ahead=30),
    }


@router.post("/api/agent/life-events")
def api_add_life_event(req: dict):
    """添加一个生活事件"""
    user_id = req.get("userId", "")
    title = req.get("title", "").strip()
    date_str = req.get("date", "").strip()
    if not user_id or not title or not date_str:
        raise HTTPException(400, "userId, title, date required")
    return add_life_event(
        user_id,
        title=title,
        date_str=date_str,
        is_lunar=bool(req.get("is_lunar", False)),
        repeat_yearly=bool(req.get("repeat_yearly", True)),
        remind_days_before=int(req.get("remind_days_before", 7)),
    )


@router.delete("/api/agent/life-events/{user_id}/{event_id}")
def api_remove_life_event(user_id: str, event_id: str):
    """删除生活事件"""
    return {"ok": remove_life_event(user_id, event_id)}


# ========== 自动记忆积累（待审队列）==========

@router.get("/api/agent/pending-insights/{user_id}")
def api_get_pending(user_id: str):
    """读待审记忆队列（前端红点提示用）"""
    items = get_pending_insights(user_id)
    return {"items": items, "count": len(items)}


@router.post("/api/agent/insight/approve")
def api_approve_insight(req: dict):
    """批准一条待审记忆 → 写入对应模块"""
    user_id = req.get("userId", "")
    insight_id = req.get("id", "")
    if not user_id or not insight_id:
        raise HTTPException(400, "userId and id required")
    return approve_insight(user_id, insight_id)


@router.post("/api/agent/insight/reject")
def api_reject_insight(req: dict):
    """拒绝一条待审记忆"""
    user_id = req.get("userId", "")
    insight_id = req.get("id", "")
    if not user_id or not insight_id:
        raise HTTPException(400, "userId and id required")
    return {"ok": reject_insight(user_id, insight_id)}


@router.post("/api/agent/analyze")
async def agent_analyze(req: dict):
    """Agent 决策引擎 — 手动触发分析"""
    user_id = req.get("userId", "default_user")
    force = req.get("force", False)
    model = req.get("model", "deepseek-v4-flash")

    # 收集数据
    market_ctx = _build_market_context()
    portfolio_ctx = _build_portfolio_context(user_id=user_id)
    memory = build_memory_summary(user_id)

    # 收集预警
    alerts = []
    try:
        from services.stock_monitor import scan_all_holdings
        stock_scan = scan_all_holdings(user_id)
        alerts.extend(stock_scan.get("signals", []))
    except Exception:
        pass
    try:
        from services.fund_monitor import scan_all_fund_holdings
        fund_scan = scan_all_fund_holdings(user_id)
        alerts.extend(fund_scan.get("alerts", []))
    except Exception:
        pass
    try:
        from services.agent_memory import check_rules
        rule_alerts = check_rules(user_id, stock_scan if 'stock_scan' in dir() else {})
        alerts.extend(rule_alerts)
    except Exception:
        pass

    # 运行决策引擎
    result = run_analysis_cycle(
        user_id=user_id,
        market_context=market_ctx,
        portfolio_context=portfolio_ctx,
        alerts=alerts,
        memory_summary=memory,
        force_deepseek=force or len(alerts) > 0,
        model=model,
    )

    # 保存信号文件 + 决策日志
    save_signal_file(user_id, result)
    if result.get("source") == "ai":
        add_decision(user_id, {
            "action": "auto_analyze",
            "summary": result.get("analysis", "")[:200],
            "direction": result.get("direction", "neutral"),
            "confidence": result.get("confidence", 0),
            "alerts_count": len(alerts),
            "skill_used": result.get("skill_used", ""),
        })

    # V6 Phase 5: 自动存档到分析历史
    try:
        from services.analysis_history import save_analysis
        analysis_text = result.get("analysis", "") or result.get("reply", "")
        if analysis_text and result.get("source") == "ai":
            save_analysis(user_id, "deepseek", "DeepSeek V3", "full", analysis_text,
                         direction=result.get("direction", "unknown"),
                         confidence=result.get("confidence", 0))
    except Exception as e:
        print(f"[HISTORY] agent analyze 存档失败: {e}")

    return result


@router.get("/api/agent/signals/{user_id}")
def get_agent_signals(user_id: str):
    """获取最新信号文件"""
    fp = DATA_DIR / user_id / "monitor" / "latest_signal.json"
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return {"analysis": "暂无信号数据", "source": "none"}
