"""
DataSourceProtocol -- external data source contract
=====================================================
Abstracts the 30+ files that import akshare/tushare directly.

Data categories (five-bucket taxonomy):
  - market:       stock prices, indices, ETFs
  - fundamental:  earnings, valuation, financials
  - macro:        GDP, CPI, PMI, SHIBOR, oil
  - alt:          news sentiment, geopolitical, policy, northbound
  - derivatives:  (empty placeholder -- family asset mgmt doesn't touch this)

Three-level degradation:
  - Primary:   Tushare (structured, stable, strong fundamentals)
  - Secondary: AKShare (wide alt-data coverage; scraper-based, may break)
  - Tertiary:  baostock (A-share daily bars, free, most stable)

Implementations (planned M1 W4):
  - infra.data_source.providers.tushare_provider
  - infra.data_source.providers.akshare_provider
  - infra.data_source.providers.baostock_provider

Design doc: docs/design/12-framework-refactor.md
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable


@runtime_checkable
class DataSourceProtocol(Protocol):
    """Structural interface for external data providers.

    Each implementation wraps ONE provider (akshare, tushare, baostock).
    The infra layer composes them into a fallback chain.
    """

    def fetch(self, metric: str, **params: Any) -> Union[Dict, List, None]:
        """Fetch a data metric.

        Args:
            metric: Identifier like "stock_price", "macro_gdp", "fund_nav".
                    Each implementation defines its supported metrics.
            **params: Metric-specific parameters (symbol, start_date, etc.)

        Returns:
            dict or list of results. None on failure (never raises).
            Implementations MUST catch all exceptions and return None.
        """
        ...

    def is_available(self) -> bool:
        """Quick health check -- can this source serve requests right now?

        Should be fast (cached result OK). Used by fallback chain to skip
        known-down providers.
        """
        ...

    @property
    def provider_name(self) -> str:
        """Human-readable name: 'akshare', 'tushare', 'baostock'."""
        ...
