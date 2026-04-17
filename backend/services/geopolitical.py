"""
钱袋子 — 地缘政治/重大事件追踪
V6 Phase 1 新增模块

职责：
  地缘事件抓取 + 关键词分类 + 严重性评级 + A股影响链
  severity >= 3 才调 LLM 做精细评估（省钱）

数据源：AKShare 新闻 + 关键词规则引擎
接入点：Pipeline enrich() → DecisionContext.modules_results["geopolitical"]
"""
import time
from datetime import datetime, timedelta

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "geopolitical",
    "scope": "public",
    "input": [],
    "output": "geopolitical_risk",
    "cost": "llm_light",       # severity >= 3 时调一次 LLM
    "tags": ["地缘", "风险事件", "黑天鹅"],
    "description": "地缘政治事件追踪+严重性评估+A股影响链",
    "layer": "data",
    "priority": 1,
}

from config import NEWS_CACHE_TTL

_geo_cache = {}
_GEO_CACHE_TTL = 1800  # 30 分钟


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 关键词体系
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GEO_EVENT_CATEGORIES = {
    "军事冲突": {
        "keywords": [
            "战争", "军事", "空袭", "导弹", "入侵", "开战", "停火",
            "以色列", "伊朗", "中东", "俄乌", "台海", "朝鲜",
            "霍尔木兹", "红海", "南海", "胡塞",
        ],
        "base_severity": 4,
    },
    "制裁升级": {
        "keywords": [
            "制裁", "禁运", "封锁", "脱钩", "出口管制", "实体清单",
            "芯片禁令", "技术封锁",
        ],
        "base_severity": 3,
    },
    "能源危机": {
        "keywords": [
            "石油危机", "天然气", "能源安全", "断供", "管道",
            "OPEC减产", "油价暴涨", "能源短缺", "OPEC",
        ],
        "base_severity": 4,
    },
    "金融风险": {
        "keywords": [
            "银行倒闭", "债务危机", "主权违约", "资本外逃",
            "汇率崩盘", "流动性危机",
        ],
        "base_severity": 3,
    },
    "贸易摩擦": {
        "keywords": [
            "关税", "贸易战", "报复", "反倾销", "WTO",
            "中美", "特朗普", "拜登",
        ],
        "base_severity": 2,
    },
}

