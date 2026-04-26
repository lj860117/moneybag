"""
Data Source infrastructure -- five-bucket taxonomy.
=====================================================
Invariant #6: All external data through infra/data_source.

Buckets:
  - market/       stock prices, indices, fund reference data
  - fundamental/  valuations, financials, ROE
  - macro/        GDP, CPI, rates (few core indicators)
  - alt/          northbound flows, margin, news, sentiment
  - synthetic/    composite / derived data

Placeholder buckets (not implemented):
  - providers/    Tushare / AKShare / baostock adapters (M1 W4)
  - fallback.py   three-level degradation chain (M1 W4)

This facade re-exports the most commonly used functions.
Callers can also import from specific buckets:
    from infra.data_source.alt import get_stock_news

Design doc: docs/design/12-framework-refactor.md section 6
"""
from infra.data_source.alt import get_stock_news
from infra.data_source.market import search_funds

__all__ = [
    "get_stock_news",
    "search_funds",
]
