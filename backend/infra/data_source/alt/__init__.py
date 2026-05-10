"""
Alt data bucket -- news, sentiment, northbound flows, margin trading.
====================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

Current scope:
  - get_stock_news()  -- delegates to services.news_data (M1 W3)
  - flows.py          -- northbound, margin, interbank, fund flow (batch 3)

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Dict, List


def get_stock_news(code: str, limit: int = 8) -> List[Dict]:
    """Fetch stock news by stock code.

    Returns list of {"title": str, "time": str, "source": str}.
    Empty list on failure (never raises).

    Strangler Fig: delegates to services.news_data.get_stock_news_by_code()
    which already handles 15-min caching + error recovery.
    """
    try:
        from services.news_data import get_stock_news_by_code
        return get_stock_news_by_code(code, limit)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_stock_news failed: {e}")
        return []


# Re-export flow adapters
from infra.data_source.alt.flows import (  # noqa: E402
    get_hsgt_hist,
    get_hsgt_hold_stock,
    get_margin_sse,
    get_bond_zh_us_rate,
    get_interbank_rate,
    get_individual_fund_flow_rank,
    get_individual_fund_flow,
    get_zt_pool,
    get_north_net_flow,
    get_block_trade_daily,
    get_insider_trade_xq,
    get_sector_fund_flow_rank,
    get_industry_board_summary,
    get_stock_individual_info,
    get_futures_news,
)
