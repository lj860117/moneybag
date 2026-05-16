"""
钱袋子 — 市场核心数据
基金净值、恐惧贪婪指数、估值百分位
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "market_data",
    "scope": "public",
    "input": [],
    "output": "market_core",
    "cost": "cpu",
    "tags": ['恐贪指数', '估值', '基金净值'],
    "description": "市场核心数据：基金净值+恐惧贪婪指数+估值百分位",
    "layer": "data",
    "priority": 1,
}
import time
from datetime import datetime
from typing import Optional
from config import NAV_CACHE_TTL
from infra.cache import MemoryCache

_nav_cache = MemoryCache(default_ttl=NAV_CACHE_TTL)

def _looks_like_stock_code(code: str) -> bool:
    """简易判断：A股股票代码（6位数字，首位 6/0/3），基金通常 0/1/5 开头但长度不同"""
    if not code or not code.isdigit() or len(code) != 6:
        return False
    # A 股：60xxxx/00xxxx/30xxxx/688xxx/8xxxxx
    return code[0] in ("6", "3") or code.startswith("000") or code.startswith("002") or code.startswith("688") or code.startswith("8")


def get_fund_nav(code: str) -> dict:
    """获取单只基金的最新净值"""
    cache_key = code
    now = time.time()

    cached = _nav_cache.get(cache_key)
    if cached is not None:
        return cached

    # FIX 2026-04-19: 股票代码直接返回空，避免 akshare 去查基金净值导致 SyntaxError 噪音日志
    if _looks_like_stock_code(code):
        return {"code": code, "nav": "N/A", "date": "N/A", "change": "0", "skip_reason": "股票代码，非基金"}

    try:
        from infra.data_source.market.stocks import get_fund_nav_history as _get_fund_nav_hist
        # 开放式基金净值
        df = _get_fund_nav_hist(code=code, indicator="单位净值走势")
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
            nav_val = float(latest["单位净值"])
            prev_val = float(prev["单位净值"])
            change = round((nav_val - prev_val) / prev_val * 100, 2)
            result = {
                "code": code,
                "nav": str(nav_val),
                "date": str(latest["净值日期"]),
                "change": str(change),
                "source": "akshare",
            }
            _nav_cache.set(cache_key, result)
            return result
    except Exception as e:
        print(f"[NAV] AKShare failed to fetch {code}: {e}")

    # 2026-04-19 A+: Tushare 降级（5000 积分基金净值接口）
    try:
        from services.tushare_data import is_configured, get_fund_nav as ts_nav
        if is_configured():
            ts = ts_nav(code, days=5)
            if ts.get("available") and ts.get("unit_nav"):
                latest_navs = ts.get("navs") or []
                if len(latest_navs) >= 2:
                    cur = float(latest_navs[-1]["unit_nav"])
                    prev = float(latest_navs[-2]["unit_nav"])
                    change = round((cur - prev) / prev * 100, 2) if prev else 0
                else:
                    change = 0
                result = {
                    "code": code,
                    "nav": str(ts["unit_nav"]),
                    "date": ts.get("nav_date", "")[:4] + "-" + ts.get("nav_date", "")[4:6] + "-" + ts.get("nav_date", "")[6:8] if ts.get("nav_date") else "",
                    "change": str(change),
                    "source": "tushare",
                }
                print(f"[NAV] Tushare 降级成功 {code}: nav={result['nav']}")
                _nav_cache.set(cache_key, result)
                return result
    except Exception as e:
        print(f"[NAV] Tushare 降级也失败 {code}: {e}")

    # 所有源失败
    return {"code": code, "nav": "N/A", "date": "N/A", "change": "0"}


def get_fear_greed_index() -> dict:
    """增强版恐惧贪婪指数（3维：涨跌幅+波动率+成交量偏离）
    返回 dict 包含综合分数和各维度明细，兼容旧代码（可用 result["score"]）

    数据来源：FallbackRunner 三级降级（Tushare→BaoStock→AKShare）
    AKShare 的 index_fear_greed_funddb 已永久删除，完全不依赖。
    """
    result = {"score": 50, "level": "中性", "dimensions": {}}
    try:
        from infra.data_source.market.stocks import get_index_daily
        df = get_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 60:
            # 列名兼容：不同数据源列名不同（AKShare=英文, BaoStock=中文, Tushare=trade_date）
            close_col = next((c for c in df.columns if c in ("close", "收盘")), None)
            vol_col = next((c for c in df.columns if c in ("volume", "成交量", "vol")), None)
            if not close_col:
                raise ValueError(f"无法识别收盘价列，列名: {list(df.columns)}")

            recent_60 = df.tail(60)
            recent_20 = df.tail(20)

            import pandas as _pd
            close_20 = _pd.to_numeric(recent_20[close_col], errors="coerce").dropna().values
            close_60 = _pd.to_numeric(recent_60[close_col], errors="coerce").dropna().values

            if len(close_20) < 10 or len(close_60) < 30:
                raise ValueError(f"数据不足: close_20={len(close_20)}, close_60={len(close_60)}")

            # 维度1: 20日涨跌幅 → 恐惧/贪婪 (权重 40%)
            change_20d = (close_20[-1] - close_20[0]) / close_20[0] * 100
            if change_20d < -15:
                dim1_score = 90
            elif change_20d < -8:
                dim1_score = 75
            elif change_20d < -3:
                dim1_score = 60
            elif change_20d < 3:
                dim1_score = 50
            elif change_20d < 8:
                dim1_score = 35
            elif change_20d < 15:
                dim1_score = 20
            else:
                dim1_score = 10
            result["dimensions"]["momentum"] = {"value": round(change_20d, 2), "score": dim1_score, "label": "20日动量"}

            # 维度2: 波动率（20日收益率标准差）→ 高波动=恐惧 (权重 30%)
            returns = [(close_20[i] - close_20[i - 1]) / close_20[i - 1] * 100 for i in range(1, len(close_20))]
            volatility = (sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)) ** 0.5
            # 历史波动率阈值（沪深300日波动率通常 0.5-2.5%）
            if volatility > 2.5:
                dim2_score = 90
            elif volatility > 1.8:
                dim2_score = 70
            elif volatility > 1.2:
                dim2_score = 50
            elif volatility > 0.7:
                dim2_score = 35
            else:
                dim2_score = 20
            result["dimensions"]["volatility"] = {"value": round(volatility, 3), "score": dim2_score, "label": "波动率"}

            # 维度3: 成交量偏离（近5日 vs 近60日均量）(权重 30%)
            if vol_col and vol_col in df.columns:
                vol_series = _pd.to_numeric(df[vol_col], errors="coerce").fillna(0)
                vol_5 = vol_series.tail(5).mean()
                vol_60 = vol_series.tail(60).mean()
                vol_ratio = vol_5 / vol_60 if vol_60 > 0 else 1
                # 缩量下跌=恐惧，放量上涨=贪婪
                if change_20d < 0:  # 下跌中
                    dim3_score = 70 if vol_ratio > 1.3 else 60 if vol_ratio > 1.0 else 80  # 缩量下跌更恐惧
                else:  # 上涨中
                    dim3_score = 20 if vol_ratio > 1.5 else 35 if vol_ratio > 1.0 else 45
                result["dimensions"]["volume"] = {"value": round(vol_ratio, 2), "score": dim3_score, "label": "量能偏离"}
            else:
                dim3_score = 50
                result["dimensions"]["volume"] = {"value": 1.0, "score": 50, "label": "量能(无数据)"}

            # 加权综合
            # FIX 2026-04-19 V7.2: 权重从 config.FGI_DIM_WEIGHTS 读取
            from config import FGI_DIM_WEIGHTS
            composite = (dim1_score * FGI_DIM_WEIGHTS["momentum"] +
                         dim2_score * FGI_DIM_WEIGHTS["volatility"] +
                         dim3_score * FGI_DIM_WEIGHTS["volume"])

            # 翻转为「贪婪分」: 0=极度恐惧, 100=极度贪婪（符合 CNN FGI 直觉）
            greed_score = round(100 - composite, 1)
            result["score"] = greed_score

            if greed_score >= 75:
                result["level"] = "极度贪婪"
            elif greed_score >= 60:
                result["level"] = "贪婪"
            elif greed_score >= 40:
                result["level"] = "中性"
            elif greed_score >= 25:
                result["level"] = "恐惧"
            else:
                result["level"] = "极度恐惧"

    except Exception as e:
        print(f"[FGI] 实时计算失败: {e}")

    # 降级: precomputed_cache（night_worker 凌晨已算好）
    if result["score"] == 50 and not result.get("dimensions"):
        try:
            from services.precomputed_cache import get_precomputed
            cached = get_precomputed("fear_greed")
            if cached and "score" in cached:
                cached["_degraded"] = "precomputed_cache"
                print(f"[FGI] 降级至 precomputed_cache: score={cached['score']}")
                return cached
        except Exception:
            pass

    return result

def get_valuation_percentile() -> dict:
    """获取沪深300估值百分位 — 优先使用加权PE-TTM（市场通用标准）

    口径说明：
    - 加权PE-TTM(~14.6): Wind/同花顺/乐咕默认口径，市场通用标准
    - 等权PE中位数(~21): ETF.run/理杏仁口径，会偏高
    - 本函数优先用加权PE-TTM，因为这是普通投资者最常看到的数字
    """
    try:
        from infra.data_source.market.stocks import get_index_pe, get_index_valuation_csindex

        # 方案1（首选）：Tushare 加权PE-TTM — 市场通用标准
        try:
            import os
            token = os.getenv("TUSHARE_TOKEN", "")
            if token:
                import tushare as ts_api
                ts_api.set_token(token)
                pro = ts_api.pro_api()
                from datetime import datetime, timedelta

                end_date = datetime.now().strftime("%Y%m%d")
                start_date = (datetime.now() - timedelta(days=3650)).strftime("%Y%m%d")

                df = pro.index_dailybasic(
                    ts_code="399300.SZ",
                    start_date=start_date,
                    end_date=end_date,
                    fields="trade_date,pe_ttm,pb,total_mv,turnover_rate"
                )
                if df is not None and len(df) >= 500:
                    pe_data = df["pe_ttm"].dropna()
                    if len(pe_data) >= 500:
                        current_pe = float(pe_data.iloc[0])
                        pe_values = pe_data.values
                        percentile = round(sum(1 for p in pe_values if p <= current_pe) / len(pe_values) * 100, 1)
                        latest_date = str(df["trade_date"].iloc[0])
                        window_years = round(len(pe_values) / 250, 1)
                        print(f"[VAL] Tushare 加权PE-TTM={current_pe}, pct={percentile}%, window={window_years}年")
                        return {
                            "index": "沪深300",
                            "percentile": percentile,
                            "level": "低估" if percentile < 30 else "适中" if percentile < 70 else "高估",
                            "current_pe": round(current_pe, 2),
                            "metric": f"加权PE-TTM(近{window_years}年分位，Wind/同花顺同口径)",
                            "date": latest_date,
                            "pe_range": f"{pe_data.min():.1f}-{pe_data.max():.1f}",
                            "window_days": len(pe_values),
                            "note": f"加权PE-TTM是市场通用口径，近{window_years}年{percentile}%分位",
                        }
        except Exception as e:
            print(f"[VAL] Tushare PE-TTM failed: {e}")

        # 方案2（降级）：乐咕中位数PE — 标注口径差异
        try:
            df = get_index_pe(symbol="沪深300")
            if df is not None and len(df) >= 250:
                pe_col = None
                for col_name in ["滚动市盈率中位数", "静态市盈率中位数"]:
                    if col_name in df.columns:
                        pe_col = col_name
                        break
                if not pe_col:
                    pe_col = next((c for c in df.columns if "中位" in c and "市盈率" in c), None)

                if pe_col:
                    pe_data = df[pe_col].dropna()
                    if len(pe_data) >= 250:
                        current_pe = float(pe_data.iloc[-1])
                        pe_values = pe_data.values
                        percentile = round(sum(1 for p in pe_values if p <= current_pe) / len(pe_values) * 100, 1)
                        latest_date = str(df["日期"].iloc[-1]) if "日期" in df.columns else ""

                        print(f"[VAL] 降级:中位数PE={current_pe}, pct={percentile}%")
                        return {
                            "index": "沪深300",
                            "percentile": percentile,
                            "level": "低估" if percentile < 30 else "适中" if percentile < 70 else "高估",
                            "current_pe": round(current_pe, 2),
                            "metric": "⚠️中位数PE(非市场通用口径，仅供参考)",
                            "date": latest_date,
                            "pe_range": f"{pe_data.min():.1f}-{pe_data.max():.1f}",
                            "window_days": len(pe_values),
                            "note": "中位数PE偏高于加权PE，市场通用标准见Wind/同花顺",
                        }
        except Exception as e:
            print(f"[VAL] legulegu median PE failed: {e}")

        # 降级：乐咕 stock_index_pe_lg（口径可能偏差，但有数据）
        try:
            df = get_index_pe(symbol="沪深300")
            if df is not None and len(df) >= 250:
                pe_col = "滚动市盈率"
                if pe_col not in df.columns:
                    # 容错：取第一个含"市盈率"的列
                    pe_col = next((c for c in df.columns if "市盈率" in c and "中位" not in c and "等权" not in c), None)
                if pe_col:
                    pe_data = df[pe_col].dropna()
                    # 用近5年数据算百分位（更合理的时间窗口）
                    window = min(1250, len(pe_data))
                    recent = pe_data.tail(window)
                    current_pe = float(recent.iloc[-1])
                    pe_values = recent.values
                    percentile = round(sum(1 for p in pe_values if p <= current_pe) / len(pe_values) * 100, 1)
                    latest_date = str(df["日期"].iloc[-1]) if "日期" in df.columns else ""
                    print(f"[VAL] PE={current_pe}, pct={percentile}%, window={window}d, date={latest_date}")
                    return {
                        "index": "沪深300",
                        "percentile": percentile,
                        "level": "低估" if percentile < 30 else "适中" if percentile < 70 else "高估",
                        "current_pe": round(current_pe, 2),
                        "metric": "PE-TTM(滚动)",
                        "date": latest_date,
                    }
        except Exception as e:
            print(f"[VAL] stock_index_pe_lg failed: {e}")
            import traceback; traceback.print_exc()

        # 降级：中证官方 stock_zh_index_value_csindex（数据量少但权威）
        try:
            df = get_index_valuation_csindex(symbol="000300")
            if df is not None and len(df) > 0:
                pe_col = "市盈率1"  # 静态PE
                if pe_col in df.columns:
                    current_pe = float(df[pe_col].iloc[-1])
                    latest_date = str(df["日期"].iloc[-1]) if "日期" in df.columns else ""
                    print(f"[VAL] CSIndex PE={current_pe}, date={latest_date} (limited data, no percentile)")
                    return {
                        "index": "沪深300",
                        "percentile": 50,  # 数据量不够算百分位，默认适中
                        "level": "适中(数据不足)",
                        "current_pe": round(current_pe, 2),
                        "metric": "PE(中证官方)",
                        "date": latest_date,
                    }
        except Exception as e:
            print(f"[VAL] CSIndex failed: {e}")

    except Exception as e:
        print(f"[VAL] Failed: {e}")

    # 降级: precomputed_cache（night_worker 凌晨已算好）
    try:
        from services.precomputed_cache import get_precomputed
        cached = get_precomputed("valuation")
        if cached and "percentile" in cached:
            cached["_degraded"] = "precomputed_cache"
            print(f"[VAL] 降级至 precomputed_cache: pct={cached['percentile']}%")
            return cached
    except Exception:
        pass

    return {"index": "沪深300", "percentile": 50, "level": "适中", "current_pe": 0, "metric": "默认"}

# ---- 技术指标 ----


def _get_nav_on_date(code: str, date_str: str) -> Optional[float]:
    """获取基金在指定日期的净值"""
    cache_key = f"hist_{code}"
    now = time.time()

    # 使用缓存的历史数据
    cached = _nav_cache.get(cache_key)
    if cached is not None:
        df = cached
    else:
        try:
            from infra.data_source.market.stocks import get_fund_nav_history as _get_fund_nav_hist
            df = _get_fund_nav_hist(code=code, indicator="单位净值走势")
            if df is not None and len(df) > 0:
                _nav_cache.set(cache_key, df)
            else:
                return None
        except Exception as e:
            print(f"[HIST_NAV] Failed for {code}: {e}")
            return None

    try:
        target = datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        # 找最接近买入日期的净值
        df["date_str"] = df["净值日期"].astype(str)
        match = df[df["date_str"] >= target].head(1)
        if len(match) > 0:
            return float(match.iloc[0]["单位净值"])
        # 如果买入日期比所有数据都新，取最新
        return float(df.iloc[-1]["单位净值"])
    except Exception as e:
        print(f"[HIST_NAV] Parse error: {e}")
        return None


