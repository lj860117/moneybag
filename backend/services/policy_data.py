"""
国内政策数据层 — 独立 service
职责：
  1. 房地产数据（开发投资/销售面积/新开工）
  2. 70城新房价格指数
  3. 分主题政策新闻抓取（房地产/公积金/科技/经济/房改）
  4. DeepSeek 政策→A股影响分析
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "policy_data",
    "scope": "public",
    "input": [],
    "output": "policy_topics",
    "cost": "llm_light",
    "tags": ['政策', '房地产', '5主题'],
    "description": "国内政策：房地产数据+70城房价+5主题新闻+DS政策影响分析",
    "layer": "data",
    "priority": 3,
}
import os
import time
import json
import traceback
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple
from infra.cache import MemoryCache

# ---- 缓存 ----
_policy_cache = MemoryCache(default_ttl=3600)
_STRUCT_TTL = 86400   # 结构化数据缓存 24 小时（月度更新）
_NEWS_TTL = 1800      # 政策新闻缓存 30 分钟
_ANALYSIS_TTL = 3600  # DeepSeek 分析缓存 1 小时


# ============================================================
# 1. 房地产结构化数据
# ============================================================

def get_real_estate_data() -> dict:
    """房地产开发投资/销售面积/新开工面积"""
    cache_key = "real_estate"
    now = time.time()
    cached = _policy_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"available": False, "data": [], "latest": {}}
    try:
        from infra.data_source.macro.indicators import get_china_real_estate
        df = get_china_real_estate()
        if df is not None and len(df) > 0:
            result["available"] = True
            # 取最近 12 条
            recent = df.tail(12)
            cols = list(recent.columns)
            records = []
            for _, row in recent.iterrows():
                r = {}
                for c in cols:
                    v = row[c]
                    try:
                        if hasattr(v, 'item'):
                            v = v.item()
                    except:
                        pass
                    r[c] = str(v) if v is not None else ""
                records.append(r)
            result["data"] = records
            if records:
                result["latest"] = records[-1]
            result["count"] = len(df)
            print(f"[POLICY] real_estate: {len(df)} rows, latest={list(result['latest'].keys())[:5]}")
    except Exception as e:
        print(f"[POLICY] real_estate fail: {e}")
        result["error"] = str(e)

    _policy_cache.set(cache_key, result, ttl=_STRUCT_TTL)
    return result


def get_house_price_index() -> dict:
    """70城新房价格指数"""
    cache_key = "house_price"
    now = time.time()
    cached = _policy_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"available": False, "data": [], "latest": {}}
    try:
        from infra.data_source.macro.indicators import get_china_new_house_price
        df = get_china_new_house_price()
        if df is not None and len(df) > 0:
            result["available"] = True
            recent = df.tail(12)
            cols = list(recent.columns)
            records = []
            for _, row in recent.iterrows():
                r = {}
                for c in cols:
                    v = row[c]
                    try:
                        if hasattr(v, 'item'):
                            v = v.item()
                    except:
                        pass
                    r[c] = str(v) if v is not None else ""
                records.append(r)
            result["data"] = records
            if records:
                result["latest"] = records[-1]
            result["count"] = len(df)
            print(f"[POLICY] house_price: {len(df)} rows")
    except Exception as e:
        print(f"[POLICY] house_price fail: {e}")
        result["error"] = str(e)

    _policy_cache.set(cache_key, result, ttl=_STRUCT_TTL)
    return result


# ============================================================
# 2. 分主题政策新闻
# ============================================================

POLICY_TOPICS = {
    "房地产": {"keywords": ["房地产", "楼市", "房价", "限购", "限贷", "首付"], "emoji": "🏠"},
    "公积金": {"keywords": ["公积金", "住房公积金", "公积金贷款"], "emoji": "🏦"},
    "科技": {"keywords": ["半导体", "芯片", "AI", "人工智能", "新能源", "科技创新"], "emoji": "🚀"},
    "经济": {"keywords": ["GDP", "经济增长", "财政", "减税", "消费券", "内需"], "emoji": "📊"},
    "房改": {"keywords": ["保障房", "城中村", "棚改", "旧改", "安居"], "emoji": "🏗️"},
}

# 中文主题名 → 前端使用的英文 key（前端 topicMap 用英文 key）
KEY_MAP = {"房地产": "realestate", "公积金": "gongjijin", "科技": "tech", "经济": "economy", "房改": "fanggai"}
_EN_TO_CN = {v: k for k, v in KEY_MAP.items()}

# topics 字典值的唯一合法键集合。任何写入路径都必须产出完全一致的键，
# 避免出现「dict vs dict 但键不同」这种比原先更隐蔽的不一致。
_TOPIC_PAYLOAD_KEYS = ("topic", "news", "emoji", "error")


def _topic_payload(
    topic_cn: str,
    news: Optional[List[Any]] = None,
    emoji: Optional[str] = None,
    error: str = "",
) -> Dict[str, Any]:
    """构造 topics 映射里的值，保证键集合恒定且类型正确。

    背景（P0 修复）：get_all_policy_topics() 历史上两条路径都往
    topics[en_key] 里塞「新闻列表」（正常路径 data.get("news", []) /
    异常路径 []），而下游 analyze_policy_impact_ds() 与 _build_policy_context()
    都按 dict 消费（data.get("emoji") / data.get("news")），于是
    AttributeError: 'list' object has no attribute 'get'
    → GET /api/policy/impact HTTP 500。

    这里把「正常路径」也一并改成 dict（不只是异常路径），否则只会把崩溃
    从异常路径转移到更高频的正常路径上。
    """
    return {
        "topic": topic_cn,
        "news": news if isinstance(news, list) else [],
        "emoji": emoji or POLICY_TOPICS.get(topic_cn, {}).get("emoji", "📋"),
        "error": str(error or ""),
    }


def _normalize_topic_payload(key: str, raw: Any) -> Dict[str, Any]:
    """把 topics 里任意历史形态的值归一化成 dict。

    兜底场景：发版后内存缓存里可能仍残留旧结构（list）。这里把 list 当作
    news 列表保留下来，而不是静默丢弃——丢掉才是真正的信息丢失。
    """
    topic_cn = _EN_TO_CN.get(key, key)
    if isinstance(raw, dict):
        return _topic_payload(
            topic_cn,
            news=raw.get("news"),
            emoji=raw.get("emoji"),
            error=raw.get("error", ""),
        )
    if isinstance(raw, list):
        return _topic_payload(topic_cn, news=raw, error="legacy list payload")
    return _topic_payload(topic_cn, error=f"unexpected payload: {type(raw).__name__}")


def _iter_policy_topics(all_topics: Any) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """统一遍历 get_all_policy_topics() 的结果，产出 (key, dict)。

    下游两处消费点都改走这里，任何非 dict 的值都会被就地归一化，
    因此不会再出现 'list' object has no attribute 'get'。
    """
    raw_topics = all_topics.get("topics", {}) if isinstance(all_topics, dict) else {}
    if not isinstance(raw_topics, dict):
        return
    for key, raw in raw_topics.items():
        yield key, _normalize_topic_payload(key, raw)


def get_policy_news_by_topic(topic: str = "房地产", limit: int = 5) -> dict:
    """按主题搜索政策新闻（东方财富数据源）"""
    cache_key = f"policy_news_{topic}"
    now = time.time()
    cached = _policy_cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"topic": topic, "news": [], "emoji": POLICY_TOPICS.get(topic, {}).get("emoji", "📋")}
    try:
        from infra.data_source.macro.indicators import get_stock_news
        df = get_stock_news(symbol=topic)
        if df is not None and len(df) > 0:
            cols = list(df.columns)
            title_col = next((c for c in cols if "标题" in c or "title" in c.lower()), cols[0] if cols else None)
            time_col = next((c for c in cols if "时间" in c or "日期" in c or "date" in c.lower()), None)
            url_col = next((c for c in cols if "链接" in c or "url" in c.lower()), None)

            for _, row in df.head(limit).iterrows():
                item = {"title": str(row[title_col]) if title_col else ""}
                if time_col:
                    item["time"] = str(row[time_col])
                if url_col:
                    item["url"] = str(row[url_col])
                result["news"].append(item)
            print(f"[POLICY] news({topic}): {len(result['news'])} items")
    except Exception as e:
        print(f"[POLICY] news({topic}) fail: {e}")
        result["error"] = str(e)

    _policy_cache.set(cache_key, result, ttl=_NEWS_TTL)
    return result


def get_all_policy_topics() -> dict:
    """一次性获取全部主题的政策新闻（key 用英文，与前端对齐）"""
    cache_key = "all_policy_topics"
    now = time.time()
    cached = _policy_cache.get(cache_key)
    if cached is not None:
        return cached

    # 并发抓取 5 个主题（避免串行 15-25 秒超时）
    from concurrent.futures import ThreadPoolExecutor, as_completed
    topics = {}

    def _fetch_topic(topic_cn):
        en_key = KEY_MAP.get(topic_cn, topic_cn)
        data = get_policy_news_by_topic(topic_cn, 5)
        if not isinstance(data, dict):
            # 上游返回了非 dict（历史形态或异常），显式标注而不是静默变形
            return en_key, _topic_payload(
                topic_cn, error=f"unexpected payload: {type(data).__name__}"
            )
        return en_key, _topic_payload(
            topic_cn,
            news=data.get("news"),
            emoji=data.get("emoji"),
            error=data.get("error", ""),
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_topic, t): t for t in POLICY_TOPICS}
        for f in as_completed(futures):
            try:
                en_key, payload = f.result(timeout=10)
                topics[en_key] = payload
            except Exception as e:
                topic_cn = futures[f]
                en_key = KEY_MAP.get(topic_cn, topic_cn)
                # 异常路径与正常路径产出完全同构的 dict（键集合一致）
                topics[en_key] = _topic_payload(topic_cn, error=str(e))
                print(f"[POLICY] topic {topic_cn} failed: {e}")

    result = {"topics": topics, "updatedAt": datetime.now().isoformat()}
    _policy_cache.set(cache_key, result, ttl=_NEWS_TTL)
    return result


# ============================================================
# 3. DeepSeek 政策影响分析
# ============================================================

def analyze_policy_impact_ds() -> dict:
    """DeepSeek 分析国内政策对 A 股各板块的影响"""
    cache_key = "policy_impact_ds"
    now = time.time()
    cached = _policy_cache.get(cache_key)
    if cached is not None:
        return cached

    # 收集全部政策新闻
    all_news = get_all_policy_topics()
    news_lines = []
    for topic, data in _iter_policy_topics(all_news):
        emoji = data.get("emoji", "")
        for n in data.get("news", [])[:3]:
            news_lines.append(f"{emoji} [{topic}] {n.get('title', '')}")

    if not news_lines:
        return {"analysis": "暂无政策新闻数据", "source": "none"}

    # 房地产数据
    re_data = get_real_estate_data()
    re_summary = ""
    if re_data.get("available"):
        latest = re_data.get("latest", {})
        re_summary = f"房地产最新数据：{json.dumps(latest, ensure_ascii=False)[:200]}"

    # 调 DeepSeek
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        result = {"analysis": "\n".join(news_lines), "source": "data_only"}
        _policy_cache.set(cache_key, result, ttl=_ANALYSIS_TTL)
        return result

    try:
        import httpx
        prompt = f"""请分析以下国内政策新闻对 A 股各板块的影响。

