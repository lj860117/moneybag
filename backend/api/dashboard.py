"""
仪表盘 & 通用路由
==================
/api/dashboard       — 综合市场仪表盘（三级降级）
/api/nav/all         — 所有推荐基金净值
/api/nav/{code}      — 单只基金净值
/api/market-status   — 市场状态（交易日/时段）
/api/glossary        — 金融术语词典
/api/health          — 健康检查

P3 高耦合路由 — dashboard 涉及异步 + precomputed_cache + 11 种数据源
"""
import config
import os
import asyncio
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

from services.data_layer import (
    get_fund_nav, get_fear_greed_index, get_valuation_percentile,
    get_technical_indicators, get_market_news,
    get_macro_calendar, get_northbound_flow, get_margin_trading,
    get_treasury_yield, get_shibor, get_dividend_yield,
    get_news_sentiment_score,
)

router = APIRouter()


@router.get("/api/glossary")
def get_glossary_api(term: str = None):
    """FIX 2026-04-19 D4: 金融术语词典（小白可用性）
    - 不传 term → 返回全部词典
    - 传 term（如 ?term=PE）→ 返回单个解释
    """
    from services.glossary import get_glossary, explain_term
    if term:
        return {"term": term, **explain_term(term)}
    return {"glossary": get_glossary()}


@router.get("/api/market-status")
def get_market_status():
    """FIX 2026-04-19 F2: 市场状态 API，前端显示『今天是否交易日』"""
    from services.signal_scout import is_trading_day
    from datetime import time as dt_time
    now = datetime.now()
    trading_day = is_trading_day(now)

    # 判断交易时段
    t = now.time()
    session = "closed"
    if trading_day:
        if dt_time(9, 30) <= t < dt_time(11, 30):
            session = "morning"
        elif dt_time(13, 0) <= t < dt_time(15, 0):
            session = "afternoon"
        elif t < dt_time(9, 30):
            session = "pre_open"
        elif dt_time(11, 30) <= t < dt_time(13, 0):
            session = "lunch"
        else:
            session = "after_close"

    return {
        "is_trading_day": trading_day,
        "session": session,
        "now": now.isoformat(),
        "weekday": now.strftime("%A"),
        "message": {
            "closed": "📅 非交易日，数据为最近一次收盘快照",
            "pre_open": "🌅 开盘前（9:30 前），数据为昨日收盘",
            "morning": "🟢 上午交易中（9:30-11:30）",
            "lunch": "☕ 午休（11:30-13:00），数据为上午收盘",
            "afternoon": "🟢 下午交易中（13:00-15:00）",
            "after_close": "🌙 已收盘，数据为今日收盘",
        }.get(session, "市场状态未知"),
    }


def _cfo_family_members() -> list:
    """需要检测 CFO 缓存新鲜度的成员名单。

    FIX 2026-09-13：此前 /api/health 把文件名硬编码成 cfo_summary_LeiJiang.json，
    于是 BuLuoGeLi 的缓存过期时**结构上看不见**（以他的身份看还会显示
    LeiJiang 的时间戳）。而首页「家庭净资产」是两人合计 —— 任一成员的数据
    过期，这个数字就可能不是最新的。

    名单刻意取自与预热线程同一个来源（api.shared_helpers.FAMILY_MEMBERS，
    由 services.cfo_dashboard._get_active_users 透出）：**检测范围必须
    等于维护范围**，否则会出现"检测没人维护的用户"这类永久误报。
    """
    try:
        from api.shared_helpers import FAMILY_MEMBERS
        return list(FAMILY_MEMBERS)
    except Exception as e:  # pragma: no cover - 正常环境不应触发
        # 读不到名单就跳过这项检测，但必须出声：否则检测会静默消失，
        # 又变成"守卫没了、却没人知道"
        print(f"[HEALTH] 跳过 CFO 新鲜度检测（读不到成员名单）: {e}")
        return []


