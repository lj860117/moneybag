"""
Market data bucket -- stock prices, K-lines, indices, ETFs, fund reference data.
================================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md section 6).

Current scope (M1 W3):
  - search_funds()  -- fund name/code search

Planned (M1 W4):
  - daily bars, index data, fund NAV
  - migrated from services/market_data.py + services/stock_data_provider.py

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Dict, List


def search_funds(query: str, limit: int = 20) -> List[Dict]:
    """Search funds by code or name keyword.

    Returns list of {"code": str, "name": str, "type": str}.
    Empty list on failure (never raises).

    Strangler Fig: uses fund_monitor._load_fund_names() for the cached
    DataFrame (24h TTL), then applies query filtering.
    Logic extracted from api/news.py search_fund().
    """
    try:
        from services.fund_monitor import _load_fund_names
        df = _load_fund_names()
        if df is None or len(df) == 0:
            return _fallback_fund_search(query, limit)

        code_col = [c for c in df.columns if "代码" in c or "code" in c.lower()]
        name_col = [c for c in df.columns
                    if "名称" in c or "简称" in c or "name" in c.lower()]
        type_col = [c for c in df.columns if "类型" in c or "type" in c.lower()]

        if not code_col or not name_col:
            return _fallback_fund_search(query, limit)

        cc, nc = code_col[0], name_col[0]
        tc = type_col[0] if type_col else None
        mask = (df[cc].astype(str).str.contains(query)
                | df[nc].astype(str).str.contains(query, case=False))
        matched = df[mask].head(limit)

        return [
            {
                "code": str(row[cc]),
                "name": str(row[nc]),
                "type": str(row[tc]) if tc else "",
            }
            for _, row in matched.iterrows()
        ]
    except Exception as e:
        print(f"[DATA_SOURCE/MARKET] search_funds: {e}")
        return _fallback_fund_search(query, limit)


def _fallback_fund_search(query: str, limit: int = 20) -> List[Dict]:
    """Fallback: search using locally cached fund rank data."""
    try:
        from services.fund_rank import _load_fund_rank_data
        rank_data = _load_fund_rank_data()
        if not rank_data:
            return []
        results = []
        for item in rank_data:
            code = item.get("code", "")
            name = item.get("name", "")
            if query in code or query.lower() in name.lower():
                results.append({
                    "code": code, "name": name,
                    "type": item.get("type", ""),
                })
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []
