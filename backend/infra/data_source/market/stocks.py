"""
Market data bucket -- stock prices, K-lines, indices, fund NAV, futures.
=========================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

All external data calls are centralized here.
Each function wraps data source providers with:
  - try/except (never raises to caller)
  - consistent return type (DataFrame or None)
  - logging on failure
  - FallbackRunner orchestration for multi-source resilience

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import pandas as pd
from typing import Any, Dict, List, Optional


# ============================================================
# Stock Prices / Quotes
# ============================================================

def get_stock_daily_hist(
    code: str,
    period: str = "daily",
    start_date: str = "",
    end_date: str = "",
    adjust: str = "qfq",
) -> Any:
    """Get stock daily OHLCV history.

    Degradation chain (K-lines):
    - AKShare (primary) → Baostock (fallback) → mootdx (fallback)
    
    Using FallbackRunner for orchestrated multi-source fallback.

    Args:
        code: stock code e.g. "000001"
        period: "daily" | "weekly" | "monthly"
        start_date: "YYYYMMDD" format (empty = all history)
        end_date: "YYYYMMDD" format (empty = today)
        adjust: "qfq" (前复权) | "hfq" (后复权) | "" (不复权)

    Returns:
        DataFrame with columns: 日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
        None on failure.
    """
    try:
        from infra.data_source.fallback import FallbackRunner
        
        # Build parameters for provider calls
        params = {
            "symbol": code,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
        }
        
        # Custom chain for K-line compatibility: AKShare → Baostock
        chain = ["akshare", "baostock"]
        
        runner = FallbackRunner(metric="stock_price", chain=chain, params=params)
        data, metadata = runner.fetch()
        
        if data is not None:
            if metadata["source"] != "akshare":
                print(f"[DATA_SOURCE/MARKET] get_stock_daily_hist({code}) 已降级至 {metadata['source']} "
                      f"({metadata['elapsed']}s, {metadata['attempts']} attempts)")
            return data
        else:
            # All providers in fallback chain failed
            print(f"[DATA_SOURCE/MARKET] get_stock_daily_hist({code}) 所有降级链都失败: {metadata.get('error', 'unknown')}")
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_daily_hist({code}) FallbackRunner异常: {e}")
    
    return None


def get_stock_realtime_quotes_em() -> Any:
    """Get all A-share realtime quotes (akshare stock_zh_a_spot_em).

    Returns:
        DataFrame with realtime price data for all A-shares.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测该接口曾直接
    ConnectionError（耗时6.4s才报错），且被 stock_data_provider.py/
    valuation_engine.py/recommend_engine.py 三处引用——是这批未处理裸调用
    里被引用最多的一个。全市场快照接口没有分页，超时兜底设 15s
    （正常网络下应在数秒内返回，15s 留足给上游偶尔变慢的余量）。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_zh_a_spot_em, 15)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_realtime_quotes_em: {e}")
        return None


def get_stock_realtime_single(code: str) -> dict | None:
    """单只股票实时行情（三级降级：AKShare → 腾讯 → Tushare 昨收）

    Args:
        code: 6位股票代码，如 "600519"

    Returns:
        dict with keys: code, name, price, change_pct, volume, amount, pe, pb, market_cap, source
        None if all sources fail.
    """
    # 降级1: AKShare 个股行情
    # v9.9.x: 接入超时保护（FIX 2026-09-01）——同 get_stock_realtime_quotes_em，
    # 这是同一个全市场快照接口，挂死时必须及时放弃转下一层降级（腾讯/Tushare），
    # 不能让第一层的裸调用无限期拖住整条三级降级链。
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        df = call_with_timeout(ak.stock_zh_a_spot_em, 15)
        if df is not None and len(df) > 0:
            row = df[df["代码"] == code]
            if len(row) > 0:
                r = row.iloc[0]
                return {
                    "code": code,
                    "name": r.get("名称", ""),
                    "price": float(r.get("最新价", 0)),
                    "change_pct": float(r.get("涨跌幅", 0)),
                    "volume": float(r.get("成交量", 0)),
                    "amount": float(r.get("成交额", 0)),
                    "pe": float(r.get("市盈率-动态", 0)),
                    "pb": float(r.get("市净率", 0)),
                    "market_cap": float(r.get("总市值", 0)),
                    "source": "akshare",
                }
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_realtime_single({code}) AKShare 失败: {e}")

    # 降级2: 腾讯行情（免费、稳定、24小时可用）
    try:
        from infra.data_source.providers.tencent_provider import get_stock_quote_tencent
        q = get_stock_quote_tencent(code)
        if q and q.get("price"):
            return {
                "code": code,
                "name": q.get("name", ""),
                "price": q["price"],
                "change_pct": q.get("change_pct", 0),
                "volume": q.get("volume", 0),
                "amount": q.get("amount", 0),
                "pe": q.get("pe", 0),
                "pb": q.get("pb", 0),
                "market_cap": q.get("market_cap", 0),
                "source": "tencent",
            }
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_realtime_single({code}) 腾讯降级失败: {e}")

    # 降级3: Tushare 昨收（有延迟但总比没有强）
    try:
        from infra.data_source.providers.tushare_provider import TushareProvider
        provider = TushareProvider()
        if provider.is_available():
            df = provider.fetch("stock_price", symbol=code)
            if df is not None and isinstance(df, pd.DataFrame) and len(df) > 0:
                latest = df.iloc[0]  # Tushare daily 默认按日期降序
                return {
                    "code": code,
                    "name": "",
                    "price": float(latest.get("close", 0)),
                    "change_pct": float(latest.get("pct_chg", 0)),
                    "volume": float(latest.get("vol", 0)),
                    "amount": float(latest.get("amount", 0)),
                    "pe": 0,
                    "pb": 0,
                    "market_cap": 0,
                    "source": "tushare_daily",
                }
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_realtime_single({code}) Tushare降级失败: {e}")

    return None


def get_stock_realtime_quotes() -> Any:
    """Get all A-share realtime quotes - legacy API (akshare stock_zh_a_spot).

    Returns:
        DataFrame with realtime price data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测该接口耗时 13.72s（分页
    拉取全市场，带进度条），比 stock_zh_a_spot_em 慢很多——超时兜底给
    25s 留余量，正常网络下应该在这个窗口内完成。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_zh_a_spot, 25)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_realtime_quotes: {e}")
        return None


def get_stock_spot_xq(symbol: str) -> Any:
    """Get single stock realtime quote from xueqiu (akshare stock_individual_spot_xq).

    Args:
        symbol: stock symbol e.g. "SH600519" or "SZ000001"

    Returns:
        DataFrame with single stock quote data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。这是 get_stock_realtime() 的
    主查询源，被 GET /api/watchlist/alerts（前端每15秒轮询交易时段）
    间接调用；雪球接口经实测已知不稳定（曾出现 JSONDecodeError），加超时
    避免网络挂死时拖住整条降级链。实测正常耗时 <1s，给 8s 留足网络抖动
    余量。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_individual_spot_xq, 8, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_spot_xq({symbol}): {e}")
        return None


def get_stock_daily_legacy(symbol: str, adjust: str = "qfq") -> Any:
    """Get stock daily K-line - legacy API (akshare stock_zh_a_daily).

    Args:
        symbol: formatted symbol e.g. "sz000001"

    Returns:
        DataFrame or None.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。这是降级链的第三层兜底
    （雪球→东财→这里），实测正常耗时 <1s，给 8s 留足网络抖动余量。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_zh_a_daily, 8, symbol=symbol, adjust=adjust)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_daily_legacy({symbol}): {e}")
        return None


def get_stock_code_name_list() -> Any:
    """Get all A-share code/name mapping (akshare stock_info_a_code_name).

    Returns:
        DataFrame with columns: code, name.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测耗时 6.92s、5553 行。
    调用方 services/stock_monitor.py::_get_stock_name() 每次调用都拉
    全量再遍历查找、完全没有缓存——这是效率问题（不在本次改动范围），
    但超时保护先接上，避免上游挂死时无限期拖住"新增持仓自动补全名称"
    这个用户可感知的写路径。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_info_a_code_name, 15)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_code_name_list: {e}")
        return None


# ============================================================
# Index Data
# ============================================================

