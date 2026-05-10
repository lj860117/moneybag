"""
Fundamental data bucket -- financial indicators, valuations, fund holdings.
============================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

All akshare calls for fundamental data are centralized here.

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Any


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
        return ak.stock_financial_analysis_indicator(symbol=symbol, start_year=start_year)
    except Exception as e:
        print(f"[DATA_SOURCE/FUNDAMENTAL] get_financial_indicators({symbol}): {e}")
        return None


def get_stock_lg_indicator(symbol: str = "000300") -> Any:
    """Get A-share Legu indicator (akshare stock_a_lg_indicator).

    Args:
        symbol: index/stock code e.g. "000300"

    Returns:
        DataFrame with valuation indicators.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_a_lg_indicator(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/FUNDAMENTAL] get_stock_lg_indicator({symbol}): {e}")
        return None


def get_fund_portfolio_holdings(symbol: str, date: str = "2025") -> Any:
    """Get fund portfolio holdings (akshare fund_portfolio_hold_em).

    Args:
        symbol: fund code e.g. "110011"
        date: year string e.g. "2025"

    Returns:
        DataFrame with fund's stock holdings.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.fund_portfolio_hold_em(symbol=symbol, date=date)
    except Exception as e:
        print(f"[DATA_SOURCE/FUNDAMENTAL] get_fund_portfolio_holdings({symbol}): {e}")
        return None
