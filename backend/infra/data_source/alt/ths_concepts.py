"""
同花顺题材数据层
============================================================
数据源（v9.9.x FIX 2026-09-01，任务#3，用户显式要求）：Tushare 为主
（5000积分账号已配置，接口稳定），AKShare 为降级兜底。

背景：AKShare 原实现依赖 ak.stock_board_concept_name_ths()（题材列表，
本身仍可用）+ ak.stock_board_concept_cons_ths()（题材成分股，任务#8
诊断发现已随 AKShare 库升级被彻底删除，AttributeError 长期被 except
吞掉，get_concept_stocks() 恒返回空列表）——两个接口必须配套才有意义
（拿到题材名却查不到成分股，题材归因功能等于摆设），故整体切换到
Tushare 保持数据源一致，而不是"半AKShare半Tushare"两套体系混用。

Tushare 实现放在 infra/data_source/providers/tushare_provider.py::
TushareProvider（metric="ths_hot_concepts"/"ths_concept_members"），
本文件是薄封装层，负责缓存和 AKShare 降级逻辑。

API:
  Tushare（主）:
    pro.ths_index(exchange='A', type='N') — 全部同花顺概念指数静态信息
    pro.ths_daily(trade_date=...) — 概念指数日线行情（涨跌幅）
    pro.ths_member(ts_code=...) — 概念成分股
  AKShare（降级兜底）:
    ak.stock_board_concept_name_ths()        — 热门题材列表
    ak.stock_board_concept_cons_ths(symbol)  — 题材成分股（已知已死，
                                                 降级时大概率仍失败，
                                                 仅作最后兜底）

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


def get_hot_concepts(limit: int = 30) -> list[dict[str, Any]]:
    """获取同花顺热门题材板块列表（按涨跌幅降序）。

    v9.9.x FIX 2026-09-01（任务#3）：Tushare 为主（ths_index join
    ths_daily），AKShare 为降级兜底。

    返回字段：板块名称、涨跌幅、成分股数量 等（字段名与原 AKShare
    版本保持一致，Tushare 路径额外带 ts_code 供 get_concept_stocks
    内部使用，下游忽略多出字段即可）。
    """
    cached = _cache.get("hot_concepts")
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    # 主：Tushare
    try:
        from infra.data_source.providers.tushare_provider import TushareProvider
        provider = TushareProvider()
        if provider.is_available():
            result = call_with_timeout(
                provider.fetch, 10, metric="ths_hot_concepts", limit=limit,
            )
            if result:
                _cache.set("hot_concepts", result)
                return result  # type: ignore[no-any-return]
    except Exception as e:
        _logger.warning("[THS] get_hot_concepts Tushare 失败: %s", e)

    # 降级：AKShare。
    #
    # ⚠️ 补充诊断（2026-09-01 任务#3 实施 Tushare 切换时发现，非本次
    # 修复目标但记录以免误导后人）：当前 AKShare 1.18.60 的
    # ak.stock_board_concept_name_ths() 实际只返回 [name, code] 两列
    # （题材名称+题材代码），**没有涨跌幅/成分股数量字段**——原有代码
    # 里 `if "涨跌幅" in df.columns` 这个判断因此从未真正成立过，
    # 一直是"看起来有排序逻辑、实际从未生效"的又一例（同 P0-c
    # ak_call() 模式）。这里保持诚实：AKShare 降级路径现在明确不提供
    # "热门"（按涨跌幅排序）语义，只能返回题材名称列表（原始顺序），
    # 字段名统一转换成与 Tushare 路径一致的"板块名称"，"涨跌幅"字段
    # 设为 None（不能编造假涨跌幅数据），下游消费方需要能处理
    # 涨跌幅=None 的情况（get_stock_theme_tags 只用"板块名称"字段，
    # 不受影响）。
    try:
        import akshare as ak  # noqa: delayed import

        df = call_with_timeout(ak.stock_board_concept_name_ths, 10)
        if df is None or df.empty:
            return []
        result: list[dict[str, Any]] = [
            {"板块名称": row.get("name"), "涨跌幅": None, "成分股数量": None,
             "code": row.get("code")}
            for _, row in df.head(limit).iterrows()
        ]
        _cache.set("hot_concepts", result)
        return result
    except Exception as e:
        _logger.warning("[THS] get_hot_concepts AKShare 降级也失败: %s", e)
        return []


def get_concept_stocks(concept_name: str) -> list[str]:
    """获取某题材的成分股 6 位代码列表。

    v9.9.x FIX 2026-09-01（任务#3）：Tushare 为主（ths_index 映射
    题材名→ts_code，再用 ths_member 查成分股），AKShare 为降级兜底
    （已知 ak.stock_board_concept_cons_ths() 已死，降级大概率仍失败，
    仅作最后一层保险，不删除是因为万一未来 AKShare 重新支持这个接口，
    不用改代码就能自动恢复）。
    """
    key = f"concept_stocks_{concept_name}"
    cached = _cache.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    # 主：Tushare
    try:
        from infra.data_source.providers.tushare_provider import TushareProvider
        provider = TushareProvider()
        if provider.is_available():
            codes = call_with_timeout(
                provider.fetch, 10, metric="ths_concept_members",
                concept_name=concept_name,
            )
            if codes:
                _cache.set(key, codes, ttl=1800)
                return codes  # type: ignore[no-any-return]
    except Exception as e:
        _logger.warning("[THS] get_concept_stocks(%s) Tushare 失败: %s", concept_name, e)

    # 降级：AKShare（已知接口已死，见模块顶部注释，仅作最后兜底）
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
        _logger.warning("[THS] get_concept_stocks(%s) AKShare 降级也失败: %s", concept_name, e)
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
