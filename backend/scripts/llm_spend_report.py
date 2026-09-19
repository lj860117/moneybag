#!/usr/bin/env python3
"""LLM 成本归因报表（回答"这几块钱花在哪"）

配套 `infra/llm/gateway.py` 新增的逐次调用明细
`DATA_DIR/llm_usage/calls/YYYY-MM-DD.jsonl`。

为什么需要它
------------
在 2026-09-19 之前，本项目**没有任何 module 维度的成本数据**：
  · `_record_usage` 只写进程内存（重启即丢，外部读不到）
  · `_record_token_cost` 只写「日期/用户」聚合，module 参数没传进去
所以"今天 ¥3.16 花在哪"只能靠猜。本脚本把明细按 模块/小时/模型 三个维度聚合，
并与官方余额采样（`llm_usage/balance_samples.csv`）做对账。

用法
----
    python3 scripts/llm_spend_report.py                 # 今天
    python3 scripts/llm_spend_report.py --date 2026-09-18
    python3 scripts/llm_spend_report.py --days 7        # 最近 7 天
    python3 scripts/llm_spend_report.py --reconcile     # 额外做官方余额对账

输出维度
--------
1. 总计：金额 / 调用次数 / token（并区分「API 真实值」与「估算值」两类）
2. 按 module 降序 —— **这是过去完全缺失的那一列**
3. 按小时 —— 定位凌晨/盘中的消耗分布
4. 单次最贵的 10 条调用
5. 失败次数（旧实现里失败调用一条都不记）
"""
from __future__ import annotations

import os
import sys
import csv
import json
import collections
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BACKEND_DIR.parent / "data")))
CALLS_DIR = DATA_DIR / "llm_usage" / "calls"
BALANCE_CSV = DATA_DIR / "llm_usage" / "balance_samples.csv"


def _iter_records(day: str):
    path = CALLS_DIR / f"{day}.jsonl"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# DeepSeek 官方峰谷规则（2026-08-23 起生效，与 infra/llm/gateway.py 保持一致）：
#   工作日 09:00-12:00、14:00-18:00 为高峰；其余时段 + 周六周日全天为低谷。
#   低谷价 = 高峰价的一半（flash 档 output：高峰 ¥9/M vs 低谷 ¥4.5/M）。
_PEAK_RANGES = ((9, 0, 12, 0), (14, 0, 18, 0))


def _is_peak(day: date, hour: int) -> bool:
    """按 DeepSeek 官方规则判断某天的某小时是否处于高峰计费窗口。"""
    if hour < 0:
        return False
    if day.weekday() >= 5:      # 周六=5 周日=6，周末全天低谷
        return False
    hm = hour * 60
    for h1, m1, h2, m2 in _PEAK_RANGES:
        if (h1 * 60 + m1) <= hm < (h2 * 60 + m2):
            return True
    return False


