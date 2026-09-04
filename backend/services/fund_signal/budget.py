"""
推送预算守门（≤2 条/日、≤4 条/月）+ 计数落盘。

设计依据：docs/design/signal-scout-fund-account.md §3.4 / §8 Q4

规则：
  * 超预算的信号 relevance 从 100 改成 40（落前端不推送）。
  * 优先级（值越小越优先；超预算时按此倒序砍，即排最后先被砍）：
      dca_preflight > fund_manager_change > fund_drawdown_rung > fund_xray_concentration
  * 计数：state.push_log = {"2026-09": ["2026-09-04T08:12:03", ...]}

⚠️ 判额度（gate）与记账（commit）【必须分离】：
    gate()   只读 —— 跑在 match() 里，match() 被前端 /api/signals 反复调用
    commit() 只写 —— 跑在 deliver() 里，那才是真正发企微的动作
  两者合一会导致「日额度 = 页面浏览次数」，信号侦察永远推不出去。
  详见 commit() 的文档字符串（含 2026-09-05 线上实测证据）。
"""
from datetime import datetime

from services.fund_signal import state
from services.fund_signal.config import (
    BUDGET_DAILY_MAX,
    BUDGET_MONTHLY_MAX,
    BUDGET_PRIORITY,
    RELEVANCE_PUSH,
    RELEVANCE_FRONTEND_ONLY,
)

# 只保留最近 N 个月的计数，防 push_log 无限增长。
_LOG_RETAIN_MONTHS = 3


def _priority(sig_type: str) -> int:
    """类型优先级；未知类型排最后（最可能被砍）。"""
    try:
        return BUDGET_PRIORITY.index(sig_type)
    except ValueError:
        return len(BUDGET_PRIORITY)


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def _day_prefix(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _retain_recent(log: dict, current_month: str) -> dict:
    """只保留最近 _LOG_RETAIN_MONTHS 个月（含当前月）。"""
    months = sorted(log.keys(), reverse=True)
    keep = set(months[:_LOG_RETAIN_MONTHS])
    keep.add(current_month)
    return {m: ts for m, ts in log.items() if m in keep}


def gate(user_id: str, signals: list, now=None) -> list:
    """按优先级 + 日/月额度守门。就地改 relevance 后返回原列表（保持引用）。

    ⚠️【只读】本函数不写 push_log —— 记账在 commit()。原因见 commit() 的
    文档字符串：gate() 跑在 match() 里，而 match() 被前端轮询反复调用，
    在这里记账会把日额度当成「页面浏览次数」烧掉。

    Args:
        user_id: 用户 ID。
        signals: 待守门的信号列表（relevance 应为 100）。
        now: 可选 datetime，测试注入用；默认 datetime.now()。
    """
    if not signals:
        return signals
    if now is None:
        now = datetime.now()

    log = state.load(user_id, state.PUSH_LOG)
    if not isinstance(log, dict):
        log = {}

    month = _month_key(now)
    day_prefix = _day_prefix(now)
    pushed_ts = list(log.get(month, []) or [])

    month_used = len(pushed_ts)
    day_used = sum(1 for ts in pushed_ts if str(ts).startswith(day_prefix))

    # 按优先级稳定排序（同优先级保持原始顺序）。
    ordered = sorted(signals, key=lambda s: _priority(s.get("type", "")))

    for sig in ordered:
        if sig.get("relevance") != RELEVANCE_PUSH:
            # 只守门「推送级」信号；已降级的不重复计额度。
            continue
        if day_used < BUDGET_DAILY_MAX and month_used < BUDGET_MONTHLY_MAX:
            pushed_ts.append(now.strftime("%Y-%m-%dT%H:%M:%S"))
            day_used += 1
            month_used += 1
        else:
            sig["relevance"] = RELEVANCE_FRONTEND_ONLY

    # ⚠️ 不再在这里 state.save()：gate() 只读，记账交给 commit()。
    return signals


def commit(user_id: str, signals: list, now=None) -> int:
    """把【实际已推送】的基金信号计入日/月额度，返回本次计数条数。

    ⚠️ 为什么要和 gate() 分开（2026-09-05 线上实测的坑）：

      gate()  跑在 match() 里 → match() 被 /api/signals 前端轮询调用（一天几十次）
      commit() 跑在 deliver() 里 → 这才是真正发企微的动作

    原实现在 gate() 里记账，等价于把「日额度 2 条」当成「页面浏览次数」来限：
    线上实测 2026-09-05 00:20:10 与 00:20:11 相隔 1 秒的两条记录，就是两次
    match()（不是两次企微推送）写进去的。日额度当场烧光 → 之后真正 deliver()
    时 gate() 把所有基金信号砍成 relevance=40 → _should_push(40 < 50) 不过 →
    「无重要信号」→ 信号侦察【永远推不出去】，而日志里连一行报错都没有。

    只统计受预算管控的基金信号（type ∈ BUDGET_PRIORITY 且 relevance 仍为
    推送级），避免把普通公共信号也算进来。

    Args:
        user_id: 用户 ID。
        signals: deliver() 已判定要推送的信号列表。
        now: 可选 datetime，测试注入用；默认 datetime.now()。

    Returns:
        本次计入额度的条数。
    """
    if not signals:
        return 0
    if now is None:
        now = datetime.now()

    counted = [
        s for s in signals
        if s.get("relevance") == RELEVANCE_PUSH and s.get("type") in BUDGET_PRIORITY
    ]
    if not counted:
        return 0

    log = state.load(user_id, state.PUSH_LOG)
    if not isinstance(log, dict):
        log = {}

    month = _month_key(now)
    pushed_ts = list(log.get(month, []) or [])
    ts = now.strftime("%Y-%m-%dT%H:%M:%S")
    for _ in counted:
        pushed_ts.append(ts)

    log[month] = pushed_ts
    state.save(user_id, state.PUSH_LOG, _retain_recent(log, month))
    return len(counted)
