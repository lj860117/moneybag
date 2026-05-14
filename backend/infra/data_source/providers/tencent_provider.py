"""
腾讯财经数据适配器
============================================================
接口: http://qt.gtimg.cn/q={sh/sz}{code}
无 Key，HTTP GET，GBK 编码，返回 ~ 分隔字符串。

关键字段位置（0-indexed）:
  parts[1]  = 股票名称
  parts[3]  = 现价
  parts[38] = 换手率 (%)
  parts[39] = PE(TTM)
  parts[44] = 总市值（亿元）
  parts[46] = PB（市净率）

用途: tushare PE/PB 不可用时的免费兜底数据源
缓存: MemoryCache 300s（5 分钟，行情数据）

Invariant #3: 缓存走 infra/cache
Invariant #5: 外部数据源走 infra/data_source
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from infra.cache import MemoryCache

_logger = logging.getLogger(__name__)
_cache: MemoryCache = MemoryCache(default_ttl=300)  # 5 分钟


def _to_symbol(code: str) -> str:
    """6 位 A 股代码 → 腾讯财经 sh/sz 前缀格式。"""
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _safe_float(s: str) -> Optional[float]:
    """安全转 float，0 视为 None（腾讯财经空值填 0）。"""
    try:
        v = float(s)
        return v if v != 0.0 else None
    except Exception:
        return None


def get_stock_quote_tencent(code: str) -> Optional[dict]:
    """腾讯财经实时报价。

    Args:
        code: 6 位 A 股代码，如 "600519"

    Returns:
        包含 pe_ttm / pb / market_cap / turnover_rate 等字段的 dict，
        失败返回 None（不抛异常）。
    """
    key = f"tencent_{code}"
    cached = _cache.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    try:
        symbol = _to_symbol(code)
        url = f"http://qt.gtimg.cn/q={symbol}"
        resp = httpx.get(
            url,
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0 (compatible; moneybag/1.0)"},
        )
        # 腾讯财经返回 GBK 编码
        text = resp.content.decode("gbk", errors="replace")

        # 格式：v_sh600519="...~字段~字段~..."
        if "~" not in text:
            return None
        # 取引号内的内容
        if '"' in text:
            inner = text.split('"')[1]
        else:
            inner = text
        parts = inner.split("~")

        if len(parts) < 50:
            _logger.debug("[TENCENT] %s 返回字段不足 50: %d", code, len(parts))
            return None

        result: dict = {
            "code": code,
            "name": parts[1] if len(parts) > 1 else "",
            "price": _safe_float(parts[3]),
            "pe_ttm": _safe_float(parts[39]),
            "pb": _safe_float(parts[46]),
            "market_cap": _safe_float(parts[44]),    # 亿元
            "turnover_rate": _safe_float(parts[38]), # %
            "source": "tencent",
        }
        _cache.set(key, result)
        return result

    except Exception as e:
        _logger.warning("[TENCENT] get_stock_quote(%s) 失败: %s", code, e)
        return None


class TencentProvider:
    """DataSourceProtocol 适配器，包装腾讯财经函数接口。"""

    @property
    def provider_name(self) -> str:
        return "tencent"

    def is_available(self) -> bool:
        """腾讯财经无鉴权，默认可用。"""
        return True

    def fetch(self, metric: str, **params: Any) -> dict[str, Any] | list[Any] | None:
        """通过 metric 路由到对应函数。

        目前仅支持 stock_quote（实时报价/PE/PB）。
        """
        if metric == "stock_quote":
            code = params.get("symbol", params.get("code", ""))
            if not code:
                return None
            return get_stock_quote_tencent(code)
        return None
