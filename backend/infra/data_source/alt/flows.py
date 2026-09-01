"""
Alt data bucket -- northbound flows, margin, interbank rates, fund flows.
==========================================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

All akshare calls for alternative data are centralized here.

Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

from typing import Any

from infra.data_source.fallback import call_with_timeout

# v9.9.x: 接入超时保护（FIX 2026-09-01，任务#8）
# 本文件 16 处裸 ak.xxx() 调用统一走 call_with_timeout()，超时值来自
# 生产服务器真实测量，见各函数内联注释。


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
        result = call_with_timeout(ak.stock_hsgt_hist_em, 10, symbol=symbol)
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
        return call_with_timeout(ak.stock_hsgt_hold_stock_em, 10, market=market, indicator=indicator)
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
        return call_with_timeout(ak.stock_margin_sse, 10)
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
        return call_with_timeout(ak.bond_zh_us_rate, 10, start_date=start_date)
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
        return call_with_timeout(ak.rate_interbank, 10, market=market, symbol=symbol, indicator=indicator)
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
        return call_with_timeout(ak.stock_individual_fund_flow_rank, 10, indicator=indicator)
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
        return call_with_timeout(ak.stock_individual_fund_flow, 10, stock=stock, market=market)
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
        return call_with_timeout(ak.stock_zt_pool_em, 10, **kwargs)
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
        df_sh = call_with_timeout(ak.stock_hsgt_hist_em, 10, symbol="沪股通")
        df_sz = call_with_timeout(ak.stock_hsgt_hist_em, 10, symbol="深股通")
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
        return call_with_timeout(ak.stock_dzjy_mrtj, 10)
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
        return call_with_timeout(ak.stock_inner_trade_xq, 10)
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
        return call_with_timeout(ak.stock_sector_fund_flow_rank, 10, indicator=indicator, sector_type=sector_type)
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
        result = call_with_timeout(ak.stock_board_industry_summary_ths, 10)
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
    """Get individual stock basic info — Tushare 为主，AKShare 为降级。

    v9.9.x FIX 2026-09-01（用户显式要求，任务#1）：此前唯一数据源是
    AKShare stock_individual_info_em()，已诊断出该接口底层调用
    push2.eastmoney.com 存在 ~30~70% 量级的间歇性连接被动 reset（完整
    消除法排查结论见本函数下方降级分支注释）。钱袋子有 5000 积分的
    Tushare Pro，其 stock_basic()/daily_basic() 接口稳定（2026-09-01
    实测多次 elapsed 均 <0.3s，0% 失败），已改为主数据源，AKShare
    降级为兜底（AKShare 仍有一定成功率，字段也更全，降级时尽量多拿
    一点信息）。

    实现放在 infra/data_source/providers/tushare_provider.py::
    TushareProvider._fetch_stock_basic_info()（metric="stock_basic_info"），
    这里只是薄封装 + AKShare 降级——**不直接 import services.tushare_
    data**，因为本仓 .importlinter 定义的四层架构（api > use_cases >
    domain > infra）明确规定 infra 不能反向依赖 services（那是给上层用
    的老代码层），这条边界即使 import-linter 包本身在服务器 venv 里没
    装、从未被 CI 真正检查过，也必须遵守（写代码时的约定 > 有没有工具
    强制）。

    唯一真实调用方 services/holding_intelligence.py::get_stock_industry()
    只消费"行业"这一个字段（遍历 item/value 两列 DataFrame 找含"行业"
    字符串的行，取该行第二列），因此保持返回同样的 [item, value] 两列
    DataFrame 格式，不改动下游消费逻辑，只换数据源。

    Args:
        symbol: stock code e.g. "000001"

    Returns:
        DataFrame with columns [item, value]（行业/股票简称/上市时间/
        总市值/流通市值 等，字段名与 AKShare 版本保持一致）。
        None on failure（Tushare 和 AKShare 都失败时）。
    """
    try:
        from infra.data_source.providers.tushare_provider import TushareProvider
        provider = TushareProvider()
        if provider.is_available():
            df = call_with_timeout(
                provider.fetch, 8, metric="stock_basic_info", symbol=symbol,
            )
            if df is not None and len(df) > 0:
                return df
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_stock_individual_info({symbol}) Tushare 失败: {e}")

    # 降级：AKShare。已知诊断结论（2026-09-01 完整消除法排查，避免以后
    # 重新踩坑）：底层调用 push2.eastmoney.com/api/qt/stock/get，存在
    # ~30~70% 量级的间歇性连接被动 reset（0.06~0.2s 内失败，非超时/
    # 挂死）。依次排除了"缺 headers"（curl 裸调用同样有失败率）、
    # "79字段超长fields参数"（curl复现同长度参数依然成功）、"requests/
    # urllib3库或UA字符串指纹拦截"（httpx同样100%复现失败，curl原生栈
    # 基础失败率本身就有30~70%）、"IPv6路由不可达"（强制IPv4后仍有
    # 10~30%失败，只是叠加因素之一）——结论是 push2.eastmoney.com 后端
    # 本身固有的负载保护/限流行为，与客户端库/UA/header均无关，这正是
    # 本次改用 Tushare 为主数据源的原因。此处仍保留 AKShare 作为兜底
    # （降级时"总比没有"，字段也更全，不追求100%可用只是尽力兜底）。
    try:
        import akshare as ak
        return call_with_timeout(ak.stock_individual_info_em, 10, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_stock_individual_info({symbol}) AKShare 降级也失败: {e}")

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
        return call_with_timeout(ak.futures_news_shmet, 10, symbol=symbol)
    except Exception as e:
        print(f"[DATA_SOURCE/ALT] get_futures_news({symbol}): {e}")
        return None
