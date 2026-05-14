"""
AkshareProvider -- DataSourceProtocol adapter for AKShare.
===========================================================
Primary source for:
  - macro:  GDP, CPI, PMI, rates (widest macro coverage)
  - alt:    news, northbound flows, margin data (sole source for alt)

Secondary source for:
  - fundamental:  some valuation/financial data

Status: IMPLEMENTED — provides all 12 supported metrics.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union, cast

from infra.cache import MemoryCache

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    # macro (primary)
    "macro_gdp",
    "macro_cpi",
    "macro_pmi",
    "macro_shibor",
    "macro_lpr",
    "macro_m1_m2",
    # alt (sole source)
    "stock_news",
    "northbound_flow",
    "margin_detail",
    "block_trade",
    # fundamental (secondary)
    "fund_name",
    "fund_rank",
})

# Caches by TTL requirement
_macro_cache = MemoryCache(default_ttl=1800)      # 30 min for macro data
_news_cache = MemoryCache(default_ttl=600)        # 10 min for news (changes frequently)
_flow_cache = MemoryCache(default_ttl=300)        # 5 min for flow data
_fund_cache = MemoryCache(default_ttl=3600)       # 1 hour for fund data


class AkshareProvider:
    """AKShare data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports akshare only on first fetch() call.

    Note: AKShare is scraper-based and may break on upstream website changes.
    The fallback chain should always have a secondary provider configured.
    
    API Notes:
    - AKShare functions return pandas DataFrames
    - Different functions for different data types (macro_china_gdp, stock_news_em, etc.)
    - Network may be unstable during peak hours or holidays
    """

    def __init__(self) -> None:
        self._ak: Any = None  # lazy-loaded akshare module
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
        """Fetch a data metric from AKShare.

        Args:
            metric: One of the _SUPPORTED_METRICS
            **params: Metric-specific parameters (symbol, start_date, end_date, etc.)

        Returns:
            DataFrame, dict, or list with requested data, or None on failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        try:
            result: dict[str, Any] | list[Any] | None = None
            if metric == "macro_gdp":
                result = self._fetch_macro_gdp(**params)
            elif metric == "macro_cpi":
                result = self._fetch_macro_cpi(**params)
            elif metric == "macro_pmi":
                result = self._fetch_macro_pmi(**params)
            elif metric == "macro_shibor":
                result = self._fetch_macro_shibor(**params)
            elif metric == "macro_lpr":
                result = self._fetch_macro_lpr(**params)
            elif metric == "macro_m1_m2":
                result = self._fetch_macro_m1_m2(**params)
            elif metric == "stock_news":
                result = self._fetch_stock_news(**params)
            elif metric == "northbound_flow":
                result = self._fetch_northbound_flow(**params)
            elif metric == "margin_detail":
                result = self._fetch_margin_detail(**params)
            elif metric == "block_trade":
                result = self._fetch_block_trade(**params)
            elif metric == "fund_name":
                result = self._fetch_fund_name(**params)
            elif metric == "fund_rank":
                result = self._fetch_fund_rank(**params)
            return result
        except Exception as e:
            logger.debug(f"AkshareProvider.fetch({metric}) failed: {e}")

        return None

    # Macro data fetchers
    def _fetch_macro_gdp(self, **params: Any) -> Any:
        """Fetch China GDP data."""
        cache_key = "ak_macro_gdp"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.macro_china_gdp()
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched GDP data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare macro_china_gdp failed: {e}")

        return None

    def _fetch_macro_cpi(self, **params: Any) -> Any:
        """Fetch China CPI data."""
        cache_key = "ak_macro_cpi"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.macro_china_cpi()
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched CPI data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare macro_china_cpi failed: {e}")

        return None

    def _fetch_macro_pmi(self, **params: Any) -> Any:
        """Fetch China PMI data."""
        cache_key = "ak_macro_pmi"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.macro_china_pmi()
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched PMI data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare macro_china_pmi failed: {e}")

        return None

    def _fetch_macro_shibor(self, **params: Any) -> Any:
        """Fetch SHIBOR rate data."""
        cache_key = "ak_macro_shibor"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.rate_interbank()  # AKShare's SHIBOR endpoint
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched SHIBOR data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare rate_interbank failed: {e}")

        return None

    def _fetch_macro_lpr(self, **params: Any) -> Any:
        """Fetch China LPR (Loan Prime Rate) data."""
        cache_key = "ak_macro_lpr"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.macro_china_lpr()
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched LPR data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare macro_china_lpr failed: {e}")

        return None

    def _fetch_macro_m1_m2(self, **params: Any) -> Any:
        """Fetch China M1/M2 money supply data."""
        cache_key = "ak_macro_m1_m2"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.macro_china_money_supply()
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched M1/M2 data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare macro_china_money_supply failed: {e}")

        return None

    # Alternative data fetchers
    def _fetch_stock_news(self, **params: Any) -> Any:
        """Fetch stock/financial news."""
        symbol = params.get("symbol", "财经")
        cache_key = f"ak_news_{symbol}"
        cached = _news_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.stock_news_em(symbol=symbol)
            if df is not None and len(df) > 0:
                _news_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched news for {symbol}: {len(df)} articles")
                return df
        except Exception as e:
            logger.debug(f"AKShare stock_news_em failed: {e}")

        return None

    def _fetch_northbound_flow(self, **params: Any) -> Any:
        """Fetch northbound flow (沪港通/深港通) history."""
        cache_key = "ak_northbound_flow"
        cached = _flow_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.stock_hsgt_hist_em()  # AKShare's northbound flow endpoint
            if df is not None and len(df) > 0:
                _flow_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched northbound flow: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare stock_hsgt_hist_em failed: {e}")

        return None

    def _fetch_margin_detail(self, **params: Any) -> Any:
        """Fetch margin trading detail (融资融券)."""
        cache_key = "ak_margin_detail"
        cached = _flow_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.stock_margin_sse()  # Shanghai margin detail
            if df is not None and len(df) > 0:
                _flow_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched margin detail: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare stock_margin_sse failed: {e}")

        return None

    def _fetch_block_trade(self, **params: Any) -> Any:
        """Fetch block trade detail (大宗交易)."""
        start_date = params.get("start_date", "")
        end_date = params.get("end_date", "")
        
        cache_key = f"ak_block_trade_{start_date}_{end_date}"
        cached = _flow_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            kwargs = {}
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            
            df = ak.stock_lhb_detail_em(**kwargs)
            if df is not None and len(df) > 0:
                _flow_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched block trades: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare stock_lhb_detail_em failed: {e}")

        return None

    # Fund data fetchers
    def _fetch_fund_name(self, **params: Any) -> Any:
        """Fetch fund name/list data."""
        cache_key = "ak_fund_name"
        cached = _fund_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            # Multiple approaches: try fund_info first
            df = None
            try:
                df = ak.fund_info_sz()  # Shenzhen fund info
            except:
                pass
            
            if df is None or len(df) == 0:
                df = ak.fund_info_sh()  # Shanghai fund info
            
            if df is not None and len(df) > 0:
                _fund_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched fund name data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare fund_info_* failed: {e}")

        return None

    def _fetch_fund_rank(self, **params: Any) -> Any:
        """Fetch fund ranking data."""
        cache_key = "ak_fund_rank"
        cached = _fund_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = ak.fund_rank_ts()  # Fund ranking from TianShu
            if df is not None and len(df) > 0:
                _fund_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched fund rank: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare fund_rank_ts failed: {e}")

        return None

    def is_available(self) -> bool:
        """Check if AKShare can be imported."""
        if self._available is not None:
            return self._available
        try:
            self._get_ak()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    @property
    def provider_name(self) -> str:
        return "akshare"

    def _get_ak(self) -> Any:
        """Lazy-import akshare module."""
        if self._ak is None:
            import akshare as ak  # noqa: delayed import
            self._ak = ak
        return self._ak
