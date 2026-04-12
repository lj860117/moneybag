"""
钱袋子 — 新闻资讯数据
基金新闻、市场新闻、政策新闻、影响分析
"""
import time
from datetime import datetime, timedelta
from config import NEWS_CACHE_TTL

_news_cache = {}


def get_fund_news(code: str, limit: int = 3) -> list:
    """获取基金/市场相关新闻"""
    cache_key = f"news_{code}"
    now = time.time()
    if cache_key in _news_cache and now - _news_cache[cache_key]["ts"] < NEWS_CACHE_TTL:
        return _news_cache[cache_key]["data"]

    # 基金代码到关键词映射
    keyword_map = {
        "110020": "沪深300",
        "050025": "标普500",
        "217022": "债券",
        "000216": "黄金",
        "008114": "红利低波",
    }
    keyword = keyword_map.get(code, "基金")
    news_list = []

    try:
        import akshare as ak
        # 尝试获取财经新闻
        try:
            df = ak.stock_news_em(symbol=keyword)
            if df is not None and len(df) > 0:
                for _, row in df.head(limit).iterrows():
                    title_col = [c for c in df.columns if "标题" in c or "title" in c.lower()]
                    time_col = [c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c]
                    source_col = [c for c in df.columns if "来源" in c or "source" in c.lower() or "文章来源" in c]
                    url_col = [c for c in df.columns if "链接" in c or "url" in c.lower() or "新闻链接" in c]
                    news_list.append({
                        "title": str(row[title_col[0]]) if title_col else str(row.iloc[0]),
                        "time": str(row[time_col[0]]) if time_col else "",
                        "source": str(row[source_col[0]]) if source_col else "东方财富",
                        "url": str(row[url_col[0]]) if url_col else "",
                    })
        except Exception as e:
            print(f"[NEWS] stock_news_em failed for {keyword}: {e}")

        # 黄金专用新闻源
        if code == "000216" and not news_list:
            try:
                df = ak.futures_news_shmet(symbol="黄金")
                if df is not None and len(df) > 0:
                    for _, row in df.head(limit).iterrows():
                        title_col = [c for c in df.columns if "标题" in c or "title" in c.lower()]
                        news_list.append({
                            "title": str(row[title_col[0]]) if title_col else str(row.iloc[0]),
                            "time": "",
                            "source": "上海金属网",
                        })
            except Exception:
                pass

    except Exception as e:
        print(f"[NEWS] Failed: {e}")

    # 如果 AKShare 新闻不可用，返回默认提示
    if not news_list:
        news_list = [{"title": f"{keyword}市场动态获取中...", "time": "", "source": "系统"}]

    _news_cache[cache_key] = {"data": news_list, "ts": now}
    return news_list


def get_market_news(limit: int = 10) -> list:
    """获取综合市场新闻（优先 A 股相关，过滤无用信息）"""
    cache_key = "market_news_all"
    now = time.time()
    if cache_key in _news_cache and now - _news_cache[cache_key]["ts"] < NEWS_CACHE_TTL:
        return _news_cache[cache_key]["data"]

    # 标题中包含这些词的直接排除
    EXCLUDE_KEYWORDS = ["荷兰", "伦敦股市", "日经", "纽约股市", "法兰克福", "巴黎股市"]

    def _is_useful(title: str) -> bool:
        """排除明显无关的海外市场新闻"""
        return not any(kw in title for kw in EXCLUDE_KEYWORDS)

    def _extract_news(df, max_n):
        """从 DataFrame 提取新闻列表"""
        results = []
        if df is None or len(df) == 0:
            return results
        title_col = next((c for c in df.columns if "标题" in c or "title" in c.lower()), df.columns[0])
        time_col = next((c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c), None)
        source_col = next((c for c in df.columns if "来源" in c or "source" in c.lower()), None)
        url_col = next((c for c in df.columns if "链接" in c or "url" in c.lower()), None)
        seen = set()
        for _, row in df.iterrows():
            title = str(row[title_col]).strip()
            if not title or title in seen:
                continue
            if not _is_useful(title):
                continue
            seen.add(title)
            results.append({
                "title": title,
                "time": str(row[time_col]) if time_col else "",
                "source": str(row[source_col]) if source_col else "东方财富",
                "url": str(row[url_col]) if url_col else "",
            })
            if len(results) >= max_n:
                break
        return results

    all_news = []
    try:
        import akshare as ak
        # 优先：A 股市场新闻（质量最高）
        try:
            df = ak.stock_news_em(symbol="A股")
            all_news.extend(_extract_news(df, limit))
            print(f"[NEWS] A股: got {len(all_news)}")
        except Exception as e:
            print(f"[NEWS] A股 failed: {e}")

        # 补充：财经新闻（如果 A 股不够）
        if len(all_news) < limit:
            try:
                df = ak.stock_news_em(symbol="财经")
                existing_titles = {n["title"] for n in all_news}
                extras = _extract_news(df, limit - len(all_news))
                extras = [n for n in extras if n["title"] not in existing_titles]
                all_news.extend(extras)
                print(f"[NEWS] 财经补充: +{len(extras)}")
            except Exception as e:
                print(f"[NEWS] 财经 failed: {e}")
    except Exception as e:
        print(f"[NEWS] import failed: {e}")

    if not all_news:
        all_news = [{"title": "市场资讯加载中...", "time": "", "source": "系统"}]

    _news_cache[cache_key] = {"data": all_news, "ts": now}
    return all_news