def report_day(day: str) -> dict:
    by_module: dict[str, dict] = collections.defaultdict(
        lambda: {"cost": 0.0, "calls": 0, "input": 0, "output": 0, "hit": 0, "miss": 0, "est": 0, "err": 0}
    )
    by_hour: dict[int, dict] = collections.defaultdict(lambda: {"cost": 0.0, "calls": 0})
    by_model: dict[str, dict] = collections.defaultdict(lambda: {"cost": 0.0, "calls": 0, "tokens": 0})
    by_phase: dict[str, dict] = collections.defaultdict(lambda: {"cost": 0.0, "calls": 0})
    rows: list[dict] = []
    total = {"cost": 0.0, "calls": 0, "input": 0, "output": 0, "hit": 0, "miss": 0, "est": 0, "err": 0}
    try:
        _day_obj = date.fromisoformat(day)
    except ValueError:
        _day_obj = date.today()

    for r in _iter_records(day):
        rows.append(r)
        cost = r.get("cost_rmb") or 0.0
        success = r.get("success", True)
        if not success:
            total["err"] += 1
        total["cost"] += cost
        total["calls"] += 1
        total["input"] += r.get("input_tokens", 0)
        total["output"] += r.get("output_tokens", 0)
        total["hit"] += r.get("cache_hit_tokens", 0)
        total["miss"] += r.get("cache_miss_tokens", 0)
        if r.get("estimated"):
            total["est"] += 1

        m = by_module[r.get("module") or "_unknown"]
        m["cost"] += cost
        m["calls"] += 1
        m["input"] += r.get("input_tokens", 0)
        m["output"] += r.get("output_tokens", 0)
        m["hit"] += r.get("cache_hit_tokens", 0)
        m["miss"] += r.get("cache_miss_tokens", 0)
        if r.get("estimated"):
            m["est"] += 1
        if not success:
            m["err"] += 1

        try:
            hour = datetime.fromisoformat(r.get("ts", "")).hour
        except (TypeError, ValueError):
            hour = -1
        by_hour[hour]["cost"] += cost
        by_hour[hour]["calls"] += 1

        # 峰谷维度：直接用记录里的 is_peak（gateway 已按官方规则算好），
        # 缺失时按 ts 的日期+小时自己判定（与 gateway._is_deepseek_peak_window 同规则）。
        ip = r.get("is_peak")
        if ip is None:
            ip = _is_peak(_day_obj, hour)
        phase = "peak" if ip else "valley"
        by_phase[phase]["cost"] += cost
        by_phase[phase]["calls"] += 1

        mm = by_model[r.get("model") or "?"]
        mm["cost"] += cost
        mm["calls"] += 1
        mm["tokens"] += r.get("input_tokens", 0) + r.get("output_tokens", 0)

    print(f"\n{'=' * 78}")
    print(f"  LLM 成本归因 · {day}")
    print(f"{'=' * 78}")
    if not rows:
        print(f"  （无明细数据：{CALLS_DIR / (day + '.jsonl')}）")
        print("  明细从本次修复部署后开始产生，历史日期无法补齐。")
        return total

    print(f"  总金额 ¥{total['cost']:.4f}   调用 {total['calls']} 次   "
          f"输入 {_fmt_tokens(total['input'])}  输出 {_fmt_tokens(total['output'])}")
    print(f"  缓存 命中 {_fmt_tokens(total['hit'])} / 未命中 {_fmt_tokens(total['miss'])}")
    if total["est"]:
        print(f"  ⚠️ 其中 {total['est']} 条 token 为**估算值**（API 未返回 usage），成本不可靠")
    if total["err"]:
        print(f"  ⚠️ 失败调用 {total['err']} 次（旧版本这些一次都不记）")

    print(f"\n  --- 按 module 降序（过去完全缺失的维度）---")
    ranked = sorted(by_module.items(), key=lambda kv: (-kv[1]["cost"], -kv[1]["calls"]))
    for name, v in ranked:
        share = (v["cost"] / total["cost"] * 100) if total["cost"] else 0.0
        flags = []
        if v["est"]:
            flags.append(f"估算{v['est']}")
        if v["err"]:
            flags.append(f"失败{v['err']}")
        flag_s = ("  [" + ",".join(flags) + "]") if flags else ""
        print(f"  ¥{v['cost']:>8.4f}  {share:>5.1f}%  {v['calls']:>4d}次  "
              f"in {_fmt_tokens(v['input']):>8s} out {_fmt_tokens(v['output']):>7s}  {name}{flag_s}")

    print(f"\n  --- 按小时 ---")
    for hour in sorted(by_hour):
        v = by_hour[hour]
        if v["calls"] == 0:
            continue
        bar = "█" * min(40, int(v["cost"] / max(total["cost"], 1e-9) * 80))
        mark = "  ←高峰" if _is_peak(_day_obj, hour) else ""
        label = f"{hour:02d}:00" if hour >= 0 else "  ?  "
        print(f"  {label}  ¥{v['cost']:>7.4f}  {v['calls']:>4d}次  {bar}{mark}")

    print(f"\n  --- 按峰谷计费窗口 ---")
    print(f"  （DeepSeek 官方：工作日 09:00-12:00 / 14:00-18:00 为高峰，"
          f"低谷价 = 高峰价的一半；周末全天低谷）")
    for name, cn in (("peak", "高峰"), ("valley", "低谷")):
        v = by_phase.get(name, {"cost": 0.0, "calls": 0})
        if v["calls"] == 0:
            continue
        share = v["cost"] / total["cost"] * 100 if total["cost"] else 0
        bar = "█" * min(40, int(v["cost"] / max(total["cost"], 1e-9) * 80))
        print(f"  {cn}  ¥{v['cost']:>7.4f}  {share:>5.1f}%  {v['calls']:>4d}次  {bar}")
    pv = by_phase.get("peak", {"cost": 0.0, "calls": 0})
    if pv["calls"] and total["cost"]:
        # 同样的调用挪到低谷，output/cache-miss 单价减半 → 粗略可省一半
        print(f"  💡 高峰占比 {pv['cost'] / total['cost'] * 100:.1f}% —— "
              f"若这部分挪到低谷，理论上可省约 ¥{pv['cost'] / 2:.4f}/天")

    print(f"\n  --- 按模型 ---")
    for name, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        print(f"  ¥{v['cost']:>8.4f}  {v['calls']:>4d}次  {_fmt_tokens(v['tokens']):>10s}  {name}")

    print(f"\n  --- 单次最贵 TOP10 ---")
    for r in sorted(rows, key=lambda r: -(r.get("cost_rmb") or 0))[:10]:
        tin = r.get("input_tokens", 0)
        tout = r.get("output_tokens", 0)
        print(f"  ¥{r.get('cost_rmb') or 0:>7.4f}  in {_fmt_tokens(tin):>7s} out {_fmt_tokens(tout):>6s}  "
              f"{r.get('ts', '')[11:19]}  {r.get('module') or '?'}"
              f"{'  估算' if r.get('estimated') else ''}")
    return total