@router.get("/api/health")
def health():
    from config import APP_VERSION
    from infra.llm.gateway import LLMGateway
    budget = LLMGateway.instance().check_budget()
    # Phase 0: API Key 状态检查
    keys_status = {}
    keys_status["deepseek"] = "ok" if os.environ.get("LLM_API_KEY") else "missing"
    keys_status["doubao"] = "ok" if os.environ.get("DOUBAO_API_KEY") else "missing"
    keys_status["tushare"] = "ok" if os.environ.get("TUSHARE_TOKEN") else "missing"
    
    # v9.5.123: 数据源健康检测（前端据此显示降级提示）
    data_health = {"overall": "ok", "degraded": [], "last_success": {}}
    try:
        from pathlib import Path
        import time as _t
        cache_dir = Path(config.DATA_DIR) / "_cache"

        # 1) 市场行情：全站共用一份，24h（周末可能不更新）
        _mkt = cache_dir / "market_context.txt"
        if _mkt.exists():
            _age_h = (_t.time() - _mkt.stat().st_mtime) / 3600
            data_health["last_success"]["市场行情"] = {
                "age_hours": round(_age_h, 1),
                "fresh": _age_h < 24,
            }
            if _age_h > 48:  # 超过2倍最大年龄 = 降级
                data_health["degraded"].append(f"市场行情(已{round(_age_h)}h未更新)")
        else:
            data_health["degraded"].append("市场行情(无缓存)")

        # 2) CFO摘要：**逐成员**检测（详见 _cfo_family_members 的说明）
        #    阈值 12h 新鲜 / 24h 降级，与原先一致
        _cfo_rows = []  # [(uid, 是否过期, 已过小时数)]
        for _uid in _cfo_family_members():
            _fp = cache_dir / f"cfo_summary_{_uid}.json"
            if not _fp.exists():
                # 缺失 ≠ 降级：文件不存在只说明「还没打开过 App」，
                # 属正常冷启动，不该在首页弹黄条
                continue
            _age_h = (_t.time() - _fp.stat().st_mtime) / 3600
            data_health["last_success"][f"CFO摘要({_uid})"] = {
                "age_hours": round(_age_h, 1),
                "fresh": _age_h < 12,
            }
            _cfo_rows.append((_uid, _age_h > 24, _age_h))

        _stale = [r for r in _cfo_rows if r[1]]
        if len(_stale) > 1 and len(_stale) == len(_cfo_rows):
            # 全体成员一起过期（典型原因：预热线程挂了）→ 合并成一条，
            # 免得手机顶部黄条被同义内容刷成好几行
            _h = round(max(r[2] for r in _stale))
            data_health["degraded"].append(f"CFO摘要(家庭{len(_stale)}人)(已{_h}h未更新)")
        else:
            # 只有个别成员过期 → 指名道姓，否则不知道是谁的数据旧了
            for _uid, _, _h in _stale:
                data_health["degraded"].append(f"CFO摘要({_uid})(已{round(_h)}h未更新)")

        if data_health["degraded"]:
            data_health["overall"] = "degraded"
    except Exception:
        pass
    
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "version": APP_VERSION,
        "llm_usage": budget,
        "keys_status": keys_status,
        "data_health": data_health,
    }


@router.get("/api/nav/all")
def get_all_nav():
    """获取所有推荐基金的净值"""
    codes = ["110020", "050025", "217022", "000216", "008114"]
    result = {}
    for code in codes:
        result[code] = get_fund_nav(code)
    return result


@router.get("/api/nav/{code}")
def get_nav(code: str):
    """获取单只基金净值"""
    return get_fund_nav(code)


