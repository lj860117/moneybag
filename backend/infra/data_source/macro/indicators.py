"""
Macro data bucket -- Chinese/global macroeconomic indicators.
==============================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

All akshare calls for macro data are centralized here.
Each function wraps akshare with:
  - try/except (never raises to caller)
  - consistent return type (DataFrame or None)
  - logging on failure

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Any


# ============================================================
# Chinese Macro Indicators
# ============================================================

def get_china_money_supply() -> Any:
    """Get China M0/M1/M2 money supply (akshare macro_china_money_supply).

    Returns:
        DataFrame with monthly money supply data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_money_supply()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_money_supply: {e}")
        return None


def get_china_social_financing() -> Any:
    """Get China social financing aggregate (akshare macro_china_shrzgm).

    Returns:
        DataFrame with social financing data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_shrzgm()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_social_financing: {e}")
        return None


def get_china_lpr() -> Any:
    """Get China LPR interest rates (akshare macro_china_lpr).

    Returns:
        DataFrame with LPR history.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_lpr()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_lpr: {e}")
        return None


def get_china_real_estate() -> Any:
    """Get China real estate development data (akshare macro_china_real_estate).

    Returns:
        DataFrame with real estate indicators.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_real_estate()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_real_estate: {e}")
        return None


def get_china_new_house_price() -> Any:
    """Get China new house price index (akshare macro_china_new_house_price).

    Returns:
        DataFrame with city-level house price data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_new_house_price()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_new_house_price: {e}")
        return None


def get_china_cpi() -> Any:
    """Get China CPI data (akshare macro_china_cpi).

    Returns:
        DataFrame with CPI history.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_cpi()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_cpi: {e}")
        return None


def get_china_pmi() -> Any:
    """Get China PMI data (akshare macro_china_pmi).

    Returns:
        DataFrame with PMI history.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_pmi()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_pmi: {e}")
        return None


def get_china_ppi() -> Any:
    """Get China PPI data (akshare macro_china_ppi).

    Returns:
        DataFrame with PPI history.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_ppi()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_ppi: {e}")
        return None


def get_china_gdp() -> Any:
    """Get China GDP data (akshare macro_china_gdp).

    Returns:
        DataFrame with quarterly GDP data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_gdp()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_gdp: {e}")
        return None


def get_china_industrial_value_added() -> Any:
    """Get China industrial value added (akshare macro_china_gyzjz).

    Returns:
        DataFrame with industrial production data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_gyzjz()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_industrial_value_added: {e}")
        return None


def get_china_retail_sales() -> Any:
    """Get China consumer goods retail sales (akshare macro_china_consumer_goods_retail).

    Returns:
        DataFrame with retail sales data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_consumer_goods_retail()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_retail_sales: {e}")
        return None


def get_china_fixed_asset_investment() -> Any:
    """Get China fixed asset investment (akshare macro_china_gdzctz).

    Returns:
        DataFrame with FAI data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_china_gdzctz()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_china_fixed_asset_investment: {e}")
        return None


# ============================================================
# US/Global Macro
# ============================================================

def get_usa_interest_rate() -> Any:
    """Get US Federal Reserve interest rate history (akshare macro_bank_usa_interest_rate).

    Returns:
        DataFrame with Fed rate history.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.macro_bank_usa_interest_rate()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_usa_interest_rate: {e}")
        return None


# ============================================================
# Market Activity (macro-adjacent, lives here for colocation)
# ============================================================

def get_market_activity() -> Any:
    """Get A-share market activity index (akshare stock_market_activity_legu).

    Returns:
        DataFrame with market activity/turnover data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_market_activity_legu()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_market_activity: {e}")
        return None


def get_lhb_detail(start_date: str = "", end_date: str = "") -> Any:
    """Get Dragon-Tiger board details (akshare stock_lhb_detail_em).

    Args:
        start_date: "YYYYMMDD" format
        end_date: "YYYYMMDD" format

    Returns:
        DataFrame with LHB detail.
        None on failure.
    """
    try:
        import akshare as ak
        kwargs = {}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return ak.stock_lhb_detail_em(**kwargs)
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_lhb_detail: {e}")
        return None


def get_management_holding_detail() -> Any:
    """Get management/insider holding changes (akshare stock_hold_management_detail_cninfo).

    Returns:
        DataFrame with insider trading data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_hold_management_detail_cninfo()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_management_holding_detail: {e}")
        return None


# ============================================================
# Global Markets
# ============================================================

def get_us_index(symbol: str = ".DJI") -> Any:
    """Get US stock index data from Sina (akshare index_us_stock_sina).

    Args:
        symbol: index symbol e.g. ".DJI", ".IXIC", ".INX"

    Returns:
        DataFrame with index OHLCV.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.index_us_stock_sina(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_us_index({symbol}): {e}")
        return None


def get_fx_spot_quote() -> Any:
    """Get foreign exchange spot quotes (akshare fx_spot_quote).

    Returns:
        DataFrame with FX rates.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.fx_spot_quote()
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_fx_spot_quote: {e}")
        return None


def get_global_market_pe(symbol: str = "美国") -> Any:
    """Get global market PE ratio (akshare stock_market_pe_lg).

    Args:
        symbol: market name e.g. "美国", "中国", "日本"

    Returns:
        DataFrame with PE ratio history.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_market_pe_lg(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_global_market_pe({symbol}): {e}")
        return None


def get_stock_news(symbol: str = "财经") -> Any:
    """Get stock/financial news (akshare stock_news_em).

    Args:
        symbol: news category e.g. "财经", "A股", or specific topic

    Returns:
        DataFrame with news articles.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_news_em(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_stock_news({symbol}): {e}")
        return None
