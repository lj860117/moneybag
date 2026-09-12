"""
钱袋子 — 新闻资讯数据
基金新闻、市场新闻、政策新闻、影响分析
V6 Phase 2: 新增 enrich() + NEWS_IMPACT_MAP 扩展（9→14条规则）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "news_data",
    "scope": "public",
    "input": [],
    "output": "news",
    "cost": "cpu",
    "tags": ["新闻", "情绪", "影响分析", "地缘", "大宗", "政策"],
    "description": "新闻资讯+14条影响规则映射+持仓新闻+Pipeline enrich",
    "layer": "data",
    "priority": 2,
}
import re
import time
from datetime import datetime, timedelta
from config import NEWS_CACHE_TTL
from infra.cache import MemoryCache

_news_cache = MemoryCache(default_ttl=NEWS_CACHE_TTL)

# 政策级判定关键词（原为 get_policy_news() 内部变量，P2-13 提到模块级复用）：
# 重要度排序里的"政策级"必须和"政策新闻"接口用同一套判据，
# 不能同一条新闻在一个地方算政策、在另一个地方算普通。
# PROVENANCE: [经验值] 人工枚举，未做统计校准。
POLICY_KEYWORDS = ["政策", "央行", "国务院", "财政", "降准", "降息", "LPR",
                   "关税", "贸易", "制裁", "外交", "中美", "特朗普", "拜登",
                   "战争", "地缘", "OPEC", "美联储", "加息", "缩表",
                   "刺激", "基建", "新质", "科技", "半导体", "芯片"]


def get_fund_news(code: str, limit: int = 3) -> list:
    """获取基金/市场相关新闻"""
    cache_key = f"news_{code}"
    now = time.time()
    cached = _news_cache.get(cache_key)
    if cached is not None:
        return cached

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
        from infra.data_source.macro.indicators import get_stock_news
        # 尝试获取财经新闻
        try:
            df = get_stock_news(symbol=keyword)
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
                from infra.data_source.alt.flows import get_futures_news
                df = get_futures_news(symbol="黄金")
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
    # Return empty instead of fake "loading..." message
    if not news_list:
        news_list = []

    _news_cache.set(cache_key, news_list)
    return news_list


def get_market_news(limit: int = 30) -> list:
    """获取综合市场新闻（优先 A 股相关，过滤无用信息）

    缓存策略：始终抓取最多 _MAX_FETCH 条并全量缓存，调用方的 limit 仅决定返回条数，
    不影响缓存内容，避免 get_market_news(5) 写入缓存后后续调用无法获得更多条目的问题。
    """
    _MAX_FETCH = 30  # 每次抓取上限（与 API 源返回量匹配）
    cache_key = "market_news_all"
    cached = _news_cache.get(cache_key)
    if cached is not None:
        return cached[:limit]

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
        from infra.data_source.macro.indicators import get_stock_news
        # 优先：A 股市场新闻（质量最高）
        try:
            df = get_stock_news(symbol="A股")
            all_news.extend(_extract_news(df, _MAX_FETCH))
            print(f"[NEWS] A股: got {len(all_news)}")
        except Exception as e:
            print(f"[NEWS] A股 failed: {e}")

        # 补充：财经新闻（如果 A 股不够）
        if len(all_news) < _MAX_FETCH:
            try:
                df = get_stock_news(symbol="财经")
                existing_titles = {n["title"] for n in all_news}
                extras = _extract_news(df, _MAX_FETCH - len(all_news))
                extras = [n for n in extras if n["title"] not in existing_titles]
                all_news.extend(extras)
                print(f"[NEWS] 财经补充: +{len(extras)}")
            except Exception as e:
                print(f"[NEWS] 财经 failed: {e}")
    except Exception as e:
        print(f"[NEWS] import failed: {e}")

    # Return empty instead of fake "loading..." message
    if not all_news:
        # 降级: 读取上次成功获取的新闻文件缓存
        try:
            from config import DATA_DIR
            news_cache_file = DATA_DIR / "cache" / "news_latest.json"
            if news_cache_file.exists():
                import json
                cached_news = json.loads(news_cache_file.read_text(encoding="utf-8"))
                if cached_news and isinstance(cached_news, list):
                    # 标注为旧数据
                    for n in cached_news:
                        n["_stale"] = True
                    print(f"[NEWS] 降级至文件缓存: {len(cached_news)} 条旧新闻")
                    _news_cache.set(cache_key, cached_news)
                    return cached_news[:limit]
        except Exception:
            pass
        all_news = []
    else:
        # 成功获取，写入文件缓存供降级使用
        try:
            from config import DATA_DIR
            import json
            cache_dir = DATA_DIR / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "news_latest.json").write_text(
                json.dumps(all_news, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    # 全量缓存（_MAX_FETCH 条），limit 仅在返回时切片
    _news_cache.set(cache_key, all_news)
    return all_news[:limit]


# ---- 宏观经济日历 ----

# ---- 宏观经济日历 ----

def get_policy_news(limit: int = 20) -> list:
    """获取政策经济新闻（政府经济政策 + 中美贸易/外交）"""
    cache_key = "policy_news"
    now = time.time()
    cached = _news_cache.get(cache_key)
    if cached is not None:
        return cached

    all_news = []
    try:
        from infra.data_source.macro.indicators import get_stock_news

        # 源1：财经新闻中筛选政策相关
        try:
            df = get_stock_news(symbol="财经")
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
                df = get_stock_news(symbol="A股")
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

    # Return empty instead of fake "loading..." message
    if not all_news:
        all_news = []

    print(f"[POLICY_NEWS] Got {len(all_news)} items")
    _news_cache.set(cache_key, all_news)
    return all_news



# ---- 新闻→行业→基金 关联分析引擎 ----

# 事件→行业→基金映射表（核心知识库, V6 Phase 2 扩展）
NEWS_IMPACT_MAP = [
    {"keywords": ["降准", "降息", "LPR", "宽松", "流动性"],
     "impact": "利好：银行间流动性增加，利率下行推动股债双牛",
     "bullish": ["110020", "217022"], "bearish": [],
     "sectors": ["银行", "地产", "债券"], "tag": "货币宽松"},
    {"keywords": ["加息", "缩表", "收紧", "美联储鹰派"],
     "impact": "利空：流动性收紧，成长股承压，美元走强",
     "bullish": ["000216"], "bearish": ["050025", "110020"],
     "sectors": ["黄金避险"], "tag": "货币收紧"},
    {"keywords": ["关税", "贸易战", "制裁", "中美对抗", "出口管制", "实体清单"],
     "impact": "出口承压，内需消费和国产替代受益",
     "bullish": ["110020", "008114"], "bearish": ["050025"],
     "sectors": ["内需消费", "国产替代"], "tag": "贸易摩擦"},
    {"keywords": ["半导体", "芯片", "科技自主", "AI", "人工智能", "算力"],
     "impact": "科技板块活跃，相关ETF受益",
     "bullish": ["110020"], "bearish": [],
     "sectors": ["科技", "半导体", "新能源"], "tag": "科技政策"},
    {"keywords": ["战争", "地缘", "冲突", "中东", "俄乌", "台海", "军事",
                   "空袭", "导弹", "以色列", "伊朗", "红海", "霍尔木兹", "胡塞"],
     "impact": "避险情绪升温，黄金和债券受益，航空消费承压",
     "bullish": ["000216", "217022"], "bearish": ["110020", "050025"],
     "sectors": ["黄金", "债券", "军工"], "tag": "地缘风险"},
    {"keywords": ["刺激", "基建", "财政扩张", "国务院", "发改委"],
     "impact": "财政刺激利好周期股和基建链",
     "bullish": ["110020", "008114"], "bearish": [],
     "sectors": ["基建", "周期", "红利"], "tag": "财政刺激"},
    {"keywords": ["油价", "OPEC", "原油", "大宗商品", "石油危机",
                   "OPEC减产", "油价暴涨", "能源危机", "天然气"],
     "impact": "大宗商品价格影响通胀预期和周期股，输入性通胀压力",
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
    # V6 Phase 2 新增规则
    {"keywords": ["印花税", "交易税", "减免"],
     "impact": "印花税调整直接影响交易成本和市场情绪",
     "bullish": ["110020"], "bearish": [],
     "sectors": ["券商", "大盘"], "tag": "印花税调整"},
    {"keywords": ["IPO", "注册制", "新股", "暂停IPO"],
     "impact": "IPO节奏变化影响市场供需和并购预期",
     "bullish": [], "bearish": [],
     "sectors": ["券商", "次新股"], "tag": "IPO政策"},
    {"keywords": ["新能源", "碳中和", "光伏", "风电", "锂电", "储能"],
     "impact": "新能源政策推动绿色转型相关板块",
     "bullish": ["110020"], "bearish": [],
     "sectors": ["新能源", "光伏", "锂电"], "tag": "新能源政策"},
    {"keywords": ["军工", "国防", "军费", "军事装备", "航天"],
     "impact": "军工板块受国防预算和地缘局势双驱动",
     "bullish": ["110020"], "bearish": [],
     "sectors": ["军工", "航天"], "tag": "军工景气"},
    {"keywords": ["银行倒闭", "债务危机", "主权违约", "资本外逃", "流动性危机"],
     "impact": "系统性金融风险，避险资产受益，风险资产承压",
     "bullish": ["000216", "217022"], "bearish": ["110020", "050025"],
     "sectors": ["黄金", "债券"], "tag": "金融风险"},
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


# ============================================================
# 持仓新闻：利好利空标签 + 去重 + 影响映射（v9.9.24 P1-1）
# ============================================================

# 个股/基金新闻的确定性情感词典。
# 为什么要有它：NEWS_IMPACT_MAP 是「宏观政策 → 板块/基金」的映射，对个股新闻
# （如「贵州茅台三季报净利润+15%」）一条都匹配不上。没有这层，持仓新闻的
# 利好利空标签就只能全落「中性」，等于没做。
# 顺序重要：先长词后短词，避免「业绩预增」被「增」类短词抢先命中。
SENTIMENT_LEXICON = {
    "利好": [
        "业绩预增", "净利润增长", "净利润同比", "扭亏为盈", "扭亏", "超预期",
        "中标", "回购", "增持", "分红", "提价", "获批", "涨停", "创新高",
        "大额订单", "扩产", "战略合作", "上调评级", "业绩预喜",
    ],
    "利空": [
        "业绩预减", "净利润下降", "净利润亏损", "预亏", "亏损", "下滑",
        "减持", "诉讼", "被罚", "退市", "问询函", "警示函", "跌停", "创新低",
        "商誉减值", "违规", "召回", "裁员", "债务违约", "业绩暴雷",
    ],
}


def classify_sentiment(title: str, code: str = "") -> tuple:
    """
    判定一条新闻的利好/利空。

    三级判定，全部可追溯（返回 label_source）：
      1. SENTIMENT_LEXICON —— 个股/基金微观事件（业绩、增减持、诉讼…）
      2. NEWS_IMPACT_MAP    —— 宏观政策事件，且本持仓代码命中规则的 bullish/bearish
      3. 都没命中           —— labeled=False，sentiment 记「中性」

    注意第 3 种情况：sentiment=中性 **不等于**「判定为中性」，而是「没判出来」。
    下游（置信度、推送裁决）必须看 labeled 字段，不能把未判定当成中性信号，
    否则又是一次「没有数据 = 没风险」的假绿。

    Returns:
        (sentiment, labeled, label_source, tag)
        sentiment ∈ {"利好", "利空", "中性"}
    """
    text = (title or "").strip()
    if not text:
        return "中性", False, "none", None

    pos_hits = [kw for kw in SENTIMENT_LEXICON["利好"] if kw in text]
    neg_hits = [kw for kw in SENTIMENT_LEXICON["利空"] if kw in text]
    if pos_hits or neg_hits:
        if len(pos_hits) > len(neg_hits):
            return "利好", True, "lexicon", None
        if len(neg_hits) > len(pos_hits):
            return "利空", True, "lexicon", None
        # 多空信号同时出现（如"业绩预增但遭大股东减持"）—— 不猜，标中性并如实说明
        return "中性", True, "lexicon_conflict", None

    # 宏观政策：只有明确命中本持仓代码时才敢下结论
    if code:
        for rule in NEWS_IMPACT_MAP:
            if not any(kw in text for kw in rule["keywords"]):
                continue
            if code in rule["bullish"]:
                return "利好", True, "impact_map", rule["tag"]
            if code in rule["bearish"]:
                return "利空", True, "impact_map", rule["tag"]
            # 命中规则但本持仓不在 bullish/bearish 名单里 → 影响不确定
            return "中性", False, "impact_map_unmapped", rule["tag"]

    return "中性", False, "none", None


def _norm_title(title: str) -> str:
    """标题归一化（用于去重）：去空白 + 去标点，避免同一条新闻因空格/全半角被算成两条"""
    import string as _string
    s = (title or "").strip()
    table = str.maketrans("", "", _string.whitespace + _string.punctuation + "　，。、；：？！“”‘’（）《》")
    return s.translate(table).lower()


def summarize_holdings_news(items: list, use_llm: bool = True, max_tokens: int = 200) -> tuple:
    """
    持仓新闻一句话摘要。

    诚实原则（v9.9.24 P1-1）：LLM 拿不到就 **明确说拿不到**，绝不用
    "市场整体平稳" 这类看起来像结论的模板话术冒充摘要 —— 那正是 P0 系列
    一直在清的"假绿"。

    Returns:
        (summary, source)  source ∈ {"llm", "rule", "none"}
    """
    if not items:
        return "", "none"

    pos = sum(1 for i in items if i.get("sentiment") == "利好")
    neg = sum(1 for i in items if i.get("sentiment") == "利空")
    unlabeled = sum(1 for i in items if not i.get("labeled"))
    fact_line = f"共 {len(items)} 条持仓新闻（利好 {pos} / 利空 {neg} / 未判定 {unlabeled}）"

    if not use_llm:
        return fact_line, "rule"

    try:
        from infra.llm.gateway import LLMGateway
        bullet = "\n".join(
            f"- [{i.get('sentiment', '中性')}] {i.get('title', '')}" for i in items[:15]
        )
        prompt = (
            "下面是某用户持仓相关的最新新闻（方括号内是系统按关键词规则打的利好/利空标签，"
            "仅供参考）。请用一句话（不超过60字）概括这些新闻对该用户持仓的整体影响。\n"
            "要求：只说新闻里真有的事；没有明确方向就说'方向不明确'；"
            "不要编造任何新闻里没有出现的数字、公司名或结论。\n\n"
            f"{bullet}"
        )
        result = LLMGateway.instance().call_sync(
            prompt,
            system="你是严谨的财经资讯编辑，只做摘要，不做投资建议，不编造信息。",
            model_tier="llm_light",
            user_id="",
            module="news_data",
            max_tokens=max_tokens,
        )
        text = (result or {}).get("content", "").strip()
        # fallback=True 表示降级/兜底，内容不可信 → 按拿不到处理
        if text and not result.get("fallback"):
            return text, "llm"
    except Exception as e:
        print(f"[NEWS] holdings news summary failed: {e}")

    return f"{fact_line}；摘要未生成（LLM 不可用）", "rule"


# ============================================================
# v9.9.26 P2-13：事件聚类 + 噪音过滤 + 重要度排序
#
# 三条设计红线：
#   1. 宁可漏合并，不可错合并 —— 两条不同事件被揉成一条，用户会少看到一条真实
#      要闻；漏合并只是多展示一条重复报道。危害不对称，所以阈值取保守值。
#   2. 被过滤的新闻不许凭空消失 —— 必须给出计数 + 命中规则名，可追溯。
#   3. 不许编造权重 —— 所有阈值/权重都是显式常量并写明 PROVENANCE（来源）。
#      [经验值] = 工程经验设定，未用统计方法校准；[派生] = 由其他已存在数据推导。
# ============================================================

# --- 聚类 ---

# 同事件判定阈值：归一化标题 2-gram 集合的 Jaccard 相似度。
# PROVENANCE: [经验值] 未校准。
# 取舍：宁可漏合并不错合并，阈值取在正负样本之间偏保守的位置。
# 标定样本（实测值，见 tests/test_news_cluster_filter_rank.py）：
#   应合并（同一事件的不同报道）: 0.524 / 0.600 / 0.857
#   不得合并（确为不同事件）    : 0.130 / 0.200  ← 由本阈值拦截
#                                0.750（预增vs预减）← 由 OPPOSITE_TERM_PAIRS 拦截
# 注意 0.750 那一组：纯字符相似度对「只差一个反义字」的标题无能为力，
# 所以阈值再怎么调也救不了，必须靠 OPPOSITE_TERM_PAIRS 硬性否决。
CLUSTER_JACCARD_THRESHOLD = 0.50

# 反向词对：字面高度相似、语义完全相反的词。
# PROVENANCE: [经验值] 手工枚举，主要来自 SENTIMENT_LEXICON 中方向相反的词，未穷举。
# 为什么必须单独列出：字集相似度分辨不了「业绩预增」和「业绩预减」（只差一个字，
# 相似度远高于阈值），一旦合并，就是把一条利好和一条利空揉成一条 ——
# 这是本项目能犯的最严重的错合并，所以做成硬性否决项，优先级高于相似度。
OPPOSITE_TERM_PAIRS = (
    ("预增", "预减"), ("预喜", "预亏"), ("增长", "下降"), ("上涨", "下跌"),
    ("增持", "减持"), ("上调", "下调"), ("利好", "利空"), ("扭亏", "亏损"),
    ("创新高", "创新低"), ("涨停", "跌停"), ("扩产", "减产"), ("中标", "流标"),
    ("加仓", "减仓"), ("买入", "卖出"), ("扭亏为盈", "业绩暴雷"),
)

# 簇代表选取打分：同簇内选「信息量最高」的一条。
# PROVENANCE: [经验值] 未校准。原则：可核实的字段优先于长度（有链接 > 有发布时间
# > 有来源），标题长度只作为并列时的次级依据，避免「标题越长越像要闻」的长度偏见。
INFO_SCORE_HAS_URL = 3
INFO_SCORE_HAS_TIME = 2
INFO_SCORE_HAS_SOURCE = 1

# --- 噪音过滤 ---

# 盘面播报噪音规则：命中即判定为「盘面播报」→ 进行情区（market_noise），不进要闻。
# PROVENANCE: [经验值] 人工归纳财经快讯标题里常见的盘面播报句式，未做统计校准。
#   mode="any"：任一组内任一关键词命中即算命中
#   mode="all"：每个分组都必须至少命中其一（分组内任一命中即可）
# 之所以要分组 + all：单用「收盘」会误伤「某公司收盘涨停」，单用「沪指」会误伤
# 「央行降准，沪指大涨」这类有真实增量的新闻。
NOISE_RULES = (
    {"name": "index_boardcast", "mode": "any",
     "keywords": (("三大指数", "盘面播报", "收盘播报", "开盘播报", "盘前播报",
                   "收评", "午评", "早评", "两市成交额", "沪深两市"),),
     "note": "纯指数/大盘盘面播报，无个股或政策增量信息"},
    {"name": "session_index_move", "mode": "all",
     "keywords": (("开盘", "收盘", "早盘", "午盘", "半日", "盘中"),
                  ("指数", "沪指", "深证成指", "创业板指", "大盘", "两市", "A股")),
     "note": "时段词+指数词同时出现才算播报，避免误伤个股新闻"},
)

# --- 重要度排序 ---

# 来源权重：渠道权威度权重，用于重要度排序。
# PROVENANCE: [经验值] **未校准** —— 按「是否一手信源/官方口径」人工分档，
# 不是统计得出的权重。项目目前没有「新闻来源 → 用户点击/采纳」的回馈日志，
# 因此这里不能声称有任何统计依据；等有日志后再做回归校准。
SOURCE_WEIGHTS = {
    # 一手/官方信源
    "证监会": 1.3, "央行": 1.3, "国务院": 1.3, "上交所": 1.3, "深交所": 1.3,
    "财政部": 1.3, "发改委": 1.3, "统计局": 1.3,
    # 官方媒体 / 主流财经媒体
    "新华社": 1.2, "人民日报": 1.2, "证券时报": 1.2, "上海证券报": 1.2,
    "中国证券报": 1.2, "财联社": 1.1,
    # 聚合平台（默认档）
    "东方财富": 1.0, "同花顺": 1.0, "新浪财经": 1.0, "雪球": 1.0,
}
# 未收录来源：不惩罚也不奖赏（1.0），避免「来源没被收录就被判低优」的隐性偏差。
# PROVENANCE: [经验值] 未校准。
DEFAULT_SOURCE_WEIGHT = 1.0

# 层级权重：涉及持仓 > 政策级 > 普通。
# PROVENANCE: [经验值] 未校准。取值刻意拉开，保证层级优先**严格成立**、不会被
# 来源权重翻转：最低档持仓 1.0×3.0=3.0 > 最高档政策 1.3×2.0=2.6 > 最高档普通
# 1.3×1.0=1.3。即来源权重只能在层内排序，不能跨层翻转。
IMPORTANCE_LEVEL_WEIGHTS = {"holding": 3.0, "policy": 2.0, "general": 1.0}


def _cluster_tokens(title: str) -> set:
    """归一化标题 → 2-gram 集合（聚类用的轻量「分词」）。

    不引入 jieba / scikit-learn：项目没有这些依赖，也不值得为标题去重引入它们。
    中文没有空格分词边界，2-gram 是效果/成本比最合适的近似。
    """
    s = _norm_title(title)
    s = re.sub(r"[0-9０-９]+", "", s)
    if len(s) < 2:
        return set(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def title_similarity(a: str, b: str) -> float:
    """两条标题的相似度（2-gram Jaccard），0~1。"""
    ta, tb = _cluster_tokens(a), _cluster_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def has_opposite_terms(a: str, b: str) -> bool:
    """两条标题是否含有语义相反的词对（预增/预减、增持/减持…）。

    这是防错合并的**硬性否决项**：字集相似度分辨不了只差一个字的反义标题。
    """
    for x, y in OPPOSITE_TERM_PAIRS:
        if (x in a and y in b) or (y in a and x in b):
            return True
    return False


def is_same_event(title_a: str, title_b: str) -> bool:
    """判定两条标题是否为同一事件的不同报道。

    宁可漏合并不错合并：反向词对一票否决，相似度还需过阈值。
    """
    if has_opposite_terms(title_a, title_b):
        return False
    return title_similarity(title_a, title_b) >= CLUSTER_JACCARD_THRESHOLD


def _info_score(item: dict) -> tuple:
    """簇内代表选取的打分键。max() 取首个最大值 → 输入顺序固定则结果固定。"""
    score = 0
    if (item.get("url") or "").strip():
        score += INFO_SCORE_HAS_URL
    if (item.get("time") or "").strip():
        score += INFO_SCORE_HAS_TIME
    if (item.get("source") or "").strip():
        score += INFO_SCORE_HAS_SOURCE
    return (score, len(item.get("title") or ""))


def cluster_news_items(items: list) -> list:
    """把同一事件的多篇报道聚成一簇。

    原地给每个 item 打上 cluster_id / is_representative / related_count，
    返回簇列表（每簇给出代表条目与成员标题，便于追溯）。

    锚点 = 簇内第一条；代表 = 簇内信息量最高的一条。比较只与锚点做，
    因此结果与输入顺序一一对应，可复现。
    """
    clusters = []
    for it in items:
        title = it.get("title", "") or ""
        toks = _cluster_tokens(title)
        for c in clusters:
            if is_same_event(c["anchor_title"], title):
                c["members"].append(it)
                break
        else:
            clusters.append({"anchor_title": title, "members": [it]})

    out = []
    for idx, c in enumerate(clusters):
        members = c["members"]
        rep = max(members, key=_info_score)
        for m in members:
            m["cluster_id"] = idx
            m["is_representative"] = m is rep
            m["related_count"] = len(members) - 1
        out.append({
            "cluster_id": idx,
            "representative": rep,
            "size": len(members),
            "related_count": len(members) - 1,
            "member_titles": [m.get("title", "") for m in members],
        })
    return out


def match_noise_rule(title: str):
    """标题命中盘面播报噪音规则则返回 (rule_name, note)，否则返回 None。

    被过滤的新闻必须能追溯「为什么被过滤」，所以返回规则名而不是 bool。
    """
    text = title or ""
    for rule in NOISE_RULES:
        hits = [any(k in text for k in group) for group in rule["keywords"]]
        ok = all(hits) if rule["mode"] == "all" else any(hits)
        if ok:
            return rule["name"], rule["note"]
    return None


def _source_weight(source: str) -> float:
    """来源权重（子串匹配，dict 插入序固定 → 结果确定）。"""
    s = (source or "").strip()
    if not s:
        return DEFAULT_SOURCE_WEIGHT
    for key, w in SOURCE_WEIGHTS.items():
        if key in s:
            return w
    return DEFAULT_SOURCE_WEIGHT


def importance_level(item: dict) -> str:
    """层级判定：涉及持仓 > 政策级 > 普通。"""
    if item.get("affected_holdings"):
        return "holding"
    text = item.get("title", "") or ""
    # 政策级判据复用 POLICY_KEYWORDS，与 get_policy_news() 保持同一套标准
    if item.get("tag") or any(k in text for k in POLICY_KEYWORDS):
        return "policy"
    return "general"


def score_importance(item: dict) -> float:
    """重要度分值 = 层级权重 × 来源权重（round 4 位，避免浮点噪声影响可复现性）。"""
    return round(
        IMPORTANCE_LEVEL_WEIGHTS[importance_level(item)] * _source_weight(item.get("source", "")),
        4,
    )


def rank_by_importance(items: list) -> list:
    """重要度降序排序；同分保持输入顺序（排序键带原始下标 → 完全稳定可复现）。"""
    scored = [(idx, score_importance(it), it) for idx, it in enumerate(items)]
    scored.sort(key=lambda t: (-t[1], t[0]))
    for _, sc, it in scored:
        it["importance_score"] = sc
        it["importance_level"] = importance_level(it)
    return [it for _, _, it in scored]


# ============================================================
# 个股/基金新闻统一接口（v3.0 新增，供各模块复用）
# ============================================================

_stock_news_cache = MemoryCache(default_ttl=NEWS_CACHE_TTL)  # {code: {"data": [...], "ts": float}}
_STOCK_NEWS_TTL = 900  # 15 分钟缓存

def get_stock_news_by_code(code: str, limit: int = 8) -> list:
    """拉取个股新闻（AKShare stock_news_em），15 分钟缓存

    新增：相关性过滤 — 只保留标题中包含股票名/代码/关键词的新闻
    """
    import time
    now = time.time()
    cached = _stock_news_cache.get(code)
    if cached is not None:
        return cached[:limit]

    try:
        from infra.data_source.macro.indicators import get_stock_news as _get_stock_news
        df = _get_stock_news(symbol=code)
        if df is not None and len(df) > 0:
            title_col = [c for c in df.columns if "标题" in c or "title" in c.lower() or "新闻" in c]
            time_col = [c for c in df.columns if "时间" in c or "date" in c.lower()]
            source_col = [c for c in df.columns if "来源" in c or "source" in c.lower()]
            if title_col:
                # 获取股票名称用于相关性过滤
                stock_name = _get_stock_name_for_filter(code)
                news = []
                for _, row in df.head(limit * 3).iterrows():  # 多拉一些用于过滤
                    title = str(row[title_col[0]])
                    # 相关性过滤：标题必须包含股票名/代码/关联关键词
                    if stock_name and not _is_relevant_news(title, code, stock_name):
                        continue
                    item = {"title": title}
                    if time_col:
                        item["time"] = str(row[time_col[0]])
                    if source_col:
                        item["source"] = str(row[source_col[0]])
                    news.append(item)
                    if len(news) >= limit:
                        break
                _stock_news_cache.set(code, news)
                return news
    except Exception as e:
        print(f"[NEWS] stock_news {code}: {e}")
    return []


def _get_stock_name_for_filter(code: str) -> str:
    """获取股票名（用于新闻相关性过滤）"""
    try:
        from services.tushare_data import validate_stock_code
        check = validate_stock_code(code)
        if check.get("valid") and check.get("name"):
            return check["name"]
    except Exception:
        pass
    return ""


def _is_relevant_news(title: str, code: str, stock_name: str) -> bool:
    """判断新闻标题是否与目标股票相关"""
    # 包含代码
    if code in title:
        return True
    # 包含股票名（如"贵州茅台"）
    if stock_name and stock_name in title:
        return True
    # 包含简称（如"茅台"）— 取名称前2-3个字
    if stock_name and len(stock_name) >= 4:
        short_name = stock_name[:2]
        if short_name in title:
            return True
    # 宽泛匹配失败 → 不相关
    return False


def get_holdings_news(
    stock_holdings: list,
    fund_holdings: list,
    limit_per: int = 3,
    llm_summary: bool = False,
) -> dict:
    """批量拉取持仓新闻（盯盘/复盘/诊断共用）

    v9.9.24 P1-1 新增三件事（原实现只做"拉取"，标签/去重/映射全缺）：
      1. 利好利空标签 —— 每条新闻带 sentiment / labeled / label_source / tag
      2. 全局去重     —— 同一条新闻（归一化标题）只保留一次，其余记为 affected_holdings
      3. 持仓影响映射 —— impact_map 给出每个持仓的利好/利空/未判定计数

    v9.9.26 P2-13 再追加三件事：
      4. 事件聚类     —— 同一事件的不同报道聚成一簇，簇内保留信息量最高的一条
      5. 噪音过滤     —— 盘面播报不进要闻，但**不凭空消失**（market_noise + 规则名）
      6. 重要度排序   —— 来源权重 × 层级（涉及持仓 > 政策级 > 普通），排序稳定可复现

    返回（**向后兼容**：stocks / funds / summary 三个键的语义不变，
    新增信息一律走新键追加）：
        {
          "stocks": {code: [news...]}, "funds": {code: [news...]},
          "summary": "一句话", "summary_source": "llm"|"rule"|"none",
          "duplicates_removed": int,
          "impact_map": {code: {"利好":n,"利空":n,"中性":n,"未判定":n}},
          "labeled_count": int, "unlabeled_count": int,
          # --- P2-13 新增键 ---
          "clusters": [{"cluster_id","representative","size","related_count","member_titles"}],
          "clusters_merged": int,          # 被合并进簇的非代表条目数
          "filtered_noise": int,           # 被判为盘面播报的条数
          "market_noise": [news...],       # 被过滤项（带 noise_rule），供行情区展示
          "importance_ranked": [news...],  # 要闻区：去噪 + 簇代表，按重要度降序
        }
    """
    result = {
        "stocks": {},
        "funds": {},
        "summary": "",
        "summary_source": "none",
        "duplicates_removed": 0,
        "impact_map": {},
        "labeled_count": 0,
        "unlabeled_count": 0,
        # --- P2-13 新增键（默认空，保证下游 .get() 永远拿得到）---
        "clusters": [],
        "clusters_merged": 0,
        "filtered_noise": 0,
        "market_noise": [],
        "importance_ranked": [],
    }

    seen_titles = {}      # norm_title -> news item（跨持仓去重）
    all_items = []        # 去重后的全部条目（供摘要用）

    def _absorb(code: str, kind: str, raw_news: list):
        """把某个持仓的新闻并入结果：打标签 + 去重 + 记录影响归属"""
        kept = []
        for n in raw_news:
            title = n.get("title", "")
            if not title:
                continue
            norm = _norm_title(title)
            item = dict(n)
            item.setdefault("code", code)
            item.setdefault("kind", kind)  # stock / fund

            if norm in seen_titles:
                # 同一条新闻已经在别的持仓下出现过 —— 不重复计数，
                # 但把本持仓记进 affected_holdings（这就是"影响映射"）
                first = seen_titles[norm]
                if code not in first["affected_holdings"]:
                    first["affected_holdings"].append(code)
                result["duplicates_removed"] += 1
                continue

            sentiment, labeled, label_source, tag = classify_sentiment(title, code)
            item["sentiment"] = sentiment
            item["labeled"] = labeled
            item["label_source"] = label_source
            item["tag"] = tag
            item["affected_holdings"] = [code]
            seen_titles[norm] = item
            all_items.append(item)
            kept.append(item)

        if kept:
            result[kind + "s"][code] = kept

    # 股票持仓新闻
    for h in (stock_holdings or [])[:10]:
        code = h.get("code", "")
        if not code:
            continue
        _absorb(code, "stock", get_stock_news_by_code(code, limit_per))

    # 基金持仓新闻
    for h in (fund_holdings or [])[:10]:
        code = h.get("code", "")
        if not code or code == "余额宝":
            continue
        try:
            news = get_fund_news(code, limit_per)
            valid = [n for n in news if n.get("title") and "加载中" not in n.get("title", "")]
            _absorb(code, "fund", valid)
        except Exception:
            pass

    # 每个持仓的影响汇总（按 affected_holdings 归属，含被去重掉的）
    for item in all_items:
        for code in item["affected_holdings"]:
            bucket = result["impact_map"].setdefault(
                code, {"利好": 0, "利空": 0, "中性": 0, "未判定": 0}
            )
            if not item["labeled"]:
                bucket["未判定"] += 1
            else:
                bucket[item["sentiment"]] += 1

    result["labeled_count"] = sum(1 for i in all_items if i["labeled"])
    result["unlabeled_count"] = sum(1 for i in all_items if not i["labeled"])

    # ---- P2-13 (4) 事件聚类：同一事件的不同报道聚成一簇 ----
    # 在 exact-dedupe（duplicates_removed）之后再做，两者分开计数互不混淆。
    result["clusters"] = cluster_news_items(all_items)
    result["clusters_merged"] = sum(c["related_count"] for c in result["clusters"])

    # ---- P2-13 (5) 噪音过滤：盘面播报进行情区，不进要闻 ----
    # 被过滤项照旧留在 stocks/funds 里（保证兼容），只是不再进 importance_ranked，
    # 并且全部登记在 market_noise 里、带上命中的规则名，绝不凭空消失。
    for _it in all_items:
        hit = match_noise_rule(_it.get("title", ""))
        _it["is_noise"] = hit is not None
        _it["noise_rule"] = hit[0] if hit else None
        if hit:
            _it["noise_rule_note"] = hit[1]
            result["market_noise"].append(_it)
    result["filtered_noise"] = len(result["market_noise"])

    # ---- P2-13 (6) 重要度排序：要闻区 = 去噪 + 簇代表 ----
    result["importance_ranked"] = rank_by_importance([
        _it for _it in all_items
        if not _it["is_noise"] and _it.get("is_representative") is True
    ])

    # 一句话摘要：默认走规则（零成本、零延迟），llm_summary=True 才调模型
    if all_items:
        result["summary"], result["summary_source"] = summarize_holdings_news(
            all_items, use_llm=llm_summary
        )
    return result


def format_holdings_news_for_prompt(holdings_news: dict) -> str:
    """把持仓新闻格式化为 prompt 注入文本

    v9.9.24 P1-1：带上利好利空标签，并把「同时影响多个持仓」标出来，
    让 LLM 看到的是结构化的影响关系，而不是一串无差别的标题。

    v9.9.26 P2-13：同一事件的多篇报道只展示簇代表，其余折叠为「另有N条相关报道」，
    避免同一个事在 prompt 里重复喂给 LLM 三遍。
    """
    lines = []
    for kind, label in (("stocks", "个股"), ("funds", "基金")):
        for code, news in holdings_news.get(kind, {}).items():
            lines.append(f"\n### {code} {label}新闻")
            for n in news:
                # 非簇代表（同一事件的其他报道）已折叠进代表条目，不再重复注入
                if n.get("is_representative") is False:
                    continue
                tag = n.get("sentiment", "中性")
                # 未判定的不能叫"中性"，否则 LLM 会当成"确认无影响"
                if not n.get("labeled", False):
                    tag = "未判定"
                affected = n.get("affected_holdings") or []
                extra = ""
                if len(affected) > 1:
                    extra = f"（同时影响：{'/'.join(c for c in affected if c != code)}）"
                related = n.get("related_count") or 0
                if related > 0:
                    extra += f"（另有{related}条相关报道）"
                lines.append(f"- [{tag}] {n['title']}{extra}")

    return "\n".join(lines) if lines else ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# V6 Phase 2: Pipeline enrich() — 让新闻模块接入 Pipeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def enrich(ctx):
    """Pipeline Layer2 自动调用 — 新闻影响分析注入 DecisionContext

    做 3 件事：
    1. 拉政策新闻 → NEWS_IMPACT_MAP 匹配 → 哪些板块/基金受影响
    2. 拉持仓新闻（如果有持仓）
    3. 综合判断 direction（bullish/bearish/neutral）
    """
    try:
        # 1. 政策新闻影响分析
        policy_news = get_policy_news(limit=20)
        impacts = analyze_news_impact(policy_news)

        # 2. 持仓新闻（如果用户有持仓的话）
        holdings_news = {}
        if getattr(ctx, "stock_holdings", None) or getattr(ctx, "fund_holdings", None):
            try:
                # P1-1：只有 Pipeline 这条链路开 LLM 摘要（盯盘/复盘走规则摘要，
                # 保持零额外延迟）。summary_source 会如实标明摘要是模型写的还是规则的。
                holdings_news = get_holdings_news(
                    getattr(ctx, "stock_holdings", []),
                    getattr(ctx, "fund_holdings", []),
                    limit_per=2,
                    llm_summary=True,
                )
            except Exception as e:
                print(f"[NEWS] holdings news failed: {e}")

        # 3. 方向判断：统计 impact 中 bullish vs bearish 数量
        bullish_count = sum(1 for i in impacts if i.get("bullish"))
        bearish_count = sum(1 for i in impacts if i.get("bearish"))
        if bullish_count > bearish_count + 1:
            direction = "bullish"
        elif bearish_count > bullish_count + 1:
            direction = "bearish"
        else:
            direction = "neutral"

        # 置信度：有影响分析 → 较高，否则较低
        confidence = min(70, 30 + len(impacts) * 10) if impacts else 30

        # 汇总 tags
        triggered_tags = [i["tag"] for i in impacts]

        ctx.modules_results["news_data"] = {
            "direction": direction,
            "score": 0.6 if direction == "bullish" else (0.4 if direction == "bearish" else 0.5),
            "confidence": confidence,
            "available": True,
            "detail": f"匹配{len(impacts)}条影响规则: {', '.join(triggered_tags)}" if triggered_tags else "无明显政策信号",
            "impacts": impacts[:5],  # 只保留前5条给 LLM
            "policy_news_count": len(policy_news),
            "holdings_news_summary": holdings_news.get("summary", ""),
            "holdings_news_summary_source": holdings_news.get("summary_source", "none"),
            # P1-1：每个持仓的 利好/利空/未判定 计数，供下游置信度与推送裁决使用
            "holdings_news_impact": holdings_news.get("impact_map", {}),
            "triggered_tags": triggered_tags,
        }

        if "news_data" not in ctx.modules_called:
            ctx.modules_called.append("news_data")

    except Exception as e:
        print(f"[NEWS] enrich failed: {e}")
        ctx.modules_results["news_data"] = {
            "available": False,
            "error": str(e),
            "direction": "neutral",
            "score": 0.5,
        }

    return ctx
