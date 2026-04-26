"""
杂项路由（小路由合集）
======================
/api/decision-log            — 决策日志查询
/api/decision-log/stats      — 决策统计
/api/admin/backup            — 一键备份
/api/signal-scout/latest     — 最新匹配信号
/api/signal-scout/history    — 历史信号
/api/signal-scout/scan       — 手动触发信号扫描
/api/judgment/scorecard      — 判断成绩单
/api/judgment/weights        — 模块权重
/api/judgment/calibrate      — 手动校准
/api/earnings/{code}         — 个股盈利预测
/api/valuation/{code}        — 个股估值评估
/api/dcf/{code}              — 个股 DCF 估值
/api/recommend/stocks        — 股票推荐
/api/decisions               — 买卖决策
/api/exposure/{code}         — 个股业务敞口
/api/fund-share/{ts_code}    — 基金/ETF 份额变化

P3 高耦合路由 — 多种 service 依赖
"""
import shutil
from datetime import datetime

from fastapi import APIRouter

from config import DATA_DIR
from services.signal_scout import (
    get_latest as scout_get_latest,
    get_history as scout_get_history,
    collect as scout_collect,
)
from services.judgment_tracker import (
    scorecard as jt_scorecard, get_weights as jt_get_weights,
    calibrate as jt_calibrate,
)

router = APIRouter()


# ---- 决策日志 ----

@router.get("/api/decision-log")
def get_decision_log_api(userId: str = "", days: int = 7):
    """查询最近 N 天的决策日志"""
    from services.decision_log import get_decisions
    return {"decisions": get_decisions(userId or None, days)}


@router.get("/api/decision-log/stats")
def get_decision_stats_api(userId: str = "", days: int = 30):
    """决策统计（V8 复盘预览）"""
    from services.decision_log import get_decision_stats
    return get_decision_stats(userId or None, days)


# ---- 管理 ----

@router.post("/api/admin/backup")
def create_backup():
    """一键备份全部用户数据"""
    backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir = DATA_DIR.parent / "backups" / backup_name
    try:
        shutil.copytree(DATA_DIR, backup_dir)
        return {"status": "ok", "path": str(backup_dir), "name": backup_name}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


# ---- 信号侦察兵 ----

@router.get("/api/signal-scout/latest")
def api_signal_scout_latest(userId: str = ""):
    """获取用户最新匹配信号"""
    if not userId:
        return {"signals": [], "total": 0}
    return scout_get_latest(userId)


@router.get("/api/signal-scout/history")
def api_signal_scout_history(userId: str = "", days: int = 7):
    """获取历史信号"""
    if not userId:
        return []
    return scout_get_history(userId, days)


@router.post("/api/signal-scout/scan")
def api_signal_scout_scan():
    """手动触发全市场信号扫描（刷新缓存）"""
    from services.signal_scout import _signal_cache
    _signal_cache.clear()
    signals = scout_collect()
    return {"total": len(signals), "scanned_at": datetime.now().isoformat()}


# ---- 判断追踪器 ----

@router.get("/api/judgment/scorecard")
def api_judgment_scorecard(userId: str = "", months: int = 3):
    """判断成绩单 — 准确率/盈亏/模块贡献"""
    uid = userId or "default"
    return jt_scorecard(uid, months)


@router.get("/api/judgment/weights")
def api_judgment_weights(userId: str = ""):
    """当前模块权重（EMA 校准后）"""
    uid = userId or "default"
    weights = jt_get_weights(uid)
    return {"weights": weights, "user_id": uid}


@router.post("/api/judgment/calibrate")
def api_judgment_calibrate(req: dict = {}):
    """手动触发 EMA 权重校准"""
    uid = req.get("userId", "default")
    return jt_calibrate(uid)


# ---- 盈利预测 + 估值 ----

@router.get("/api/earnings/{code}")
def api_earnings(code: str):
    """个股盈利预测（一致预期+评级分布+目标价）"""
    from services.earnings_forecast import get_stock_forecast
    return get_stock_forecast(code)


@router.get("/api/valuation/{code}")
def api_valuation(code: str):
    """个股估值评估（Forward PE+PEG+目标价空间）"""
    from services.valuation_engine import assess_valuation
    return assess_valuation(code)


@router.get("/api/dcf/{code}")
def api_dcf(code: str):
    """个股 DCF 估值（现金流折现法）"""
    from services.valuation_engine import dcf_valuation
    return dcf_valuation(code)


# ---- 推荐引擎 + 买卖决策 ----

@router.get("/api/recommend/stocks")
def api_recommend_stocks(userId: str = "", topN: int = 10, pool: str = "hot", period: str = "medium"):
    """股票推荐（优先凌晨预计算缓存，否则实时算）"""
    from services.precomputed_cache import get_precomputed
    cached = get_precomputed("recommendations")
    if cached:
        cached["from_cache"] = True
        return cached
    from services.recommend_engine import get_stock_recommendations
    return get_stock_recommendations(userId, topN, pool, period)


@router.get("/api/decisions")
def api_decisions(userId: str = ""):
    """买卖决策（优先凌晨预计算缓存，否则实时算）"""
    uid = userId or "default"
    from services.precomputed_cache import get_precomputed
    cached = get_precomputed("decisions", user_id=uid)
    if cached:
        cached["from_cache"] = True
        return cached
    from services.decision_maker import generate_decisions
    return generate_decisions(uid)


@router.get("/api/exposure/{code}")
def api_exposure(code: str):
    """个股业务敞口（出口占比+地缘脆弱性）"""
    from services.business_exposure import get_business_exposure
    return get_business_exposure(code)


@router.get("/api/fund-share/{ts_code}")
def api_fund_share(ts_code: str):
    """基金/ETF 份额变化"""
    from services.tushare_data import get_fund_share
    return get_fund_share(ts_code)
