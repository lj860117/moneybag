"""
管家 Steward & 周报路由
========================
/api/steward/ask       — 管家决策 Pipeline
/api/steward/briefing  — 管家每日简报（快速版，0 次 LLM）
/api/steward/review    — 管家收盘复盘
/api/regime            — 当前市场状态（4 类分类）
/api/llm-usage         — LLM 调用用量统计
/api/weekly-report     — 生成/获取周报
/api/weekly-report/history — 历史周报列表

P3 高耦合路由 — 依赖 steward, regime_engine, llm_gateway, weekly_report
"""
from fastapi import APIRouter, HTTPException

from services.steward import get_steward
from services.regime_engine import classify as classify_regime
from services.llm_gateway import llm_usage
from services.weekly_report import generate as generate_weekly, get_history as get_weekly_history

router = APIRouter()


@router.post("/api/steward/ask")
def steward_ask(req: dict):
    """管家决策 — 完整 Pipeline 流程"""
    user_id = req.get("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    question = req.get("question", "综合分析")
    pipeline = req.get("pipeline", None)
    steward = get_steward()
    return steward.ask(user_id, question, pipeline_override=pipeline)


@router.get("/api/steward/briefing")
def steward_briefing(userId: str = ""):
    """管家每日简报（快速版，0 次 LLM）"""
    if not userId:
        raise HTTPException(400, "userId required")
    steward = get_steward()
    return steward.briefing(userId)


@router.get("/api/steward/briefing-history")
def steward_briefing_history(userId: str = "", days: int = 7):
    """往期晨报列表（MB-005）"""
    if not userId:
        raise HTTPException(400, "userId required")
    steward = get_steward()
    items = steward.briefing_history(userId, days)
    return {"history": items, "count": len(items)}


@router.get("/api/steward/review")
async def steward_review(userId: str = ""):
    """管家收盘复盘（完整版，含体检）— 30s 硬超时保护"""
    if not userId:
        raise HTTPException(400, "userId required")
    import asyncio
    steward = get_steward()
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, steward.review, userId),
            timeout=30.0,
        )
        return result
    except asyncio.TimeoutError:
        print(f"[STEWARD] review timeout 30s for {userId}")
        return {
            "direction": "neutral",
            "confidence": 0,
            "conclusion": "分析超时，请稍后重试",
            "modules_status": {"called": 0, "succeeded": 0, "failed": 0, "skipped": 0},
            "modules_errors": {"timeout": "review 整体超时 30s"},
            "elapsed": 30.0,
            "error": "steward review 计算超时(30s)，可能是模块数据源响应慢，请稍后重试。",
        }


@router.get("/api/regime")
def get_regime():
    """获取当前市场状态（4 类分类）"""
    return classify_regime()


@router.get("/api/llm-usage")
def get_llm_usage(userId: str = ""):
    """LLM 调用用量统计（按用户×模块）"""
    return llm_usage(userId)


@router.get("/api/weekly-report")
def weekly_report_api(userId: str = "", weeks_ago: int = 0):
    """生成/获取周报"""
    if not userId:
        return {"error": "userId required"}
    return generate_weekly(userId, weeks_ago)


@router.get("/api/weekly-report/history")
def weekly_report_history(userId: str = "", limit: int = 4):
    """获取历史周报列表"""
    if not userId:
        return {"reports": []}
    return {"reports": get_weekly_history(userId, limit)}


# ============================================================
# 家庭 CFO 首页聚合接口
# ============================================================

@router.get("/api/market-panorama")
def market_panorama():
    """市场全景 — 一次请求返回市场全貌数据

    聚合：市场温度 + 热点板块 + 重要新闻 + 资产判断 + 机构观点
    全部纯规则/缓存，不调 LLM。
    """
    from services.market_panorama import generate_market_panorama
    return generate_market_panorama()


@router.get("/api/cfo-summary")
def cfo_summary(userId: str = ""):
    """家庭 CFO 今日面板 — 一次请求返回首页全部数据

    聚合：净资产 + 今日提醒 + 配置 + 情绪提醒 + 本周待办
    全部纯规则计算，不调 LLM，保证 <2s 响应。
    """
    from services.cfo_dashboard import generate_cfo_summary
    return generate_cfo_summary(userId or "default")