【政策新闻】
{chr(10).join(news_lines)}

{re_summary}

请按以下格式回答（200字内）：
1. 最重要的 2-3 条政策及其影响板块
2. 利好板块和利空板块
3. 对普通投资者的操作建议"""

        from infra.llm.gateway import LLMGateway
        gw = LLMGateway.instance()
        llm_result = gw.call_sync(
            prompt,
            system="",
            model_tier="llm_light",
            user_id="",
            module="policy_analysis",
            max_tokens=400,
        )
        if not llm_result.get("fallback") and llm_result.get("content"):
            analysis = llm_result["content"]
            result = {"analysis": analysis, "source": "ai", "newsCount": len(news_lines)}
            _policy_cache.set(cache_key, result)
            return result
    except Exception as e:
        print(f"[POLICY] LLM Gateway analysis fail: {e}")

    result = {"analysis": "\n".join(news_lines), "source": "data_only"}
    _policy_cache.set(cache_key, result)
    return result


# ============================================================
# 4. 政策数据汇总（供 market context 使用）
# ============================================================

def get_policy_summary_for_context() -> str:
    """为 DeepSeek system prompt 生成政策数据摘要"""
    lines = []

    # 房地产
    re_data = get_real_estate_data()
    if re_data.get("available"):
        latest = re_data.get("latest", {})
        # 取前3个字段
        first_items = list(latest.items())[:3]
        if first_items:
            lines.append("房地产：" + "，".join(f"{k}={v}" for k, v in first_items))

    # 房价
    hp_data = get_house_price_index()
    if hp_data.get("available"):
        latest = hp_data.get("latest", {})
        first_items = list(latest.items())[:3]
        if first_items:
            lines.append("房价指数：" + "，".join(f"{k}={v}" for k, v in first_items))

    # 政策新闻标题
    all_topics = get_all_policy_topics()
    for topic, data in _iter_policy_topics(all_topics):
        news = data.get("news", [])
        if news:
            lines.append(f"{data.get('emoji','')} {topic}：{news[0].get('title', '')}")

    return "\n".join(lines) if lines else ""
