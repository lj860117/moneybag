"""
钱袋子 — 基金智能筛选
从全量基金排行中多维打分筛选 TOP 推荐
参考：豆包方案（指增基金评分、回撤/规模/费率/超额排序）
"""

# ---- V4 底座：MODULE_META ----
MODULE_META = {
    "name": "fund_screen",
    "scope": "public",
    "input": ['fund_type', 'sort_by'],
    "output": "screened_funds",
    "cost": "cpu",
    "tags": ['基金筛选', '多维打分'],
    "description": "基金智能筛选：多维打分(收益+稳定+费率)排序TOP推荐",
    "layer": "analysis",
    "priority": 3,
}
import time
from config import FUND_RANK_CACHE_TTL
from infra.cache import MemoryCache
from services.fund_rank import _load_fund_rank_data
from services.utils import find_col as _find_col, safe_float as _safe_float, parse_fee as _parse_fee

_fund_screen_cache = MemoryCache(default_ttl=FUND_RANK_CACHE_TTL)


def screen_funds(
    fund_type: str = "all",
    sort_by: str = "score",
    top_n: int = 20,
    user_id: str = "",
) -> dict:
    """
    多维度基金筛选（V2：含质量过滤 + 回撤惩罚 + 用户持仓去重）
    fund_type: all / stock / bond / index / hybrid / qdii
    sort_by: score / 1y / 3y / ytd
    top_n: 返回前N只
    """
    cache_key = f"fund_screen_{fund_type}_{sort_by}_{top_n}_{user_id}"
    now = time.time()
    cached = _fund_screen_cache.get(cache_key)
    if cached is not None:
        return cached

    rank_data = _load_fund_rank_data()
    if not rank_data:
        return {"funds": [], "total": 0, "error": "基金排行数据暂不可用"}

    # 加载 Tushare fund_rank 数据（含 list_date / issue_amount）
    ts_rank_map = _load_ts_rank_map()

    candidates = []
    excluded_count = 0

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

            # ========== 质量硬过滤（V2 新增）==========
            ts_info = ts_rank_map.get(code, {})

            # 过滤1：近1年涨幅 > 100% 的极端品种
            if r1y > 100:
                excluded_count += 1
                continue

            # 过滤2：近3月 > 40% 的短期过热品种（追入大概率站岗）
            if r3m is not None and r3m > 40:
                excluded_count += 1
                continue

            # 过滤3：基金成立不足2年（新基金没有足够历史验证）
            list_date = ts_info.get("list_date", "")
            if list_date:
                try:
                    from datetime import datetime as _dt
                    fund_age_days = (now - _dt.strptime(list_date, "%Y%m%d").timestamp()) / 86400
                    if fund_age_days < 730:  # < 2 年
                        excluded_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # 过滤4：发行规模 < 2 亿份（小盘容易操纵/清盘风险）
            issue_amount = ts_info.get("issue_amount")
            if issue_amount is not None:
                try:
                    if float(issue_amount) < 2.0:
                        excluded_count += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # ========== 新评分公式（V2）==========
            score = _compute_quality_score(r1y, r3y, r6m, r3m, fee, list_date, issue_amount)

            # 质量标签
            quality_tags = _compute_quality_tags(r1y, r3m, r6m, list_date, issue_amount)

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
                "quality_tags": quality_tags,
            })
        except Exception:
            continue

    # 用户持仓去重（同类降权）
    if user_id:
        candidates = _apply_user_dedup(candidates, user_id)

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
        "excluded_count": excluded_count,
        "filter": fund_type,
        "sort": sort_by,
        "quality_note": f"已过滤 {excluded_count} 只低质量基金（过热/小盘/新基/极端涨幅）",
    }
    _fund_screen_cache.set(cache_key, result, ttl=FUND_RANK_CACHE_TTL)
    return result


