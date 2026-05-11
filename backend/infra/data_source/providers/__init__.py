"""
Data source provider adapters -- Tushare, AKShare, baostock, mootdx, 腾讯财经.
============================================================
Each provider implements domain.protocols.DataSourceProtocol.

Degradation priority per category (12-framework-refactor.md section 6.1):
  - market:      Tushare > baostock > AKShare > mootdx（TCP，不封IP）
  - fundamental: Tushare > AKShare > mootdx finance（积分耗尽兜底）
  - macro:       AKShare > Tushare
  - alt:         AKShare (sole source)
  - 行情指标:    Tushare PE/PB > 腾讯财经（无 Key，5min 缓存）

Invariant #6: All external data through infra/data_source.
"""
from infra.data_source.providers.akshare_provider import AkshareProvider
from infra.data_source.providers.tushare_provider import TushareProvider
from infra.data_source.providers.baostock_provider import BaostockProvider
from infra.data_source.providers.mootdx_provider import get_daily_hist_mootdx, get_finance_mootdx
from infra.data_source.providers.tencent_provider import get_stock_quote_tencent

__all__ = [
    "AkshareProvider",
    "TushareProvider",
    "BaostockProvider",
    "get_daily_hist_mootdx",
    "get_finance_mootdx",
    "get_stock_quote_tencent",
]
