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
        
        runner = FallbackRunner(metric="stock_price", chain=chain, **params)
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
    """
    try:
        import akshare as ak
        return ak.stock_zh_a_spot_em()
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_realtime_quotes_em: {e}")
        return None


def get_stock_realtime_quotes() -> Any:
    """Get all A-share realtime quotes - legacy API (akshare stock_zh_a_spot).

    Returns:
        DataFrame with realtime price data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_zh_a_spot()
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
    """
    try:
        import akshare as ak
        return ak.stock_individual_spot_xq(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_spot_xq({symbol}): {e}")
        return None


def get_stock_daily_legacy(symbol: str, adjust: str = "qfq") -> Any:
    """Get stock daily K-line - legacy API (akshare stock_zh_a_daily).

    Args:
        symbol: formatted symbol e.g. "sz000001"

    Returns:
        DataFrame or None.
    """
    try:
        import akshare as ak
        return ak.stock_zh_a_daily(symbol=symbol, adjust=adjust)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_stock_daily_legacy({symbol}): {e}")
        return None


def get_stock_code_name_list() -> Any:
    """Get all A-share code/name mapping (akshare stock_info_a_code_name).

    Returns:
        DataFrame with columns: code, name.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_info_a_code_name()
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
        
        runner = FallbackRunner(metric="index_daily", chain=chain, **params)
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
    """
    try:
        import akshare as ak
        return ak.stock_index_pe_lg(symbol=symbol)
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
    """
    try:
        import akshare as ak
        return ak.stock_zh_index_value_csindex(symbol=symbol)
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
        
        runner = FallbackRunner(metric="fund_nav", chain=chain, **params)
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
    """
    try:
        import akshare as ak
        return ak.fund_name_em()
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_fund_name_list: {e}")
        return None


def get_fund_estimated_nav() -> Any:
    """Get fund estimated NAV (realtime estimation, akshare fund_value_estimation_em).

    Returns:
        DataFrame with estimated NAV for all open funds.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.fund_value_estimation_em()
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
    """
    try:
        import akshare as ak
        return ak.fund_open_fund_rank_em(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_fund_rank({symbol}): {e}")
        return None


def get_etf_fund_daily() -> Any:
    """Get ETF fund daily data (akshare fund_etf_fund_daily_em).

    Returns:
        DataFrame with ETF daily data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.fund_etf_fund_daily_em()
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
    """
    try:
        import akshare as ak
        return ak.futures_main_sina(symbol=symbol)
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
    """
    try:
        import akshare as ak
        return ak.futures_foreign_hist(symbol=symbol)
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
    """
    try:
        import akshare as ak
        return ak.stock_restricted_release_summary_em()
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] get_restricted_release_summary: {e}")
        return None
