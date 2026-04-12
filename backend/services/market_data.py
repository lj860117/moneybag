"""
钱袋子 — 市场核心数据
基金净值、恐惧贪婪指数、估值百分位
"""
import time
from typing import Optional
from config import NAV_CACHE_TTL

_nav_cache = {}

def get_fund_nav(code: str) -> dict:
    """获取单只基金的最新净值"""
    cache_key = code
    now = time.time()

    if cache_key in _nav_cache and now - _nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        return _nav_cache[cache_key]["data"]

    try:
        import akshare as ak
        # 开放式基金净值
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
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
            }
            _nav_cache[cache_key] = {"data": result, "ts": now}
            return result
    except Exception as e:
        print(f"[NAV] Failed to fetch {code}: {e}")

    # 降级：返回空
    return {"code": code, "nav": "N/A", "date": "N/A", "change": "0"}


def get_fear_greed_index() -> dict:
    """增强版恐惧贪婪指数（3维：涨跌幅+波动率+成交量偏离）
    返回 dict 包含综合分数和各维度明细，兼容旧代码（可用 result["score"]）
    """
    result = {"score": 50, "level": "中性", "dimensions": {}}
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        if df is not None and len(df) >= 60:
            recent_60 = df.tail(60)
            recent_20 = df.tail(20)

            close_20 = recent_20["close"].values
            close_60 = recent_60["close"].values

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
            if "volume" in df.columns:
                vol_5 = df.tail(5)["volume"].mean()
                vol_60 = recent_60["volume"].mean()
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
            composite = dim1_score * 0.4 + dim2_score * 0.3 + dim3_score * 0.3
            result["score"] = round(composite, 1)

            if composite >= 75:
                result["level"] = "极度恐惧"
            elif composite >= 60:
                result["level"] = "恐惧"
            elif composite >= 40:
                result["level"] = "中性"
            elif composite >= 25:
                result["level"] = "贪婪"
            else:
                result["level"] = "极度贪婪"

    except Exception as e:
        print(f"[FGI] Failed: {e}")
    return result

def get_valuation_percentile() -> dict:
    """获取沪深300真实PE估值百分位（近5年）"""
    try:
        import akshare as ak

        # 优先：乐咕 stock_index_pe_lg — 数据全、更新快
        try:
            df = ak.stock_index_pe_lg(symbol="沪深300")
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
            df = ak.stock_zh_index_value_csindex(symbol="000300")
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
    return {"index": "沪深300", "percentile": 50, "level": "适中", "current_pe": 0, "metric": "默认"}

# ---- 技术指标 ----


def _get_nav_on_date(code: str, date_str: str) -> Optional[float]:
    """获取基金在指定日期的净值"""
    cache_key = f"hist_{code}"
    now = time.time()

    # 使用缓存的历史数据
    if cache_key in _nav_cache and now - _nav_cache[cache_key]["ts"] < NAV_CACHE_TTL:
        df = _nav_cache[cache_key]["data"]
    else:
        try:
            import akshare as ak
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and len(df) > 0:
                _nav_cache[cache_key] = {"data": df, "ts": now}
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


