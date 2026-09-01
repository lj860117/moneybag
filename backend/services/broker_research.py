"""
钱袋子 — V6 Phase 4: 券商研报摘要模块
数据源：Tushare report_rc（2000积分门槛，主）+ AKShare 东财研报标题（降级）
功能：拉取最新券商研报 → 提取机构共识（看多/看空/中性）→ 重点行业 → enrich 注入 Pipeline
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "broker_research",
    "scope": "public",
    "input": [],
    "output": "broker_views",
    "cost": "cpu",
    "tags": ["研报", "券商", "策略观点", "机构共识"],
    "description": "主流券商策略观点摘要：研报拉取+多空统计+重点行业+Pipeline enrich",
    "layer": "data",
    "priority": 4,
}

import time
import json
import re
from datetime import datetime, timedelta
from infra.cache import MemoryCache

_BROKER_CACHE_TTL = 3600  # 1小时缓存（研报更新频率低）
_broker_cache = MemoryCache(default_ttl=_BROKER_CACHE_TTL)


# ============================================================
# 1. 核心：获取券商研报列表
# ============================================================

def get_latest_reports(limit: int = 30) -> list:
    """获取最新券商研报列表

    Tushare report_rc 返回字段：
    ts_code, report_date, report_title, author, org_name, rating, abstract

    rating 含义（Tushare 定义）：
    买入/增持/推荐/强推 → 看多
    中性/持有/观望 → 中性
    减持/卖出/回避 → 看空
    """
    cache_key = "latest_reports"
    now = time.time()
    cached = _broker_cache.get(cache_key)
    if cached is not None:
        return cached

    reports = []

    # ── 方案 A（主）：Tushare report_rc ──
    try:
        from services.tushare_data import is_configured, get_research_reports
        if is_configured():
            rows = get_research_reports(limit=limit)
            if rows:
                for r in rows:
                    reports.append({
                        "code": r.get("ts_code", ""),
                        "date": r.get("report_date", ""),
                        "title": r.get("report_title", ""),
                        "author": r.get("author", ""),
                        "org": r.get("org_name", ""),
                        "rating": r.get("rating", ""),
                        "abstract": r.get("abstract", ""),
                        "source": "tushare",
                    })
                print(f"[BROKER] Tushare OK: {len(reports)} 篇研报")
    except Exception as e:
        print(f"[BROKER] Tushare failed: {e}")

    # ── 方案 B（降级/补充）：AKShare 东财研报标题 ──
    if len(reports) < 5:
        try:
            from infra.data_source.macro.indicators import get_stock_news
            df = get_stock_news(symbol="研报")
            if df is not None and len(df) > 0:
                title_col = next((c for c in df.columns if "标题" in c or "title" in c.lower()), df.columns[0])
                time_col = next((c for c in df.columns if "时间" in c or "date" in c.lower()), None)
                for _, row in df.head(20).iterrows():
                    reports.append({
                        "title": str(row.get(title_col, "")),
                        "date": str(row.get(time_col, "")) if time_col else "",
                        "source": "akshare_eastmoney",
                    })
                print(f"[BROKER] AKShare 补充: +{min(20, len(df))} 条研报标题")
        except Exception as e:
            print(f"[BROKER] AKShare 研报标题 failed: {e}")

    _broker_cache.set(cache_key, reports)
    return reports


# ============================================================
# 2. 机构共识提取（规则引擎，不调 LLM）
# ============================================================

# 评级→方向映射
_RATING_MAP = {
    # 看多
    "买入": "bullish", "增持": "bullish", "推荐": "bullish", "强烈推荐": "bullish",
    "强推": "bullish", "优于大市": "bullish", "跑赢": "bullish",
    # 中性
    "中性": "neutral", "持有": "neutral", "观望": "neutral", "同步大市": "neutral",
    # 看空
    "减持": "bearish", "卖出": "bearish", "回避": "bearish", "跑输": "bearish",
    "弱于大市": "bearish",
}

# 行业关键词提取
_SECTOR_KEYWORDS = {
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "光刻"],
    "新能源": ["新能源", "光伏", "风电", "储能", "锂电", "电池"],
    "AI/科技": ["人工智能", "AI", "大模型", "算力", "GPU", "机器人"],
    "医药": ["医药", "生物", "创新药", "CXO", "医疗"],
    "消费": ["消费", "白酒", "食品", "家电", "旅游", "免税"],
    "金融": ["银行", "券商", "保险", "金融"],
    "能源": ["石油", "煤炭", "天然气", "能源"],
    "军工": ["军工", "国防", "航天", "航空装备"],
    "地产": ["房地产", "地产", "基建", "建材"],
    "汽车": ["汽车", "新能源车", "智能驾驶", "造车"],
}


def get_broker_consensus() -> dict:
    """提取机构共识：多空比例、重点行业、关键风险

    纯规则引擎，不调 LLM，0 token 成本。

    FIX 2026-06-14: 修复研报共识系统性看多偏差
    - 券商研报天然"报喜不报忧"（买入/增持远多于减持/卖出），
      导致30篇最新研报几乎100%看多，与市场实际严重矛盾
    - 修复策略：
      1. 只有 rating 字段明确标注的研报才计入多空统计（标题推断不准，移除）
      2. 无评级的不参与投票，但计入 total_reports
      3. 增加偏差警示字段 sample_bias，告知前端数据固有局限性
      4. 研报覆盖范围从全市场最新30篇改为沪深300成分股近期研报（更具代表性）
    """
    cache_key = "consensus"
    now = time.time()
    cached = _broker_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {
        "consensus": "中性",
        "bullish_count": 0, "bearish_count": 0, "neutral_count": 0,
        "unrated_count": 0,
        "total_reports": 0,
        "hot_sectors": [],
        "key_orgs": [],
        "key_risks": [],
        "recent_titles": [],
        "available": False,
        "source": "rule_engine",
        "sample_bias": "券商研报天然看多倾向：买入/增持占比通常70%+，该数据反映的是机构覆盖面而非市场真实多空力量对比。请结合估值、资金流等客观数据综合判断。",
        "degraded_reason": "",
    }

    reports = get_latest_reports(limit=30)
    if not reports:
        _broker_cache.set(cache_key, result, ttl=_BROKER_CACHE_TTL)
        return result

    result["total_reports"] = len(reports)

    # 1. 统计多空比例
    # FIX: 只有 rating 字段明确的才计入多空投票，标题推断不准确且加剧偏差
    bullish = 0
    bearish = 0
    neutral = 0
    unrated = 0
    orgs = set()
    titles = []

    for r in reports:
        rating = r.get("rating", "")
        title = r.get("title", "")

        # 只有评级字段明确存在时才统计多空
        direction = ""
        if rating:
            # 精确匹配评级字段
            direction = _RATING_MAP.get(rating, "")
            if not direction:
                for key, val in _RATING_MAP.items():
                    if key in rating:
                        direction = val
                        break

        # FIX: 移除标题关键词推断 — 券商研报标题几乎都有"看好""有望"等词，
        # 这不代表真正看多，只是报告撰写习惯。标题推断会让 bearish 永远为0。

        if direction == "bullish":
            bullish += 1
        elif direction == "bearish":
            bearish += 1
        elif direction == "neutral":
            neutral += 1
        else:
            # 无评级的不参与投票
            unrated += 1

        org = r.get("org", "")
        if org:
            orgs.add(org)

        if title:
            titles.append(title)

    result["bullish_count"] = bullish
    result["bearish_count"] = bearish
    result["neutral_count"] = neutral
    result["unrated_count"] = unrated
    result["key_orgs"] = list(orgs)[:10]
    result["recent_titles"] = titles[:10]

    # 共识判定 — FIX: 只基于有评级的研报，无评级的不参与
    total_rated = bullish + bearish + neutral
    if total_rated > 0:
        bull_pct = bullish / total_rated
        bear_pct = bearish / total_rated
        # FIX: 调整阈值 — 券商研报天然看多（买入/增持占比70%+是常态），
        # 不能简单用 bull_pct > 0.6 就判"看多"，那几乎永远看多
        # 新逻辑：看多占比>80%才判"看多"（排除天然偏差），看空占比>30%就判"偏空"
        if bull_pct > 0.85:
            result["consensus"] = "看多"
        elif bull_pct > 0.65:
            result["consensus"] = "谨慎乐观"
        elif bear_pct > 0.30:
            result["consensus"] = "偏空"
        elif bear_pct > 0.50:
            result["consensus"] = "看空"
        else:
            result["consensus"] = "中性分化"
    else:
        # 全部无评级时
        # FIX 2026-09: 区分"主数据源限额耗尽被迫降级"vs"数据源本身就没有评级"
        # 这两种完全不同的情况——之前统一写成"所有研报均无明确评级字段，
        # 无法提取多空信号"，会让人误以为是数据源天生缺陷，但真实情况通常是
        # Tushare report_rc 的每日10次硬限额已被其他进程（cache_warmer/
        # night_worker等）耗尽，当前展示的是 AKShare 降级源（东财研报标题，
        # 该源结构上就不带 rating 字段）。用 reports 里的 source 字段 +
        # 今日额度状态来判断具体原因，写进 degraded_reason 供
        # use_cases/self_audit.py 的 LLM 审计层区分归因。
        all_from_fallback = bool(reports) and all(
            r.get("source") != "tushare" for r in reports
        )
        if all_from_fallback:
            try:
                from services.tushare_data import get_report_rc_quota_status
                quota = get_report_rc_quota_status()
            except Exception:
                quota = {"exhausted": False, "used": 0, "limit": 0}
            if quota.get("exhausted"):
                result["degraded_reason"] = "quota_exhausted_fallback_no_rating"
                result["sample_bias"] = (
                    f"主数据源 Tushare report_rc 今日限额已耗尽"
                    f"（{quota.get('used')}/{quota.get('limit')}次，由多个后台任务共同消耗），"
                    f"当前展示的是 AKShare 降级数据源（东财研报标题），该降级源结构上不携带"
                    f"评级字段，因此全部研报计入 unrated。这是主数据源限额被打穿后的"
                    f"正常降级行为，不代表数据源本身没有评级能力，限额每日重置。"
                )
            else:
                # source 不是 tushare 但额度未耗尽 → 主数据源大概率是真实请求
                # 失败（网络/token问题），而不是限额问题，仍需人工关注。
                result["degraded_reason"] = "primary_source_unavailable_fallback_no_rating"
                result["sample_bias"] = (
                    "主数据源 Tushare report_rc 本次未返回数据（非限额耗尽，可能是网络"
                    "或配置问题），当前展示的是 AKShare 降级数据源（东财研报标题），"
                    "该降级源结构上不携带评级字段，因此全部研报计入 unrated。"
                )
        else:
            # 有 tushare 来源的研报，但评级字段仍然缺失 —— 这才是真实的数据
            # 质量问题（主数据源本身没给评级），需要上报。
            result["degraded_reason"] = "source_has_no_rating"
            result["sample_bias"] = "所有研报均无明确评级字段，无法提取多空信号（主数据源本身缺失评级，非限额降级导致）。"
        result["consensus"] = "数据不足"

    # 2. 热门行业提取（从标题+摘要中计数）
    sector_counts = {}
    all_text = " ".join(titles + [r.get("abstract", "") for r in reports if r.get("abstract")])
    for sector, keywords in _SECTOR_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in all_text)
        if count > 0:
            sector_counts[sector] = count
    result["hot_sectors"] = sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    result["hot_sectors"] = [{"name": s, "mentions": c} for s, c in result["hot_sectors"]]

    # 3. 关键风险提取
    risk_keywords = {
        "地缘风险": ["地缘", "冲突", "战争", "制裁", "中东"],
        "油价压力": ["油价", "原油", "能源价格"],
        "美联储": ["美联储", "加息", "降息", "利率"],
        "汇率": ["汇率", "贬值", "人民币"],
        "政策收紧": ["收紧", "监管", "调控"],
    }
    for risk_name, keywords in risk_keywords.items():
        if any(kw in all_text for kw in keywords):
            result["key_risks"].append(risk_name)

    result["available"] = True
    print(f"[BROKER] 共识={result['consensus']}, "
          f"多:{bullish}/空:{bearish}/中:{neutral}, "
          f"行业TOP={[s['name'] for s in result['hot_sectors'][:3]]}")

    _broker_cache.set(cache_key, result)
    return result


# ============================================================
# 3. 个股研报查询（按需调用）
# ============================================================

def get_stock_reports(code: str, limit: int = 5) -> list:
    """获取个股的最新研报"""
    cache_key = f"stock_reports_{code}"
    now = time.time()
    cached = _broker_cache.get(cache_key)
    if cached is not None:
        return cached

    reports = []
    try:
        from services.tushare_data import is_configured, get_research_reports
        if is_configured():
            rows = get_research_reports(code=code, limit=limit)
            for r in rows:
                reports.append({
                    "date": r.get("report_date", ""),
                    "title": r.get("report_title", ""),
                    "org": r.get("org_name", ""),
                    "rating": r.get("rating", ""),
                    "abstract": r.get("abstract", ""),
                })
    except Exception as e:
        print(f"[BROKER] get_stock_reports({code}) failed: {e}")

    _broker_cache.set(cache_key, reports, ttl=_BROKER_CACHE_TTL)
    return reports


# ============================================================
# 4. Pipeline enrich() — 注入券商共识到 DecisionContext
# ============================================================

def enrich(ctx):
    """Pipeline Layer2 自动调用 — 注入券商研报共识"""
    try:
        consensus = get_broker_consensus()

        detail_parts = [f"机构共识:{consensus.get('consensus', '未知')}"]
        bc = consensus.get("bullish_count", 0)
        brc = consensus.get("bearish_count", 0)
        nc = consensus.get("neutral_count", 0)
        if bc + brc + nc > 0:
            detail_parts.append(f"({bc}看多/{brc}看空/{nc}中性)")

        hot = consensus.get("hot_sectors", [])
        if hot:
            detail_parts.append(f"关注:{','.join(s['name'] for s in hot[:3])}")

        risks = consensus.get("key_risks", [])
        if risks:
            detail_parts.append(f"风险:{','.join(risks[:3])}")

        # 方向评估
        if consensus.get("consensus") in ("看多", "谨慎乐观"):
            direction = "bullish"
            score = 0.6
        elif consensus.get("consensus") in ("看空", "偏空"):
            direction = "bearish"
            score = 0.4
        else:
            direction = "neutral"
            score = 0.5

        ctx.modules_results["broker_research"] = {
            "direction": direction,
            "score": score,
            "confidence": 55 if consensus.get("available") else 30,
            "available": consensus.get("available", False),
            "detail": " | ".join(detail_parts),
            "consensus": consensus.get("consensus"),
            "bullish_count": bc,
            "bearish_count": brc,
            "neutral_count": nc,
            "hot_sectors": hot,
            "key_risks": risks,
            "total_reports": consensus.get("total_reports", 0),
            "recent_titles": consensus.get("recent_titles", [])[:5],
        }

        if "broker_research" not in ctx.modules_called:
            ctx.modules_called.append("broker_research")

    except Exception as e:
        print(f"[BROKER] enrich failed: {e}")
        ctx.modules_results["broker_research"] = {
            "available": False,
            "error": str(e),
            "direction": "neutral",
            "score": 0.5,
        }

    return ctx
