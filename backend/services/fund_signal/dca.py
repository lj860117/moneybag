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

⚠️ 为什么第一条不能「修正」成市值加权（2026-09 专项核对结论，改动前必读）：

  ┌── 第 0 步（先做，否则下面全是白算）：先查净值日期，再谈口径 ───────────────
  │ 下面的恒等式只能解释【同一天净值】下 A 与 B 的差。
  │ 不同日期的两个数字对比，先把净值日期对齐再说 —— 日期不一致时，
  │ 恒等式既不能证实也不能证伪任何东西。
  │
  │ 本项目真实案例（已犯过一次，别再犯）：
  │   调研脚本 -2.21% 与线上 -3.99%，看着差 1.78pct 像口径问题，
  │   实际**两者都是口径 A**，差异 100% 来自净值日期漂移
  │   （调研用 09-04 净值、线上用 09-05；6 只境内基金一个交易日普跌
  │    1.8~4.1 pct，2 只 QDII 净值还 T+1/T+2 滞后）。
  │
  │ 判据因此是三步，不是一步：
  │   1. 先确认对比双方用的是【同一天净值】
  │      （QDII 尤其注意：它可能比境内基金滞后 1~2 天，同一份快照里
  │       各基金的 nav_date 本来就不齐，见 _build_snapshot 取 max）
  │   2. 日期一致后，再算 Var_c(r)/(1+A) 对一下
  │   3. 对得上 → 口径差异，不改 `_pct(total_mv, total_cost)`；
  │      对不上 → 才是实现问题，再查
  │
  │ 🚫 陷阱：两个数字【数值接近】推不出【同口径】。
  │    既不同口径、也不同日期时，接近纯属巧合 —— 本项目已按这个错误
  │    推断做过一轮排查（-2.21% 口径A/09-04 与 -2.05% 口径B/09-05 只差
  │    0.16pct，被误当成同一口径），结论是白查。
  └─────────────────────────────────────────────────────────────────────────

  两个口径在数学上**必然不等**。看到「调研脚本算出来和线上对不上」不是 bug，
  先用下面的恒等式对一下（前提是第 0 步的日期已经对齐），对得上就是口径差异。

    口径 A（本模块 / 生产口径，成本加权）= Σmv / Σcost - 1 = Σ(ωc_i × r_i)
    口径 B（基金评价口径，市值加权）      = Σ(ωm_i × r_i)
    恒等式：B - A = Var_c(r) / (1 + A)  ≥ 0

  记 r_i = unit_nav_i / cost_nav_i - 1，cost_i = shares_i × cost_nav_i，
    ωc_i = cost_i / Σcost（成本权重），ωm_i = mv_i / Σmv（市值权重），
    Var_c(r) = Σ(ωc_i × r_i²) - A²，即 r_i 按【成本】权重的加权方差。

  推导（简写，全程用 ωm_i = ωc_i × (1+r_i)/(1+A) 这一个代换）：
    A - B = Σ(ωc_i - ωm_i) × r_i
          = Σ ωc_i × r_i × [1 - (1+r_i)/(1+A)]
          = [A × Σ(ωc_i × r_i) - Σ(ωc_i × r_i²)] / (1+A)      # Σ(ωc_i × r_i) = A
          = [A² - E_c(r²)] / (1+A)
          = -Var_c(r) / (1+A)                                   # Var_c(r) = E_c(r²) - A²

  业务含义（决定用哪个）：
    A 是用户真实盈亏 —— 投入多少 vs 现在值多少，赢家不放大，用户能据以决策。
    B 按当前市值放大赢家权重，**系统性高估收益**，Var_c(r) 越大高估越多。
    组合未归零时 1+A > 0 且 Var_c(r) ≥ 0，故 **B ≥ A 恒成立**，
    等号仅在所有持仓 r_i 完全相同时成立（退化工况，见回归测试）。
  → 给用户看的收益率必须是 A；B 只能作「基金评价」横向对比，不能用于展示盈亏。

  实测锚点（LeiJiang 组合 8 只基金，2026-09 当时的实测值）：
    A = -3.99% vs B = -2.05%，差 1.9464 pct；
    公式预测 Var_c(r)/(1+A) = 1.9450 pct，残差 0.0014 pct ——
    残差仅来自 portfolio._largest_remainder_weights 把 weight_mv 取整到 2 位小数。
    另已验证 A == Σ(ωc_i × r_i) 精确成立（True）。
  ⚠️ 上面两个百分数是「当时的实测值」而非断言值：用户重录持仓后必然变化，
    不要把它们写进任何测试的 assert。

  可操作判据：将来有人报告「两个算法差 X pct」时，先算 Var_c(r)/(1+A) 对一下 ——
    对得上 → 口径差异，不是 bug，不要改本文件的 `_pct(total_mv, total_cost)`；
    对不上 → 才是实现问题，再查。

  回归锁：tests/test_fund_signal_combo_caliber.py（恒等式、方向性、Var=0 退化）。
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
