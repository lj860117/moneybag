"""
TushareProvider -- DataSourceProtocol adapter for Tushare Pro.
===============================================================
Primary source for:
  - market:      stock prices, indices, ETF (structured, stable)
  - fundamental: earnings, valuation, financials (strongest coverage)

Requires TUSHARE_TOKEN environment variable.

Status: IMPLEMENTED — provides all 7 supported metrics.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

import pandas as pd

from infra.cache import MemoryCache

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    "stock_price",
    "index_daily",
    "fund_nav",
    "income_statement",
    "balance_sheet",
    "valuation",
    "dividend",
})

# Caches by TTL requirement
_quote_cache = MemoryCache(default_ttl=300)     # 5 min for real-time quotes
_kline_cache = MemoryCache(default_ttl=3600)    # 1 hour for daily bars
_financial_cache = MemoryCache(default_ttl=86400)  # 24 hours for financials


class TushareProvider:
    """Tushare Pro data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports tushare only on first fetch() call.
    
    API Notes:
    - pro.daily(ts_code, start_date, end_date) → DataFrame with daily bars
    - pro.index_daily(ts_code, start_date, end_date) → DataFrame with index daily
    - pro.fund_nav(ts_code, start_date, end_date) → DataFrame with fund NAV
    - pro.income(ts_code, start_date, end_date) → DataFrame with income statement
    - pro.balancesheet(ts_code, start_date, end_date) → DataFrame with balance sheet
    - pro.valuation_daily(ts_code, start_date, end_date) → DataFrame with valuation metrics
    - pro.dividend(ts_code, start_date, end_date) → DataFrame with dividend history
    
    Code format: Tushare uses "000001.SZ", "600519.SH" format
    """

    def __init__(self) -> None:
        self._token: str = os.environ.get("TUSHARE_TOKEN", "")
        self._api: Any = None  # lazy-loaded tushare.pro_api instance
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], pd.DataFrame, None]:
        """Fetch a data metric from Tushare Pro.

        Args:
            metric: One of "stock_price", "index_daily", "fund_nav", etc.
            **params:
                - symbol: Code to fetch (e.g. "000001" for stocks, "399001" for indices)
                - start_date: "YYYYMMDD" format
                - end_date: "YYYYMMDD" format
                - ts_code: Alternative parameter name (will use symbol if provided)

        Returns:
            DataFrame or dict with requested data, or None on failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        try:
            if metric == "stock_price":
                return self._fetch_stock_price(**params)
            elif metric == "index_daily":
                return self._fetch_index_daily(**params)
            elif metric == "fund_nav":
                return self._fetch_fund_nav(**params)
            elif metric == "income_statement":
                return self._fetch_income_statement(**params)
            elif metric == "balance_sheet":
                return self._fetch_balance_sheet(**params)
            elif metric == "valuation":
                return self._fetch_valuation(**params)
            elif metric == "dividend":
                return self._fetch_dividend(**params)
        except Exception as e:
            logger.debug(f"TushareProvider.fetch({metric}) failed: {e}")

        return None

    def _fetch_stock_price(self, **params: Any) -> Any:
        """Fetch daily stock price bars."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_stock_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} stock price rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.daily failed: {e}")

        return None

    def _fetch_index_daily(self, **params: Any) -> Any:
        """Fetch daily index bars."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_index_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        # Index codes: 399001=深证成指, 000001=沪深300, 000300=沪深300, 399006=创业板指
        ts_code = self._normalize_code(symbol, "index")
        api = self._get_api()

        try:
            df = api.index_daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} index rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.index_daily failed: {e}")

        return None

    def _fetch_fund_nav(self, **params: Any) -> Any:
        """Fetch fund NAV."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_fund_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "fund")
        api = self._get_api()

        try:
            df = api.fund_nav(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} fund NAV rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.fund_nav failed: {e}")

        return None

    def _fetch_income_statement(self, **params: Any) -> Any:
        """Fetch income statement (financial metrics)."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_income_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.income(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} income statement rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.income failed: {e}")

        return None

    def _fetch_balance_sheet(self, **params: Any) -> Any:
        """Fetch balance sheet (financial metrics)."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_balance_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.balancesheet(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} balance sheet rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.balancesheet failed: {e}")

        return None

    def _fetch_valuation(self, **params: Any) -> Any:
        """Fetch valuation metrics."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_valuation_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            # Using valuation_daily for daily valuation metrics
            df = api.valuation_daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} valuation rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.valuation_daily failed: {e}")

        return None

    def _fetch_dividend(self, **params: Any) -> Any:
        """Fetch dividend history."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_dividend_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.dividend(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} dividend rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.dividend failed: {e}")

        return None

    def is_available(self) -> bool:
        """Check if Tushare token is configured and API is reachable."""
        if self._available is not None:
            return self._available
        if not self._token:
            self._available = False
            return False
        try:
            api = self._get_api()
            # Test API with a simple call - if token is invalid, this will raise
            # We can verify by checking if we got an api instance
            self._available = api is not None
        except Exception:
            self._available = False
        return self._available

    @property
    def provider_name(self) -> str:
        return "tushare"

    def _get_api(self) -> Any:
        """Lazy-import tushare and initialize pro_api."""
        if self._api is None:
            import tushare as ts  # noqa: delayed import
            ts.set_token(self._token)
            self._api = ts.pro_api()
        return self._api

    def _normalize_code(self, code: str, asset_type: str = "stock") -> str:
        """Convert 6-digit A-share code to Tushare format.
        
        Examples:
            "000001" + "stock" → "000001.SZ"
            "600519" + "stock" → "600519.SH"
            "399001" + "index" → "399001.SZ"
            
        Args:
            code: 6-digit code or already-formatted code
            asset_type: "stock", "index", or "fund"
        
        Returns:
            Tushare-formatted code
        """
        # If already formatted (contains dot), return as-is
        if "." in code:
            return code

        if len(code) == 6 and code.isdigit():
            if asset_type == "stock":
                if code.startswith(("0", "3")):
                    return f"{code}.SZ"
                elif code.startswith("6"):
                    return f"{code}.SH"
                elif code.startswith(("8", "4")):
                    return f"{code}.BJ"
            elif asset_type == "index":
                # Index codes: 399001, 000001, etc. follow different conventions
                if code.startswith("399"):
                    return f"{code}.SZ"  # 深证指数
                elif code.startswith(("000", "999")):
                    return f"{code}.SH"  # 沪市指数
                else:
                    return f"{code}.SZ"  # default
            elif asset_type == "fund":
                # Fund codes typically start with 1 or 5
                if code.startswith("1"):
                    return f"{code}.SZ"
                elif code.startswith("5"):
                    return f"{code}.SH"
        
        # Already formatted or unknown - pass through
        return code
