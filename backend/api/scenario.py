"""
情景分析 API
=============
预设情景列表、情景分析（支持缓存）、自定义情景。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter

router = APIRouter(tags=["情景分析"])


@router.get("/api/scenarios")
def api_scenarios_list():
    """列出所有预设情景"""
    from services.scenario_engine import list_scenarios
    return {"scenarios": list_scenarios()}


@router.get("/api/scenario/{scenario_id}")
def api_scenario_analyze(scenario_id: str, userId: str = ""):
    """预设情景分析（优先凌晨预计算缓存）"""
    try:
        from services.precomputed_cache import get_precomputed
        cached = get_precomputed(f"scenario_{scenario_id}")
        if cached:
            cached["from_cache"] = True
            return cached
    except Exception:
        pass
    from services.scenario_engine import analyze_scenario
    return analyze_scenario(scenario_id=scenario_id, user_id=userId)


@router.post("/api/scenario/custom")
def api_scenario_custom(req: dict = {}):
    """自定义情景分析"""
    from services.scenario_engine import analyze_scenario
    text = req.get("text", "")
    user_id = req.get("userId", "")
    if not text:
        return {"error": "需要 text 参数描述假设情景"}
    return analyze_scenario(custom_text=text, user_id=user_id)
