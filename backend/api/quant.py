"""
量化引擎 API
=============
ML 选股、因子 IC 检验、蒙特卡洛模拟、AI 预测、遗传因子、组合优化、
强化学习仓位、LLM 因子生成。

Design doc: docs/design/12-framework-refactor.md §二
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.ml_stock_screen import ml_stock_screen
from services.factor_ic import compute_factor_ic, compute_ic_decay
from services.monte_carlo import monte_carlo_single, monte_carlo_portfolio, monte_carlo_compare

router = APIRouter(tags=["量化引擎"])


# ---- ML 选股 ----

@router.get("/api/stock-screen/ml")
def get_ml_stock_screen(top_n: int = 30):
    """LightGBM 多因子选股：ML增强版"""
    return ml_stock_screen(top_n)


# ---- 因子 IC 检验 ----

@router.get("/api/factor-ic")
def api_factor_ic(forward_days: int = 20, pool_size: int = 200):
    """因子 IC 检验：验证30因子中哪些真正预测未来收益"""
    return compute_factor_ic(forward_days=forward_days, pool_size=pool_size)


@router.get("/api/factor-ic/decay")
def api_factor_ic_decay(pool_size: int = 150):
    """IC 衰减曲线：因子在不同预测周期下的效果"""
    return compute_ic_decay(pool_size=pool_size)


# ---- 蒙特卡洛模拟 ----

@router.get("/api/monte-carlo/{code}")
def api_monte_carlo_single(
    code: str,
    simulations: int = 5000,
    horizon_days: int = 250,
    initial: float = 10000,
    discipline: bool = True,
):
    """单只股票蒙特卡洛模拟：概率分布预测"""
    return monte_carlo_single(
        code=code, simulations=simulations, horizon_days=horizon_days,
        initial_investment=initial, apply_discipline=discipline,
    )


@router.post("/api/monte-carlo/portfolio")
def api_monte_carlo_portfolio(req: dict):
    """组合蒙特卡洛模拟"""
    holdings = req.get("holdings", [])
    simulations = req.get("simulations", 3000)
    horizon_days = req.get("horizon_days", 250)
    initial = req.get("initial", 100000)
    discipline = req.get("discipline", True)
    return monte_carlo_portfolio(
        holdings=holdings, simulations=simulations,
        horizon_days=horizon_days, initial_investment=initial,
        apply_discipline=discipline,
    )


@router.get("/api/monte-carlo/compare/{code}")
def api_monte_carlo_compare(code: str, simulations: int = 3000, horizon_days: int = 250):
    """纪律 vs 无纪律蒙特卡洛对比"""
    return monte_carlo_compare(code=code, simulations=simulations, horizon_days=horizon_days)


# ---- AI 预测引擎（已废弃 — M5 W3 返回 410 Gone）----
# 设计决策: 10-roadmap.md §四 废弃时间表
# 原因: ai_predictor v1 已被 decision_maker_v2 + RAG 取代
# 前端迁移: app.js line 2508/2528 仍调用，需更新前端消除调用

_GONE_BODY = {
    "status": "gone",
    "code": 410,
    "message": "此接口已废弃。AI 预测功能已整合到决策复盘系统（/api/decisions/review）。",
    "migration_guide": "使用 POST /api/decisions/review 提交交易复盘，"
                       "或 GET /api/decisions/monthly-report/{user_id} 查看决策质量报告。",
    "deprecated_since": "2026-05-15",
    "removed_at": "2026-07-01",
}


@router.get("/api/ai-predict/{code}")
def api_ai_predict(code: str, days: int = 5):
    """[已废弃] AI 预测单只股票未来 N 天涨跌 — 返回 410 Gone"""
    return JSONResponse(status_code=410, content=_GONE_BODY)


@router.get("/api/ai-predict/portfolio/{user_id}")
def api_ai_predict_portfolio(user_id: str, days: int = 5):
    """[已废弃] AI 预测用户持仓组合 — 返回 410 Gone"""
    return JSONResponse(status_code=410, content=_GONE_BODY)


@router.post("/api/ai-predict/batch")
def api_ai_predict_batch(request: Request):
    """[已废弃] 批量预测多只股票 — 返回 410 Gone"""
    return JSONResponse(status_code=410, content=_GONE_BODY)


# ---- 遗传编程因子挖掘 ----

@router.get("/api/genetic-factor/{code}")
def api_genetic_factor(code: str, generations: int = 30, top_k: int = 10):
    """对单只股票运行遗传因子挖掘"""
    from services.genetic_factor import evolve_factors
    return evolve_factors(code=code, generations=generations, top_k=top_k)


# ---- 组合优化器 ----

@router.get("/api/portfolio-optimize/{user_id}")
def api_portfolio_optimize(user_id: str, method: str = "all", max_weight: float = 0.20):
    """组合优化：5种方法对比"""
    from services.portfolio_optimizer import optimize_portfolio
    return optimize_portfolio(user_id, method=method, max_weight=max_weight)


# ---- 强化学习仓位建议 ----

@router.get("/api/rl-position/{code}")
def api_rl_position(code: str):
    """RL 仓位建议（单只股票）"""
    from services.rl_position import get_rl_recommendation
    return get_rl_recommendation(code)


@router.get("/api/rl-position/portfolio/{user_id}")
def api_rl_portfolio(user_id: str):
    """RL 仓位建议（全部持仓）"""
    from services.rl_position import get_rl_portfolio_advice
    return get_rl_portfolio_advice(user_id)


# ---- LLM 因子生成 ----

@router.get("/api/llm-factor/{code}")
def api_llm_factor(code: str, count: int = 5, iterations: int = 2):
    """LLM 驱动的 Alpha 因子生成"""
    from services.llm_factor_gen import generate_alpha_factors
    return generate_alpha_factors(code=code, count=count, iterations=iterations)
