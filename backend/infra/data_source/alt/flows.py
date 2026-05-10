"""
Alt data bucket -- northbound flows, margin, interbank rates, fund flows.
==========================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

All akshare calls for alternative data are centralized here.

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Any


# ============================================================
# Northbound / HSGT (Hong Kong-Shanghai/Shenzhen Connect)
# ============================================================

def get_hsgt_hist(symbol: str = "北向资金") -> Any:
    """Get northbound/southbound capital flow history (akshare stock_hsgt_hist_em).

    Args:
        symbol: "北向资金" | "沪股通" | "深股通" | "南向资金"

    Returns:
        DataFrame with daily capital flow data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_hsgt_hist_em(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_hsgt_hist({symbol}): {e}")
        return None


def get_hsgt_hold_stock(market: str = "北向", indicator: str = "今日排行") -> Any:
    """Get northbound holding stock rankings (akshare stock_hsgt_hold_stock_em).

    Args:
        market: "北向" | "沪股通" | "深股通"
        indicator: "今日排行" | "3日排行" | "5日排行" | "10日排行" | "月排行" | "季排行"

    Returns:
        DataFrame with stock-level northbound holdings.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_hsgt_hold_stock_em(market=market, indicator=indicator)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_hsgt_hold_stock({market}, {indicator}): {e}")
        return None


# ============================================================
# Margin Trading
# ============================================================

def get_margin_sse() -> Any:
    """Get Shanghai Stock Exchange margin trading data (akshare stock_margin_sse).

    Returns:
        DataFrame with daily margin balance data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_margin_sse()
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_margin_sse: {e}")
        return None


# ============================================================
# Bond / Interest Rates
# ============================================================

def get_bond_zh_us_rate(start_date: str = "20240101") -> Any:
    """Get China-US treasury yield spread (akshare bond_zh_us_rate).

    Args:
        start_date: "YYYYMMDD" format

    Returns:
        DataFrame with bond yield data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.bond_zh_us_rate(start_date=start_date)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_bond_zh_us_rate: {e}")
        return None


def get_interbank_rate(
    market: str = "上海银行同业拆借市场",
    symbol: str = "Shibor人民币",
    indicator: str = "隔夜",
) -> Any:
    """Get interbank lending rate (akshare rate_interbank).

    Args:
        market: e.g. "上海银行同业拆借市场"
        symbol: e.g. "Shibor人民币"
        indicator: e.g. "隔夜", "1周", "1月"

    Returns:
        DataFrame with SHIBOR data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.rate_interbank(market=market, symbol=symbol, indicator=indicator)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_interbank_rate({indicator}): {e}")
        return None


# ============================================================
# Fund Flow
# ============================================================

def get_individual_fund_flow_rank(indicator: str = "今日") -> Any:
    """Get individual stock fund flow ranking (akshare stock_individual_fund_flow_rank).

    Args:
        indicator: "今日" | "3日" | "5日" | "10日"

    Returns:
        DataFrame with stock-level fund flow rankings.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_individual_fund_flow_rank(indicator=indicator)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_individual_fund_flow_rank({indicator}): {e}")
        return None


def get_individual_fund_flow(stock: str, market: str = "sh") -> Any:
    """Get individual stock fund flow details (akshare stock_individual_fund_flow).

    Args:
        stock: stock code e.g. "000001"
        market: "sh" | "sz"

    Returns:
        DataFrame with inflow/outflow data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_individual_fund_flow(stock=stock, market=market)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_individual_fund_flow({stock}): {e}")
        return None


# ============================================================
# Market Microstructure / Activity
# ============================================================

def get_zt_pool(date: str = "") -> Any:
    """Get daily limit-up (涨停) stock pool (akshare stock_zt_pool_em).

    Args:
        date: "YYYYMMDD" format (empty = today)

    Returns:
        DataFrame with limit-up stocks.
        None on failure.
    """
    try:
        import akshare as ak
        kwargs = {"date": date} if date else {"date": __import__("datetime").datetime.now().strftime("%Y%m%d")}
        return ak.stock_zt_pool_em(**kwargs)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_zt_pool({date}): {e}")
        return None


def get_north_net_flow() -> Any:
    """Get northbound net capital inflow (akshare stock_hsgt_north_net_flow_in_em).

    Returns:
        DataFrame with daily northbound net flow.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_hsgt_north_net_flow_in_em()
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_north_net_flow: {e}")
        return None


def get_block_trade_daily() -> Any:
    """Get block trade (大宗交易) daily summary (akshare stock_dzjy_mrtj).

    Returns:
        DataFrame with block trade data.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_dzjy_mrtj()
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_block_trade_daily: {e}")
        return None


def get_insider_trade_xq() -> Any:
    """Get insider trading from Xueqiu (akshare stock_inner_trade_xq).

    Returns:
        DataFrame with insider trade records.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_inner_trade_xq()
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_insider_trade_xq: {e}")
        return None


def get_sector_fund_flow_rank(indicator: str = "今日", sector_type: str = "行业资金流") -> Any:
    """Get sector/industry fund flow ranking (akshare stock_sector_fund_flow_rank).

    Args:
        indicator: "今日" | "3日" | "5日" | "10日"
        sector_type: "行业资金流" | "概念资金流" | "地域资金流"

    Returns:
        DataFrame with sector fund flow rankings.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type=sector_type)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_sector_fund_flow_rank({indicator}): {e}")
        return None


def get_industry_board_summary() -> Any:
    """Get industry board summary from THS (akshare stock_board_industry_summary_ths).

    Returns:
        DataFrame with industry board data (涨跌幅/成交量/换手率 etc.).
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_board_industry_summary_ths()
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_industry_board_summary: {e}")
        return None


def get_stock_individual_info(symbol: str) -> Any:
    """Get individual stock basic info (akshare stock_individual_info_em).

    Args:
        symbol: stock code e.g. "000001"

    Returns:
        DataFrame with stock info (总市值/流通市值/行业/上市时间 etc.).
        None on failure.
    """
    try:
        import akshare as ak
        return ak.stock_individual_info_em(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_stock_individual_info({symbol}): {e}")
        return None


# ============================================================
# News (specific to alt data, not the macro/general news)
# ============================================================

def get_futures_news(symbol: str = "黄金") -> Any:
    """Get futures market news (akshare futures_news_shmet).

    Args:
        symbol: commodity name e.g. "黄金", "白银"

    Returns:
        DataFrame with futures news.
        None on failure.
    """
    try:
        import akshare as ak
        return ak.futures_news_shmet(symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_futures_news({symbol}): {e}")
        return None
