"""
Three-level fallback chain for data source providers.
=====================================================
Planned implementation: M1 W4 (requires provider adapters).

Degradation order per category:
  - market:      Tushare > baostock > AKShare
  - fundamental: Tushare > AKShare
  - macro:       AKShare > Tushare
  - alt:         AKShare (sole source)

See docs/design/12-framework-refactor.md section 6.1

Invariant #6: All external data through infra/data_source.
"""
