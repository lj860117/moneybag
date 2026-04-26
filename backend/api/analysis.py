"""
分析历史 API
=============
分析记录查询、最新分析、详情、多源对比、外部分析接收。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

router = APIRouter(tags=["分析历史"])


@router.get("/api/analysis/history")
def api_analysis_history(userId: str = "", source: str = "", type: str = "", days: int = 30):
    """查询分析历史列表"""
    from services.analysis_history import get_analysis_history
    records = get_analysis_history(userId or "default", source=source, analysis_type=type, days=days)
    return {"records": records, "total": len(records)}


@router.get("/api/analysis/latest")
def api_analysis_latest(userId: str = ""):
    """各来源最新分析（对比视图）"""
    from services.analysis_history import get_latest_by_source
    return get_latest_by_source(userId or "default")


@router.get("/api/analysis/detail/{record_id}")
def api_analysis_detail(record_id: str, userId: str = ""):
    """获取单条分析完整内容"""
    from services.analysis_history import get_analysis_detail
    return get_analysis_detail(userId or "default", record_id)


@router.post("/api/analysis/compare")
def api_analysis_compare(req: dict = {}):
    """多源对比（获取各来源最新+可选强制刷新DS）"""
    user_id = req.get("userId", "default")
    from services.analysis_history import get_latest_by_source
    return get_latest_by_source(user_id)


@router.post("/api/analysis/external")
def api_analysis_external(req: dict = {}):
    """接收外部分析（Claude/WorkBuddy 自动推送入口）"""
    from services.analysis_history import save_analysis
    uid = req.get("userId", "default")
    text = req.get("analysis", "")
    if not text:
        return {"ok": False, "error": "analysis 不能为空"}
    return save_analysis(
        user_id=uid,
        source=req.get("source", "claude"),
        source_label=req.get("sourceLabel", "Claude"),
        analysis_type=req.get("type", "full"),
        analysis_text=text,
        direction=req.get("direction", "unknown"),
        confidence=int(req.get("confidence", 0)),
        metadata=req.get("metadata"),
    )