def reconcile(days: list[str]) -> None:
    """把账面总额与官方余额采样的日差做对比（最硬的验证）。"""
    if not BALANCE_CSV.exists():
        print("\n（无余额采样数据，跳过对账。请先跑 scripts/llm_spend_sampler.py）")
        return
    samples: list[tuple[datetime, float]] = []
    with BALANCE_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                samples.append((datetime.fromisoformat(row["timestamp_iso"]),
                                float(row["balance_cny"])))
            except (KeyError, TypeError, ValueError):
                continue
    samples.sort()
    print(f"\n{'=' * 78}")
    print("  对账：账面 cost_rmb  vs  官方余额差额")
    print(f"{'=' * 78}")
    print(f"  {'日期':<12}{'账面 ¥':>12}{'官方 ¥':>12}{'倍数':>10}")
    for day in days:
        ledger = 0.0
        for r in _iter_records(day):
            ledger += r.get("cost_rmb") or 0.0
        # 取该日 00:00~24:00 内采样点覆盖的余额差
        d0 = datetime.fromisoformat(day + "T00:00:00")
        d1 = d0 + timedelta(days=1)
        window = [s for s in samples if d0 <= s[0] < d1]
        official = ""
        ratio = ""
        if len(window) >= 2:
            drop = window[0][1] - window[-1][1]
            official = f"{drop:.4f}"
            ratio = f"{(drop / ledger):.1f}x" if ledger > 0 else "n/a"
        print(f"  {day:<12}{ledger:>12.4f}{str(official):>12}{str(ratio):>10}")
    print("  说明：采样若未覆盖整日，官方列会偏小——以官方后台日柱为准。")


def main() -> int:
    args = sys.argv[1:]
    reconciled = "--reconcile" in args

    if "--days" in args:
        n = int(args[args.index("--days") + 1])
        days = [(date.today() - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    elif "--date" in args:
        days = [args[args.index("--date") + 1]]
    else:
        days = [date.today().isoformat()]

    for day in days:
        report_day(day)
    if reconciled:
        reconcile(days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
