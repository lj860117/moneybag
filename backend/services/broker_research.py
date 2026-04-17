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

_broker_cache = {}
_BROKER_CACHE_TTL = 3600  # 1小时缓存（研报更新频率低）


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
    if cache_key in _broker_cache and now - _broker_cache[cache_key]["ts"] < _BROKER_CACHE_TTL:
        return _broker_cache[cache_key]["data"]

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
            import akshare as ak
            df = ak.stock_news_em(symbol="研报")
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

    _broker_cache[cache_key] = {"data": reports, "ts": now}
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
    """
    cache_key = "consensus"
    now = time.time()
    if cache_key in _broker_cache and now - _broker_cache[cache_key]["ts"] < _BROKER_CACHE_TTL:
        return _broker_cache[cache_key]["data"]

    result = {
        "consensus": "中性",
        "bullish_count": 0, "bearish_count": 0, "neutral_count": 0,
        "total_reports": 0,
        "hot_sectors": [],
        "key_orgs": [],
        "key_risks": [],
        "recent_titles": [],
        "available": False,
        "source": "rule_engine",
    }

    reports = get_latest_reports(limit=30)
    if not reports:
        _broker_cache[cache_key] = {"data": result, "ts": now}
        return result

    result["total_reports"] = len(reports)

    # 1. 统计多空比例
    bullish = 0
    bearish = 0
    neutral = 0
    orgs = set()
    titles = []

    for r in reports:
        rating = r.get("rating", "")
        # 精确匹配评级
        direction = _RATING_MAP.get(rating, "")
        if not direction and rating:
            # 模糊匹配
            for key, val in _RATING_MAP.items():
                if key in rating:
                    direction = val
                    break
        if direction == "bullish":
            bullish += 1
        elif direction == "bearish":
            bearish += 1
        elif direction == "neutral":
            neutral += 1

        org = r.get("org", "")
        if org:
            orgs.add(org)

        title = r.get("title", "")
        if title:
            titles.append(title)

    result["bullish_count"] = bullish
    result["bearish_count"] = bearish
    result["neutral_count"] = neutral
    result["key_orgs"] = list(orgs)[:10]
    result["recent_titles"] = titles[:10]

    # 共识判定
    total_rated = bullish + bearish + neutral
    if total_rated > 0:
        bull_pct = bullish / total_rated
        if bull_pct > 0.6:
            result["consensus"] = "看多"
        elif bull_pct > 0.4:
            result["consensus"] = "谨慎乐观"
        elif bearish / total_rated > 0.4:
            result["consensus"] = "偏空"
        elif bearish / total_rated > 0.6:
            result["consensus"] = "看空"
        else:
            result["consensus"] = "中性分化"

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

    _broker_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 3. 个股研报查询（按需调用）
# ============================================================

def get_stock_reports(code: str, limit: int = 5) -> list:
    """获取个股的最新研报"""
    cache_key = f"stock_reports_{code}"
    now = time.time()
    if cache_key in _broker_cache and now - _broker_cache[cache_key]["ts"] < _BROKER_CACHE_TTL:
        return _broker_cache[cache_key]["data"]

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

    _broker_cache[cache_key] = {"data": reports, "ts": now}
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
