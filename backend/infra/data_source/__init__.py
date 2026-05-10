"""
Data Source infrastructure -- five-bucket taxonomy.
=====================================================
Invariant #6: All external data through infra/data_source.

Buckets:
  - market/       stock prices, indices, fund reference data, futures
  - fundamental/  valuations, financials, ROE
  - macro/        GDP, CPI, rates (few core indicators)
  - alt/          northbound flows, margin, news, sentiment
  - synthetic/    composite / derived data

Placeholder buckets (not implemented):
  - providers/    Tushare / AKShare / baostock adapters (M1 W4)
  - fallback.py   three-level degradation chain (M1 W4)

This facade re-exports the most commonly used functions.
Callers can also import from specific buckets:
    from infra.data_source.market import get_stock_daily_hist
    from infra.data_source.alt import get_stock_news

Design doc: docs/design/12-framework-refactor.md section 6
"""
from infra.data_source.alt import get_stock_news
from infra.data_source.market import search_funds
from infra.data_source.market.stocks import (
    get_stock_daily_hist,
    get_stock_realtime_quotes_em,
    get_stock_realtime_quotes,
    get_stock_spot_xq,
    get_stock_daily_legacy,
    get_stock_code_name_list,
    get_index_daily,
    get_index_pe,
    get_index_valuation_csindex,
    get_fund_nav_history,
    get_fund_name_list,
    get_fund_estimated_nav,
    get_fund_rank,
    get_etf_fund_daily,
    get_futures_main,
    get_futures_foreign_hist,
    get_restricted_release_summary,
)

__all__ = [
    "get_stock_news",
    "search_funds",
    "get_stock_daily_hist",
    "get_stock_realtime_quotes_em",
    "get_stock_realtime_quotes",
    "get_stock_spot_xq",
    "get_stock_daily_legacy",
    "get_stock_code_name_list",
    "get_index_daily",
    "get_index_pe",
    "get_index_valuation_csindex",
    "get_fund_nav_history",
    "get_fund_name_list",
    "get_fund_estimated_nav",
    "get_fund_rank",
    "get_etf_fund_daily",
    "get_futures_main",
    "get_futures_foreign_hist",
    "get_restricted_release_summary",
]