def get_index_daily(symbol: str = "sh000300") -> Any:
    """Get index daily K-line with multi-source fallback.
    
    Degradation chain:
    1. Tushare (most recent data, enterprise-grade)
    2. Baostock (free, stable, no API limits)
    3. AKShare (always available)
    
    Using FallbackRunner for orchestrated multi-source fallback.
    
    Symbol mapping examples:
    - sh000300 / 000300 → Tushare: 399300.SZ (沪深300)
    - sh000001 / 000001 → Tushare: 000001.SH (上证指数)
    - sz399001 / 399001 → Tushare: 399001.SZ (深证成指)

    Args:
        symbol: index symbol e.g. "sh000300" (沪深300)

    Returns:
        DataFrame with date/open/high/low/close/volume.
        None on failure.
    """
    try:
        from infra.data_source.fallback import FallbackRunner
        
        params = {"symbol": symbol}
        
        # Chain: Tushare (preferred) → Baostock → AKShare
        # Tushare has best data freshness, Baostock is free & stable, AKShare is fallback
        chain = ["tushare", "baostock", "akshare"]
        
        runner = FallbackRunner(metric="index_daily", chain=chain, params=params)
        data, metadata = runner.fetch()
        
        if data is not None:
            if metadata["source"] != "tushare":
                print(f"[DATA_SOURCE/MARKET] get_index_daily({symbol}) 已降级至 {metadata['source']} "
                      f"({metadata['elapsed']}s, {metadata['attempts']} attempts)")
            return data
        else:
            print(f"[DATA_SOURCE/MARKET] get_index_daily({symbol}) 所有降级链都失败: {metadata.get('error', 'unknown')}")
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_index_daily({symbol}) FallbackRunner异常: {e}")
    
    return None


def get_index_pe(symbol: str = "沪深300") -> Any:
    """Get index PE ratio history (akshare stock_index_pe_lg).

    Args:
        symbol: index name in Chinese e.g. "沪深300"

    Returns:
        DataFrame with PE history.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测 <1s，调用方
    services/factor_data.py 和 services/market_data.py 均有各自的
    factor_cache/precomputed_cache 兜底，超时保护给10s留网络抖动余量。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_index_pe_lg, 10, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_index_pe({symbol}): {e}")
        return None


def get_index_valuation_csindex(symbol: str = "000300") -> Any:
    """Get index valuation from CSIndex (akshare stock_zh_index_value_csindex).

    Args:
        symbol: CSIndex code e.g. "000300"

    Returns:
        DataFrame with valuation data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测 <1s，超时兜底10s。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_zh_index_value_csindex, 10, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_index_valuation_csindex({symbol}): {e}")
        return None


# ============================================================
# Fund Data
# ============================================================

def get_fund_nav_history(code: str, indicator: str = "单位净值走势") -> Any:
    """Get fund NAV history with multi-source fallback.
    
    Degradation chain:
    1. AKShare (primary source for fund NAV)
    2. Tushare (fallback - 5000积分接口)
    
    Using FallbackRunner for orchestrated multi-source fallback.

    Args:
        code: fund code e.g. "110011"
        indicator: "单位净值走势" | "累计净值走势" | "同类排名走势"

    Returns:
        DataFrame with NAV history.
        None on failure.
    """
    try:
        from infra.data_source.fallback import FallbackRunner
        
        params = {"symbol": code, "indicator": indicator}
        
        # Chain: AKShare (primary) → Tushare (fallback)
        chain = ["akshare", "tushare"]
        
        runner = FallbackRunner(metric="fund_nav", chain=chain, params=params)
        data, metadata = runner.fetch()
        
        if data is not None:
            if metadata["source"] != "akshare":
                print(f"[DATA_SOURCE/MARKET] get_fund_nav_history({code}) 已降级至 {metadata['source']} "
                      f"({metadata['elapsed']}s)")
            return data
        else:
            print(f"[DATA_SOURCE/MARKET] get_fund_nav_history({code}) 所有降级链都失败: {metadata.get('error', 'unknown')}")
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_fund_nav_history({code}) FallbackRunner异常: {e}")
    
    return None


