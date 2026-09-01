"""
同花顺题材数据层
============================================================
API:
  ak.stock_board_concept_name_ths()        — 热门题材列表（名称/涨跌幅/成分数）
  ak.stock_board_concept_cons_ths(symbol)  — 题材成分股

缓存: MemoryCache 1800s（30min，题材日内变化慢）
用途: 个股题材归因 → 推荐引擎 theme 维度评分

Invariant #3: 所有缓存走 infra/cache
Invariant #5: 外部数据源走 infra/data_source
"""
from __future__ import annotations

import logging
from typing import Any

from infra.cache import MemoryCache
from infra.data_source.fallback import call_with_timeout

_logger = logging.getLogger(__name__)
_cache: MemoryCache = MemoryCache(default_ttl=1800)  # 30 分钟

# v9.9.x: 接入超时保护（FIX 2026-09-01，任务#8）



def get_hot_concepts(limit: int = 30) -> list[dict[str, Any]]:
    """获取同花顺热门题材板块列表（按涨跌幅降序）。

    返回字段：板块名称、涨跌幅、成分股数量 等（原始 AKShare 列名）。
    """
    cached = _cache.get("hot_concepts")
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    try:
        import akshare as ak  # noqa: delayed import

        df = call_with_timeout(ak.stock_board_concept_name_ths, 10)
        if df is None or df.empty:
            return []
        if "涨跌幅" in df.columns:
            df = df.sort_values("涨跌幅", ascending=False)
        result: list[dict[str, Any]] = df.head(limit).to_dict("records")
        _cache.set("hot_concepts", result)
        return result
    except Exception as e:
        _logger.warning("[THS] get_hot_concepts 失败: %s", e)
        return []


def get_concept_stocks(concept_name: str) -> list[str]:
    """获取某题材的成分股 6 位代码列表。

    ⚠️ 已知失效（2026-09-01 任务#8 排查发现）：`ak.stock_board_concept_
    cons_ths()` 在当前 AKShare 1.18.60 中已不存在（`AttributeError`），
    应是随库升级被移除。本函数因此长期恒返回 `[]`（空列表），被上层
    `get_stock_theme_tags()` 消费后表现为"任何股票都不在任何题材中"，
    是静默的功能性缺失，不是网络问题。

    可能替代方案（未验证兼容性，需要单独评估，此处不擅自替换）：
    `ak.stock_board_concept_cons_em(symbol=...)`（东财口径，symbol 需
    传板块代码或名称，与本函数当前接收的同花顺题材名称体系可能不
    完全对应，需要先验证 `get_hot_concepts()` 返回的题材名能否直接
    喂给东财接口）。这里仅补充超时保护，不改动功能逻辑。
    """
    key = f"concept_stocks_{concept_name}"
    cached = _cache.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    try:
        import akshare as ak  # noqa: delayed import

        df = call_with_timeout(ak.stock_board_concept_cons_ths, 10, symbol=concept_name)
        if df is None or df.empty:
            return []
        # 兼容"代码"/"股票代码"两种列名
        code_col = next((c for c in df.columns if "代码" in c), None)
        if not code_col:
            return []
        codes: list[str] = [str(c).zfill(6) for c in df[code_col].tolist() if c]

        _cache.set(key, codes, ttl=1800)
        return codes
    except Exception as e:
        _logger.warning("[THS] get_concept_stocks(%s) 失败: %s", concept_name, e)
        return []


def get_stock_theme_tags(code: str, top_concepts: int = 20) -> list[str]:
    """反查某只股票属于哪些热门题材（遍历 top N 题材的成分股列表）。

    Args:
        code: 6 位 A 股代码，如 "600519"
        top_concepts: 遍历前 N 个热门题材（越大越全但越慢）

    Returns:
        命中的题材名称列表，空列表表示不在任何热门题材中。
    """
    key = f"theme_tags_{code}"
    cached = _cache.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    tags: list[str] = []
    try:
        concepts = get_hot_concepts(top_concepts)
        for c in concepts:
            # AKShare 返回列名可能是"板块名称"或"名称"
            name: str = c.get("板块名称", c.get("名称", ""))
            if not name:
                continue
            members = get_concept_stocks(name)
            if code in members:
                tags.append(name)
    except Exception as e:
        _logger.warning("[THS] get_stock_theme_tags(%s) 失败: %s", code, e)

    _cache.set(key, tags)
    return tags
