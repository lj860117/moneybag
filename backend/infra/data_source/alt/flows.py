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
    """Get northbound/southbound capital flow history with Tushare fallback.
    
    Degradation chain: AKShare stock_hsgt_hist_em() → Tushare hsgt_detail()

    ⚠️ 口径（2026-08 修正）：北向【净买入】自 2024-08-19 起沪深交易所停止日频
    披露、改为按季度公布。因此：
      - AKShare stock_hsgt_hist_em 的净买入列自 2024-08-16 起全为 NaN；
      - Tushare hsgt_detail 的 north_money 是【当日成交额】（买入额+卖出额，
        恒为正），**不是净买入**。故降级分支的列名为 `北向成交额(亿)`，
        原名 `北向资金` 会被下游误读成净流入，已改名。
    调用方务必按列名判断语义，不要假设本函数返回的是净流入。

    Args:
        symbol: "北向资金" | "沪股通" | "深股通" | "南向资金"

    Returns:
        DataFrame with daily capital flow data.
        AKShare 路径：原始列（净买入列在 2024-08 后为 NaN）。
        Tushare 降级路径：['日期', '北向成交额(亿)'] —— 语义是成交额。
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        result = ak.stock_hsgt_hist_em(symbol=symbol)
        if result is not None and len(result) > 0:
            return result
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_hsgt_hist({symbol}) - AKShare failed: {e}")

    # Fallback: Tushare (for 北向资金 only, as others don't have direct equiv)
    if symbol == "北向资金":
        try:
            import os
            ts_token = os.environ.get("TUSHARE_TOKEN", "")
            if ts_token:
                import tushare as ts
                ts.set_token(ts_token)
                pro = ts.pro_api()
                result = pro.hsgt_detail(start_date="20230101")
                if result is not None and len(result) > 0:
                    import pandas as pd
                    transformed = pd.DataFrame({
                        '日期': result['trade_date'],
                        # north_money 是当日成交额（百万元）→ /100 得亿元。
                        # 列名必须体现"成交额"，不能叫"北向资金"（会被读成净流入）。
                        '北向成交额(亿)': result['north_money'] / 100,
                    })
                    print(f"[DATA_SOURCE/ALT] get_hsgt_hist: Fallback to Tushare success "
                          f"({len(transformed)} rows, 语义=成交额非净流入)")
                    return transformed
        except Exception as e:
            print(f"[DATA_SOURCE/ALT] get_hsgt_hist (Tushare fallback failed): {e}")

    print(f"[DATA_SOURCE/ALT] get_hsgt_hist({symbol}): All sources failed")
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
    """Get northbound net capital inflow (AKShare only — Tushare 降级已移除).

    ⚠️ 口径（2026-08 修正，请勿重新添加 Tushare 降级）：
    北向【净买入】自 2024-08-19 起沪深交易所停止日频披露、改为按季度公布，
    因此**任何数据源都拿不到日频北向净流入**。

    原实现的 Tushare 降级分支把 `hsgt_detail` 的 `north_money / 100` 塞进列
    `北向资金(亿)` —— 而 north_money 是【当日成交额】（买入额+卖出额，恒为正、
    2500~3300 亿量级），**不是净买入**。那等于把成交额伪装成净流入返回给调用方，
    是凭空造数据，已整段删除。

    现在的行为：AKShare 路径语义正确（读 stock_hsgt_hist_em 的当日净买额），
    但该列自 2024-08-16 起全为 NaN，实际会被下面的 `!= 0` 过滤掉 → 返回 None。
    **返回 None 是诚实的结果**，表示"净流入确实拿不到"，调用方应据此跳过该维度，
    而不是拿一个假数字继续算。

    只看成交额请改用 `services.tushare_data.get_northbound_flow()`
    （明确区分 available / net_flow_available）。

    Returns:
        DataFrame ['日期','北向资金(亿)','沪股通(亿)','深股通(亿)'] —— 仅当交易所
        恢复日频净买入披露时才会有数据。
        None：净流入不可得（2024-08-19 起的常态）。
    """
    # Primary: AKShare (v1.18+ 用 stock_hsgt_hist_em，旧接口 stock_hsgt_north_net_flow_in_em 已废弃)
    try:
        import akshare as ak
        import pandas as pd
        # 用沪股通+深股通合并计算北向净流入
        df_sh = ak.stock_hsgt_hist_em(symbol="沪股通")
        df_sz = ak.stock_hsgt_hist_em(symbol="深股通")
        if df_sh is not None and df_sz is not None and len(df_sh) > 0:
            # 列结构：[日期, 当日净买额, 买入成交额, 卖出成交额, ...]
            # 第 0 列是日期，第 1 列是当日净买额（单位亿），用列位置取（列名可能是中文，env 环境渲染可能乱码）
            date_col = df_sh.columns[0]
            # 取第一个 numeric 列（就是净买额）
            numeric_cols_sh = df_sh.select_dtypes(include="number").columns.tolist()
            numeric_cols_sz = df_sz.select_dtypes(include="number").columns.tolist()
            if numeric_cols_sh:
                net_col_sh = numeric_cols_sh[0]  # 当日净买额（亿）
                net_col_sz = numeric_cols_sz[0] if numeric_cols_sz else net_col_sh
                # 按日期对齐，合并北向
                df_sh_idx = df_sh.set_index(date_col)
                df_sz_idx = df_sz.set_index(df_sz.columns[0])
                combined = df_sh_idx[[net_col_sh]].join(
                    df_sz_idx[[net_col_sz]].rename(columns={net_col_sz: "_sz"}),
                    how="left"
                )
                combined["_sz"] = combined["_sz"].fillna(0)
                merged = pd.DataFrame({
                    "日期": combined.index,
                    "北向资金(亿)": (combined[net_col_sh].fillna(0) + combined["_sz"]).values,
                    "沪股通(亿)": combined[net_col_sh].values,
                    "深股通(亿)": combined["_sz"].values,
                })
                # 过滤掉净流入全0的行（2024-08 后净买额为 NaN→fillna(0)，会在此被滤掉）
                merged = merged[merged["北向资金(亿)"] != 0].tail(30)
                if len(merged) > 0:
                    return merged
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_north_net_flow (AKShare failed): {e}")

    # 注意：此处**故意没有** Tushare 降级 —— 详见函数 docstring。
    # Tushare 的 north_money 是成交额而非净买入，用它降级等于伪造净流入数据。
    print("[DATA_SOURCE/ALT] get_north_net_flow: 净流入不可得"
          "（2024-08-19 起交易所改按季度披露），返回 None")
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
    """Get industry board summary from THS with Tushare fallback.
    
    降级链:
    1. AKShare stock_board_industry_summary_ths()
    2. Cached last-known-good data (24hr grace period)
    
    Returns:
        DataFrame with industry board data (涨跌幅/成交量/换手率 etc.).
        None on failure.
    """
    import os
    import json
    from pathlib import Path
    from datetime import datetime, timedelta
    
    # Try primary source first
    try:
        import akshare as ak
        result = ak.stock_board_industry_summary_ths()
        if result is not None and len(result) > 10:
            # Cache successful result to disk for grace period
            try:
                cache_dir = Path(__file__).parent.parent.parent / ".cache"
                cache_dir.mkdir(exist_ok=True)
                cache_file = cache_dir / "industry_board_cache.json"
                result.to_json(cache_file, orient='records', force_ascii=False)
                print(f"[DATA_SOURCE/ALT] get_industry_board_summary: AKShare success, cached")
            except Exception:
                pass  # Cache write failure is not critical
            return result
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_industry_board_summary (AKShare failed): {e}")
    
    # Fallback: Try to restore from cache (24hr grace period)
    try:
        cache_dir = Path(__file__).parent.parent.parent / ".cache"
        cache_file = cache_dir / "industry_board_cache.json"
        
        if cache_file.exists():
            # Check cache age
            mtime = cache_file.stat().st_mtime
            cache_age = datetime.now().timestamp() - mtime
            if cache_age < 86400:  # 24 hours
                import pandas as pd
                cached_data = pd.read_json(cache_file)
                print(f"[DATA_SOURCE/ALT] get_industry_board_summary: Using cached data ({cache_age/3600:.1f}h old)")
                return cached_data
            else:
                print(f"[DATA_SOURCE/ALT] get_industry_board_summary: Cache expired ({cache_age/3600:.1f}h old)")
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_industry_board_summary (cache restore failed): {e}")
    
    # Both AKShare and cache failed
    print("[DATA_SOURCE/ALT] get_industry_board_summary: All sources failed")
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
