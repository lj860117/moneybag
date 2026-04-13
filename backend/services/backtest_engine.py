"""
钱袋子 — 简单回测引擎 V1
给定持仓/策略，回测过去 1-3 年的收益表现

功能：
  1. 单只股票/基金历史收益率
  2. 持仓组合历史表现（加权）
  3. 定投模拟（按月定投 vs 一次性买入）
  4. 最大回撤 / 年化收益 / 夏普比率

数据源：AKShare stock_zh_a_hist / fund_open_fund_info_em
"""
import time
import math
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

_bt_cache = {}


def _get_stock_hist(code: str, period: str = "daily", days: int = 750) -> list:
    """获取个股历史日K线（收盘价序列），多源降级"""
    cache_key = f"hist_{code}_{days}"
    now = time.time()
    if cache_key in _bt_cache and now - _bt_cache[cache_key]["ts"] < 86400:
        return _bt_cache[cache_key]["data"]

    prices = []
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

        # 尝试东方财富（可能被反爬）
        try:
            df = ak.stock_zh_a_hist(symbol=code, period=period, start_date=start, end_date=end, adjust="qfq")
            if df is not None and len(df) > 5:
                close_col = next((c for c in df.columns if "收盘" in str(c).lower() or "close" in str(c).lower()), None)
                date_col = next((c for c in df.columns if "日期" in str(c).lower() or "date" in str(c).lower()), None)
                if close_col and date_col:
                    prices = [
                        {"date": str(row[date_col])[:10], "close": float(row[close_col])}
                        for _, row in df.iterrows()
                        if row[close_col] is not None
                    ]
                    print(f"[BACKTEST] {code}: 东方财富 {len(prices)} 条")
        except Exception as e:
            print(f"[BACKTEST] {code} 东方财富失败: {e}")

        # 降级：新浪历史数据
        if len(prices) < 10:
            try:
                df2 = ak.stock_zh_a_daily(symbol=f"sh{code}" if code.startswith("6") else f"sz{code}", adjust="qfq")
                if df2 is not None and len(df2) > 5:
                    df2 = df2.tail(days)
                    close_col = next((c for c in df2.columns if "close" in str(c).lower()), None)
                    if close_col:
                        prices = [
                            {"date": str(row.name)[:10] if hasattr(row, 'name') else str(idx)[:10], "close": float(row[close_col])}
                            for idx, row in df2.iterrows()
                            if row[close_col] is not None
                        ]
                        print(f"[BACKTEST] {code}: 新浪降级 {len(prices)} 条")
            except Exception as e2:
                print(f"[BACKTEST] {code} 新浪也失败: {e2}")

    except Exception as e:
        print(f"[BACKTEST] hist {code}: {e}")

    _bt_cache[cache_key] = {"data": prices, "ts": time.time()}
    return prices


def _get_fund_hist(code: str, days: int = 750) -> list:
    """获取基金历史净值序列"""
    cache_key = f"fund_hist_{code}_{days}"
    now = time.time()
    if cache_key in _bt_cache and now - _bt_cache[cache_key]["ts"] < 86400:
        return _bt_cache[cache_key]["data"]

    prices = []
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=code, indicator="累计净值走势")
        if df is not None and len(df) > 0:
            # 取最近 days 天
            df = df.tail(days)
            for _, row in df.iterrows():
                try:
                    prices.append({
                        "date": str(row.iloc[0])[:10],
                        "close": float(row.iloc[1]),
                    })
                except (ValueError, TypeError, IndexError):
                    continue
    except Exception as e:
        print(f"[BACKTEST] fund_hist {code}: {e}")

    _bt_cache[cache_key] = {"data": prices, "ts": time.time()}
    return prices


def _calc_metrics(prices: list) -> dict:
    """计算回测指标：年化收益/最大回撤/夏普比率/胜率"""
    if len(prices) < 10:
        return {"error": "数据不足"}

    closes = [p["close"] for p in prices]
    total_return = (closes[-1] - closes[0]) / closes[0]
    trading_days = len(closes)
    years = trading_days / 250

    # 年化收益率
    if years > 0 and total_return > -1:
        annual_return = (1 + total_return) ** (1 / years) - 1
    else:
        annual_return = total_return

    # 最大回撤
    peak = closes[0]
    max_dd = 0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak
        if dd > max_dd:
            max_dd = dd

    # 日收益率序列
    daily_returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            daily_returns.append((closes[i] - closes[i - 1]) / closes[i - 1])

    # 波动率（年化）
    if daily_returns:
        avg_r = sum(daily_returns) / len(daily_returns)
        var_r = sum((r - avg_r) ** 2 for r in daily_returns) / len(daily_returns)
        volatility = math.sqrt(var_r) * math.sqrt(250)
    else:
        volatility = 0

    # 夏普比率（无风险利率 2%）
    risk_free = 0.02
    sharpe = (annual_return - risk_free) / volatility if volatility > 0 else 0

    # 胜率（日涨天数占比）
    up_days = sum(1 for r in daily_returns if r > 0)
    win_rate = up_days / len(daily_returns) if daily_returns else 0

    return {
        "totalReturn": round(total_return * 100, 2),
        "annualReturn": round(annual_return * 100, 2),
        "maxDrawdown": round(max_dd * 100, 2),
        "volatility": round(volatility * 100, 2),
        "sharpe": round(sharpe, 2),
        "winRate": round(win_rate * 100, 1),
        "tradingDays": trading_days,
        "years": round(years, 1),
        "startDate": prices[0]["date"],
        "endDate": prices[-1]["date"],
        "startPrice": round(closes[0], 2),
        "endPrice": round(closes[-1], 2),
    }


