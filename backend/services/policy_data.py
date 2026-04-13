"""
国内政策数据层 — 独立 service
职责：
  1. 房地产数据（开发投资/销售面积/新开工）
  2. 70城新房价格指数
  3. 分主题政策新闻抓取（房地产/公积金/科技/经济/房改）
  4. DeepSeek 政策→A股影响分析
"""
import os
import time
import json
import traceback
from datetime import datetime

# ---- 缓存 ----
_policy_cache = {}
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
    if cache_key in _policy_cache and now - _policy_cache[cache_key]["ts"] < _STRUCT_TTL:
        return _policy_cache[cache_key]["data"]

    result = {"available": False, "data": [], "latest": {}}
    try:
        import akshare as ak
        df = ak.macro_china_real_estate()
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

    _policy_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_house_price_index() -> dict:
    """70城新房价格指数"""
    cache_key = "house_price"
    now = time.time()
    if cache_key in _policy_cache and now - _policy_cache[cache_key]["ts"] < _STRUCT_TTL:
        return _policy_cache[cache_key]["data"]

    result = {"available": False, "data": [], "latest": {}}
    try:
        import akshare as ak
        df = ak.macro_china_new_house_price()
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

    _policy_cache[cache_key] = {"data": result, "ts": now}
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


def get_policy_news_by_topic(topic: str = "房地产", limit: int = 5) -> dict:
    """按主题搜索政策新闻（东方财富数据源）"""
    cache_key = f"policy_news_{topic}"
    now = time.time()
    if cache_key in _policy_cache and now - _policy_cache[cache_key]["ts"] < _NEWS_TTL:
        return _policy_cache[cache_key]["data"]

    result = {"topic": topic, "news": [], "emoji": POLICY_TOPICS.get(topic, {}).get("emoji", "📋")}
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=topic)
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

    _policy_cache[cache_key] = {"data": result, "ts": now}
    return result


def get_all_policy_topics() -> dict:
    """一次性获取全部主题的政策新闻（key 用英文，与前端对齐）"""
    cache_key = "all_policy_topics"
    now = time.time()
    if cache_key in _policy_cache and now - _policy_cache[cache_key]["ts"] < _NEWS_TTL:
        return _policy_cache[cache_key]["data"]

    # 中文→英文 key 映射（前端 topicMap 用英文 key）
    KEY_MAP = {"房地产": "realestate", "公积金": "gongjijin", "科技": "tech", "经济": "economy", "房改": "fanggai"}
    topics = {}
    for topic_cn in POLICY_TOPICS:
        en_key = KEY_MAP.get(topic_cn, topic_cn)
        data = get_policy_news_by_topic(topic_cn, 5)
        # 直接返回 news 数组（前端需要 topics[key] = [{title,time,url}]）
        topics[en_key] = data.get("news", [])

    result = {"topics": topics, "updatedAt": datetime.now().isoformat()}
    _policy_cache[cache_key] = {"data": result, "ts": now}
    return result


# ============================================================
# 3. DeepSeek 政策影响分析
# ============================================================

def analyze_policy_impact_ds() -> dict:
    """DeepSeek 分析国内政策对 A 股各板块的影响"""
    cache_key = "policy_impact_ds"
    now = time.time()
    if cache_key in _policy_cache and now - _policy_cache[cache_key]["ts"] < _ANALYSIS_TTL:
        return _policy_cache[cache_key]["data"]

    # 收集全部政策新闻
    all_news = get_all_policy_topics()
    news_lines = []
    for topic, data in all_news.get("topics", {}).items():
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
        _policy_cache[cache_key] = {"data": result, "ts": now}
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

        with httpx.Client(timeout=15) as client:
            resp = client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.7,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis = data["choices"][0]["message"]["content"]
                result = {"analysis": analysis, "source": "ai", "newsCount": len(news_lines)}
                _policy_cache[cache_key] = {"data": result, "ts": now}
                return result
    except Exception as e:
        print(f"[POLICY] DeepSeek analysis fail: {e}")

    result = {"analysis": "\n".join(news_lines), "source": "data_only"}
    _policy_cache[cache_key] = {"data": result, "ts": now}
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
    for topic, data in all_topics.get("topics", {}).items():
        news = data.get("news", [])
        if news:
            lines.append(f"{data.get('emoji','')} {topic}：{news[0].get('title', '')}")

    return "\n".join(lines) if lines else ""
