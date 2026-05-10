"""
Fundamental data bucket -- valuations, PE/PB, ROE, earnings, financials.
========================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

Current scope (绞杀者 batch 3):
  - Financial analysis indicators (ROE, EPS, etc.)
  - Legu valuation indicator
  - Fund portfolio holdings

Invariant #6: All external data through infra/data_source.
"""
from infra.data_source.fundamental.financials import (
    get_financial_indicators,
    get_stock_lg_indicator,
    get_fund_portfolio_holdings,
)