# ---- 宏观经济日历 ----

# ---- 宏观经济日历 ----

def get_policy_news(limit: int = 8) -> list:
    """获取政策经济新闻（政府经济政策 + 中美贸易/外交）"""
    cache_key = "policy_news"
    now = time.time()
    if cache_key in _news_cache and now - _news_cache[cache_key]["ts"] < NEWS_CACHE_TTL:
        return _news_cache[cache_key]["data"]

    POLICY_KEYWORDS = ["政策", "央行", "国务院", "财政", "降准", "降息", "LPR",
                       "关税", "贸易", "制裁", "外交", "中美", "特朗普", "拜登",
                       "战争", "地缘", "OPEC", "美联储", "加息", "缩表",
                       "刺激", "基建", "新质", "科技", "半导体", "芯片"]

    all_news = []
    try:
        import akshare as ak

        # 源1：财经新闻中筛选政策相关
        try:
            df = ak.stock_news_em(symbol="财经")
            if df is not None and len(df) > 0:
                title_col = next((c for c in df.columns if "标题" in c or "title" in c.lower()), df.columns[0])
                time_col = next((c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c), None)
                source_col = next((c for c in df.columns if "来源" in c or "source" in c.lower()), None)
                url_col = next((c for c in df.columns if "链接" in c or "url" in c.lower()), None)
                for _, row in df.iterrows():
                    title = str(row[title_col]).strip()
                    if any(kw in title for kw in POLICY_KEYWORDS):
                        all_news.append({
                            "title": title,
                            "time": str(row[time_col]) if time_col else "",
                            "source": str(row[source_col]) if source_col else "东方财富",
                            "url": str(row[url_col]) if url_col else "",
                            "category": "policy" if any(kw in title for kw in ["政策", "央行", "国务院", "财政", "降准", "降息", "LPR", "刺激", "基建"]) else "international",
                        })
                    if len(all_news) >= limit:
                        break
        except Exception as e:
            print(f"[POLICY_NEWS] stock_news_em(财经) failed: {e}")

        # 源2：A股新闻中补充政策类
        if len(all_news) < limit:
            try:
                df = ak.stock_news_em(symbol="A股")
                if df is not None and len(df) > 0:
                    title_col = next((c for c in df.columns if "标题" in c or "title" in c.lower()), df.columns[0])
                    time_col = next((c for c in df.columns if "时间" in c or "date" in c.lower() or "发布" in c), None)
                    source_col = next((c for c in df.columns if "来源" in c or "source" in c.lower()), None)
                    url_col = next((c for c in df.columns if "链接" in c or "url" in c.lower()), None)
                    existing_titles = {n["title"] for n in all_news}
                    for _, row in df.iterrows():
                        title = str(row[title_col]).strip()
                        if title in existing_titles:
                            continue
                        if any(kw in title for kw in POLICY_KEYWORDS):
                            all_news.append({
                                "title": title,
                                "time": str(row[time_col]) if time_col else "",
                                "source": str(row[source_col]) if source_col else "东方财富",
                                "url": str(row[url_col]) if url_col else "",
                                "category": "policy" if any(kw in title for kw in ["政策", "央行", "国务院", "财政", "降准", "降息", "LPR", "刺激", "基建"]) else "international",
                            })
                        if len(all_news) >= limit:
                            break
            except Exception as e:
                print(f"[POLICY_NEWS] stock_news_em(A股) failed: {e}")

    except Exception as e:
        print(f"[POLICY_NEWS] Fatal: {e}")

    if not all_news:
        all_news = [{"title": "政策资讯加载中...", "time": "", "source": "系统", "category": "policy"}]

    print(f"[POLICY_NEWS] Got {len(all_news)} items")
    _news_cache[cache_key] = {"data": all_news, "ts": now}
    return all_news


