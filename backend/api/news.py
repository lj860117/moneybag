"""
新闻 & 数据 API（新闻 / 基金信息 / 政策 / 技术指标 / 宏观 / 基金搜索）
====================================================================
从 main.py 提取的 P2 路由。

Design doc: docs/design/12-framework-refactor.md §四
"""
import time
from datetime import datetime

from fastapi import APIRouter

router = APIRouter(tags=["新闻与数据"])

from services.data_layer import (
    get_fund_news, get_market_news, get_fund_dynamic_info,
    get_policy_news, analyze_news_impact, get_technical_indicators,
    get_macro_calendar,
    _macro_cache as macro_cache,
    _nav_cache as nav_cache,
)
from services.ds_enhance import (
    deep_analyze_news_impact, assess_news_risk,
)


# ---- 新闻 ----

@router.get("/api/news/portfolio")
def get_portfolio_news():
    """获取所有持仓基金的相关新闻"""
    codes = ["110020", "050025", "217022", "000216", "008114"]
    result = {}
    for code in codes:
        result[code] = get_fund_news(code, 3)
    return result


@router.get("/api/news/{code}")
def get_news_by_fund(code: str, limit: int = 3):
    """获取单只基金相关新闻"""
    return {"code": code, "news": get_fund_news(code, limit)}


@router.get("/api/news")
def get_all_news(limit: int = 10):
    """获取综合市场新闻"""
    return {"news": get_market_news(limit)}


# ---- 基金动态数据 ----

@router.get("/api/fund/info/{code}")
def get_fund_info(code: str):
    """获取基金动态信息（收益率、净值等）"""
    return get_fund_dynamic_info(code)


@router.get("/api/fund/info-batch")
def get_fund_info_batch(codes: str = ""):
    """批量获取基金动态信息，codes 用逗号分隔"""
    if not codes:
        return {"funds": {}}
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    result = {}
    for code in code_list:
        result[code] = get_fund_dynamic_info(code)
    return {"funds": result, "updatedAt": datetime.now().strftime("%Y-%m-%d")}


# ---- 政策新闻 & 影响分析 ----

@router.get("/api/news/policy")
def get_policy_news_api(limit: int = 8):
    """获取政策 & 国际新闻"""
    return {"news": get_policy_news(limit)}


@router.get("/api/news/impact")
def get_news_impact_api():
    """分析最新新闻对持仓基金的影响（30分钟缓存）"""
    cache_key = "news_impact"
    now = time.time()
    cached = macro_cache.get(cache_key)
    if cached is not None:
        return cached
    policy_news = get_policy_news(15)
    market_news = get_market_news(10)
    all_news = policy_news + market_news
    impacts = analyze_news_impact(all_news)
    result = {
        "impacts": impacts[:8],
        "total_news_analyzed": len(all_news),
        "timestamp": datetime.now().isoformat(),
    }
    macro_cache.set(cache_key, result, ttl=1800)
    return result


@router.get("/api/news/deep-impact")
def get_deep_news_impact():
    """新闻深度影响分析 — DeepSeek 分析事件→行业→持仓"""
    policy = get_policy_news(8)
    market = get_market_news(5)
    return {"impacts": deep_analyze_news_impact(policy + market)}


@router.get("/api/news/risk-assess")
def get_news_risk():
    """新闻风控评估 — DeepSeek 评估新闻对持仓风险影响"""
    policy = get_policy_news(8)
    market = get_market_news(5)
    headlines = [n["title"] for n in (policy + market) if "加载中" not in n.get("title", "")]
    return assess_news_risk(headlines)


# ---- 技术指标 ----

@router.get("/api/technical")
def get_tech_indicators():
    """获取沪深300技术指标（RSI/MACD/布林带）"""
    return get_technical_indicators()


# ---- 宏观经济日历 ----

@router.get("/api/macro")
def get_macro_data():
    """获取宏观经济事件日历"""
    return {"events": get_macro_calendar()}


# ---- 基金搜索 ----

@router.get("/api/fund/search")
def search_fund(q: str = ""):
    """基金搜索 — 输入关键词/代码返回基金列表"""
    if not q or len(q) < 2:
        return {"results": []}

    cache_key = f"fund_search_{q}"
    now = time.time()
    cached = nav_cache.get(cache_key)
    if cached is not None:
        return {"results": cached}

    results = []
    try:
        import akshare as ak
        df = ak.fund_name_em()
        if df is not None and len(df) > 0:
            code_col = [c for c in df.columns if "代码" in c or "code" in c.lower()]
            name_col = [c for c in df.columns if "名称" in c or "简称" in c or "name" in c.lower()]
            type_col = [c for c in df.columns if "类型" in c or "type" in c.lower()]

            if code_col and name_col:
                cc, nc = code_col[0], name_col[0]
                tc = type_col[0] if type_col else None
                mask = df[cc].astype(str).str.contains(q) | df[nc].astype(str).str.contains(q, case=False)
                matched = df[mask].head(20)
                for _, row in matched.iterrows():
                    results.append({
                        "code": str(row[cc]),
                        "name": str(row[nc]),
                        "type": str(row[tc]) if tc else "",
                    })
    except Exception as e:
        print(f"[FUND_SEARCH] {e}")
        # 降级：用本地缓存搜
        try:
            from services.data_layer import _load_fund_rank_data
            rank_data = _load_fund_rank_data()
            if rank_data:
                for item in rank_data:
                    code = item.get("code", "")
                    name = item.get("name", "")
                    if q in code or q.lower() in name.lower():
                        results.append({"code": code, "name": name, "type": item.get("type", "")})
                    if len(results) >= 20:
                        break
        except Exception:
            pass

    if results:
        nav_cache.set(cache_key, results, ttl=86400)

    return {"results": results}
