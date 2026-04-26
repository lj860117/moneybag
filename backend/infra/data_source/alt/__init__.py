"""
Alt data bucket -- news, sentiment, northbound flows, margin trading.
====================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md section 6).

Current scope (M1 W3):
  - get_stock_news()  -- delegates to services.news_data (Strangler Fig)

Planned (M1 W4):
  - northbound flows, margin, dragon-tiger, sentiment
  - all migrated from services/alt_data.py + services/factor_data.py

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
