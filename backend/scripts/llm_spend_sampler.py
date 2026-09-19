#!/usr/bin/env python3
"""DeepSeek 余额高频采样（成本时间线）

背景（2026-09-19）
-----------------
钱袋子官方后台显示近 30 天扣费 ¥50.54，而自记账只有 ¥2.53（低估约 20 倍）。
在把记账链路修好之前，"钱到底花在哪个小时/哪个任务上"完全没有证据，
因为既有的 `llm_balance_monitor.py` **每天只在 08:00 跑一次**，
两次快照之差只能给出"一整天花了多少"，无法定位到具体时段。

本脚本每 N 分钟采一次余额，追加成 CSV，得到的差值序列可以：
  1. 直接回答"凌晨那几块钱是谁花的"（对齐 crontab 时间点）
  2. 验证"是否存在持续的外部盗刷流"（余额单调匀速下降 = 有外部消费）
  3. 在记账修复后做**对账**：CSV 的日累计 vs 账本的 cost_rmb

用法
----
    python3 scripts/llm_spend_sampler.py            # 采一次，追加一行
    python3 scripts/llm_spend_sampler.py --report   # 打印最近 24h 的差额序列

输出
----
    DATA_DIR/llm_usage/balance_samples.csv
    字段：timestamp_iso,balance_cny,delta_since_prev_cny

设计约束
--------
· 只读余额接口，不产生任何 LLM 调用（**不花一分钱**）
· 任何异常都静默退出（timeout 15s），绝不阻塞 cron
· 缺 key 时直接跳过，不报错
"""
from __future__ import annotations

import os
import sys
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

DATA_DIR = Path(os.environ.get("DATA_DIR", str(BACKEND_DIR.parent / "data")))
CSV_PATH = DATA_DIR / "llm_usage" / "balance_samples.csv"
BALANCE_URL = "https://api.deepseek.com/user/balance"
HEADER = ["timestamp_iso", "balance_cny", "delta_since_prev_cny"]


def _load_env_file() -> None:
    """复用项目的 .env（cron 已 source，但手工执行时需要兜底）。"""
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fetch_balance(timeout: float = 15.0) -> float | None:
    key = os.environ.get("LLM_API_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return None
    try:
        import httpx
        resp = httpx.get(
            BALANCE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        infos = (resp.json() or {}).get("balance_infos") or []
        for info in infos:
            if info.get("currency") == "CNY":
                return float(info.get("total_balance", 0))
        if infos:
            return float(infos[0].get("total_balance", 0))
    except Exception:
        return None
    return None


def _read_last_balance() -> float | None:
    if not CSV_PATH.exists():
        return None
    last: float | None = None
    try:
        with CSV_PATH.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    last = float(row["balance_cny"])
                except (KeyError, TypeError, ValueError):
                    continue
    except Exception:
        return None
    return last


def sample_once() -> int:
    balance = _fetch_balance()
    if balance is None:
        return 0
    prev = _read_last_balance()
    delta = "" if prev is None else f"{prev - balance:.4f}"

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    need_header = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with CSV_PATH.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if need_header:
            writer.writerow(HEADER)
        writer.writerow([datetime.now().isoformat(timespec="seconds"), f"{balance:.4f}", delta])
    return 1


def report(hours: int = 24) -> None:
    if not CSV_PATH.exists():
        print(f"还没有采样数据：{CSV_PATH}")
        return
    cutoff = datetime.now() - timedelta(hours=hours)
    rows: list[tuple[datetime, float, float | None]] = []
    with CSV_PATH.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                ts = datetime.fromisoformat(row["timestamp_iso"])
                bal = float(row["balance_cny"])
                d = float(row["delta_since_prev_cny"]) if row.get("delta_since_prev_cny") else None
            except (KeyError, TypeError, ValueError):
                continue
            if ts >= cutoff:
                rows.append((ts, bal, d))

    print(f"=== DeepSeek 余额采样 · 最近 {hours}h（{len(rows)} 个点）===")
    total = 0.0
    for ts, bal, d in rows:
        mark = ""
        if d is not None and d > 0:
            total += d
            mark = "  🔴 消耗" if d >= 0.5 else "  ·"
        print(f"{ts:%Y-%m-%d %H:%M}  余额 ¥{bal:>8.4f}   本次变化 {'' if d is None else f'{d:+.4f}'}{mark}")
    print(f"--- 区间合计消耗 ¥{total:.4f} ---")
    if rows:
        print(f"（首点 ¥{rows[0][1]:.4f} → 末点 ¥{rows[-1][1]:.4f}）")


def main() -> int:
    _load_env_file()
    if "--report" in sys.argv:
        hours = 24
        for i, a in enumerate(sys.argv):
            if a == "--hours" and i + 1 < len(sys.argv):
                hours = int(sys.argv[i + 1])
        report(hours)
        return 0
    return 1 if sample_once() == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
