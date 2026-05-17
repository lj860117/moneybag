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
from fastapi.responses import JSONResponse

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
async def api_recommend_stocks(userId: str = "", topN: int = 10, pool: str = "hot", period: str = "medium"):
    """股票推荐 — 缓存优先+后台异步更新，前端秒回

    策略：
    1. 有 precomputed 缓存 → 立即返回（<50ms）
    2. 有 file_cache 缓存 → 兜底返回
    3. 无缓存 → 返回"计算中"提示 + 后台启动异步计算
    """
    from services.precomputed_cache import get_precomputed, save_precomputed
    cached = get_precomputed("recommendations")
    if cached:
        cached["from_cache"] = True
        # 后台静默刷新（如果缓存超过2小时）
        import time as _time
        if _time.time() - cached.get("ts", 0) > 7200:
            _trigger_recommend_update(userId, topN, pool, period)
        return cached

    # 兜底：尝试从 recommend_engine 的 file_cache 读取
    try:
        from pathlib import Path
        from config import DATA_DIR
        import json as _json
        _file_cache_dir = Path(DATA_DIR) / "_cache"
        # 找最近的有效推荐缓存文件（任何用户的都行，因为候选池是一样的）
        cache_files = sorted(_file_cache_dir.glob("recommend_rec_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for cf in cache_files[:5]:
            payload = _json.loads(cf.read_text())
            result = payload.get("data", payload)
            recs = result.get("recommendations", result.get("stocks", []))
            if recs:
                result["from_cache"] = True
                result["_note"] = "来自历史缓存"
                return result
    except Exception:
        pass

    # 无缓存：后台触发计算，立即返回友好提示
    _trigger_recommend_update(userId, topN, pool, period)
    return {
        "stocks": [],
        "total": 0,
        "computing": True,
        "message": "📊 推荐列表正在后台计算（约20秒），请稍后刷新查看。",
        "source": "computing",
    }


_recommend_computing = False  # 防止重复触发

def _trigger_recommend_update(userId: str, topN: int, pool: str, period: str):
    """后台线程异步计算推荐结果并写入缓存"""
    global _recommend_computing
    if _recommend_computing:
        return  # 已有计算在跑，不重复触发
    _recommend_computing = True
    import threading
    def _do_compute():
        global _recommend_computing
        try:
            from services.recommend_engine import get_stock_recommendations
            from services.precomputed_cache import save_precomputed
            result = get_stock_recommendations(userId, topN, pool, period)
            if result and (result.get("recommendations") or result.get("stocks")):
                save_precomputed("recommendations", result)
                count = len(result.get("recommendations", result.get("stocks", [])))
                print(f"[RECOMMEND] 后台计算完成: {count} 只")
        except Exception as e:
            print(f"[RECOMMEND] 后台计算失败: {e}")
        finally:
            _recommend_computing = False
    t = threading.Thread(target=_do_compute, daemon=True, name="recommend-bg")
    t.start()


@router.get("/api/decisions")
def api_decisions(userId: str = ""):
    """[已废弃] 买卖决策 — 返回 410 Gone

    M5 W3: decision_maker v1 已废弃。
    迁移目标: POST /api/decisions/review (事后复盘) 或 GET /api/decisions/monthly-report/{user_id} (月度报告)
    设计决策: 10-roadmap.md §四 废弃时间表
    """
    return JSONResponse(status_code=410, content={
        "status": "gone",
        "code": 410,
        "message": "此接口已废弃。旧版买卖决策已被决策复盘系统取代。",
        "migration_guide": "使用 POST /api/decisions/review 提交交易复盘，"
                           "或 GET /api/decisions/monthly-report/{user_id} 查看决策质量报告。",
        "deprecated_since": "2026-05-15",
        "removed_at": "2026-07-01",
    })


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
