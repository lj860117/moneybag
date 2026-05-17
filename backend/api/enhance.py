"""
DeepSeek 智能增强路由
=====================
/api/ai-comment/stock    — 单只股票 AI 点评
/api/ai-comment/fund     — 单只基金 AI 点评
/api/assets/advice       — 存款智能建议
/api/ds/asset-diagnosis  — AI 资产诊断
/api/daily-focus         — 首页'今日关注'

P3 高耦合路由 — 依赖 ds_enhance, shared_helpers
"""
from fastapi import APIRouter, HTTPException

from services.ds_enhance import (
    analyze_idle_cash, comment_single_stock, comment_single_fund,
    generate_daily_focus, diagnose_user_assets,
)
from api.shared_helpers import _build_market_context

router = APIRouter()


@router.get("/api/ai-comment/stock")
def ai_comment_stock(code: str, name: str = "", score: float = 0,
                     pe: float = 0, roe: float = 0, gross_margin: float = 0):
    """单只股票 AI 点评（按需，用户点击时调用）"""
    comment = comment_single_stock(code, name, {
        "score": score, "pe": pe, "roe": roe, "gross_margin": gross_margin,
    })
    return {"code": code, "name": name, "comment": comment}


@router.get("/api/ai-comment/fund")
def ai_comment_fund(code: str, name: str = "", score: float = 0,
                    fee: str = "", r3m: float = None, r6m: float = None,
                    r1y: float = None, r3y: float = None):
    """单只基金 AI 点评（按需，用户点击时调用）"""
    returns = {}
    if r3m is not None: returns["3m"] = r3m
    if r6m is not None: returns["6m"] = r6m
    if r1y is not None: returns["1y"] = r1y
    if r3y is not None: returns["3y"] = r3y
    comment = comment_single_fund(code, name, {
        "score": score, "fee": fee, "returns": returns,
    })
    return {"code": code, "name": name, "comment": comment}


@router.post("/api/assets/advice")
def get_asset_advice(req: dict):
    """存款智能建议 — DeepSeek 分析闲置资金配置"""
    try:
        return analyze_idle_cash(
            cash_amount=float(req.get("cashAmount", 0)),
            monthly_expense=float(req.get("monthlyExpense", 0)),
            risk_profile=req.get("riskProfile", "稳健型"),
        )
    except Exception as e:
        print(f"[ASSETS-ADVICE] error: {e}")
        return {"advice": "暂无建议，请稍后重试。", "source": "fallback", "error": str(e)}


@router.post("/api/ds/asset-diagnosis")
def get_asset_diagnosis(req: dict):
    """AI 资产诊断 — DeepSeek 全量分析用户资产结构"""
    user_id = req.get("userId", "")
    if not user_id:
        raise HTTPException(400, "userId required")
    return diagnose_user_assets(user_id)


@router.get("/api/daily-focus")
def get_daily_focus():
    """首页'今日关注' — DeepSeek 个性化生成"""
    market_ctx = _build_market_context()
    return generate_daily_focus(market_ctx)
