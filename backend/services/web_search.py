"""
钱袋子 — 联网搜索服务
============================================================
意图分流中"非理财问题"需要实时信息时调用。

降级链: 秘塔 Metaso（中文最好）→ wttr.in（天气专用兜底）

配置: 环境变量 METASO_API_KEY（秘塔开放平台获取）

Invariant #5: 外部数据源走 infra/data_source
"""
import os
import httpx
from infra.cache import MemoryCache

_cache = MemoryCache(default_ttl=600)  # 搜索结果缓存 10 分钟


def _get_metaso_key() -> str:
    return os.environ.get("METASO_API_KEY", "")


def search_web(query: str, limit: int = 5) -> list:
    """联网搜索，返回 [{title, snippet, url}, ...]

    优先秘塔，失败返回空列表（不抛异常）。
    """
    cached = _cache.get(f"search_{query}")
    if cached is not None:
        return cached

    results = _search_metaso(query, limit)
    if results:
        _cache.set(f"search_{query}", results)
    return results


def search_weather(city: str) -> str:
    """天气查询（wttr.in 免费 API，无需 Key）

    返回一句话天气描述，失败返回空字符串。
    """
    cached = _cache.get(f"weather_{city}")
    if cached is not None:
        return cached

    try:
        # wttr.in 支持中文城市名
        resp = httpx.get(
            f"https://wttr.in/{city}?format=%l:+%c+%t+%w+%h&lang=zh",
            timeout=8,
            headers={"User-Agent": "curl/7.0"},
        )
        if resp.status_code == 200:
            result = resp.text.strip()
            _cache.set(f"weather_{city}", result)
            return result
    except Exception as e:
        print(f"[SEARCH] wttr.in failed: {e}")

    return ""


def _search_metaso(query: str, limit: int = 5) -> list:
    """秘塔搜索 API（官方: metaso.cn/api/v1/search）"""
    api_key = _get_metaso_key()
    if not api_key:
        print("[SEARCH] METASO_API_KEY 未配置，跳过搜索")
        return []

    try:
        resp = httpx.post(
            "https://metaso.cn/api/v1/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"q": query, "scope": "webpage", "size": limit},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            # 秘塔返回结构: {data: {list: [{title, snippet, url}, ...]}}
            items = data.get("data", {})
            if isinstance(items, dict):
                items = items.get("list", []) or items.get("items", []) or items.get("results", [])
            elif isinstance(items, list):
                pass
            else:
                items = []
            return [
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", item.get("content", item.get("description", "")))[:200],
                    "url": item.get("url", item.get("link", "")),
                }
                for item in items[:limit]
                if isinstance(item, dict)
            ]
        else:
            print(f"[SEARCH] Metaso {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SEARCH] Metaso failed: {e}")

    return []


def format_search_for_prompt(results: list) -> str:
    """把搜索结果格式化为给 DeepSeek 的 context"""
    if not results:
        return ""
    lines = ["以下是联网搜索到的实时信息："]
    for i, r in enumerate(results[:5], 1):
        lines.append(f"{i}. {r.get('title', '')}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
    lines.append("\n请基于以上搜索结果回答用户问题。")
    return "\n".join(lines)
