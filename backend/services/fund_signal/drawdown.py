"""
P0-3 回撤档位状态机。

设计依据：docs/design/signal-scout-fund-account.md §3.4 / §4.2

铁律（B8，跨 portfolio/render 必须一致）：
  * 触发基准 = 成本净值：dd_cost = unit_nav / cost_nav - 1。
  * adj_nav 只用于「定位 60 日高点日期」：argmax(adj_nav) 后取该日 unit_nav 计价。
  * 禁止 adj_nav / cost_nav（002163 会得出 +161.37% 而非 +11.13%）。

状态机（RUNGS = [-20, -30, -40]，config.DRAWDOWN_RUNGS）：
  * deepest = 最大 i 使 dd_cost*100 <= RUNGS[i]，否则 -1
  * deepest > state.rung          → 触发（rung = deepest），推 1 条（按天合并）
  * dd_cost*100 > RUNGS[rung]+5.0 → 重新武装（rung -= 1），不推送
  * 其余                          → 无动作（档位内震荡不重推）
冷启动：状态文件不存在 → 写 rung，cold_start=True，不推送。
合并：同一天所有触发项合成 1 条 Signal，正文按 dd_cost 升序取 Top3。
"""
import math

from services.fund_signal import state
from services.fund_signal.config import (
    DRAWDOWN_RUNGS,
    DRAWDOWN_REARM_BUFFER_PCT,
    DRAWDOWN_ROLL_LOOKBACK_DAYS,
)


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _fmt_md(value) -> str:
    """YYYYMMDD / YYYY-MM-DD → MM-DD；脏值返回 ""。"""
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[4:6]}-{s[6:8]}"
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s[5:]
    return ""


def _deepest_rung(dd_pct: float) -> int:
    """返回最大 i 使 dd_pct <= RUNGS[i]，否则 -1。dd_pct 为百分数本体（负值）。"""
    deepest = -1
    for i, rung in enumerate(DRAWDOWN_RUNGS):
        if dd_pct <= rung:
            deepest = i
    return deepest


def _roll_ref(pos) -> tuple:
    """「相对近 60 日高点」参考口径。

    正确算法：用 adj_nav 定位高点【日期】，再取该日 unit_nav 计价。
    返回 (dd_roll_pct, high_date_md, high_unit_nav)；数据不足返回 (None, "", None)。
    """
    navs = pos.nav_history or []
    if not navs:
        return (None, "", None)
    window = navs[-DRAWDOWN_ROLL_LOOKBACK_DAYS:]
    high_row = max(window, key=lambda r: _safe_float(r.get("adj_nav"), 0.0))
    high_unit = _safe_float(high_row.get("unit_nav"), 0.0)
    if high_unit <= 0 or pos.unit_nav <= 0:
        return (None, "", None)
    dd_roll = (pos.unit_nav / high_unit - 1.0) * 100.0
    return (round(dd_roll, 2), _fmt_md(high_row.get("nav_date")), round(high_unit, 4))


def collect(user_id: str, positions: list) -> list:
    """P0-3 采集。返回 [] 或 1 条按天合并的 Signal。"""
    if not positions:
        return []

    st = state.load(user_id, state.DRAWDOWN_STATE)
    cold_start = not st

    # 计算每只基金的触发口径 dd_cost 与档位。
    items: list = []
    for p in positions:
        if p.cost_nav <= 0 or p.unit_nav <= 0:
            print(f"[FUND_SIGNAL] {p.code} 缺 unit_nav/cost_nav，本轮不计入回撤触发")
            continue
        dd_cost = (p.unit_nav / p.cost_nav - 1.0) * 100.0  # 触发只用 unit_nav
        roll = _roll_ref(p)
        items.append({
            "code": p.code,
            "name": p.name,
            "dd_cost_pct": round(dd_cost, 2),
            "rung": _deepest_rung(dd_cost),
            "dd_roll_pct": roll[0],
            "high_date": roll[1],
            "high_unit_nav": roll[2],
            "nav_date": _fmt_md(p.nav_date),
            "cost_nav": round(p.cost_nav, 4),
            "unit_nav": round(p.unit_nav, 4),
            "weight_mv": p.weight_mv,
            "is_qdii": p.is_qdii,
        })

    if cold_start:
        # 冷启动静默：写当期档位，不推送。
        state.save(user_id, state.DRAWDOWN_STATE, {
            "cold_start": True,
            "rungs": {it["code"]: it["rung"] for it in items},
        })
        return []

    prev_rungs = st.get("rungs", {}) if isinstance(st, dict) else {}
    triggered: list = []
    new_rungs: dict = {}

    for it in items:
        code = it["code"]
        dd = it["dd_cost_pct"]
        deepest = it["rung"]
        prev = prev_rungs.get(code, -1)

        if deepest > prev:
            # 跌破更深档 → 触发
            triggered.append(it)
            new_rungs[code] = deepest
        elif prev >= 0 and dd > DRAWDOWN_RUNGS[prev] + DRAWDOWN_REARM_BUFFER_PCT:
            # 回升到「档位线 + 5pct」上方 → 重新武装（降一档），不推送
            new_rungs[code] = prev - 1 if prev - 1 >= -1 else -1
        else:
            # 档位内震荡 / 已回不到再触发位 → 保持
            new_rungs[code] = prev

    state.save(user_id, state.DRAWDOWN_STATE, {
        "cold_start": False,
        "rungs": new_rungs,
    })

    if not triggered:
        return []

    from services.fund_signal.render import render_drawdown
    sig = render_drawdown(triggered, positions)
    return [sig] if sig else []