# 地缘 → 行业影响映射表
GEO_SECTOR_IMPACT = {
    "军事冲突": {
        "bullish": ["黄金", "军工", "石油", "债券"],
        "bearish": ["航空", "旅游", "消费", "科技"],
        "a_share_impact": "避险情绪升温，资金从成长转向防御",
    },
    "制裁升级": {
        "bullish": ["国产替代", "军工", "信创"],
        "bearish": ["半导体", "消费电子", "出口链"],
        "a_share_impact": "科技脱钩压力，国产替代逻辑强化",
    },
    "能源危机": {
        "bullish": ["石油", "煤炭", "新能源", "黄金"],
        "bearish": ["航空", "化工", "运输", "消费"],
        "a_share_impact": "输入性通胀压力，央行政策空间收窄",
    },
    "金融风险": {
        "bullish": ["黄金", "债券", "现金"],
        "bearish": ["银行", "地产", "保险"],
        "a_share_impact": "流动性收紧恐慌，金融板块承压",
    },
    "贸易摩擦": {
        "bullish": ["内需消费", "国产替代"],
        "bearish": ["出口链", "外贸", "跨境电商"],
        "a_share_impact": "出口承压，内需消费和国产替代受益",
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 核心函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_geopolitical_events(limit: int = 30) -> dict:
    """抓取地缘新闻 + 分类 + 评级（纯规则，0 LLM）

    Returns:
        {
            "events": [{title, time, source, categories, severity, ...}],
            "risk_level": "normal/elevated/high/critical",
            "top_category": "军事冲突" | None,
            "event_count": int,
            "available": bool,
        }
    """
    cache_key = "geo_events"
    now = time.time()
    if cache_key in _geo_cache and now - _geo_cache[cache_key]["ts"] < _GEO_CACHE_TTL:
        return _geo_cache[cache_key]["data"]

    events = []
    all_titles_seen = set()

    try:
        import akshare as ak

        # 源1: 财经新闻
        try:
            df = ak.stock_news_em(symbol="财经")
            if df is not None and len(df) > 0:
                events.extend(_extract_geo_events(df, all_titles_seen, limit))
        except Exception as e:
            print(f"[GEO] stock_news_em(财经) failed: {e}")

        # 源2: A股新闻补充
        if len(events) < limit // 2:
            try:
                df = ak.stock_news_em(symbol="A股")
                if df is not None and len(df) > 0:
                    events.extend(_extract_geo_events(df, all_titles_seen, limit - len(events)))
            except Exception as e:
                print(f"[GEO] stock_news_em(A股) failed: {e}")

    except Exception as e:
        print(f"[GEO] akshare import failed: {e}")

    # 计算总体风险等级
    max_severity = max((e["severity"] for e in events), default=0)
    multi_category = len(set(cat for e in events for cat in e["categories"])) > 1

    # 多类别同时命中 → severity +1
    if multi_category and max_severity > 0:
        max_severity = min(5, max_severity + 1)

    risk_level = _severity_to_risk_level(max_severity)
    top_category = events[0]["categories"][0] if events and events[0]["categories"] else None

    result = {
        "events": events,
        "risk_level": risk_level,
        "max_severity": max_severity,
        "top_category": top_category,
        "event_count": len(events),
        "available": True,
        "updated_at": datetime.now().isoformat(),
    }

    print(f"[GEO] 抓取 {len(events)} 条地缘事件, 风险={risk_level}, 最高severity={max_severity}")
    _geo_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_geopolitical_risk_score() -> dict:
    """输出综合地缘风险分 0-100

    Returns:
        {
            "score": 0-100,
            "level": "low/moderate/high/extreme",
            "top_events": [...],
            "sector_impact": {...},
            "available": bool,
        }
    """
    geo_data = get_geopolitical_events()
    if not geo_data["available"] or not geo_data["events"]:
        return {
            "score": 0,
            "level": "low",
            "top_events": [],
            "sector_impact": {},
            "available": True,
        }

    # 风险分 = max_severity * 20（满分 100）
    max_sev = geo_data["max_severity"]
    score = min(100, max_sev * 20)

    # 事件密度加分（同一轮超过 5 条地缘新闻 → +10）
    if geo_data["event_count"] >= 5:
        score = min(100, score + 10)

    # 行业影响
    sector_impact = {}
    for event in geo_data["events"][:5]:
        for cat in event["categories"]:
            if cat in GEO_SECTOR_IMPACT:
                impact = GEO_SECTOR_IMPACT[cat]
                sector_impact[cat] = impact

    level = "low"
    if score >= 80:
        level = "extreme"
    elif score >= 60:
        level = "high"
    elif score >= 30:
        level = "moderate"

    return {
        "score": score,
        "level": level,
        "top_events": [
            {"title": e["title"][:60], "severity": e["severity"], "category": e["categories"][0]}
            for e in geo_data["events"][:3]
        ],
        "sector_impact": sector_impact,
        "available": True,
    }


def get_geo_impact_on_sectors() -> dict:
    """地缘 → 行业 → 持仓影响链

    Returns:
        {
            "has_impact": bool,
            "impacts": [{category, bullish, bearish, a_share_impact}],
            "summary": str,
        }
    """
    geo_data = get_geopolitical_events()
    if not geo_data["events"]:
        return {"has_impact": False, "impacts": [], "summary": "当前无明显地缘风险事件"}

    seen_cats = set()
    impacts = []
    for event in geo_data["events"]:
        for cat in event["categories"]:
            if cat not in seen_cats and cat in GEO_SECTOR_IMPACT:
                seen_cats.add(cat)
                impact = GEO_SECTOR_IMPACT[cat]
                impacts.append({
                    "category": cat,
                    "trigger_event": event["title"][:50],
                    "bullish": impact["bullish"],
                    "bearish": impact["bearish"],
                    "a_share_impact": impact["a_share_impact"],
                })

    if not impacts:
        return {"has_impact": False, "impacts": [], "summary": "地缘新闻未触发行业影响规则"}

    # 汇总
    all_bullish = list(set(s for i in impacts for s in i["bullish"]))
    all_bearish = list(set(s for i in impacts for s in i["bearish"]))
    summary = f"地缘风险影响：看多{'/'.join(all_bullish[:3])}，看空{'/'.join(all_bearish[:3])}"

    return {
        "has_impact": True,
        "impacts": impacts,
        "summary": summary,
        "bullish_sectors": all_bullish,
        "bearish_sectors": all_bearish,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pipeline enrich() — 核心接入函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def enrich(ctx):
    """Pipeline Layer2 自动调用 — 把地缘数据注入 DecisionContext

    接口规范：
      - 从 ctx 不需要读什么（scope=public）
      - 把结果写入 ctx.modules_results["geopolitical"]
      - 返回修改后的 ctx
    """
    try:
        risk = get_geopolitical_risk_score()
        sector_impact = get_geo_impact_on_sectors()
        events = get_geopolitical_events()

        # 方向判断：地缘风险高 → bearish
        score = risk.get("score", 0)
        if score >= 60:
            direction = "bearish"
        elif score >= 30:
            direction = "neutral"
        else:
            direction = "bullish"

        ctx.modules_results["geopolitical"] = {
            "direction": direction,
            "score": max(0, 50 - score),  # 风险越高分越低（0-50 scale）
            "confidence": min(80, 30 + score),
            "available": True,
            "detail": risk.get("level", "low") + f" (score={score})",
            "risk_score": score,
            "risk_level": risk.get("level", "low"),
            "top_events": risk.get("top_events", []),
            "sector_impact": sector_impact,
            "event_count": events.get("event_count", 0),
            "max_severity": events.get("max_severity", 0),
        }

        if "geopolitical" not in ctx.modules_called:
            ctx.modules_called.append("geopolitical")

    except Exception as e:
        print(f"[GEO] enrich failed: {e}")
        ctx.modules_results["geopolitical"] = {
            "available": False,
            "error": str(e),
            "direction": "neutral",
            "score": 0,
        }

    return ctx


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 内部辅助函数
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _extract_geo_events(df, seen_titles: set, max_count: int) -> list:
    """从 AKShare 新闻 DataFrame 提取地缘相关事件"""
    events = []
    if df is None or len(df) == 0:
        return events

    title_col = next((c for c in df.columns if "标题" in c or "title" in c.lower()), df.columns[0])
    time_col = next((c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c), None)
    source_col = next((c for c in df.columns if "来源" in c or "source" in c.lower()), None)

    for _, row in df.iterrows():
        title = str(row[title_col]).strip()
        if not title or title in seen_titles:
            continue

        # 匹配地缘关键词
        matched_categories = []
        max_severity = 0
        for cat_name, cat_info in GEO_EVENT_CATEGORIES.items():
            if any(kw in title for kw in cat_info["keywords"]):
                matched_categories.append(cat_name)
                max_severity = max(max_severity, cat_info["base_severity"])

        if not matched_categories:
            continue

        seen_titles.add(title)
        events.append({
            "title": title,
            "time": str(row[time_col]) if time_col else "",
            "source": str(row[source_col]) if source_col else "东方财富",
            "categories": matched_categories,
            "severity": max_severity,
        })

        if len(events) >= max_count:
            break

    # 按 severity 降序排列
    events.sort(key=lambda x: x["severity"], reverse=True)
    return events


def _severity_to_risk_level(severity: int) -> str:
    """严重性分数 → 风险等级"""
    if severity >= 5:
        return "critical"
    elif severity >= 4:
        return "high"
    elif severity >= 3:
        return "elevated"
    elif severity >= 2:
        return "normal"
    return "normal"
