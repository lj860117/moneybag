"""
Data source provider adapters -- Tushare, AKShare, baostock.
============================================================
Each provider implements domain.protocols.DataSourceProtocol.

Degradation priority per category (12-framework-refactor.md section 6.1):
  - market:      Tushare > baostock > AKShare
  - fundamental: Tushare > AKShare
  - macro:       AKShare > Tushare
  - alt:         AKShare (sole source)

Invariant #6: All external data through infra/data_source.
"""
from infra.data_source.providers.akshare_provider import AkshareProvider
from infra.data_source.providers.tushare_provider import TushareProvider
from infra.data_source.providers.baostock_provider import BaostockProvider

__all__ = [
    "AkshareProvider",
    "TushareProvider",
    "BaostockProvider",
]
