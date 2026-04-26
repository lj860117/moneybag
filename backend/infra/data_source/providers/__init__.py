"""
Data source provider adapters -- Tushare, AKShare, baostock.
============================================================
Each provider implements domain.protocols.DataSourceProtocol.
Planned implementation: M1 W4.

Degradation priority per category (12-framework-refactor.md section 6.1):
  - market:      Tushare > baostock > AKShare
  - fundamental: Tushare > AKShare
  - macro:       AKShare > Tushare
  - alt:         AKShare (sole source)

Invariant #6: All external data through infra/data_source.
"""
