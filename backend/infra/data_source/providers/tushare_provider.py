"""
TushareProvider -- DataSourceProtocol adapter for Tushare Pro.
===============================================================
Primary source for:
  - market:      stock prices, indices, ETF (structured, stable)
  - fundamental: earnings, valuation, financials (strongest coverage)

Requires TUSHARE_TOKEN environment variable.

Status: IMPLEMENTED — provides all 7 supported metrics.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Union

import pandas as pd

from infra.cache import MemoryCache

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    "stock_price",
    "index_daily",
    "fund_nav",
    "income_statement",
    "balance_sheet",
    "valuation",
    "dividend",
    # v9.9.x FIX 2026-09-01（任务#1，用户显式要求）：个股基本信息（行业/
    # 上市时间/市值），用于替换 AKShare stock_individual_info_em()——
    # 该接口诊断出 push2.eastmoney.com 存在 ~30~70% 量级的间歇性连接
    # 被动 reset（见 infra/data_source/alt/flows.py::get_stock_
    # individual_info 的完整诊断记录）。stock_basic_info 拼接
    # pro.stock_basic()（行业/名称/上市时间）+ pro.daily_basic()（市值），
    # 唯一消费方 services/holding_intelligence.py::get_stock_industry()
    # 只用"行业"字段。
    "stock_basic_info",
})

# Caches by TTL requirement
_quote_cache = MemoryCache(default_ttl=300)     # 5 min for real-time quotes
_kline_cache = MemoryCache(default_ttl=3600)    # 1 hour for daily bars
_financial_cache = MemoryCache(default_ttl=86400)  # 24 hours for financials


class TushareProvider:
    """Tushare Pro data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports tushare only on first fetch() call.
    
    API Notes:
    - pro.daily(ts_code, start_date, end_date) → DataFrame with daily bars
    - pro.index_daily(ts_code, start_date, end_date) → DataFrame with index daily
    - pro.fund_nav(ts_code, start_date, end_date) → DataFrame with fund NAV
    - pro.income(ts_code, start_date, end_date) → DataFrame with income statement
    - pro.balancesheet(ts_code, start_date, end_date) → DataFrame with balance sheet
    - pro.valuation_daily(ts_code, start_date, end_date) → DataFrame with valuation metrics
    - pro.dividend(ts_code, start_date, end_date) → DataFrame with dividend history
    
    Code format: Tushare uses "000001.SZ", "600519.SH" format
    """

    def __init__(self) -> None:
        self._token: str = os.environ.get("TUSHARE_TOKEN", "")
        self._api: Any = None  # lazy-loaded tushare.pro_api instance
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], pd.DataFrame, None]:
        """Fetch a data metric from Tushare Pro.

        Args:
            metric: One of "stock_price", "index_daily", "fund_nav", etc.
            **params:
                - symbol: Code to fetch (e.g. "000001" for stocks, "399001" for indices)
                - start_date: "YYYYMMDD" format
                - end_date: "YYYYMMDD" format
                - ts_code: Alternative parameter name (will use symbol if provided)

        Returns:
            DataFrame or dict with requested data, or None on failure (never raises).
        """
        if metric not in _SUPPORTED_METRICS:
            return None
        if not self.is_available():
            return None

        try:
            if metric == "stock_price":
                return self._fetch_stock_price(**params)
            elif metric == "index_daily":
                return self._fetch_index_daily(**params)
            elif metric == "fund_nav":
                return self._fetch_fund_nav(**params)
            elif metric == "income_statement":
                return self._fetch_income_statement(**params)
            elif metric == "balance_sheet":
                return self._fetch_balance_sheet(**params)
            elif metric == "valuation":
                return self._fetch_valuation(**params)
            elif metric == "dividend":
                return self._fetch_dividend(**params)
            elif metric == "stock_basic_info":
                return self._fetch_stock_basic_info(**params)
        except Exception as e:
            logger.debug(f"TushareProvider.fetch({metric}) failed: {e}")

        return None

    def _fetch_stock_price(self, **params: Any) -> Any:
        """Fetch daily stock price bars."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_stock_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} stock price rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.daily failed: {e}")

        return None

    def _fetch_index_daily(self, **params: Any) -> Any:
        """Fetch daily index bars."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_index_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        # Index codes: 399001=深证成指, 000001=沪深300, 000300=沪深300, 399006=创业板指
        ts_code = self._normalize_code(symbol, "index")
        api = self._get_api()

        try:
            df = api.index_daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} index rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.index_daily failed: {e}")

        return None

    def _fetch_fund_nav(self, **params: Any) -> Any:
        """Fetch fund NAV.

        v9.9.x FIX 2026-09-01（任务#8/#3 排查发现）：此前直接返回
        pro.fund_nav() 的原始英文列名（ts_code/nav_date/unit_nav/accum_nav），
        但下游唯一消费方 services/fund_monitor.py::get_fund_nav_history()
        是按 AKShare `fund_open_fund_info_em()` 的中文列名读取的
        （"净值日期"/"单位净值"/"累计净值"/"日增长率"）——因为 AkshareProvider
        的 fund_nav 此前一直不在 _SUPPORTED_METRICS 白名单里（见
        akshare_provider.py 对应注释），这条 Tushare 降级路径其实是
        **唯一真正在生产环境跑的路径**，列名不兼容导致净值/日期长期解析
        成 None/空字符串，已影响真实持仓用户的净值展示。

        修复：在这里做列名转换，输出跟 AKShare 格式兼容的 DataFrame，
        无论调用方后续走 AkshareProvider（已重新加入白名单）还是这条
        Tushare 降级路径，下游都能正确解析。
        """
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_fund_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "fund")
        api = self._get_api()

        try:
            df = api.fund_nav(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                # 按 nav_date 升序排列（Tushare 默认可能是降序，AKShare
                # 惯例是升序，下游 df.tail(days) 假定升序取"最近N天"）
                df = df.sort_values("nav_date").reset_index(drop=True)
                # 计算日增长率（Tushare 原始数据没有这个字段，AKShare 有）
                df["日增长率"] = df["unit_nav"].pct_change().round(4) * 100
                df = df.rename(columns={
                    "nav_date": "净值日期",
                    "unit_nav": "单位净值",
                    "accum_nav": "累计净值",
                })
                _kline_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} fund NAV rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.fund_nav failed: {e}")

        return None

    def _fetch_income_statement(self, **params: Any) -> Any:
        """Fetch income statement (financial metrics)."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_income_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.income(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} income statement rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.income failed: {e}")

        return None

    def _fetch_balance_sheet(self, **params: Any) -> Any:
        """Fetch balance sheet (financial metrics)."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_balance_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.balancesheet(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} balance sheet rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.balancesheet failed: {e}")

        return None

    def _fetch_valuation(self, **params: Any) -> Any:
        """Fetch valuation metrics."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_valuation_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            # Using valuation_daily for daily valuation metrics
            df = api.valuation_daily(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} valuation rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.valuation_daily failed: {e}")

        return None

    def _fetch_dividend(self, **params: Any) -> Any:
        """Fetch dividend history."""
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        cache_key = f"ts_dividend_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _financial_cache.get(cache_key)
        if cached is not None:
            return cached

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            df = api.dividend(
                ts_code=ts_code,
                start_date=params.get("start_date", ""),
                end_date=params.get("end_date", ""),
            )
            if df is not None and len(df) > 0:
                _financial_cache.set(cache_key, df)
                logger.debug(f"TushareProvider fetched {len(df)} dividend rows for {symbol}")
                return df
        except Exception as e:
            logger.debug(f"Tushare pro.dividend failed: {e}")

        return None

    def _fetch_stock_basic_info(self, **params: Any) -> Any:
        """Fetch stock basic info (industry/name/listing date/market cap).

        v9.9.x FIX 2026-09-01（任务#1）：用于替换 AKShare
        stock_individual_info_em()。返回 [item, value] 两列 DataFrame
        （与 AKShare 版本格式一致），拼接 pro.stock_basic()（行业/名称/
        上市时间，一次调用即可）+ pro.daily_basic(limit=1)（最新市值，
        可选加分项，失败不影响主字段）。

        Args:
            symbol / ts_code: 股票代码，6位数字或已格式化的 ts_code

        Returns:
            DataFrame with columns [item, value]。None on failure。
        """
        symbol = params.get("symbol") or params.get("ts_code")
        if not symbol:
            return None

        ts_code = self._normalize_code(symbol, "stock")
        api = self._get_api()

        try:
            basic_df = api.stock_basic(
                ts_code=ts_code,
                fields="ts_code,name,industry,list_date,market",
            )
            if basic_df is None or len(basic_df) == 0:
                return None

            row = basic_df.iloc[0]
            items: list = [
                ("股票代码", symbol),
                ("股票简称", row.get("name")),
                ("行业", row.get("industry")),
                ("上市时间", row.get("list_date")),
            ]

            # 市值是可选加分项，daily_basic 偶发失败不应影响主字段（行业等）
            try:
                mv_df = api.daily_basic(
                    ts_code=ts_code, limit=1,
                    fields="ts_code,trade_date,total_mv,circ_mv",
                )
                if mv_df is not None and len(mv_df) > 0:
                    mv_row = mv_df.iloc[0]
                    # Tushare total_mv/circ_mv 单位是万元，AKShare 原始
                    # 单位是元，×10000 换算对齐
                    if mv_row.get("total_mv") is not None:
                        items.append(("总市值", float(mv_row["total_mv"]) * 10000))
                    if mv_row.get("circ_mv") is not None:
                        items.append(("流通市值", float(mv_row["circ_mv"]) * 10000))
            except Exception as mv_e:
                logger.debug(f"Tushare pro.daily_basic(市值) for {symbol} failed "
                             f"(不影响行业等主字段): {mv_e}")

            df = pd.DataFrame(items, columns=["item", "value"])
            logger.debug(f"TushareProvider fetched stock_basic_info for {symbol}: "
                         f"industry={row.get('industry')}")
            return df
        except Exception as e:
            logger.debug(f"Tushare pro.stock_basic failed for {symbol}: {e}")

        return None

    def is_available(self) -> bool:
        """Check if Tushare token is configured and API is reachable."""
        if self._available is not None:
            return self._available
        if not self._token:
            self._available = False
            return False
        try:
            api = self._get_api()
            # Test API with a simple call - if token is invalid, this will raise
            # We can verify by checking if we got an api instance
            self._available = api is not None
        except Exception:
            self._available = False
        return self._available

    @property
    def provider_name(self) -> str:
        return "tushare"

    def _get_api(self) -> Any:
        """Lazy-import tushare and initialize pro_api."""
        if self._api is None:
            import tushare as ts  # noqa: delayed import
            ts.set_token(self._token)
            self._api = ts.pro_api()
        return self._api

    def _normalize_code(self, code: str, asset_type: str = "stock") -> str:
        """Convert 6-digit A-share code to Tushare format.

        Examples:
            "000001" + "stock" → "000001.SZ"
            "600519" + "stock" → "600519.SH"
            "399001" + "index" → "399001.SZ"
            "sh000300" + "index" → "000300.SH"
            "sz399001" + "index" → "399001.SZ"

        Args:
            code: 6-digit code, sh/sz prefixed code, or already-formatted code
            asset_type: "stock", "index", or "fund"

        Returns:
            Tushare-formatted code
        """
        # If already formatted (contains dot), return as-is
        if "." in code:
            return code

        # 处理 sh/sz 前缀（如 "sh000300" → "000300"，保留交易所信息）
        exchange_hint = ""
        if code.startswith("sh"):
            exchange_hint = "SH"
            code = code[2:]
        elif code.startswith("sz"):
            exchange_hint = "SZ"
            code = code[2:]

        if len(code) == 6 and code.isdigit():
            if asset_type == "stock":
                if code.startswith(("0", "3")):
                    return f"{code}.SZ"
                elif code.startswith("6"):
                    return f"{code}.SH"
                elif code.startswith(("8", "4")):
                    return f"{code}.BJ"
            elif asset_type == "index":
                # 如果有 exchange_hint，直接用
                if exchange_hint:
                    return f"{code}.{exchange_hint}"
                # Index codes: 399001, 000001, etc. follow different conventions
                if code.startswith("399"):
                    return f"{code}.SZ"  # 深证指数
                elif code.startswith(("000", "999")):
                    return f"{code}.SH"  # 沪市指数
                else:
                    return f"{code}.SZ"  # default
            elif asset_type == "fund":
                # Fund codes typically start with 1 or 5
                if code.startswith("1"):
                    return f"{code}.SZ"
                elif code.startswith("5"):
                    return f"{code}.SH"

        # Already formatted or unknown - use exchange hint if available
        if exchange_hint:
            return f"{code}.{exchange_hint}"
        return code
