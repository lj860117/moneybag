"""
AkshareProvider -- DataSourceProtocol adapter for AKShare.
===========================================================
Primary source for:
  - macro:  GDP, CPI, PMI, rates (widest macro coverage)
  - alt:    news, northbound flows, margin data (sole source for alt)

Secondary source for:
  - fundamental:  some valuation/financial data

Status: STUB — method bodies are placeholders for M2 migration.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union

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


class AkshareProvider:
    """AKShare data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports akshare only on first fetch() call.

    Note: AKShare is scraper-based and may break on upstream website changes.
    The fallback chain should always have a secondary provider configured.
    """

    def __init__(self) -> None:
        self._ak: Any = None  # lazy-loaded akshare module
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
        """Fetch a data metric from AKShare.

        Returns None on any failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        # TODO(M2): implement per-metric dispatch
        # Example:
        #   ak = self._get_ak()
        #   if metric == "stock_news":
        #       df = ak.stock_news_em(symbol=params["symbol"])
        #       return df.to_dict("records")
        logger.debug("AkshareProvider.fetch(%s) — stub, returning None", metric)
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