def get_fund_name_list() -> Any:
    """Get all fund code/name list (akshare fund_name_em).

    Returns:
        DataFrame with fund codes and names.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测全市场 ~2.8万条数据
    耗时约 8s（P0-c 已实测过同一接口），给 15s 留足余量。被
    services/fund_monitor.py 的 _load_fund_names() 用（24h缓存），
    冷启动/缓存过期时会触发一次真实调用。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.fund_name_em, 15)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_fund_name_list: {e}")
        return None


def get_fund_estimated_nav() -> Any:
    """Get fund estimated NAV (realtime estimation, akshare fund_value_estimation_em).

    Returns:
        DataFrame with estimated NAV for all open funds.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。此接口目前无独立降级源
    （fund_monitor.py 里的第三层兜底走的是完全不同的 fundgz 单只查询
    services.market_data.get_fund_nav，不经过这个函数），加超时防止
    上游挂死时拖住 GET /api/fund-holdings/alerts（2h缓存过期后触发）。
    实测正常耗时 <1s，给 10s 留余量（全市场表比单只查询稍慢，且此接口
    2026-09-01 实测已出现过 TypeError: 'NoneType' object is not
    subscriptable——上游东财接口本身不稳定，超时和这个已知问题是两件
    独立的事：超时保护接口挂死，异常兜底走 except 分支返回 None。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.fund_value_estimation_em, 10)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_fund_estimated_nav: {e}")
        return None



def get_fund_rank(symbol: str = "全部") -> Any:
    """Get open fund ranking (akshare fund_open_fund_rank_em).

    Args:
        symbol: fund type filter e.g. "全部", "股票型", "混合型"

    Returns:
        DataFrame with fund ranking data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测全量约2万条耗时5.41s，
    调用方 services/fund_rank.py 有24h缓存兜底，超时给20s留余量
    （比实测值宽松，避免正常但稍慢的一次请求被误杀）。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.fund_open_fund_rank_em, 20, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_fund_rank({symbol}): {e}")
        return None


def get_etf_fund_daily() -> Any:
    """Get ETF fund daily data (akshare fund_etf_fund_daily_em).

    Returns:
        DataFrame with ETF daily data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测 <1s，调用方
    services/market_factors.py 有1h缓存兜底，超时兜底10s。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.fund_etf_fund_daily_em, 10)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_etf_fund_daily: {e}")
        return None


# ============================================================
# Futures / Commodities
# ============================================================

def get_futures_main(symbol: str = "AU0") -> Any:
    """Get main futures contract data from Sina (akshare futures_main_sina).

    Args:
        symbol: futures symbol e.g. "AU0" (gold), "CU0" (copper)

    Returns:
        DataFrame with OHLCV data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测 <1s，超时兜底10s。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.futures_main_sina, 10, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_futures_main({symbol}): {e}")
        return None


def get_futures_foreign_hist(symbol: str = "布伦特原油") -> Any:
    """Get foreign commodity futures history (akshare futures_foreign_hist).

    Args:
        symbol: commodity name in Chinese e.g. "布伦特原油"

    Returns:
        DataFrame with price history.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测该接口当前直接报
    ValueError: Expected object or value（数据解析错误，是上游接口
    本身的问题，跟超时无关，这里不修那个独立问题，只保证挂死场景下
    也能及时放弃而不是无限等待——两个问题各自的 except 分支都能兜住）。
    超时给10s。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.futures_foreign_hist, 10, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_futures_foreign_hist({symbol}): {e}")
        return None


# ============================================================
# Alt data (restricted shares — lives in market for colocation with stock data)
# ============================================================

def get_restricted_release_summary() -> Any:
    """Get restricted share release schedule (akshare stock_restricted_release_summary_em).

    Returns:
        DataFrame with upcoming restricted share release data.
        None on failure.

    v9.9.x: 接入超时保护（FIX 2026-09-01）。实测 <1s，调用方
    services/market_factors.py 有1h缓存兜底，超时兜底10s。
    """
    try:
        import akshare as ak
        from infra.data_source.fallback import call_with_timeout
        return call_with_timeout(ak.stock_restricted_release_summary_em, 10)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_restricted_release_summary: {e}")
        return None
