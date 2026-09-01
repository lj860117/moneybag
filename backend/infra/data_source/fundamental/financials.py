"""
Fundamental data bucket -- financial indicators, valuations, fund holdings.
============================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

All akshare calls for fundamental data are centralized here.

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Any

from infra.data_source.fallback import call_with_timeout

# v9.9.x: 接入超时保护（FIX 2026-09-01，任务#8）


def get_financial_indicators(symbol: str, start_year: str = "2024") -> Any:
    """Get stock financial analysis indicators (akshare stock_financial_analysis_indicator).

    Args:
        symbol: stock code e.g. "000001"
        start_year: start year for data e.g. "2024"

    Returns:
        DataFrame with financial indicators (ROE, EPS, etc.).
        None on failure.
    """
    try:
        import akshare as ak
        return call_with_timeout(ak.stock_financial_analysis_indicator, 10, symbol=symbol, start_year=start_year)
    except Exception as e:
        print(f"[DATA_SOURCE/FUNDAMENTAL] get_financial_indicators({symbol}): {e}")
        return None


def get_stock_lg_indicator(symbol: str = "000300") -> Any:
    """Get A-share Legu dividend-yield indicator.

    ⚠️ 接口已变更（2026-09-01 任务#8 排查发现）：原 `ak.stock_a_lg_
    indicator()` 在当前 AKShare 1.18.60 中已不存在（`AttributeError`），
    应是随库升级被移除/改名。原 try/except 一直静默吞掉这个异常，本
    函数长期恒返回 None，唯一调用方 `services/factor_data.py` 的
    `get_dividend_yield_factor()`（沪深300股息率价值因子）"方案B"分支
    因此从未真正生效过（同 P0-c `ak_call()` 模式——看起来有实现，
    实际完全没接上）。

    已找到并验证可用的替代接口：`ak.stock_a_gxl_lg(symbol="上证A股")`
    （乐咕乐股-A股股息率），返回列 ['日期','股息率']，与调用方
    `next((c for c in df.columns if "股息率" in str(c)), None)` 的
    查找逻辑完全兼容（2026-09-01 measured elapsed 0.57s, 5261 rows）。
    已切换到新接口（原 symbol="000300" 语义是指数代码，新接口的
    symbol 是市场分类而非指数代码，默认改用"上证A股"作为沪深300的
    近似代理——两者口径不完全等价，但都是"A股整体股息率"量级，用于
    因子评级的粗粒度参考足够；如需精确匹配沪深300成分股加权股息率，
    需要另外的精确算法，超出本次任务#8的范围）。

    Args:
        symbol: 市场分类，choice of {"上证A股", "深证A股", "创业板", "科创板"}。
            为兼容旧调用签名默认值改为对应关系：旧默认"000300"（沪深300
            指数代码）→ 新默认"上证A股"（市场级近似代理，非精确对应）。

    Returns:
        DataFrame ['日期','股息率']。
        None on failure.
    """
    market_map = {
        "000300": "上证A股",  # 旧调用习惯的沪深300指数代码 → 近似代理
    }
    resolved_symbol = market_map.get(symbol, symbol if symbol in {"上证A股", "深证A股", "创业板", "科创板"} else "上证A股")
    try:
        import akshare as ak
        return call_with_timeout(ak.stock_a_gxl_lg, 10, symbol=resolved_symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/FUNDAMENTAL] get_stock_lg_indicator({symbol}): {e}")
        return None


def get_fund_portfolio_holdings(symbol: str, date: str = "2025") -> Any:
    """Get fund portfolio holdings (akshare fund_portfolio_hold_em).

    ⚠️ 已知失效（2026-09-01 任务#8 排查发现）：多组 symbol/date 组合
    （包括默认参数）均 100% 复现 `Can not decode value starting with
    character ';'`（JSON 解析失败，页面返回内容已变化，非网络问题，
    3次+重试同样报错）。当前通过 except 静默返回 None，需要单独排查
    上游返回格式或寻找替代接口，此处仅补充超时保护，不做功能修复。

    Args:
        symbol: fund code e.g. "110011"
        date: year string e.g. "2025"

    Returns:
        DataFrame with fund's stock holdings.
        None on failure（当前恒为 None，见上方注释）。
    """
    try:
        import akshare as ak
        return call_with_timeout(ak.fund_portfolio_hold_em, 10, symbol=symbol, date=date)
    except Exception as e:
        print(f"[DATA_SOURCE/FUNDAMENTAL] get_fund_portfolio_holdings({symbol}): {e}")
        return None

