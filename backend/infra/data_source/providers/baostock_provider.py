"""
BaostockProvider -- DataSourceProtocol adapter for baostock.
=============================================================
Tertiary source (most stable, free, no API key required):
  - market:  A-share daily bars, indices (most reliable fallback)

Used as last-resort in the three-level degradation chain.

Status: IMPLEMENTED — provides stock_price and index_daily metrics.
Satisfies: domain.protocols.DataSourceProtocol (structural subtyping)
Invariant #6: All external data through infra/data_source.
"""
from __future__ import annotations

import logging
import pandas as pd
from typing import Any, Dict, List, Union

from infra.cache import MemoryCache

logger = logging.getLogger(__name__)

_SUPPORTED_METRICS = frozenset({
    "stock_price",
    "index_daily",
    "stock_industry",
})

# Cache for K-line data (1 hour) - baostock queries are slow
_kline_cache = MemoryCache(default_ttl=3600)


class BaostockProvider:
    """Baostock data source adapter.

    Structural implementation of DataSourceProtocol.
    Lazy-imports baostock only on first fetch() call.

    baostock requires login()/logout() session management.
    This adapter handles it internally.
    
    API Notes:
    - query_history_k_data_plus(code, fields, start_date, end_date, frequency=5, adjustflag=3)
      frequency=5 = daily, adjustflag=3 = forward-adjusted (前复权)
      Returns ResultSet with .data list of bar objects
    - Code format: "sz000001" (shenzhen), "sh600519" (shanghai), "bj920000" (beijing)
    """

    def __init__(self) -> None:
        self._bs: Any = None  # lazy-loaded baostock module
        self._logged_in: bool = False
        self._available: bool | None = None

    def fetch(self, metric: str, **params: Any) -> Union[Dict[str, Any], List[Any], None]:
        """Fetch a data metric from baostock.

        Args:
            metric: One of "stock_price", "index_daily", "stock_industry"
            **params:
                - symbol: 6-digit code e.g. "000001"
                - start_date: "YYYYMMDD" (optional)
                - end_date: "YYYYMMDD" (optional)
                - adjust: "qfq" (前复权) | "hfq" (后复权) | "" (不复权) [only for stock_price]

        Returns:
            DataFrame with OHLCV data, or None on any failure (never raises).
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
            elif metric == "stock_industry":
                return self._fetch_stock_industry(**params)
        except Exception as e:
            logger.debug(f"BaostockProvider.fetch({metric}) failed: {e}")

        return None

    def _fetch_stock_price(self, **params: Any) -> Any:
        """Fetch stock daily OHLCV history.

        Returns DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额
        (compatible with akshare format)
        """
        symbol = params.get("symbol", "")
        if not symbol:
            return None

        # Check cache
        cache_key = f"bs_stock_{symbol}_{params.get('start_date', '')}_{params.get('end_date', '')}"
        cached = _kline_cache.get(cache_key)
        if cached is not None:
            return cached

        # Convert code format: "000001" / "sh000001" → "sz.000001"
        bs_code = self._normalize_code(symbol)

        # Map adjust flag: "qfq" (前复权) → adjustflag="3"
        adjust = params.get("adjust", "qfq")
        if adjust == "hfq":
            adjustflag = "2"
        elif adjust == "":
            adjustflag = "1"
        else:
            adjustflag = "3"

        bs = self._get_bs()
        start_date = self._format_date(params.get("start_date", ""))
        end_date = self._format_date(params.get("end_date", ""))

        rs = bs.query_history_k_data_plus(
            code=bs_code,
            fields="date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag=adjustflag,
        )

        if rs.error_code != "0":
            logger.debug(f"Baostock query failed: {rs.error_msg}")
            return None

        if not rs.data or len(rs.data) == 0:
            return None

        # Convert to DataFrame (rs.data 是 list of lists)
        try:
            df = pd.DataFrame(rs.data, columns=rs.fields)
            # 转为数值型 + 中文列名（兼容 AKShare 格式）
            df = df.rename(columns={
                "date": "日期", "open": "开盘", "close": "收盘",
                "high": "最高", "low": "最低", "volume": "成交量", "amount": "成交额",
            })
            for col in ["开盘", "收盘", "最高", "最低", "成交额"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if "成交量" in df.columns:
                df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce").fillna(0).astype(int)

            # 过滤空行
            df = df[df["收盘"].notna() & (df["收盘"] > 0)]

            if len(df) == 0:
                return None

            _kline_cache.set(cache_key, df)
            logger.debug(f"BaostockProvider fetched {len(df)} rows for {symbol}")
            return df

        except Exception as e:
            logger.debug(f"Failed to convert Baostock result: {e}")
            return None

    def _fetch_index_daily(self, **params: Any) -> Any:
        """Fetch index daily OHLCV history.

        For indices like Shanghai Composite (sh.000001), CSI300 (sh.000300).
        Returns DataFrame with same columns as stock_price.
        """
        symbol = params.get("symbol", "")
        if not symbol:
            return None

        # 处理 sh/sz 前缀：sh000300 → sh.000300
        if symbol.startswith("sh") and "." not in symbol:
            bs_code = f"sh.{symbol[2:]}"
        elif symbol.startswith("sz") and "." not in symbol:
            bs_code = f"sz.{symbol[2:]}"
        elif len(symbol) == 6 and symbol.isdigit():
            # 指数：000xxx/999xxx 默认上海，399xxx 默认深圳
            if symbol.startswith("399"):
                bs_code = f"sz.{symbol}"
            else:
                bs_code = f"sh.{symbol}"
        else:
            bs_code = symbol

        # 直接调 stock_price 逻辑但用指数代码
        modified_params = {**params, "symbol": bs_code}
        return self._fetch_stock_price(**modified_params)

    def _fetch_stock_industry(self, **params: Any) -> Any:
        """Fetch stock industry classification.
        
        Returns DataFrame with stock code and industry information.
        Note: Baostock may not have a direct industry API, so this returns None for now.
        """
        # Baostock doesn't have a dedicated industry classification API
        # This would require mapping from another source or query_stock_industry() if available
        logger.debug("BaostockProvider: stock_industry not yet implemented")
        return None

    def is_available(self) -> bool:
        """Check if baostock can be imported and logged in."""
        if self._available is not None:
            return self._available
        try:
            self._login()
            self._available = True
        except Exception:
            self._available = False
        return self._available

    @property
    def provider_name(self) -> str:
        return "baostock"

    def _get_bs(self) -> Any:
        """Lazy-import baostock and ensure login."""
        if self._bs is None:
            self._login()
        return self._bs

    def _login(self) -> None:
        """Login to baostock (required before any query)."""
        if not self._logged_in:
            import baostock as bs  # noqa: delayed import
            bs.login()
            self._bs = bs
            self._logged_in = True

    def _normalize_code(self, code: str) -> str:
        """Convert 6-digit A-share code to Baostock format.

        Examples:
            "000001" → "sz.000001" (shenzhen)
            "600519" → "sh.600519" (shanghai)
            "sh000300" → "sh.000300"
            "sz.000001" → "sz.000001" (already correct)
        """
        # 已经是 baostock 格式
        if "." in code and len(code) == 9:
            return code
        # sh/sz 前缀无点号
        if code.startswith("sh") and len(code) == 8:
            return f"sh.{code[2:]}"
        if code.startswith("sz") and len(code) == 8:
            return f"sz.{code[2:]}"
        # 纯数字
        if len(code) == 6 and code.isdigit():
            if code.startswith(("0", "3")):
                return f"sz.{code}"
            elif code.startswith("6"):
                return f"sh.{code}"
            elif code.startswith(("8", "4")):
                return f"bj.{code}"
        # 无法识别，原样返回
        return code

    @staticmethod
    def _format_date(date_str: str) -> str:
        """将 YYYYMMDD 转为 baostock 需要的 YYYY-MM-DD 格式"""
        if not date_str:
            return ""
        if "-" in date_str:
            return date_str  # 已经是正确格式
        if len(date_str) == 8:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        return date_str

    def __del__(self) -> None:
        """Best-effort logout on garbage collection."""
        if self._logged_in and self._bs is not None:
            try:
                self._bs.logout()
            except Exception:
                pass