def _compute_quality_score(r1y, r3y, r6m, r3m, fee, list_date, issue_amount) -> float:
    """V2 评分公式：收益(40%) + 稳定性(30%) + 费率(10%) + 成熟度(20%)"""
    score = 0

    # ---- 收益维度 40% ----
    # 近1年占一半，长期占更多
    score += min(max(r1y, -50), 80) * 0.20  # r1y 上限80（防止暴涨基金霸榜）
    if r3y is not None:
        r3y_ann = r3y / 3  # 年化
        score += min(max(r3y_ann, -20), 40) * 0.12
    if r6m is not None:
        score += min(max(r6m, -20), 40) * 0.08

    # ---- 稳定性维度 30% ----
    periods = [x for x in [r3m, r6m, r1y] if x is not None]
    if len(periods) >= 2:
        # 所有周期都为正 = 稳定上涨
        all_pos = all(x > 0 for x in periods)
        if all_pos:
            score += 6

        # 回撤代理惩罚：1年涨但3月跌 = 中间有过大回撤
        if r3m is not None and r1y > 0 and r3m < -5:
            drawdown_proxy = abs(r3m)
            score -= min(drawdown_proxy * 0.3, 8)  # 最多扣8分

        # 过热惩罚：近3月涨幅过大 = 追入风险高
        if r3m is not None and r3m > 20:
            overheat_penalty = (r3m - 20) * 0.2  # 每超20%一个点扣0.2
            score -= min(overheat_penalty, 6)  # 最多扣6分

        # 波动惩罚：长短期收益差距大
        spread = max(periods) - min(periods)
        if spread > 60:
            score -= 6
        elif spread > 40:
            score -= 3

    # ---- 费率维度 10% ----
    fee_pct = _parse_fee(fee)
    if fee_pct is not None:
        if fee_pct < 0.15:
            score += 4  # 0 费率（C类）大加分
        elif fee_pct < 0.5:
            score += 2
        elif fee_pct > 1.5:
            score -= 3

    # ---- 成熟度维度 20% ----
    if list_date:
        try:
            from datetime import datetime as _dt
            age_years = (time.time() - _dt.strptime(list_date, "%Y%m%d").timestamp()) / (365.25 * 86400)
            if age_years >= 5:
                score += 5  # 老基金加分
            elif age_years >= 3:
                score += 3
        except (ValueError, TypeError):
            pass

    # 规模适中加分（5-200亿最优区间）
    if issue_amount is not None:
        try:
            amt = float(issue_amount)
            if 5 <= amt <= 200:
                score += 3
            elif amt > 500:
                score -= 2  # 规模过大（规模诅咒）
        except (ValueError, TypeError):
            pass

    return score


def _compute_quality_tags(r1y, r3m, r6m, list_date, issue_amount) -> list:
    """生成质量标签（前端展示用）"""
    tags = []
    if list_date:
        try:
            from datetime import datetime as _dt
            age_years = (time.time() - _dt.strptime(list_date, "%Y%m%d").timestamp()) / (365.25 * 86400)
            if age_years >= 5:
                tags.append("🏛️ 老牌基金")
        except (ValueError, TypeError):
            pass

    if issue_amount is not None:
        try:
            amt = float(issue_amount)
            if 10 <= amt <= 100:
                tags.append("📐 规模适中")
        except (ValueError, TypeError):
            pass

    # 收益一致性
    periods = [x for x in [r3m, r6m, r1y] if x is not None]
    if len(periods) >= 2 and all(x > 0 for x in periods):
        tags.append("📈 持续盈利")

    return tags


def _load_ts_rank_map() -> dict:
    """加载 Tushare fund_rank_ts.json 中的质量字段"""
    import json
    from pathlib import Path
    import os

    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    rank_file = data_dir / "fund_rank_ts.json"
    if not rank_file.exists():
        # 尝试相对路径
        rank_file = Path("data") / "fund_rank_ts.json"
    if not rank_file.exists():
        return {}

    try:
        data = json.loads(rank_file.read_text(encoding="utf-8"))
        ranks = data.get("ranks", {})
        result = {}
        for category_list in ranks.values():
            if not isinstance(category_list, list):
                continue
            for item in category_list:
                code = item.get("code", "")
                if code and (item.get("list_date") or item.get("issue_amount")):
                    result[code] = {
                        "list_date": item.get("list_date", ""),
                        "issue_amount": item.get("issue_amount"),
                    }
        return result
    except Exception:
        return {}


def _apply_user_dedup(candidates: list, user_id: str) -> list:
    """用户持仓去重：已持有同类基金的降权50%"""
    try:
        from services.fund_monitor import load_fund_holdings
        user_funds = load_fund_holdings(user_id)
        if not user_funds:
            return candidates

        # 提取用户持有基金的关键词（从名字提取类型）
        user_keywords = set()
        _TYPE_KEYWORDS = ["科技", "半导体", "新能源", "医药", "消费", "金融", "军工",
                          "QDII", "债", "指数", "ETF", "混合", "黄金", "港股", "美股"]
        for f in user_funds:
            name = f.get("name", "")
            for kw in _TYPE_KEYWORDS:
                if kw in name:
                    user_keywords.add(kw)

        if not user_keywords:
            return candidates

        # 同类基金降权
        for c in candidates:
            name = c.get("name", "")
            overlap = sum(1 for kw in user_keywords if kw in name)
            if overlap > 0:
                c["score"] = c["score"] * 0.5  # 同类降权50%
                if "quality_tags" not in c:
                    c["quality_tags"] = []
                c["quality_tags"].append("⚠️ 与持仓重复")

        return candidates
    except Exception:
        return candidates

