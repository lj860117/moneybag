"""
钱袋子 — A 股行情多源数据层
自动降级：东方财富(全字段) → 新浪基础行情+雪球补充(PE/PB/换手率/市值)
解决东方财富 stock_zh_a_spot_em 频繁反爬问题

架构：
  stock_screen.py / ml_stock_screen.py
              ↓ 调用
  stock_data_provider.get_stock_data()
              ↓ 自动选源
  东方财富 | 新浪+雪球混合 | 缓存
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "stock_data_provider",
    "scope": "public",
    "input": [],
    "output": "stock_data",
    "cost": "cpu",
    "tags": ['A股', '多源', '降级'],
    "description": "A股行情多源数据层：东财→新浪+雪球自动降级",
    "layer": "data",
    "priority": 1,
}
import time
import traceback
import concurrent.futures
from config import STOCK_CACHE_TTL

# 数据层缓存（所有消费方共享同一份数据）
_provider_cache = {}
_CACHE_KEY = "stock_all_data"


def get_stock_data() -> dict:
    """
    获取 A 股全量行情数据（统一格式）

    返回格式：
    {
        "stocks": [  # 标准化后的股票列表
            {
                "code": "600519",
                "name": "贵州茅台",
                "price": 1437.51,
                "change_pct": -1.13,
                "pe": 19.996,
                "pb": 7.003,
                "turnover": 0.13,
                "market_cap": 18001.5,   # 亿元
                "volume": 1596585,
                "amount": 2296538367.96,
                "high": ..., "low": ..., "open": ...,
                "amplitude": 0.93,
                "change_5d": None,       # 新浪源无此字段
                "change_20d": None,
                "change_60d": None,
            }, ...
        ],
        "source": "em" | "sina+xq" | "cache",
        "count": 5501,
        "elapsed": 13.2,
    }
    """
    now = time.time()
    if _CACHE_KEY in _provider_cache:
        cached = _provider_cache[_CACHE_KEY]
        if now - cached["ts"] < STOCK_CACHE_TTL:
            return {**cached["data"], "source": "cache"}

    # 尝试源 1：东方财富（字段最全，但经常反爬）
    result = _try_em_source()
    if result and len(result["stocks"]) > 100:
        _provider_cache[_CACHE_KEY] = {"data": result, "ts": now}
        return result

    # 降级源 2：新浪基础行情 + 雪球并发补充 PE/PB
    print("[DATA_PROVIDER] 东方财富不可用，降级到新浪+雪球")
    result = _try_sina_xq_source()
    if result and len(result["stocks"]) > 100:
        _provider_cache[_CACHE_KEY] = {"data": result, "ts": now}
        return result

    # 所有源都失败
    return {"stocks": [], "source": "none", "count": 0, "elapsed": 0,
            "error": "所有数据源均不可用，请稍后重试"}


def _try_em_source() -> dict | None:
    """东方财富源：stock_zh_a_spot_em"""
    try:
        import akshare as ak
        from services.utils import find_col as _fc, safe_float as _sf

        t0 = time.time()
        print("[DATA_PROVIDER] Trying 东方财富...")
        df = ak.stock_zh_a_spot_em()
        if df is None or len(df) < 100:
            return None

        cols = list(df.columns)
        stocks = []
        for _, row in df.iterrows():
            try:
                code = str(row.get(_fc(cols, ["代码"]), ""))
                name = str(row.get(_fc(cols, ["名称"]), ""))
                price = _sf(row.get(_fc(cols, ["最新价"]), None))
                if not code or not name or price is None or price <= 0:
                    continue

                stocks.append({
                    "code": code, "name": name, "price": price,
                    "change_pct": _sf(row.get(_fc(cols, ["涨跌幅"]), None)),
                    "pe": _sf(row.get(_fc(cols, ["市盈率"]), None)),
                    "pb": _sf(row.get(_fc(cols, ["市净率"]), None)),
                    "turnover": _sf(row.get(_fc(cols, ["换手率"]), None)),
                    "market_cap": _round_yi(_sf(row.get(_fc(cols, ["总市值"]), None))),
                    "volume": _sf(row.get(_fc(cols, ["成交量"]), None)),
                    "amount": _sf(row.get(_fc(cols, ["成交额"]), None)),
                    "high": _sf(row.get(_fc(cols, ["最高"]), None)),
                    "low": _sf(row.get(_fc(cols, ["最低"]), None)),
                    "open": _sf(row.get(_fc(cols, ["今开"]), None)),
                    "amplitude": _sf(row.get(_fc(cols, ["振幅"]), None)),
                    "change_5d": _sf(row.get(_fc(cols, ["5日涨跌幅", "5日涨跌"]), None)),
                    "change_20d": _sf(row.get(_fc(cols, ["20日涨跌幅", "20日涨跌"]), None)),
                    "change_60d": _sf(row.get(_fc(cols, ["60日涨跌幅", "60日涨跌"]), None)),
                })
            except Exception:
                continue

        elapsed = time.time() - t0
        print("[DATA_PROVIDER] 东方财富 OK: {} stocks in {:.1f}s".format(len(stocks), elapsed))
        return {"stocks": stocks, "source": "em", "count": len(stocks), "elapsed": round(elapsed, 1)}

    except Exception as e:
        print("[DATA_PROVIDER] 东方财富 FAIL: {}".format(str(e)[:120]))
        return None


def _try_sina_xq_source() -> dict | None:
    """
    新浪+雪球混合源：
    1. 新浪 stock_zh_a_spot 获取全量基础行情（~13秒, 5500只）
    2. 先按基础指标预筛到 TOP 500（成交额排序）
    3. 雪球 stock_individual_spot_xq 并发补充 PE/PB/换手率/市值（~30秒, 50并发）
    """
    try:
        import akshare as ak
        from services.utils import safe_float as _sf

        t0 = time.time()

        # Step 1: 新浪全量行情
        print("[DATA_PROVIDER] 新浪源加载中...")
        df = ak.stock_zh_a_spot()
        if df is None or len(df) < 100:
            print("[DATA_PROVIDER] 新浪源数据不足")
            return None

        t_sina = time.time() - t0
        print("[DATA_PROVIDER] 新浪 OK: {} stocks in {:.1f}s".format(len(df), t_sina))

        # Step 2: 基础数据提取 + 预筛选
        base_stocks = []
        for _, row in df.iterrows():
            try:
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                price = _sf(row.get("最新价", None))
                if not code or not name or price is None or price <= 0:
                    continue
                # 排除 ST
                if "ST" in name:
                    continue

                amount = _sf(row.get("成交额", None))
                base_stocks.append({
                    "code": code, "name": name, "price": price,
                    "change_pct": _sf(row.get("涨跌幅", None)),
                    "volume": _sf(row.get("成交量", None)),
                    "amount": amount or 0,
                    "high": _sf(row.get("最高", None)),
                    "low": _sf(row.get("最低", None)),
                    "open": _sf(row.get("今开", None)),
                    "amplitude": None,  # 新浪无振幅，后面从高低价算
                    "pe": None, "pb": None, "turnover": None, "market_cap": None,
                    "change_5d": None, "change_20d": None, "change_60d": None,
                })
            except Exception:
                continue

        # 按成交额排序，取 TOP 200 补充详情（平衡速度与覆盖率）
        base_stocks.sort(key=lambda x: x["amount"], reverse=True)
        top_candidates = base_stocks[:200]
        rest_stocks = base_stocks[200:]  # 剩下的保留基础数据

        # 补算振幅
        for s in base_stocks:
            if s["high"] and s["low"] and s["price"]:
                prev_close = s["price"] / (1 + (s["change_pct"] or 0) / 100) if s["change_pct"] else s["price"]
                if prev_close > 0:
                    s["amplitude"] = round((s["high"] - s["low"]) / prev_close * 100, 2)

        # Step 3: 雪球并发补充 PE/PB/换手率/市值
        # Step 3: 雪球并发补充 PE/PB/换手率/市值（20线程并发）
        print("[DATA_PROVIDER] 雪球补充 TOP {} 只...".format(len(top_candidates)))
        t_xq_start = time.time()

        def _fetch_xq_detail(stock):
            """单只雪球查询"""
            try:
                code = stock["code"]
                # 构造雪球 symbol：沪市 SH，深市 SZ，北交所 BJ
                if code.startswith("6"):
                    symbol = "SH" + code
                elif code.startswith(("0", "3")):
                    symbol = "SZ" + code
                elif code.startswith(("8", "4")):
                    symbol = "BJ" + code
                else:
                    # 前缀已含交易所（如 bj920000）
                    prefix = code[:2].upper()
                    num = code[2:] if len(code) > 6 else code
                    if prefix in ("BJ", "SH", "SZ"):
                        symbol = prefix + num
                    else:
                        symbol = "SH" + code  # fallback

                xq_df = ak.stock_individual_spot_xq(symbol=symbol)
                if xq_df is None or len(xq_df) == 0:
                    return stock

                # 转为 dict 方便查找
                xq_data = {}
                for _, r in xq_df.iterrows():
                    xq_data[str(r["item"])] = r["value"]

                # 补充字段
                pe_val = _sf(xq_data.get("市盈率(TTM)", xq_data.get("市盈率(动)", None)))
                pb_val = _sf(xq_data.get("市净率", None))
                turnover_val = _sf(xq_data.get("周转率", None))
                mcap_val = _sf(xq_data.get("资产净值/总市值", None))  # 这个是总市值
                amp_val = _sf(xq_data.get("振幅", None))

                if pe_val is not None:
                    stock["pe"] = pe_val
                if pb_val is not None:
                    stock["pb"] = pb_val
                if turnover_val is not None:
                    stock["turnover"] = turnover_val
                if mcap_val is not None:
                    stock["market_cap"] = round(mcap_val / 1e8, 1)  # 转亿元
                if amp_val is not None:
                    stock["amplitude"] = amp_val

                return stock
            except Exception:
                return stock  # 查询失败保留基础数据

        # 并发查询（最大 20 线程，避免被雪球封）
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(_fetch_xq_detail, s): s for s in top_candidates}
            enriched = []
            for future in concurrent.futures.as_completed(futures):
                enriched.append(future.result())

        t_xq = time.time() - t_xq_start
        # 统计补充成功率
        enriched_count = sum(1 for s in enriched if s["pe"] is not None)
        print("[DATA_PROVIDER] 雪球补充完成: {}/{} 有PE数据, {:.1f}s".format(
            enriched_count, len(enriched), t_xq))

        # 合并：补充过的 + 剩余基础数据
        all_stocks = enriched + rest_stocks
        elapsed = time.time() - t0
        print("[DATA_PROVIDER] 新浪+雪球 OK: {} stocks in {:.1f}s".format(len(all_stocks), elapsed))

        return {
            "stocks": all_stocks,
            "source": "sina+xq",
            "count": len(all_stocks),
            "elapsed": round(elapsed, 1),
            "detail": {
                "sina_total": len(base_stocks),
                "xq_enriched": len(enriched),
                "xq_with_pe": enriched_count,
                "sina_time": round(t_sina, 1),
                "xq_time": round(t_xq, 1),
            },
        }

    except Exception as e:
        print("[DATA_PROVIDER] 新浪+雪球 FAIL: {}".format(str(e)[:120]))
        traceback.print_exc()
        return None


def _round_yi(val):
    """将元转为亿元"""
    if val is None:
        return None
    return round(val / 1e8, 1)
