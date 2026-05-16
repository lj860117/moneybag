"""
Macro data bucket -- Chinese/global macroeconomic indicators.
==============================================================
Part of the five-bucket data source taxonomy (12-framework-refactor.md §6).

Degradation chain: AKShare (primary) → Tushare (fallback)
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
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    return ts.pro_api()


# ============================================================
# Chinese Macro Indicators
# ============================================================

def get_china_money_supply() -> Any:
    """Get China M0/M1/M2 money supply.

    Degradation chain: AKShare macro_china_money_supply → Tushare cn_m
    
    Returns:
        DataFrame with monthly money supply data.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_money_supply failed: {e}")

    # Fallback: Tushare cn_m
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
    """Get China social financing aggregate (社融总额).

    Degradation chain: AKShare macro_china_shrzgm → Tushare cn_m_shrzgm
    
    Returns:
        DataFrame with social financing data.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_shrzgm()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_social_financing failed: {e}")
    
    # Fallback: Tushare cn_m_shrzgm
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_m_shrzgm(start_m='202001')
            if df is not None and len(df) > 0:
                import pandas as pd
                # 对齐列名格式
                result = pd.DataFrame({
                    '月份': df['month'],
                    '社融规模同比': df['m_shrzgm_yoy'],
                    '社融规模': df['m_shrzgm'],
                })
                print(f"[DATA_SOURCE/MACRO] 社融 降级到 Tushare 成功: {len(result)} rows")
                return result
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_m_shrzgm failed: {e}")

    return None


def get_china_lpr() -> Any:
    """Get China LPR interest rates (贷款市场报价利率).

    Degradation chain: AKShare macro_china_lpr → Tushare cn_lpr
    
    Returns:
        DataFrame with LPR history.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_lpr()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_lpr failed: {e}")
    
    # Fallback: Tushare cn_lpr
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_lpr(start_date='20200101')
            if df is not None and len(df) > 0:
                import pandas as pd
                # 对齐列名
                result = pd.DataFrame({
                    '日期': df['trade_date'],
                    '1Y': df['lpr_1y'],
                    '5Y': df['lpr_5y'],
                })
                print(f"[DATA_SOURCE/MACRO] LPR 降级到 Tushare 成功: {len(result)} rows")
                return result
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_lpr failed: {e}")

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
    """Get China CPI data (消费者价格指数).

    Degradation chain: AKShare macro_china_cpi → Tushare cn_cpi
    
    Returns:
        DataFrame with CPI history.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_cpi()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_cpi failed: {e}")

    # Fallback: Tushare cn_cpi
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
    """Get China PMI data (制造业采购经理指数).

    Degradation chain: AKShare macro_china_pmi → Tushare cn_pmi
    
    Returns:
        DataFrame with PMI history.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_pmi()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_pmi failed: {e}")

    # Fallback: Tushare cn_pmi
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
    """Get China PPI data (生产者价格指数).

    Degradation chain: AKShare macro_china_ppi → Tushare cn_ppi
    
    Returns:
        DataFrame with PPI history.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_ppi()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_ppi failed: {e}")

    # Fallback: Tushare cn_ppi
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
    """Get China GDP data (国内生产总值).

    Degradation chain: AKShare macro_china_gdp → Tushare cn_gdp
    
    Returns:
        DataFrame with quarterly GDP data.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_gdp()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_gdp failed: {e}")
    
    # Fallback: Tushare cn_gdp
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            df = pro.cn_gdp(start_quarter='2020Q1')
            if df is not None and len(df) > 0:
                import pandas as pd
                result = pd.DataFrame({
                    '季度': df['quarter'],
                    '国内生产总值': df['gdp'],
                    '同比增长': df['gdp_yoy'],
                })
                print(f"[DATA_SOURCE/MACRO] GDP 降级到 Tushare 成功: {len(result)} rows")
                return result
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare cn_gdp failed: {e}")

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
    """Get China consumer goods retail sales (社会零售总额).

    Degradation chain: AKShare macro_china_consumer_goods_retail → Tushare
    
    Returns:
        DataFrame with retail sales data.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_consumer_goods_retail()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_retail_sales failed: {e}")
    
    # Fallback: Tushare (note: no direct cn_retail, use proxy or return None with logging)
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            # Tushare doesn't have direct retail sales API, use cn_consumer_goods_retail proxy if available
            # Fallback to None if not available
            print(f"[DATA_SOURCE/MACRO] Retail sales fallback not yet available (Tushare API limitation)")
            return None
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare retail fallback failed: {e}")

    return None


def get_china_fixed_asset_investment() -> Any:
    """Get China fixed asset investment (固定资产投资).

    Degradation chain: AKShare macro_china_gdzctz → Tushare proxy
    
    Returns:
        DataFrame with FAI data.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_china_gdzctz()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_china_fixed_asset_investment failed: {e}")
    
    # Fallback: Tushare (note: limited availability)
    if _tushare_available():
        try:
            print(f"[DATA_SOURCE/MACRO] Fixed asset investment fallback not yet available (Tushare API limitation)")
            return None
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare FAI fallback failed: {e}")

    return None


# ============================================================
# US/Global Macro
# ============================================================

def get_usa_interest_rate() -> Any:
    """Get US Federal Reserve interest rate history (美联储利率).

    Degradation chain: AKShare macro_bank_usa_interest_rate → Tushare proxy
    
    Returns:
        DataFrame with Fed rate history.
        None on failure.
    """
    # Primary: AKShare
    try:
        import akshare as ak
        df = ak.macro_bank_usa_interest_rate()
        if df is not None and len(df) > 0:
            return df
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] AKShare get_usa_interest_rate failed: {e}")
    
    # Fallback: Tushare (global macro data)
    if _tushare_available():
        try:
            pro = _get_tushare_api()
            # Tushare macro_global_fed_rate if available
            print(f"[DATA_SOURCE/MACRO] US interest rate fallback not yet available (Tushare API limitation)")
            return None
        except Exception as e:
            print(f"[DATA_SOURCE/MACRO] Tushare US rate fallback failed: {e}")

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


