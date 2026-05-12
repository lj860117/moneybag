"""
Macro data bucket -- Chinese/global macroeconomic indicators.
==============================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

降级链: AKShare（免费爬虫，凌晨不稳定）→ Tushare（付费 API，5000积分，稳定）
Each function wraps data source calls with:
  - try/except (never raises to caller)
  - consistent return type (DataFrame or None)
  - logging on failure
  - automatic fallback to Tushare when AKShare fails

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import os
from typing import Any


# ============================================================
# Tushare 降级辅助
# ============================================================

def _tushare_available() -> bool:
    """检查 Tushare token 是否配置"""
    return bool(os.environ.get("TUSHARE_TOKEN", ""))


def _get_tushare_api() -> Any:
    """获取 Tushare Pro API 实例"""
    import tushare as ts
    return ts.pro_api()


# ============================================================
# Chinese Macro Indicators
# ============================================================

def get_china_money_supply() -> Any:
    """Get China M0/M1/M2 money supply.

    降级链: AKShare macro_china_money_supply → Tushare cn_m
    Returns:
        DataFrame with monthly money supply data.
        None on failure.
    """
    # 第一优先: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_money_supply failed: {e}")

    # 降级: Tushare cn_m
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_m(start_m='202001')
            if df is not None and len(df) > 0:
                import pandas as pd
                result = pd.DataFrame({
                    '月份': df['month'],
                    '货币和准货币(M2)-同比增长': df['m2_yoy'],
                    'M2-数量': df['m2'],
                })
                print(f"[DATA_SOURCE/MACRO] M2 降级到 Tushare 成功: {len(result)} rows")
                return result
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_m failed: {e}")

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
    """Get China CPI data.

    降级链: AKShare macro_china_cpi → Tushare cn_cpi
    Returns:
        DataFrame with CPI history.
        None on failure.
    """
    # 第一优先: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_cpi()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_cpi failed: {e}")

    # 降级: Tushare cn_cpi
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_cpi(start_m='202001')
            if df is not None and len(df) > 0:
                # 统一列名格式，让上层 macro_data.py 能识别
                df = df.rename(columns={'month': '月份', 'nt_yoy': '全国-同比增长'})
                print(f"[DATA_SOURCE/MACRO] CPI 降级到 Tushare 成功: {len(df)} rows")
                return df
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_cpi failed: {e}")

    return None


def get_china_pmi() -> Any:
    """Get China PMI data.

    降级链: AKShare macro_china_pmi → Tushare cn_pmi
    Returns:
        DataFrame with PMI history.
        None on failure.
    """
    # 第一优先: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_pmi()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_pmi failed: {e}")

    # 降级: Tushare cn_pmi
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_pmi(start_m='202001')
            if df is not None and len(df) > 0:
                # PMI010000 是制造业 PMI 指数
                import pandas as pd
                result = pd.DataFrame({
                    '月份': df['MONTH'],
                    '制造业-指数': df['PMI010000'],
                })
                print(f"[DATA_SOURCE/MACRO] PMI 降级到 Tushare 成功: {len(result)} rows")
                return result
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_pmi failed: {e}")

    return None


def get_china_ppi() -> Any:
    """Get China PPI data.

    降级链: AKShare macro_china_ppi → Tushare cn_ppi
    Returns:
        DataFrame with PPI history.
        None on failure.
    """
    # 第一优先: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_ppi()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_ppi failed: {e}")

    # 降级: Tushare cn_ppi
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_ppi(start_m='202001')
            if df is not None and len(df) > 0:
                import pandas as pd
                result = pd.DataFrame({
                    '月份': df['month'],
                    '当月同比增长': df['ppi_yoy'],
                })
                print(f"[DATA_SOURCE/MACRO] PPI 降级到 Tushare 成功: {len(result)} rows")
                return result
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_ppi failed: {e}")

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
