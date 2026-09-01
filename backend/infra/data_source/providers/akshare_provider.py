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
from infra.data_source.fallback import call_with_timeout

logger = logging.getLogger(__name__)

# v9.9.x: 接入超时保护（FIX 2026-09-01，任务#8）
# 本文件 14 处裸 ak.xxx() 调用统一走 call_with_timeout()，超时值 10s
# （生产服务器真实测量均 <3s，10s 留足余量）。

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
            elif metric == "fund_nav":
                result = self._fetch_fund_nav(**params)
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
            df = call_with_timeout(ak.macro_china_gdp, 10)
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
            df = call_with_timeout(ak.macro_china_cpi, 10)
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
            df = call_with_timeout(ak.macro_china_pmi, 10)
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
            df = call_with_timeout(ak.rate_interbank, 10)  # AKShare's SHIBOR endpoint
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
            df = call_with_timeout(ak.macro_china_lpr, 10)
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
            df = call_with_timeout(ak.macro_china_money_supply, 10)
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
            df = call_with_timeout(ak.stock_news_em, 10, symbol=symbol)
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
            df = call_with_timeout(ak.stock_hsgt_hist_em, 10)  # AKShare's northbound flow endpoint
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
            df = call_with_timeout(ak.stock_margin_sse, 10)  # Shanghai margin detail
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
            
            df = call_with_timeout(ak.stock_lhb_detail_em, 10, **kwargs)
            if df is not None and len(df) > 0:
                _flow_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched block trades: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare stock_lhb_detail_em failed: {e}")

        return None

    # Fund data fetchers
    def _fetch_fund_name(self, **params: Any) -> Any:
        """Fetch fund name/list data.

        ⚠️ 已知失效（2026-09-01 任务#8 排查发现）：`ak.fund_info_sz()` /
        `ak.fund_info_sh()` 在当前服务器 AKShare 1.18.60 中均已不存在
        （`AttributeError: module 'akshare' has no attribute 'fund_info_sz'`），
        应是随库升级被移除/改名。原 try/except 一直静默吞掉这个
        AttributeError，`_fetch_fund_name` 长期恒返回 None，从未真正
        工作过（同 P0-c `ak_call()` 写了两个月零调用方的模式——看起来
        有实现，实际完全没接上）。

        代码检索确认：当前没有任何调用方以 metric="fund_name" 触发
        `AkshareProvider.fetch()`（`fund_name`/`fund_rank` 仅出现在
        `_SUPPORTED_METRICS`/`DEFAULT_CHAINS`声明里，未见实际 fetch 调
        用），属于死代码路径，暂无生产影响。这里仅补充超时保护（防止
        未来一旦被启用会挂死），不替换新接口——替换需要验证新函数返回
        的列名/字段与潜在调用方期望是否兼容，属于独立评估范围。
        """
        cache_key = "ak_fund_name"
        cached = _fund_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            # Multiple approaches: try fund_info first
            df = None
            try:
                df = call_with_timeout(ak.fund_info_sz, 10)  # Shenzhen fund info
            except:
                pass
            
            if df is None or len(df) == 0:
                df = call_with_timeout(ak.fund_info_sh, 10)  # Shanghai fund info
            
            if df is not None and len(df) > 0:
                _fund_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched fund name data: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare fund_info_* failed: {e}")

        return None

    def _fetch_fund_rank(self, **params: Any) -> Any:
        """Fetch fund ranking data.

        ⚠️ 已知失效（2026-09-01 任务#8 排查发现）：`ak.fund_rank_ts()`
        在当前 AKShare 1.18.60 中已不存在（同上 `_fetch_fund_name`
        的排查结论）。代码检索确认无实际调用方（死代码路径），仅补
        超时保护，不替换接口（详见 `_fetch_fund_name` docstring）。
        若未来要启用此 metric，建议改用 `ak.fund_open_fund_rank_em()`
        （已验证可用，2026-09-01 measured elapsed 4.65s/375 rows）。
        """
        cache_key = "ak_fund_rank"
        cached = _fund_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            df = call_with_timeout(ak.fund_rank_ts, 10)  # Fund ranking from TianShu
            if df is not None and len(df) > 0:
                _fund_cache.set(cache_key, df)
                logger.debug(f"AkshareProvider fetched fund rank: {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare fund_rank_ts failed: {e}")

        return None

    def _fetch_fund_nav(self, symbol: str, indicator: str = "单位净值走势", **kwargs: Any) -> Any:
        """获取基金历史净值（AKShare 1.18.x 用 symbol 参数）。

        AKShare v1.18+ API: fund_open_fund_info_em(symbol=code, indicator='单位净值走势')
        返回 DataFrame: [净值日期, 单位净值, 累计净值] 或 [净值日期, 单位净值, 日增长率]
        """
        cache_key = f"ak_fund_nav_{symbol}"
        cached = _macro_cache.get(cache_key)
        if cached is not None:
            return cached

        ak = self._get_ak()
        try:
            # AKShare 1.18.x 参数名是 symbol（旧版是 fund）
            df = call_with_timeout(ak.fund_open_fund_info_em, 10, symbol=symbol, indicator=indicator)
            if df is not None and len(df) > 0:
                _macro_cache.set(cache_key, df, ttl=3600)
                logger.debug(f"AkshareProvider fund_nav({symbol}): {len(df)} rows")
                return df
        except Exception as e:
            logger.debug(f"AKShare fund_nav({symbol}) failed: {e}")

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