# ============================================================
# 全球期货快照（A50/美股指数期货/原油/黄金）
# ============================================================

def get_global_futures_snapshot() -> dict:
    """获取全球主要期货品种的实时快照（用于外盘速览）

    数据源: AKShare futures_global_spot_em（东方财富全球期货行情）
    品种: A50期指、小型标普、小型道指、小型纳指、NYMEX原油、COMEX黄金

    Returns:
        dict: {
            "a50": {"price": float, "change_pct": float, "prev_close": float},
            "sp500": {...},
            "dji": {...},
            "nasdaq": {...},
            "oil": {...},
            "gold": {...},
            "available": bool,
            "source": "akshare_futures_global"
        }
        若获取失败返回 {"available": False}
    """
    # 品种代码 → 字段映射（"名称"中的关键词 + 代码前缀）
    TARGETS = {
        "a50": {"code": "CN00Y", "label": "A50期指当月连续"},
        "sp500": {"code": "ES00Y", "label": "小型标普当月连续"},
        "dji": {"code": "YM00Y", "label": "小型道指当月连续"},
        "nasdaq": {"code": "NQ00Y", "label": "小型纳指当月连续"},
        "oil": {"code": "CL00Y", "label": "NYMEX原油"},
        "gold": {"code": "GC00Y", "label": "COMEX黄金(美元/盎司)"},
    }

    result: dict = {"available": False, "source": "akshare_futures_global"}

    try:
        import akshare as ak
        import math
        df = ak.futures_global_spot_em()
        if df is None or len(df) == 0:
            print("[DATA_SOURCE/MACRO] get_global_futures_snapshot: 空数据")
            return result

        # 建立代码→行索引，加速查找
        code_col = next((c for c in df.columns if "代码" in c or "code" in c.lower()), df.columns[1])
        code_index = {str(row[code_col]).strip(): idx for idx, row in df.iterrows()}

        found_count = 0
        for key, target in TARGETS.items():
            code = target["code"]
            if code not in code_index:
                # 黄金代码可能不是 AU00Y，尝试模糊匹配
                if key == "gold":
                    # 搜索 COMEX黄金 或 纽约金
                    name_col = next((c for c in df.columns if "名称" in c or "name" in c.lower()), df.columns[2])
                    gold_rows = df[df[name_col].str.contains("COMEX黄金|纽约金|黄金当月", case=False, na=False)]
                    if len(gold_rows) > 0:
                        row = gold_rows.iloc[0]
                    else:
                        result[key] = None
                        continue
                else:
                    result[key] = None
                    continue
            else:
                row = df.loc[code_index[code]]

            # 提取价格数据
            try:
                price_col = next((c for c in df.columns if "最新价" in c or "latest" in c.lower()), None)
                change_col = next((c for c in df.columns if "涨跌幅" in c), None)
                prev_col = next((c for c in df.columns if "昨结" in c or "昨收" in c), None)

                price = float(row[price_col]) if price_col and not _is_nan(row[price_col]) else None
                change_pct = float(row[change_col]) if change_col and not _is_nan(row[change_col]) else None
                prev_close = float(row[prev_col]) if prev_col and not _is_nan(row[prev_col]) else None

                if price is not None or change_pct is not None:
                    result[key] = {
                        "price": round(price, 2) if price else None,
                        "change_pct": round(change_pct, 2) if change_pct is not None else None,
                        "prev_close": round(prev_close, 2) if prev_close else None,
                        "label": target["label"],
                    }
                    found_count += 1
                else:
                    result[key] = None
            except (ValueError, TypeError, KeyError) as e:
                print(f"[DATA_SOURCE/MACRO] futures {key} parse error: {e}")
                result[key] = None

        result["available"] = found_count >= 2  # 至少2个品种有数据才算可用
        print(f"[DATA_SOURCE/MACRO] get_global_futures_snapshot: {found_count}/{len(TARGETS)} 品种可用")

    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_global_futures_snapshot failed: {e}")

    return result


def _is_nan(value) -> bool:
    """检查值是否为 NaN（兼容 pandas NaN 和 Python float nan）"""
    try:
        import math
        if value is None:
            return True
        return math.isnan(float(value))
    except (ValueError, TypeError):
        return False


# ============================================================
# 恒生指数（新浪港股指数日线，T+0 更新）
# ============================================================

def get_hsi_latest() -> dict | None:
    """获取恒生指数最新收盘数据（含涨跌幅）

    数据源: AKShare stock_hk_index_daily_sina(symbol='HSI')
    - 新浪源，稳定性优于东方财富港股接口
    - 返回日线数据，交易日当天 16:00 后即有当日数据

    Returns:
        {"price": float, "change_pct": float, "prev_close": float, "date": str, "label": "恒生指数"}
        获取失败返回 None
    """
    try:
        import akshare as ak
        df = ak.stock_hk_index_daily_sina(symbol="HSI")
        if df is None or len(df) < 2:
            print("[DATA_SOURCE/MACRO] get_hsi_latest: 数据不足")
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(last["close"])
        prev_close = float(prev["close"])
        change_pct = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0

        return {
            "price": round(close, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
            "date": str(last["date"]),
            "label": "恒生指数",
        }
    except Exception as e:
        print(f"[DATA_SOURCE/MACRO] get_hsi_latest failed: {e}")
        return None