@router.get("/api/dashboard")
async def get_market_dashboard():
    """V4.5 综合市场仪表盘 — 三级降级: 新鲜缓存 → 过期缓存 → 5s超时实时 → 空壳"""

    # === 第1级: 新鲜缓存（秒出） ===
    stale_result = None  # 保存过期缓存，备用
    try:
        from services.precomputed_cache import get_precomputed, PRECOMPUTED_DIR
        pc_factors = get_precomputed("factors")
        pc_fgi = get_precomputed("fear_greed")
        pc_val = get_precomputed("valuation")

        if pc_factors and pc_fgi and pc_val:
            pc_tech = get_precomputed("technical") or {}
            return {
                "valuation": pc_val,
                "fear_greed": pc_fgi,
                "technical": pc_tech,
                "northbound": pc_factors.get("northbound", {}),
                "margin": pc_factors.get("margin", {}),
                "shibor": pc_factors.get("shibor", {}),
                "from_cache": True,
                "cache_note": "凌晨预计算数据，盘中每30分钟刷新",
            }

        # === 第2级: 过期缓存也读出来备用 ===
        # 直接读磁盘文件，跳过 TTL 检查
        from datetime import date as _date, timedelta as _td
        for days_ago in range(4):  # 最多找 4 天前
            d = _date.today() - _td(days=days_ago)
            factors_f = PRECOMPUTED_DIR / f"factors_{d}.json"
            fgi_f = PRECOMPUTED_DIR / f"fear_greed_{d}.json"
            val_f = PRECOMPUTED_DIR / f"valuation_{d}.json"
            if factors_f.exists() and fgi_f.exists() and val_f.exists():
                try:
                    pf = json.loads(factors_f.read_text(encoding="utf-8")).get("data", {})
                    pfgi = json.loads(fgi_f.read_text(encoding="utf-8")).get("data", {})
                    pval = json.loads(val_f.read_text(encoding="utf-8")).get("data", {})
                    stale_result = {
                        "valuation": pval,
                        "fear_greed": pfgi,
                        "northbound": pf.get("northbound", {}),
                        "margin": pf.get("margin", {}),
                        "shibor": pf.get("shibor", {}),
                        "from_cache": True,
                        "stale": True,
                        "cache_note": f"数据截至 {d}（缓存已过期，优先展示历史数据）",
                    }
                    break
                except Exception:
                    pass
    except Exception:
        pass

    # === 第3级: 实时拉取（5s 超时，不是之前的 30s） ===
    try:
        loop = asyncio.get_event_loop()

        async def _fetch_realtime():
            return await asyncio.gather(
                loop.run_in_executor(None, get_valuation_percentile),
                loop.run_in_executor(None, get_fear_greed_index),
                loop.run_in_executor(None, get_technical_indicators),
                loop.run_in_executor(None, lambda: get_market_news(8)),
                loop.run_in_executor(None, get_macro_calendar),
                loop.run_in_executor(None, get_northbound_flow),
                loop.run_in_executor(None, get_margin_trading),
                loop.run_in_executor(None, get_treasury_yield),
                loop.run_in_executor(None, get_shibor),
                loop.run_in_executor(None, get_dividend_yield),
                loop.run_in_executor(None, get_news_sentiment_score),
            )

        (val, fgi_data, tech, news, macro,
         northbound, margin, treasury, shibor_data, dividend, sentiment
        ) = await asyncio.wait_for(_fetch_realtime(), timeout=8.0)

        return {
            "valuation": val,
            "fearGreed": fgi_data,
            "technical": tech,
            "news": news,
            "macro": macro,
            "northbound": northbound,
            "margin": margin,
            "treasury": treasury,
            "shibor": shibor_data,
            "dividend": dividend,
            "sentiment": sentiment,
            "version": "4.5",
            "updatedAt": datetime.now().isoformat(),
        }
    except (asyncio.TimeoutError, Exception) as e:
        print(f"[DASHBOARD] 实时拉取超时/失败: {e}")

    # === 第4级: 返回过期缓存（总好过空白） ===
    if stale_result:
        return stale_result

    # === 第5级: 空壳（绝不转圈） ===
    return {"valuation": {}, "fear_greed": {}, "from_cache": False, "error": "数据源暂不可用"}
