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

    降级链: 秘塔 → DuckDuckGo（免费无需Key）→ 空列表
    """
    cached = _cache.get(f"search_{query}")
    if cached is not None:
        return cached

    results = _search_metaso(query, limit)
    if not results:
        results = _search_duckduckgo(query, limit)
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
        print("[SEARCH] METASO_API_KEY 未配置，尝试 DuckDuckGo")
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
            # 秘塔返回结构: {webpages: [{title, link, snippet, score}, ...]}
            items = data.get("webpages", [])
            return [
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", "")[:200],
                    "url": item.get("link", ""),
                }
                for item in items[:limit]
                if isinstance(item, dict)
            ]
        else:
            print(f"[SEARCH] Metaso {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SEARCH] Metaso failed: {e}")

    return []


def _search_duckduckgo(query: str, limit: int = 5) -> list:
    """Bing 中国搜索（免费，无需 API Key，国内服务器可达）

    通过 cn.bing.com 获取搜索结果。
    """
    try:
        resp = httpx.get(
            "https://cn.bing.com/search",
            params={"q": query, "setlang": "zh-cn", "count": str(limit)},
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            print(f"[SEARCH] Bing HTTP {resp.status_code}")
            return []

        # 简单 HTML 解析（不引入 BeautifulSoup 依赖）
        import re
        html = resp.text
        results = []

        # 提取所有 <h2> 中的链接作为搜索结果
        # Bing 结构: <h2><a href="url" ...>title</a></h2>
        h2_links = re.findall(
            r'<h2[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )

        for url, title in h2_links[:limit]:
            # 过滤非结果链接（广告、导航等）
            if not url.startswith("http"):
                continue
            # 清理 HTML 标签和实体
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            title_clean = title_clean.replace("&#183;", "·").replace("&amp;", "&")
            if not title_clean or len(title_clean) < 5:
                continue

            # 尝试在 title 附近找摘要
            snippet = ""
            # 在整个 HTML 中找到这个链接后面的 <p> 标签
            title_pos = html.find(title[:20])
            if title_pos > 0:
                nearby = html[title_pos:title_pos + 1000]
                p_match = re.search(r'<p[^>]*>(.*?)</p>', nearby, re.DOTALL)
                if p_match:
                    snippet = re.sub(r'<[^>]+>', '', p_match.group(1)).strip()[:200]

            results.append({
                "title": title_clean[:100],
                "snippet": snippet,
                "url": url,
            })

        if results:
            print(f"[SEARCH] Bing: {len(results)} 条结果")
        else:
            print("[SEARCH] Bing: 无结果（可能 HTML 结构变化）")
        return results

    except Exception as e:
        print(f"[SEARCH] Bing failed: {e}")
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
