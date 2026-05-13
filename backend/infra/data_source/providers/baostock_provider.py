"""
BaostockProvider -- DataSourceProtocol adapter for baostock.
=============================================================
Tertiary source (most stable, free, no API key required):
  - market:  A-share daily bars, indices (most reliable fallback)

Used as last-resort in the three-level degradation chain.

Status: IMPLEMENTED — provides stock_price and index_daily metrics.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import pandas as pd
from typing import Any, Dict, List, Union

from infra.cache import MemoryCache

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    "stock_price",
    "index_daily",
    "stock_industry",
})

# Cache for K-line data (1 hour) - baostock queries are slow
_kline_cache = MemoryCache(default_ttl=3600)


class BaostockProvider:
    """Baostock data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports baostock only on first fetch() call.

    baostock requires login()/logout() session management.
    This adapter handles it internally.
    
    API Notes:
    - query_history_k_data_plus(code, fields, start_date, end_date, frequency=5, adjustflag=3)
      frequency=5 = daily, adjustflag=3 = forward-adjusted (前复权)
      Returns ResultSet with .data list of bar objects
    - Code format: "sz000001" (shenzhen), "sh600519" (shanghai), "bj920000" (beijing)
    """

    def __init__(self) -> None:
        self._bs: Any = None  # lazy-loaded baostock module
        self._logged_in: bool = False
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
        """Fetch a data metric from baostock.

        Args:
            metric: One of "stock_price", "index_daily", "stock_industry"
            **params:
                - symbol: 6-digit code e.g. "000001"
                - start_date: "YYYYMMDD" (optional)
                - end_date: "YYYYMMDD" (optional)
                - adjust: "qfq" (前复权) | "hfq" (后复权) | "" (不复权) [only for stock_price]

        Returns:
            DataFrame with OHLCV data, or None on any failure (never raises).
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
            elif metric == "stock_industry":
                return self._fetch_stock_industry(**params)
        except Exception as e:
            logger.debug(f"BaostockProvider.fetch({metric}) failed: {e}")

        return None

    def _fetch_stock_price(self, **params: Any) -> Any:
        """Fetch stock daily OHLCV history.
        
        Returns DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额
        (compatible with akshare format)
        """
        symbol = params.get("symbol", "")
        if not symbol:
            return None

        # Check cache
        cache_key = f"bs_stock_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        # Convert code format: "000001" → "sz000001"
        bs_code = self._normalize_code(symbol)

        # Map adjust flag: "qfq" (前复权) → adjustflag=3
        adjust = params.get("adjust", "qfq")
        if adjust == "hfq":
            adjustflag = 2  # 后复权 (backward-adjusted)
        elif adjust == "":
            adjustflag = 1  # 不复权 (no adjustment)
        else:
            adjustflag = 3  # 前复权 (forward-adjusted) - default

        bs = self._get_bs()
        start_date = params.get("start_date", "")
        end_date = params.get("end_date", "")

        # Query: frequency=5 = daily
        rs = bs.query_history_k_data_plus(
            code=bs_code,
            fields="date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency=5,
            adjustflag=adjustflag
        )

        if rs.error_code != "0":
            logger.debug(f"Baostock query_history_k_data_plus failed: {rs.error_msg}")
            return None

        if not rs.data or len(rs.data) == 0:
            return None

        # Convert to DataFrame (matching akshare format)
        try:
            records = []
            for bar in rs.data:
                records.append({
                    "日期": bar.date,
                    "开盘": float(bar.open),
                    "收盘": float(bar.close),
                    "最高": float(bar.high),
                    "最低": float(bar.low),
                    "成交量": int(float(bar.volume)) if bar.volume else 0,
                    "成交额": float(bar.amount) if bar.amount else 0,
                })

            df = pd.DataFrame(records)
            
            # Cache and return
            _kline_cache.set(cache_key, df)
            logger.debug(f"BaostockProvider fetched {len(df)} rows for {symbol}")
            return df

        except Exception as e:
            logger.debug(f"Failed to convert Baostock result to DataFrame: {e}")
            return None

    def _fetch_index_daily(self, **params: Any) -> Any:
        """Fetch index daily OHLCV history.
        
        For indices like Shanghai Composite (sh000001), Shenzhen Composite (sz399001).
        Returns DataFrame with same columns as stock_price.
        """
        symbol = params.get("symbol", "")
        if not symbol:
            return None

        # Index symbols are typically passed with prefix already, or we can map them
        # Common indices: 000001 (沪深300), 399001 (深证成指)
        if symbol.startswith(("sh", "sz")):
            bs_code = symbol
        else:
            # Assume it's a code like "000001" and default to shanghai
            bs_code = f"sh{symbol}"

        # Use same logic as stock_price for index
        return self._fetch_stock_price(**{**params, "symbol": bs_code})

    def _fetch_stock_industry(self, **params: Any) -> Any:
        """Fetch stock industry classification.
        
        Returns DataFrame with stock code and industry information.
        Note: Baostock may not have a direct industry API, so this returns None for now.
        """
        # Baostock doesn't have a dedicated industry classification API
        # This would require mapping from another source or query_stock_industry() if available
        logger.debug("BaostockProvider: stock_industry not yet implemented")
        return None

    def is_available(self) -> bool:
        """Check if baostock can be imported and logged in."""
        if self._available is not None:
            return self._available
        try:
            self._login()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    @property
    def provider_name(self) -> str:
        return "baostock"

    def _get_bs(self) -> Any:
        """Lazy-import baostock and ensure login."""
        if self._bs is None:
            self._login()
        return self._bs

    def _login(self) -> None:
        """Login to baostock (required before any query)."""
        if not self._logged_in:
            import baostock as bs  # noqa: delayed import
            bs.login()
            self._bs = bs
            self._logged_in = True

    def _normalize_code(self, code: str) -> str:
        """Convert 6-digit A-share code to Baostock format.
        
        Examples:
            "000001" → "sz000001" (shenzhen)
            "600519" → "sh600519" (shanghai)
            "920000" → "bj920000" (beijing)
        """
        if len(code) == 6 and code.isdigit():
            if code.startswith(("0", "3")):
                return f"sz{code}"
            elif code.startswith("6"):
                return f"sh{code}"
            elif code.startswith(("8", "4")):
                return f"bj{code}"
        # Already prefixed or unknown format - pass through
        return code

    def __del__(self) -> None:
        """Best-effort logout on garbage collection."""
        if self._logged_in and self._bs is not None:
            try:
                self._bs.logout()
            except Exception:
                pass
