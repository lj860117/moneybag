"""
钱袋子 — 行业轮动分析
V6 Phase 3: 同花顺行业板块数据 + 资金流排名 + 轮动信号

数据源:
  - stock_board_industry_summary_ths(): 同花顺 ~90 个行业板块
    字段: 涨跌幅/总成交量/总成交额/净流入/上涨家数/下跌家数/领涨股
  - 东方财富接口在腾讯云被限制，降级用同花顺
"""

import time
from datetime import datetime

# ---- MODULE_META ----
MODULE_META = {
    "name": "sector_rotation",
    "scope": "public",
    "input": [],
    "output": "sector_rotation",
    "cost": "cpu",
    "tags": ["行业", "轮动", "资金流", "板块", "同花顺"],
    "description": "行业轮动分析：~90行业涨跌/资金流/成交排名+轮动信号",
    "layer": "data",
    "priority": 3,
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 缓存
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_sector_cache = {}
_CACHE_TTL = 1800  # 30 分钟


def _get_cached(key, ttl=_CACHE_TTL):
    """从缓存取数据"""
    entry = _sector_cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None


def _set_cached(key, data):
    """写入缓存"""
    _sector_cache[key] = {"data": data, "ts": time.time()}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 核心数据获取
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_sector_ranking() -> dict:
    """获取行业板块涨跌排名 + 资金流排名

    Returns:
        {
            "available": True,
            "source": "ths",
            "total_sectors": 90,
            "top_gainers": [{板块, 涨跌幅, 净流入, 成交额, 领涨股, ...}, ...],
            "top_losers": [{...}, ...],
            "top_inflow": [{...}, ...],
            "top_outflow": [{...}, ...],
            "market_breadth": {"up": 50, "down": 30, "flat": 10},
            "rotation_signal": "进攻/防御/均衡/分化",
            "timestamp": "...",
        }
    """
    cached = _get_cached("sector_ranking")
    if cached:
        return cached

    try:
        import akshare as ak
        df = ak.stock_board_industry_summary_ths()

        if df is None or len(df) < 10:
            return {"available": False, "error": "行业数据不足", "source": "ths"}

        # 标准化列名
        col_map = {}
        for c in df.columns:
            cl = c.lower().strip()
            if "板块" in c or "行业" in c or cl == "板块":
                col_map["name"] = c
            elif "涨跌幅" in c:
                col_map["change_pct"] = c
            elif "净流入" in c:
                col_map["net_inflow"] = c
            elif "总成交额" in c or "成交额" in c:
                col_map["turnover"] = c
            elif "总成交量" in c or "成交量" in c:
                col_map["volume"] = c
            elif "上涨家数" in c:
                col_map["up_count"] = c
            elif "下跌家数" in c:
                col_map["down_count"] = c
            elif "领涨股" == c:
                col_map["leader"] = c
            elif "领涨股-涨跌幅" in c:
                col_map["leader_chg"] = c

        if "name" not in col_map or "change_pct" not in col_map:
            return {"available": False, "error": f"列名不匹配: {list(df.columns)}", "source": "ths"}

        # 转数值
        for field in ["change_pct", "net_inflow", "turnover", "volume"]:
            if field in col_map:
                df[col_map[field]] = _safe_float_series(df[col_map[field]])

        # 排序
        df_sorted = df.sort_values(col_map["change_pct"], ascending=False)

        def _row_to_dict(row):
            d = {"name": str(row.get(col_map.get("name", ""), ""))}
            if "change_pct" in col_map:
                d["change_pct"] = round(float(row.get(col_map["change_pct"], 0)), 2)
            if "net_inflow" in col_map:
                d["net_inflow"] = round(float(row.get(col_map["net_inflow"], 0)), 2)
            if "turnover" in col_map:
                d["turnover"] = round(float(row.get(col_map["turnover"], 0)), 2)
            if "up_count" in col_map:
                d["up_count"] = int(row.get(col_map["up_count"], 0))
            if "down_count" in col_map:
                d["down_count"] = int(row.get(col_map["down_count"], 0))
            if "leader" in col_map:
                d["leader"] = str(row.get(col_map["leader"], ""))
            if "leader_chg" in col_map:
                d["leader_chg"] = round(float(row.get(col_map["leader_chg"], 0)), 2)
            return d

        top_gainers = [_row_to_dict(r) for _, r in df_sorted.head(10).iterrows()]
        top_losers = [_row_to_dict(r) for _, r in df_sorted.tail(10).iloc[::-1].iterrows()]

        # 资金流排名
        if "net_inflow" in col_map:
            df_flow = df.sort_values(col_map["net_inflow"], ascending=False)
            top_inflow = [_row_to_dict(r) for _, r in df_flow.head(10).iterrows()]
            top_outflow = [_row_to_dict(r) for _, r in df_flow.tail(10).iloc[::-1].iterrows()]
        else:
            top_inflow = []
            top_outflow = []

        # 市场广度
        total_up = 0
        total_down = 0
        for _, row in df.iterrows():
            up = int(row.get(col_map.get("up_count", ""), 0) or 0)
            dn = int(row.get(col_map.get("down_count", ""), 0) or 0)
            total_up += up
            total_down += dn
        total_stocks = total_up + total_down
        breadth = {
            "up": total_up,
            "down": total_down,
            "total": total_stocks,
            "up_pct": round(total_up / max(total_stocks, 1) * 100, 1),
        }

        # 轮动信号判断
        rotation_signal = _classify_rotation(top_gainers, top_losers, breadth)

        result = {
            "available": True,
            "source": "ths",
            "total_sectors": len(df),
            "top_gainers": top_gainers[:5],
            "top_losers": top_losers[:5],
            "top_inflow": top_inflow[:5],
            "top_outflow": top_outflow[:5],
            "market_breadth": breadth,
            "rotation_signal": rotation_signal,
            "timestamp": datetime.now().isoformat(),
        }

        _set_cached("sector_ranking", result)
        return result

    except Exception as e:
        print(f"[SECTOR] get_sector_ranking 失败: {e}")
        return {"available": False, "error": str(e), "source": "ths"}


def _safe_float_series(series):
    """安全转浮点数"""
    import pandas as pd
    return pd.to_numeric(series, errors="coerce").fillna(0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 轮动信号分类
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 进攻型行业关键词
_OFFENSIVE_SECTORS = {"半导体", "芯片", "元件", "通信设备", "软件开发", "计算机设备",
                      "光伏设备", "电池", "新能源", "汽车", "证券", "互联网"}
# 防御型行业关键词
_DEFENSIVE_SECTORS = {"银行", "电力", "煤炭", "石油", "公用事业", "食品饮料",
                      "中药", "医药", "农牧", "水务", "交运设备", "保险"}


def _classify_rotation(gainers: list, losers: list, breadth: dict) -> dict:
    """分类当前轮动状态

    Returns:
        {
            "style": "进攻/防御/均衡/分化",
            "description": "...",
            "offensive_strength": 0-100,
            "defensive_strength": 0-100,
        }
    """
    # 计算 TOP5 涨幅行业中 进攻/防御 占比
    off_count = 0
    def_count = 0
    for g in gainers[:5]:
        name = g.get("name", "")
        if any(k in name for k in _OFFENSIVE_SECTORS):
            off_count += 1
        if any(k in name for k in _DEFENSIVE_SECTORS):
            def_count += 1

    # 市场广度辅助判断
    up_pct = breadth.get("up_pct", 50)

    if off_count >= 3 and up_pct > 55:
        style = "进攻"
        desc = f"进攻型行业领涨({off_count}/5)，市场情绪积极({up_pct:.0f}%上涨)"
    elif def_count >= 3:
        style = "防御"
        desc = f"防御型行业领涨({def_count}/5)，资金偏向避险"
    elif up_pct < 40:
        style = "防御"
        desc = f"多数行业下跌({up_pct:.0f}%上涨)，防御氛围浓厚"
    elif abs(off_count - def_count) <= 1 and 45 < up_pct < 55:
        style = "均衡"
        desc = f"进攻({off_count})防御({def_count})均衡，市场观望"
    else:
        style = "分化"
        desc = f"行业分化，进攻{off_count}防御{def_count}，上涨{up_pct:.0f}%"

    return {
        "style": style,
        "description": desc,
        "offensive_count": off_count,
        "defensive_count": def_count,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pipeline enrich()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def enrich(ctx):
    """Pipeline Layer2 自动调用 — 行业轮动数据注入 DecisionContext

    注入:
    1. TOP5 涨幅/跌幅行业
    2. TOP5 资金净流入/流出行业
    3. 市场广度（上涨/下跌家数）
    4. 轮动信号（进攻/防御/均衡/分化）
    """
    try:
        data = get_sector_ranking()

        if data.get("available"):
            rotation = data.get("rotation_signal", {})
            style = rotation.get("style", "均衡")

            # 方向判断
            if style == "进攻":
                direction = "bullish"
                score = 0.65
            elif style == "防御":
                direction = "bearish"
                score = 0.35
            elif style == "分化":
                direction = "neutral"
                score = 0.45
            else:
                direction = "neutral"
                score = 0.5

            breadth = data.get("market_breadth", {})
            top_g = data.get("top_gainers", [])
            top_l = data.get("top_losers", [])
            top_in = data.get("top_inflow", [])

            # 构造简洁的 detail
            g_names = ",".join(g["name"] for g in top_g[:3])
            l_names = ",".join(l["name"] for l in top_l[:3])
            in_names = ",".join(i["name"] for i in top_in[:3])

            detail_parts = [
                f"轮动:{style}",
                f"涨幅TOP3:{g_names}",
                f"跌幅TOP3:{l_names}",
            ]
            if in_names:
                detail_parts.append(f"资金流入TOP3:{in_names}")
            detail_parts.append(
                f"广度:{breadth.get('up',0)}涨/{breadth.get('down',0)}跌({breadth.get('up_pct',50):.0f}%)"
            )

            ctx.modules_results["sector_rotation"] = {
                "direction": direction,
                "score": score,
                "confidence": min(70, 30 + data.get("total_sectors", 0)),
                "available": True,
                "detail": " | ".join(detail_parts),
                "rotation_style": style,
                "rotation_desc": rotation.get("description", ""),
                "top_gainers": top_g[:5],
                "top_losers": top_l[:5],
                "top_inflow": top_in[:5],
                "top_outflow": data.get("top_outflow", [])[:5],
                "market_breadth": breadth,
                "total_sectors": data.get("total_sectors", 0),
            }
        else:
            ctx.modules_results["sector_rotation"] = {
                "available": False,
                "error": data.get("error", "unknown"),
                "direction": "neutral",
                "score": 0.5,
            }

        if "sector_rotation" not in ctx.modules_called:
            ctx.modules_called.append("sector_rotation")

    except Exception as e:
        print(f"[SECTOR] enrich failed: {e}")
        ctx.modules_results["sector_rotation"] = {
            "available": False,
            "error": str(e),
            "direction": "neutral",
            "score": 0.5,
        }

    return ctx
