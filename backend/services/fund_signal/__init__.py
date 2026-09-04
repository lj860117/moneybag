"""
信号侦察 — 纯基金账户专用信号包（C 方案）。

对外唯一入口：`build_signal_pool()`，由 signal_scout.match() 在唯一接缝处调用。

数据流：
  match() → build_signal_pool()（判账户类型）
          → 持股/混合账户：原样 collect()
          → 纯基金账户：_collect_public()（news+technical）+ _collect_fund_signals()
             （P0-1 穿透 / P0-2 经理 / P0-3 回撤 / P1-1 定投）→ budget.gate()

设计依据：docs/design/signal-scout-fund-account.md §3.2
"""
from services.fund_signal import state  # noqa: F401  （提供 state 常量引用）

__all__ = ["build_signal_pool"]


def build_signal_pool(user_id: str, stock_codes: dict, fund_codes: dict) -> list:
    """signal_scout.match() 的唯一接缝。

    ⚠️⚠️ 绕过 collect() 是【条件式】的，不是无条件的：
      - stock_codes 非空（用户持股）→ 原样 collect()，五个采集器一个都不少
      - stock_codes 为空 且 fund_codes 非空（纯基金账户）→ 才跳过三个个股事件采集器

    ⚠️ 分支必须【每次调用时动态求值】，禁止在模块加载时算成常量或用模块级开关缓存：
    用户今天 0 只股票、明天买入股票，行为必须跟着变。
    """
    from services import signal_scout

    try:
        is_pure_fund = (not stock_codes) and bool(fund_codes)
        if not is_pure_fund:
            # 混合账户 / 空账户 / 无持仓：行为与改造前完全一致
            return signal_scout.collect()

        # 纯基金账户：P0-0 —— 只跑 news + technical。
        # unlock / holder_change / fund_flow 一次都不调用，真省下 share_float
        # 那 10 万行网络预算，而不是「跑完再过滤」。
        pool = _collect_public()
        if pool is None:            # 子采集器缺失/被重命名 → 整体降级
            return signal_scout.collect()

        pool.extend(_collect_fund_signals(user_id) or [])

        # 兜底过滤：即便走纯基金路径，也剔除个股事件类信号。
        # 这是 PRD P0-0 验收标准（matched 中不得出现这三类）的最后一道防线。
        blocked = signal_scout._HOLDING_REQUIRED_TYPES
        return [s for s in pool if s.get("type") not in blocked]
    except Exception as e:
        # 信号侦察是旁路，任何异常都不得阻断 match()/Pipeline
        print(f"[FUND_SIGNAL] build_signal_pool failed: {e}，降级回 collect()")
        return signal_scout.collect()


def _collect_public():
    """纯基金账户的公共信号：只跑 news + technical。

    返回 None = 子采集器缺失（可能已被重命名），调用方需整体降级回 collect()。
    """
    from services import signal_scout

    pool: list = []
    for name in ("_collect_news_signals", "_collect_technical_signals"):
        fn = getattr(signal_scout, name, None)
        if fn is None:
            print(f"[FUND_SIGNAL] {name} 不存在（可能已被重命名），降级回 collect()")
            return None
        try:
            pool.extend(fn() or [])
        except Exception as e:
            print(f"[FUND_SIGNAL] {name} failed: {e}")
    return pool


def _collect_fund_signals(user_id: str) -> list:
    """P0-1/P0-2/P0-3/P1-1 四类基金信号。内部各自 try/except，
    单个采集器失败不影响其他；全部走 render + budget 后返回。
    """
    from services.fund_signal import (
        budget,
        drawdown,
        dca,
        manager,
        portfolio,
        render,
        sw_industry,
        xray,
    )
    from services.fund_signal import state as _state

    signals: list = []

    # 持仓加载是四类信号的共同前置，失败即本轮无基金信号。
    try:
        positions = portfolio.load_positions(user_id)
    except Exception as e:
        print(f"[FUND_SIGNAL] load_positions({user_id}) failed: {e}")
        return []
    if not positions:
        return []

    # ---- P0-1 组合穿透体检 ----
    xray_result = None
    try:
        portfolios = portfolio.fetch_portfolios([p.code for p in positions])
        sw = sw_industry.load_sw_l2()
        sw_map = sw.get("map", {}) if isinstance(sw, dict) else {}
        sw_source = sw.get("source", "sw_l2") if isinstance(sw, dict) else "sw_l2"
        xray_result = xray.compute_exposure(positions, portfolios, sw_map, sw_source)
        should_emit = xray.decide_emit(xray_result, portfolios, _state.load(user_id, _state.XRAY_STATE))
        if should_emit:
            sig = render.render_xray(xray_result, positions)
            if sig:
                signals.append(sig)
            _last_end = {c: (pf.get("end_date", "") if isinstance(pf, dict) else "")
                         for c, pf in portfolios.items()}
            _state.save(user_id, _state.XRAY_STATE,
                        {"baseline_sent": True, "last_end_dates": _last_end})
    except Exception as e:
        print(f"[FUND_SIGNAL] xray collect failed: {e}")

    # ---- P0-2 基金经理变更 ----
    try:
        signals.extend(manager.collect(user_id, positions) or [])
    except Exception as e:
        print(f"[FUND_SIGNAL] manager collect failed: {e}")

    # ---- P0-3 回撤档位状态机 ----
    try:
        signals.extend(drawdown.collect(user_id, positions) or [])
    except Exception as e:
        print(f"[FUND_SIGNAL] drawdown collect failed: {e}")

    # ---- P1-1 定投前瞻（复用 P0-1 的穿透结果，零新增数据依赖）----
    try:
        signals.extend(dca.collect(user_id, positions, xray_result) or [])
    except Exception as e:
        print(f"[FUND_SIGNAL] dca collect failed: {e}")

    # ---- 预算守门（≤2 条/日、≤4 条/月，按优先级砍）----
    try:
        signals = budget.gate(user_id, signals)
    except Exception as e:
        print(f"[FUND_SIGNAL] budget gate failed: {e}")

    return signals
