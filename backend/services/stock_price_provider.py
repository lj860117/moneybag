"""
统一股票日线价格数据 Provider（2026-04-19 新增）
===================================================
职责：
  - Tushare pro_bar/daily 主源（5000 积分稳定）
  - AKShare stock_zh_a_hist 降级（反爬时兜底）
  - 统一 pandas DataFrame 输出（兼容现有 8 个调用点）

统一字段（中文兼容旧代码）：
  日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 涨跌幅

使用：
  from services.stock_price_provider import get_daily_df
  df = get_daily_df("600036", days=120)
"""
import time
import pandas as pd
from services.tushare_data import is_configured, get_daily_price


_price_cache: dict = {}
_CACHE_TTL = 300  # 5 分钟


def _cache_get(key: str):
    v = _price_cache.get(key)
    if v and time.time() - v["ts"] < _CACHE_TTL:
        return v["df"]
    return None


def _cache_put(key: str, df):
    _price_cache[key] = {"df": df, "ts": time.time()}


def _from_tushare(code: str, days: int) -> pd.DataFrame:
    """Tushare 路径：返回统一字段 DataFrame"""
    rows = get_daily_price(code, days=days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # 字段映射：tushare → 中文（兼容旧代码）
    rename = {
        "trade_date": "日期",
        "open": "开盘",
        "close": "收盘",
        "high": "最高",
        "low": "最低",
        "vol": "成交量",
        "amount": "成交额",
        "pct_chg": "涨跌幅",
    }
    df = df.rename(columns=rename)
    # 日期格式：20260419 → 2026-04-19（AKShare 兼容）
    if "日期" in df.columns:
        df["日期"] = pd.to_datetime(df["日期"], format="%Y%m%d", errors="coerce")
    # 类型归一
    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def _from_akshare(code: str, days: int, adjust: str = "qfq") -> pd.DataFrame:
    """AKShare 降级路径"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust=adjust)
        if df is None or len(df) == 0:
            return pd.DataFrame()
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[STOCK_PROVIDER] AKShare failed for {code}: {e}")
        return pd.DataFrame()


def get_daily_df(code: str, days: int = 120, adjust: str = "qfq") -> pd.DataFrame:
    """
    统一入口：Tushare 主源 → AKShare 降级
    code: 纯数字 (600036 / 000001)，不带前缀
    返回 DataFrame，字段与 AKShare stock_zh_a_hist 兼容（中文列名）
    """
    # 清理 code（去掉 sh/sz 前缀）
    clean = str(code).strip()
    for prefix in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break

    cache_key = f"{clean}_{days}_{adjust}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.copy()

    # 主源：Tushare
    if is_configured():
        try:
            df = _from_tushare(clean, days)
            if df is not None and len(df) >= 5:
                print(f"[STOCK_PROVIDER] {clean} Tushare OK: {len(df)} 行")
                _cache_put(cache_key, df)
                return df.copy()
        except Exception as e:
            print(f"[STOCK_PROVIDER] {clean} Tushare failed: {e}, fallback AKShare")

    # 降级：AKShare
    df = _from_akshare(clean, days, adjust=adjust)
    if df is not None and len(df) > 0:
        print(f"[STOCK_PROVIDER] {clean} AKShare OK: {len(df)} 行 (降级)")
        _cache_put(cache_key, df)
        return df.copy()

    print(f"[STOCK_PROVIDER] {clean} 所有源失败")
    return pd.DataFrame()


def get_close_series(code: str, days: int = 120) -> list:
    """便捷：只要收盘价列表（按时间升序）"""
    df = get_daily_df(code, days=days)
    if df.empty or "收盘" not in df.columns:
        return []
    return df["收盘"].dropna().astype(float).tolist()


MODULE_META = {
    "name": "stock_price_provider",
    "scope": "public",
    "input": ["code", "days"],
    "output": "daily_df",
    "cost": "cache_first",
    "tags": ["infrastructure", "price", "tushare", "akshare"],
    "description": "统一股票日线数据入口：Tushare 主 + AKShare 降级",
    "layer": "infrastructure",
    "priority": 0,
}