def _simulate_dca(prices: list, monthly_amount: float = 1000) -> dict:
    """模拟定投：每月第一个交易日定投固定金额"""
    if len(prices) < 20:
        return {"error": "数据不足"}

    # 按月分组
    months_seen = set()
    buy_points = []
    for p in prices:
        month_key = p["date"][:7]  # YYYY-MM
        if month_key not in months_seen:
            months_seen.add(month_key)
            buy_points.append(p)

    # 定投执行
    total_invested = 0
    total_shares = 0
    for bp in buy_points:
        price = bp["close"]
        if price > 0:
            shares = monthly_amount / price
            total_shares += shares
            total_invested += monthly_amount

    # 终值
    final_price = prices[-1]["close"]
    final_value = total_shares * final_price
    dca_return = (final_value - total_invested) / total_invested if total_invested > 0 else 0

    # 对比一次性买入
    lump_return = (prices[-1]["close"] - prices[0]["close"]) / prices[0]["close"]

    return {
        "dcaReturn": round(dca_return * 100, 2),
        "lumpReturn": round(lump_return * 100, 2),
        "dcaBetter": dca_return > lump_return,
        "totalInvested": round(total_invested, 0),
        "finalValue": round(final_value, 0),
        "avgCost": round(total_invested / total_shares, 4) if total_shares > 0 else 0,
        "totalShares": round(total_shares, 2),
        "months": len(buy_points),
        "monthlyAmount": monthly_amount,
    }


def backtest_single(code: str, asset_type: str = "stock", years: int = 3) -> dict:
    """单只股票/基金回测"""
    days = years * 365

    if asset_type == "fund":
        prices = _get_fund_hist(code, days)
    else:
        prices = _get_stock_hist(code, days=days)

    if not prices:
        return {"code": code, "error": "无历史数据"}

    metrics = _calc_metrics(prices)
    dca = _simulate_dca(prices)

    return {
        "code": code,
        "type": asset_type,
        "metrics": metrics,
        "dca": dca,
        "priceCount": len(prices),
    }


def backtest_portfolio(holdings: list, years: int = 3) -> dict:
    """组合回测：按权重加权计算组合收益

    holdings 格式：[{"code":"600519","type":"stock","weight":0.3}, ...]
    """
    if not holdings:
        return {"error": "无持仓"}

    results = {}

    def _fetch(h):
        code = h["code"]
        atype = h.get("type", "stock")
        days = years * 365
        if atype == "fund":
            return code, _get_fund_hist(code, days)
        return code, _get_stock_hist(code, days=days)

    # 并发获取历史数据
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch, h): h for h in holdings}
        for f in as_completed(futures):
            try:
                code, prices = f.result()
                results[code] = prices
            except Exception:
                pass

    # 对齐日期（取所有持仓的公共交易日）
    all_dates = None
    for code, prices in results.items():
        if prices:
            date_set = set(p["date"] for p in prices)
            if all_dates is None:
                all_dates = date_set
            else:
                all_dates = all_dates & date_set

    if not all_dates or len(all_dates) < 10:
        return {"error": "公共交易日不足", "holdings": len(results)}

    sorted_dates = sorted(all_dates)

    # 构建价格映射
    price_maps = {}
    for code, prices in results.items():
        price_maps[code] = {p["date"]: p["close"] for p in prices}

    # 计算组合日收益
    portfolio_prices = []
    for date in sorted_dates:
        port_value = 0
        for h in holdings:
            code = h["code"]
            weight = h.get("weight", 1.0 / len(holdings))
            pm = price_maps.get(code, {})
            if date in pm and sorted_dates[0] in pm:
                # 归一化到首日=1，然后加权
                base = pm[sorted_dates[0]]
                if base > 0:
                    port_value += (pm[date] / base) * weight

        portfolio_prices.append({"date": date, "close": port_value})

    # 计算组合指标
    metrics = _calc_metrics(portfolio_prices)

    # 单只明细
    individual = {}
    for h in holdings:
        code = h["code"]
        prices = results.get(code, [])
        if prices:
            individual[code] = _calc_metrics(prices)

    return {
        "portfolio": metrics,
        "individual": individual,
        "holdingCount": len(holdings),
        "commonDays": len(sorted_dates),
        "dateRange": f"{sorted_dates[0]} ~ {sorted_dates[-1]}",
    }
