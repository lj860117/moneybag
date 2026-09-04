"""
推送预算守门（≤2 条/日、≤4 条/月）+ 计数落盘。

设计依据：docs/design/signal-scout-fund-account.md §3.4 / §8 Q4

规则：
  * 超预算的信号 relevance 从 100 改成 40（落前端不推送）。
  * 优先级（值越小越优先；超预算时按此倒序砍，即排最后先被砍）：
      dca_preflight > fund_manager_change > fund_drawdown_rung > fund_xray_concentration
  * 计数：state.push_log = {"2026-09": ["2026-09-04T08:12:03", ...]}
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

    log[month] = pushed_ts
    state.save(user_id, state.PUSH_LOG, _retain_recent(log, month))
    return signals
