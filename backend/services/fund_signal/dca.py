"""
P1-1 定投前瞻（每月 24 日快照，幂等）。

设计依据：docs/design/signal-scout-fund-account.md §3.4 / §4.3

规则：
  * 触发日 = 每月 24 日；非交易日（signal_scout.is_trading_day）向前顺延。
  * 幂等：state.last_push_month = "YYYY-MM"，同月已推过直接返回 []。
  * 冷启动宽限：上线首月若距 24 日不足 3 天（DCA_SKIP_LAUNCH_GRACE_DAYS）→ 跳过本月。

口径（铁律 B8）：
  * 组合整体相对成本 = 总市值 / 总成本 - 1，不是各基金收益率的加权平均。
  * 单只基金相对成本 = unit_nav / cost_nav - 1，禁止用 adj_nav。
"""
from datetime import date

from services.fund_signal import state
from services.fund_signal.config import (
    DCA_TRIGGER_DAY,
    DCA_SKIP_LAUNCH_GRACE_DAYS,
)


def resolve_trigger_day(today=None) -> date:
    """本月触发日：24 日；非交易日向前顺延（周休/法定假日）。"""
    from services.signal_scout import is_trading_day
    if today is None:
        today = date.today()
    d = date(today.year, today.month, DCA_TRIGGER_DAY)
    while not is_trading_day(d):
        d = date.fromordinal(d.toordinal() - 1)
    return d


def _pct(numerator: float, denominator: float):
    """(a/b - 1) * 100，保留 2 位；分母 <=0 返回 None。"""
    if denominator <= 0:
        return None
    return round((numerator / denominator - 1.0) * 100.0, 2)


def _build_snapshot(positions, xray) -> dict:
    """汇总组合与单只基金的相对成本、浮盈浮亏、集中度。"""
    total_mv = sum(p.market_value for p in positions)
    total_cost = sum(p.shares * p.cost_nav for p in positions)

    rows = []
    for p in positions:
        rows.append({
            "code": p.code,
            "name": p.name,
            "cost_nav": round(p.cost_nav, 4),
            "unit_nav": round(p.unit_nav, 4),
            "dd_cost_pct": _pct(p.unit_nav, p.cost_nav),
            "weight_mv": p.weight_mv,
            "is_qdii": p.is_qdii,
        })
    # dd 升序（最深回撤在前）；None 视为最浅（排末尾）。
    rows_sorted = sorted(rows, key=lambda r: r["dd_cost_pct"] if r["dd_cost_pct"] is not None else 999.0)

    gainers = [r for r in rows if r["dd_cost_pct"] is not None and r["dd_cost_pct"] > 0]
    losers = [r for r in rows if r["dd_cost_pct"] is not None and r["dd_cost_pct"] <= 0]

    return {
        "combo_cost_pct": _pct(total_mv, total_cost),
        "rows": rows_sorted,
        "deepest": rows_sorted[0] if rows_sorted else None,
        "best": rows_sorted[-1] if rows_sorted else None,
        "gainers": gainers,
        "losers": losers,
        "xray": xray,
        "nav_date": max((p.nav_date for p in positions), default=""),
    }


def collect(user_id: str, positions: list, xray=None, today=None) -> list:
    """P1-1 采集。非触发日 / 同月已推 / 冷启动宽限 → 返回 []。"""
    if today is None:
        today = date.today()
    month = today.strftime("%Y-%m")

    st = state.load(user_id, state.DCA_STATE)
    if st.get("last_push_month") == month:
        return []  # 幂等：同月只推 1 次

    if today != resolve_trigger_day(today):
        return []  # 今天不是（顺延后的）触发日

    # 冷启动宽限：距 24 日不足 3 天（含 24 日当天）→ 跳过本月
    if not st and (DCA_TRIGGER_DAY - today.day) < DCA_SKIP_LAUNCH_GRACE_DAYS:
        state.save(user_id, state.DCA_STATE, {"last_push_month": month, "skipped": True})
        return []

    if not positions:
        return []

    snap = _build_snapshot(positions, xray)
    state.save(user_id, state.DCA_STATE, {"last_push_month": month})

    from services.fund_signal.render import render_dca
    sig = render_dca(snap, positions)
    return [sig] if sig else []
