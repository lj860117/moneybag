"""
MootdxProvider — 通达信 TCP 数据适配器
============================================================
安装: pip install mootdx>=0.7.0
协议: TCP 二进制（通达信协议），不走东财 HTTP，不封 IP

提供两个函数:
  get_daily_hist_mootdx(code, days)  → pandas DataFrame（列名兼容 baostock/AKShare）
  get_finance_mootdx(code)           → dict（EPS/ROE/净利润/主营收入/毛利率）

缓存:
  K 线数据: MemoryCache 3600s（1 小时）
  财务数据: MemoryCache 86400s（24 小时，季报不频繁更新）

用途:
  - K 线: 作为 baostock 日线的第三降级（降级链: 东财 > baostock > mootdx）
  - 财务: 作为 tushare fina_indicator 耗尽积分时的兜底候选池来源

Invariant #3: 缓存走 infra/cache
Invariant #5: 外部数据源走 infra/data_source
"""
from __future__ import annotations

import logging
from typing import Optional, Any

from infra.cache import MemoryCache

_logger = logging.getLogger(__name__)
_kline_cache:   MemoryCache = MemoryCache(default_ttl=3600)   # 1 小时
_finance_cache: MemoryCache = MemoryCache(default_ttl=86400)  # 24 小时


def get_daily_hist_mootdx(code: str, days: int = 90) -> Optional[Any]:
    """获取日线 K 线数据（通达信 TCP，不封 IP）。

    Args:
        code: 6 位 A 股代码，如 "000001"
        days: 拉取天数（对应 offset 参数）

    Returns:
        pandas DataFrame，列名已统一为中文（收盘/开盘/最高/最低/成交量/成交额），
        与 baostock/AKShare 格式兼容。失败返回 None。
    """
    key = f"mdx_kline_{code}_{days}"
    cached = _kline_cache.get(key)
    if cached is not None:
        return cached

    try:
        from mootdx.quotes import Quotes  # type: ignore[import-not-found]  # noqa: delayed import

        client = Quotes.factory(market="std", bestip=True, timeout=15)
        # frequency=9 对应日线
        df = client.bars(symbol=code, frequency=9, offset=days)
        if df is None or len(df) == 0:
            return None

        # 统一列名 — mootdx 返回英文列名，改为与 baostock/AKShare 一致的中文
        rename_map = {
            "close":  "收盘",
            "open":   "开盘",
            "high":   "最高",
            "low":    "最低",
            "vol":    "成交量",
            "amount": "成交额",
            "datetime": "日期",  # mootdx 实际返回 datetime 列，非 date
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        _kline_cache.set(key, df)
        return df

    except ImportError:
        _logger.warning("[MOOTDX] mootdx 未安装，跳过: pip install mootdx>=0.7.0")
        return None
    except Exception as e:
        _logger.warning("[MOOTDX] get_daily_hist(%s) 失败: %s", code, e)
        return None


def get_finance_mootdx(code: str) -> Optional[dict]:
    """获取季报财务快照（替代 tushare fina_indicator）。

    返回字段: eps / roe / net_profit / revenue / gross_margin
    数据来源: 通达信财务数据（TCP，不消耗 tushare 积分）

    Args:
        code: 6 位 A 股代码，如 "000001"

    Returns:
        财务快照 dict，失败返回 None。
    """
    key = f"mdx_finance_{code}"
    cached = _finance_cache.get(key)
    if cached is not None:
        return cached  # type: ignore[no-any-return]

    try:
        from mootdx.quotes import Quotes  # noqa: delayed import

        client = Quotes.factory(market="std", bestip=True, timeout=15)
        data = client.finance(symbol=code)
        if not data:
            return None

        # data 可能是列表（多期）或单条 dict，取最新一期
        latest: dict = data[0] if isinstance(data, list) else data

        result: dict = {
            "code": code,
            "eps":          latest.get("eps"),               # 每股收益
            "roe":          latest.get("roe"),               # 净资产收益率 (%)
            "net_profit":   latest.get("net_profit"),        # 净利润（万元）
            "revenue":      latest.get("total_revenue"),     # 主营收入（万元）
            "gross_margin": latest.get("gross_profit_rate"), # 毛利率 (%)
            "source":       "mootdx",
        }
        _finance_cache.set(key, result)
        return result

    except ImportError:
        _logger.warning("[MOOTDX] mootdx 未安装，跳过: pip install mootdx>=0.7.0")
        return None
    except Exception as e:
        _logger.warning("[MOOTDX] get_finance(%s) 失败: %s", code, e)
        return None
