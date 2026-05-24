"""
钱袋子 — 联网搜索服务
============================================================
意图分流中"非理财问题"需要实时信息时调用。

降级链（按优先级）：
  1. 秘塔 Metaso — 中文最好，需配置 METASO_API_KEY
  2. 东方财富新闻搜索 — 无需 key，国内稳定，财经新闻质量高
  3. Bing 中国搜索 — 免费兜底，结果质量较差

配置: 环境变量 METASO_API_KEY（秘塔开放平台获取，可选）

Invariant #5: 外部数据源走 infra/data_source
"""
import os
import re
import json
import httpx
from infra.cache import MemoryCache

_cache = MemoryCache(default_ttl=600)  # 搜索结果缓存 10 分钟


def _get_metaso_key() -> str:
    return os.environ.get("METASO_API_KEY", "")


def search_web(query: str, limit: int = 5) -> list:
    """联网搜索，返回 [{title, snippet, url, date?}, ...]

    降级链: 秘塔 → 东方财富 → Bing → 空列表
    """
    cached = _cache.get(f"search_{query}")
    if cached is not None:
        return cached

    results = _search_metaso(query, limit)
    if not results:
        results = _search_eastmoney(query, limit)
    if not results:
        results = _search_bing(query, limit)

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
            results = [
                {
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", "")[:200],
                    "url": item.get("link", ""),
                }
                for item in items[:limit]
                if isinstance(item, dict) and item.get("title")
            ]
            if results:
                print(f"[SEARCH] Metaso: {len(results)} 条结果")
            return results
        else:
            print(f"[SEARCH] Metaso {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SEARCH] Metaso failed: {e}")

    return []


def _search_eastmoney(query: str, limit: int = 5) -> list:
    """东方财富新闻搜索（无需 Key，国内服务器稳定可达）

    适合财经新闻、政策、宏观、国际时事等查询。
    """
    try:
        param = json.dumps({
            "uid": "",
            "keyword": query,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "from": 0,
                    "size": limit,
                    "returnFields": ["title", "content", "date", "url", "mediaName"]
                }
            }
        }, ensure_ascii=False)

        resp = httpx.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"param": param, "cb": "cb"},
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://so.eastmoney.com/",
            },
        )
        if resp.status_code != 200:
            print(f"[SEARCH] EastMoney HTTP {resp.status_code}")
            return []

        # 去掉 JSONP 包装：cb({...})
        m = re.search(r'cb\((.*)\)\s*$', resp.text, re.DOTALL)
        if not m:
            return []

        data = json.loads(m.group(1))
        articles = data.get("result", {}).get("cmsArticleWebOld", [])
        if not isinstance(articles, list):
            return []

        results = []
        for art in articles[:limit]:
            title = re.sub(r'<[^>]+>', '', art.get("title", "")).strip()
            snippet = re.sub(r'<[^>]+>', '', art.get("content", "")).strip()[:200]
            url = art.get("url", "")
            date = art.get("date", "")[:10]  # 只取日期部分
            source = art.get("mediaName", "")

            if not title or len(title) < 4:
                continue
            results.append({
                "title": title,
                "snippet": snippet,
                "url": url,
                "date": date,
                "source": source,
            })

        if results:
            print(f"[SEARCH] EastMoney: {len(results)} 条结果")
        return results

    except Exception as e:
        print(f"[SEARCH] EastMoney failed: {e}")
    return []


def _search_bing(query: str, limit: int = 5) -> list:
    """Bing 中国搜索兜底（免费，无需 API Key）

    使用更精准的选择器，过滤导航/广告。
    """
    try:
        resp = httpx.get(
            "https://cn.bing.com/search",
            params={"q": query, "setlang": "zh-cn", "count": str(limit * 3)},
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            follow_redirects=True,
        )
        if resp.status_code != 200:
            print(f"[SEARCH] Bing HTTP {resp.status_code}")
            return []

        html = resp.text
        results = []

        # Bing 自然结果结构：<li class="b_algo"><h2><a href="...">title</a></h2>
        # 用更精准的 class="b_algo" 匹配，过滤广告/导航
        algo_blocks = re.findall(
            r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
            html, re.DOTALL
        )

        for block in algo_blocks:
            # 提取链接和标题
            link_m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_m:
                continue
            url, title_raw = link_m.group(1), link_m.group(2)

            # 只要 http(s) 链接，排除 Bing 内部
            if not url.startswith("http") or "bing.com" in url:
                continue

            title = re.sub(r'<[^>]+>', '', title_raw).strip()
            title = title.replace("&#183;", "·").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            if not title or len(title) < 5:
                continue

            # 摘要：找 <p class="b_lineclamp..."> 或 <div class="b_caption">
            snippet = ""
            snippet_m = re.search(r'<p[^>]+class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
            if not snippet_m:
                snippet_m = re.search(r'<div[^>]+class="[^"]*b_caption[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if snippet_m:
                snippet = re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip()[:200]

            results.append({"title": title[:100], "snippet": snippet, "url": url})
            if len(results) >= limit:
                break

        if results:
            print(f"[SEARCH] Bing: {len(results)} 条结果")
        else:
            print("[SEARCH] Bing: 无结果（页面结构可能变化）")
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
        title = r.get("title", "")
        date = r.get("date", "")
        source = r.get("source", "")
        meta = f"（{date}，{source}）" if date and source else f"（{date}）" if date else f"（{source}）" if source else ""
        lines.append(f"{i}. {title}{meta}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
    lines.append("\n请基于以上搜索结果回答用户问题，如信息不足请如实说明。")
    return "\n".join(lines)