@app.get("/api/news/policy")
def get_policy_news_api(limit: int = 8):
    """获取政策 & 国际新闻（政府经济政策 + 中美贸易外交 + 地缘冲突）"""
    return {"news": get_policy_news(limit)}


# ---- 新闻→行业→基金 关联分析引擎 ----

# 事件→行业→基金映射表（核心知识库）
NEWS_IMPACT_MAP = [
    {"keywords": ["降准", "降息", "LPR", "宽松", "流动性"],
     "impact": "利好：银行间流动性增加，利率下行推动股债双牛",
     "bullish": ["110020", "217022"], "bearish": [],
     "sectors": ["银行", "地产", "债券"], "tag": "货币宽松"},
    {"keywords": ["加息", "缩表", "收紧", "美联储鹰派"],
     "impact": "利空：流动性收紧，成长股承压，美元走强",
     "bullish": ["000216"], "bearish": ["050025", "110020"],
     "sectors": ["黄金避险"], "tag": "货币收紧"},
    {"keywords": ["关税", "贸易战", "制裁", "中美对抗"],
     "impact": "出口承压，内需消费和国产替代受益",
     "bullish": ["110020", "008114"], "bearish": ["050025"],
     "sectors": ["内需消费", "国产替代"], "tag": "贸易摩擦"},
    {"keywords": ["半导体", "芯片", "科技自主", "AI", "人工智能"],
     "impact": "科技板块活跃，相关ETF受益",
     "bullish": ["110020"], "bearish": [],
     "sectors": ["科技", "半导体", "新能源"], "tag": "科技政策"},
    {"keywords": ["战争", "地缘", "冲突", "中东", "俄乌"],
     "impact": "避险情绪升温，黄金和债券受益",
     "bullish": ["000216", "217022"], "bearish": ["110020", "050025"],
     "sectors": ["黄金", "债券", "军工"], "tag": "地缘风险"},
    {"keywords": ["刺激", "基建", "财政扩张", "国务院", "发改委"],
     "impact": "财政刺激利好周期股和基建链",
     "bullish": ["110020", "008114"], "bearish": [],
     "sectors": ["基建", "周期", "红利"], "tag": "财政刺激"},
    {"keywords": ["油价", "OPEC", "原油", "大宗商品"],
     "impact": "大宗商品价格影响通胀预期和周期股",
     "bullish": ["000216", "008114"], "bearish": ["217022"],
     "sectors": ["能源", "黄金", "通胀链"], "tag": "大宗商品"},
    {"keywords": ["房地产", "楼市", "限购", "房贷"],
     "impact": "地产政策影响金融和消费",
     "bullish": ["110020"], "bearish": [],
     "sectors": ["地产", "银行", "建材"], "tag": "地产政策"},
    {"keywords": ["汇率", "人民币", "贬值", "升值", "外汇"],
     "impact": "汇率波动影响QDII基金和外贸企业",
     "bullish": [], "bearish": [],
     "sectors": ["外贸", "QDII"], "tag": "汇率波动"},
]

# 基金代码→名称映射
FUND_NAME_MAP = {
    "110020": "沪深300", "050025": "标普500", "217022": "债券",
    "000216": "黄金", "008114": "红利低波"
}


def analyze_news_impact(news_list: list) -> list:
    """分析新闻对持仓基金的影响"""
    impacts = []
    seen_tags = set()
    for n in news_list:
        title = n.get("title", "")
        for rule in NEWS_IMPACT_MAP:
            if any(kw in title for kw in rule["keywords"]) and rule["tag"] not in seen_tags:
                bullish_names = [FUND_NAME_MAP.get(c, c) for c in rule["bullish"]]
                bearish_names = [FUND_NAME_MAP.get(c, c) for c in rule["bearish"]]
                impacts.append({
                    "trigger": title[:40] + ("..." if len(title) > 40 else ""),
                    "tag": rule["tag"],
                    "impact": rule["impact"],
                    "bullish": bullish_names,
                    "bearish": bearish_names,
                    "sectors": rule["sectors"],
                    "bullish_codes": rule["bullish"],
                    "bearish_codes": rule["bearish"],
                })
                seen_tags.add(rule["tag"])
                break
    return impacts


@app.get("/api/news/impact")
def get_news_impact_api():
    """分析最新新闻对持仓基金的影响"""
    policy_news = get_policy_news(15)
    market_news = get_market_news(10)
    all_news = policy_news + market_news
    impacts = analyze_news_impact(all_news)
    return {
        "impacts": impacts[:8],
        "total_news_analyzed": len(all_news),
        "timestamp": datetime.now().isoformat(),
    }



