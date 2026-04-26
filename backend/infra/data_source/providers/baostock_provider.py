"""
BaostockProvider -- DataSourceProtocol adapter for baostock.
=============================================================
Tertiary source (most stable, free, no API key required):
  - market:  A-share daily bars, indices (most reliable fallback)

Used as last-resort in the three-level degradation chain.

Status: STUB — method bodies are placeholders for M2 migration.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    "stock_price",
    "index_daily",
    "stock_industry",
})


class BaostockProvider:
    """Baostock data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports baostock only on first fetch() call.

    baostock requires login()/logout() session management.
    This adapter handles it internally.
    """

    def __init__(self) -> None:
        self._bs: Any = None  # lazy-loaded baostock module
        self._logged_in: bool = False
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
        """Fetch a data metric from baostock.

        Returns None on any failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        # TODO(M2): implement per-metric dispatch
        # Example:
        #   bs = self._get_bs()
        #   if metric == "stock_price":
        #       rs = bs.query_history_k_data_plus(
        #           code=params["symbol"], fields="date,open,high,low,close,volume",
        #           start_date=params.get("start_date"), ...
        #       )
        #       return [row for row in rs.data]
        logger.debug("BaostockProvider.fetch(%s) — stub, returning None", metric)
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

    def __del__(self) -> None:
        """Best-effort logout on garbage collection."""
        if self._logged_in and self._bs is not None:
            try:
                self._bs.logout()
            except Exception:
                pass
