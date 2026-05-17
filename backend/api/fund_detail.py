"""
基金详情 + 经理规模战绩 + 政策受益映射 API
v9.3.4 新增
"""
from __future__ import annotations

import time
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter

router = APIRouter()

# 简易内存缓存
_detail_cache: dict = {}
_CACHE_TTL = 3600 * 6  # 6 小时


def _get_cached(key: str):
    entry = _detail_cache.get(key)
    if entry and time.time() - entry["t"] < _CACHE_TTL:
        return entry["v"]
    return None


def _set_cached(key: str, val):
    _detail_cache[key] = {"v": val, "t": time.time()}


# ──────────────────────────────────────────────────────────
# Phase 1: 基金详情（经理 + 基本信息 + 持仓）
# ──────────────────────────────────────────────────────────
@router.get("/api/fund/detail/{code}")
def fund_detail(code: str):
    """基金完整详情：基本信息 + 经理 + 持仓 + 收益"""
    cache_key = f"fund_detail_{code}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    from services.tushare_data import get_fund_manager, get_fund_portfolio, get_fund_share
    from services.fund_rank import get_fund_dynamic_info

    # 基础信息（收益率/费率/净值）
    info = get_fund_dynamic_info(code)

    # 基金经理
    mgr_data = get_fund_manager(code)
    manager = None
    if mgr_data.get("available") and mgr_data.get("managers"):
        m = mgr_data["managers"][0]
        begin = m.get("begin_date", "")
        tenure_years = 0
        if begin:
            try:
                bd = datetime.strptime(begin, "%Y%m%d")
                tenure_years = round((datetime.now() - bd).days / 365.25, 1)
            except Exception:
                pass
        manager = {
            "name": m.get("name", "未知"),
            "gender": m.get("gender", ""),
            "begin_date": begin,
            "tenure_years": tenure_years,
            "resume": (m.get("resume") or "")[:200],
        }

    # 基金规模（份额 × 净值）
    scale_billion = None
    ts_code = code if "." in code else f"{code}.OF"
    share_data = get_fund_share(ts_code, days=10)
    if share_data.get("available") and info.get("nav"):
        shares_yi = share_data.get("shares_latest", 0)  # 亿份
        nav = info["nav"]
        scale_billion = round(shares_yi * nav / 10, 2)  # 亿元

    # 持仓明细
    portfolio_data = get_fund_portfolio(code)
    top_holdings = []
    if portfolio_data.get("available"):
        top_holdings = portfolio_data.get("top_holdings", [])[:5]

    result = {
        "code": code,
        "name": info.get("name", code),
        "nav": info.get("nav"),
        "fee": info.get("fee", ""),
        "scale_billion": scale_billion,
        "returns": info.get("returns", {}),
        "manager": manager,
        "top_holdings": [
            {"symbol": h.get("symbol", ""), "ratio": h.get("stk_mkv_ratio", "")}
            for h in top_holdings
        ],
        "source": "tushare+天天基金",
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _set_cached(cache_key, result)
    return result


# ──────────────────────────────────────────────────────────
# Phase 2: 经理规模-业绩对照（规模诅咒检测）
# ──────────────────────────────────────────────────────────
@router.get("/api/fund/manager-track/{code}")
def fund_manager_track(code: str):
    """基金经理在不同规模阶段的业绩对照"""
    cache_key = f"fund_mgr_track_{code}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    from services.tushare_data import get_fund_manager, _call_tushare

    # 获取经理信息
    mgr_data = get_fund_manager(code)
    if not mgr_data.get("available") or not mgr_data.get("managers"):
        return {"available": False, "reason": "未找到基金经理信息"}

    manager_name = mgr_data["managers"][0].get("name", "未知")
    begin_date = mgr_data["managers"][0].get("begin_date", "")

    # 拉取净值历史（从任期开始到现在，按季度采样）
    ts_code = code if "." in code else f"{code}.OF"
    start = begin_date or (datetime.now() - timedelta(days=365 * 5)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    nav_rows = _call_tushare(
        "fund_nav", {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,ann_date,nav_date,unit_nav,accum_nav"
    )

    # 拉取份额历史
    share_rows = _call_tushare(
        "fund_share", {"ts_code": ts_code, "start_date": start, "end_date": end},
        "ts_code,trade_date,fd_share,total_share"
    )

    if not nav_rows or len(nav_rows) < 10:
        return {"available": False, "reason": "净值数据不足", "manager": manager_name}

    # 按季度分组计算
    nav_sorted = sorted(nav_rows, key=lambda r: r.get("nav_date") or r.get("ann_date", ""))
    share_map = {}
    for s in (share_rows or []):
        d = s.get("trade_date", "")
        share_val = float(s.get("fd_share") or s.get("total_share") or 0)
        if d and share_val > 0:
            share_map[d] = share_val

    # 每季度末采样
    track = []
    quarters_seen = set()
    for row in nav_sorted:
        date_str = row.get("nav_date") or row.get("ann_date", "")
        if not date_str or len(date_str) < 8:
            continue
        quarter = date_str[:4] + "Q" + str((int(date_str[4:6]) - 1) // 3 + 1)
        if quarter in quarters_seen:
            continue
        quarters_seen.add(quarter)

        unit_nav = float(row.get("unit_nav") or row.get("accum_nav") or 0)
        if unit_nav <= 0:
            continue

        # 找最近的份额数据
        closest_share = 0
        for sd in sorted(share_map.keys(), key=lambda x: abs(int(x) - int(date_str)))[:1]:
            closest_share = share_map[sd]
            break

        scale_billion = round(closest_share * unit_nav / 1e8 / 10, 2) if closest_share > 0 else None

        track.append({
            "quarter": quarter,
            "date": date_str,
            "nav": round(unit_nav, 4),
            "scale_billion": scale_billion,
        })

    # 计算每季度收益率
    for i in range(1, len(track)):
        prev_nav = track[i - 1]["nav"]
        cur_nav = track[i]["nav"]
        if prev_nav > 0:
            track[i]["quarter_return_pct"] = round((cur_nav - prev_nav) / prev_nav * 100, 2)

    # 过滤掉第一个（没有收益率）和没有规模的
    track_with_data = [t for t in track[1:] if t.get("scale_billion") and t.get("quarter_return_pct") is not None]

    # AI 总结（如果有 LLM 可用）
    verdict = ""
    if track_with_data and len(track_with_data) >= 4:
        # 简单规则判断规模诅咒
        early = track_with_data[:len(track_with_data) // 2]
        late = track_with_data[len(track_with_data) // 2:]
        early_median = sorted([t["quarter_return_pct"] for t in early])[len(early) // 2] if early else 0
        late_median = sorted([t["quarter_return_pct"] for t in late])[len(late) // 2] if late else 0
        early_scale = sum(t["scale_billion"] for t in early if t["scale_billion"]) / max(len(early), 1)
        late_scale = sum(t["scale_billion"] for t in late if t["scale_billion"]) / max(len(late), 1)

        if late_scale > early_scale * 2 and late_median < early_median * 0.5:
            verdict = f"⚠️ 规模诅咒明显：规模从{early_scale:.0f}亿增至{late_scale:.0f}亿后，季度收益中位数从{early_median:.1f}%降至{late_median:.1f}%"
        elif late_scale > early_scale * 1.5 and late_median < early_median:
            verdict = f"🟡 存在规模压力：规模{early_scale:.0f}→{late_scale:.0f}亿，收益略有下降"
        else:
            verdict = f"✅ 规模管理良好：规模{early_scale:.0f}→{late_scale:.0f}亿，收益未明显下滑"

    result = {
        "available": True,
        "manager": manager_name,
        "current_scale_billion": track_with_data[-1]["scale_billion"] if track_with_data else None,
        "track": track_with_data[-12:],  # 最多返回最近 12 个季度
        "verdict": verdict,
        "source": "tushare",
    }
    _set_cached(cache_key, result)
    return result


# ──────────────────────────────────────────────────────────
# Phase 3: 政策 → 受益基金/股票映射
# ──────────────────────────────────────────────────────────
@router.get("/api/policy/beneficiaries")
def policy_beneficiaries(topic: str = "数字基建"):
    """分析某政策主题的受益基金和股票"""
    cache_key = f"policy_benef_{topic}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    # 用 DeepSeek 分析政策 → 受益行业 + 标的
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return {"available": False, "reason": "LLM 不可用"}

    try:
        from services.llm_gateway import LLMGateway
        gw = LLMGateway.instance()

        prompt = f"""你是 A 股行业分析师。用户想了解「{topic}」政策的受益标的。

请按以下 JSON 格式回答（不要 markdown，纯 JSON）：
{{
  "summary": "一段话总结政策规模和核心方向（50字内）",
  "industries": ["受益行业1", "受益行业2", "受益行业3"],
  "funds": [
    {{"name": "基金名称", "code": "6位代码", "reason": "匹配原因（10字内）"}},
    ...最多5只
  ],
  "stocks": [
    {{"name": "公司名称", "code": "6位代码", "reason": "匹配原因（10字内）"}},
    ...最多5只
  ]
}}

要求：
1. 基金优先推 ETF（流动性好、费率低），其次主动基金
2. 股票推行业龙头（市值大、流动性好）
3. 所有代码必须是真实存在的 A 股/场内基金代码
4. 如果不确定代码，宁可不推也不要编造"""

        llm_result = gw.call_sync(
            messages=[{"role": "user", "content": prompt}],
            purpose="policy_beneficiaries",
            max_tokens=800,
        )

        import json as json_mod
        text = llm_result.get("content", "") if isinstance(llm_result, dict) else str(llm_result)
        # 尝试提取 JSON
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

        data = json_mod.loads(text)
        result = {
            "available": True,
            "topic": topic,
            "summary": data.get("summary", ""),
            "industries": data.get("industries", []),
            "funds": data.get("funds", [])[:5],
            "stocks": data.get("stocks", [])[:5],
            "source": "deepseek",
            "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        _set_cached(cache_key, result)
        return result

    except Exception as e:
        return {"available": False, "reason": f"分析失败: {str(e)[:100]}"}
