"""
钱袋子 — 基金智能筛选
从全量基金排行中多维打分筛选 TOP 推荐
参考：豆包方案（指增基金评分、回撤/规模/费率/超额排序）
"""
import time
from config import FUND_RANK_CACHE_TTL
from services.fund_rank import _load_fund_rank_data, _fund_rank_cache


def screen_funds(
    fund_type: str = "all",
    sort_by: str = "score",
    top_n: int = 20,
) -> dict:
    """
    多维度基金筛选
    fund_type: all / stock / bond / index / hybrid / qdii
    sort_by: score / 1y / 3y / ytd
    top_n: 返回前N只
    """
    cache_key = f"fund_screen_{fund_type}_{sort_by}_{top_n}"
    now = time.time()
    if cache_key in _fund_rank_cache and now - _fund_rank_cache[cache_key]["ts"] < FUND_RANK_CACHE_TTL:
        return _fund_rank_cache[cache_key]["data"]

    rank_data = _load_fund_rank_data()
    if not rank_data:
        return {"funds": [], "total": 0, "error": "基金排行数据暂不可用"}

    # 遍历所有基金，提取关键指标
    candidates = []
    for code, row in rank_data.items():
        try:
            cols = list(row.index) if hasattr(row, "index") else []
            name = str(row.get(_find_col(cols, ["简称", "名称"]) or cols[1], ""))

            # 类型过滤
            if fund_type != "all":
                if fund_type == "stock" and not any(k in name for k in ["股票", "指数", "沪深", "中证", "创业", "科创", "ETF联接"]):
                    continue
                elif fund_type == "bond" and not any(k in name for k in ["债", "利率", "信用"]):
                    continue
                elif fund_type == "index" and not any(k in name for k in ["指数", "ETF联接", "沪深300", "中证500", "中证1000"]):
                    continue
                elif fund_type == "qdii" and "QDII" not in name and "标普" not in name and "纳斯达克" not in name:
                    continue

            # 提取收益率
            r1y = _safe_float(row.get(_find_col(cols, ["近1年"]), None))
            r3y = _safe_float(row.get(_find_col(cols, ["近3年"]), None))
            r6m = _safe_float(row.get(_find_col(cols, ["近6月"]), None))
            r3m = _safe_float(row.get(_find_col(cols, ["近3月"]), None))
            rytd = _safe_float(row.get(_find_col(cols, ["今年来"]), None))
            fee = str(row.get(_find_col(cols, ["手续费"]), ""))

            # 至少有1年收益率才纳入
            if r1y is None:
                continue

            # 多维评分
            score = 0
            # 近1年收益占35%
            score += min(max(r1y, -50), 100) * 0.35
            # 近3年收益占25%
            if r3y is not None:
                score += min(max(r3y / 3, -30), 50) * 0.25  # 年化
            # 近6月收益占20%（短期动量）
            if r6m is not None:
                score += min(max(r6m, -30), 50) * 0.20
            # 近3月收益占10%
            if r3m is not None:
                score += min(max(r3m, -20), 30) * 0.10
            # 费率扣分：费率越低越好
            fee_pct = _parse_fee(fee)
            if fee_pct is not None and fee_pct < 0.5:
                score += 2  # 低费率加分
            elif fee_pct is not None and fee_pct > 1.5:
                score -= 2  # 高费率扣分

            candidates.append({
                "code": code,
                "name": name,
                "score": round(score, 2),
                "returns": {
                    "3m": r3m,
                    "6m": r6m,
                    "1y": r1y,
                    "3y": r3y,
                    "ytd": rytd,
                },
                "fee": fee,
            })
        except Exception:
            continue

    # 排序
    if sort_by == "1y":
        candidates.sort(key=lambda x: x["returns"].get("1y") or -999, reverse=True)
    elif sort_by == "3y":
        candidates.sort(key=lambda x: x["returns"].get("3y") or -999, reverse=True)
    elif sort_by == "ytd":
        candidates.sort(key=lambda x: x["returns"].get("ytd") or -999, reverse=True)
    else:
        candidates.sort(key=lambda x: x["score"], reverse=True)

    top = candidates[:top_n]
    result = {
        "funds": top,
        "total": len(candidates),
        "filter": fund_type,
        "sort": sort_by,
    }
    _fund_rank_cache[cache_key] = {"data": result, "ts": time.time()}
    return result


def _find_col(cols, keywords):
    """模糊匹配列名"""
    for kw in keywords:
        for c in cols:
            if kw in str(c):
                return c
    return None


def _safe_float(val):
    """安全转float"""
    try:
        v = float(val)
        if v != v:  # NaN
            return None
        return round(v, 2)
    except (ValueError, TypeError):
        return None


def _parse_fee(fee_str: str):
    """从费率字符串中提取数值，如 '0.15%' → 0.15"""
    try:
        s = str(fee_str).replace("%", "").strip()
        return float(s)
    except (ValueError, TypeError):
        return None
