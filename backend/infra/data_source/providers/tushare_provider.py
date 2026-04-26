"""
TushareProvider -- DataSourceProtocol adapter for Tushare Pro.
===============================================================
Primary source for:
  - market:      stock prices, indices, ETF (structured, stable)
  - fundamental: earnings, valuation, financials (strongest coverage)

Requires TUSHARE_TOKEN environment variable.

Status: STUB — method bodies are placeholders for M2 migration.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

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


class TushareProvider:
    """Tushare Pro data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports tushare only on first fetch() call.
    """

    def __init__(self) -> None:
        self._token: str = os.environ.get("TUSHARE_TOKEN", "")
        self._api: Any = None  # lazy-loaded tushare.pro_api instance
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
        """Fetch a data metric from Tushare Pro.

        Returns None on any failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        # TODO(M2): implement per-metric dispatch
        # Example:
        #   api = self._get_api()
        #   if metric == "stock_price":
        #       df = api.daily(ts_code=params["symbol"], ...)
        #       return df.to_dict("records")
        logger.debug("TushareProvider.fetch(%s) — stub, returning None", metric)
        return None

    def is_available(self) -> bool:
        """Check if Tushare token is configured and API is reachable."""
        if self._available is not None:
            return self._available
        if not self._token:
            self._available = False
            return False
        try:
            self._get_api()
            self._available = True
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
