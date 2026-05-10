"""
Macro data bucket -- GDP, CPI, PMI, LPR, social financing, global markets.
============================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md section 6).

Current scope (绞杀者 batch 2):
  - Chinese macro: money supply, social financing, LPR, CPI, PPI, PMI, GDP, etc.
  - Global: US interest rate, US indices, FX, global PE
  - Market activity: LHB, management holdings, market activity index
  - News: stock_news_em wrapper

Invariant #6: All external data through infra/data_source.
"""
from infra.data_source.macro.indicators import (
    # Chinese macro
    get_china_money_supply,
    get_china_social_financing,
    get_china_lpr,
    get_china_real_estate,
    get_china_new_house_price,
    get_china_cpi,
    get_china_pmi,
    get_china_ppi,
    get_china_gdp,
    get_china_industrial_value_added,
    get_china_retail_sales,
    get_china_fixed_asset_investment,
    # US/Global macro
    get_usa_interest_rate,
    # Market activity
    get_market_activity,
    get_lhb_detail,
    get_management_holding_detail,
    # Global markets
    get_us_index,
    get_fx_spot_quote,
    get_global_market_pe,
    get_stock_news,
)
