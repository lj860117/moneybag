#!/usr/bin/env python3
"""
钱袋子 — 运行态势快照汇总（元巡检）
=====================================
定位：不是再跑一遍数据源探针（那是 self_audit.py 的职责），而是
     「看守巡检官本身」——把散落在各处的巡检产物，按「新鲜度」汇总
     成一份结构化快照，暴露「哪个巡检自己失效了」。

背景（2026-09-06 调研发现）：
  - data/health/ 停在 2026-06-15（数据源巡检 3 个月未产出）
  - data/audit/latest.json mtime 停在 06-28（但 systemd timer 显示它在跑）
  - qwen 模型欠费（Arrearage）散在 llm_balance_monitor.log 里无人汇总
  → 痛点不是「缺监控」，而是「监控的产物散落、且没人看监控是否还活着」。

职责（纯规则，零 LLM，零联网）：
  1. 汇总各巡检产物/日志的「最近更新时间」→ 算出 stale_days（失效天数）
  2. 汇总各 cron 的「退出码 + 最近运行时间」（从日志 mtime + 内容推断）
  3. 抓取散落的失效证据：LLM 余额告警、qwen 欠费、watchdog 杀进程、磁盘占用
  4. 落盘 data/ops/snapshot_{date}.json（原子写，符合铁律 M4）

用法：
  cd /opt/moneybag/backend && /opt/moneybag/venv/bin/python scripts/ops_summary.py

cron（建议 08:03，紧跟 night_worker 之后）：
  3 8 * * * cd /opt/moneybag/backend && set -a && . /opt/moneybag/backend/.env \
    && set +a && /opt/moneybag/venv/bin/python scripts/ops_summary.py \
    >> /var/log/moneybag/ops_summary.log 2>&1
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# 确保能 import 项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR  # noqa: E402

# 复用项目的原子写（铁律 M4）。persistence 在 services 下，脚本可直接 import。
try:
    from services.persistence import atomic_write_json
except Exception:  # pragma: no cover - 兜底，避免 import 失败阻断
    atomic_write_json = None

# 快照落盘目录
OPS_DIR = DATA_DIR / "ops"
OPS_DIR.mkdir(parents=True, exist_ok=True)

# 磁盘告警阈值（GB）
DISK_WARN_GB = 5.0

# ── 数据目录漂移修复 ─────────────────────────────────────────
# 2026-09-06 发现：实际存在两个并行 data 目录——
#   /opt/moneybag/data/          （config.DATA_DIR + systemd 注入，权威）
#   /opt/moneybag/backend/data/  （历史遗留，部分 cron 仍在写入）
# health/audit/llm_usage 等产物被写散在两处。本脚本同时扫描两处取最新。
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_LEGACY_DATA_DIR = _BACKEND_DIR / "data"


def _candidate_dirs(sub: str) -> list[Path]:
    """返回某个子目录在权威 + 历史两处的候选路径（去重、存在才保留）。"""
    dirs: list[Path] = []
    for base in (DATA_DIR, _LEGACY_DATA_DIR):
        p = base / sub
        if p.exists() and p not in dirs:
            dirs.append(p)
    return dirs


# 各巡检产物的「新鲜度」清单：(名称, 子目录, glob, 预期最大 stale 天数)
# 超过 stale 天数即标记 ok=False，表示该巡检链可能已失效。
# path 改为子目录名，由 _candidate_dirs 展开成两处候选。
FRESHNESS_CHECKS: list[dict[str, Any]] = [
    {
        "name": "数据源健康巡检",
        "sub": "health",
        "glob": "*.json",
        "exclude": "_last_alert.json",
        "max_stale_days": 1,
    },
    {
        "name": "周度自检",
        "sub": "audit",
        "glob": "latest.json",
        "exclude": None,
        "max_stale_days": 7,
    },
    {
        "name": "LLM 用量",
        "sub": "llm_usage",
        "glob": f"{date.today().isoformat()}.json",
        "exclude": None,
        "max_stale_days": 1,
    },
    {
        "name": "余额监控",
        "sub": "logs",
        "glob": "llm_balance_monitor.log",
        "exclude": None,
        "max_stale_days": 1,
        # 余额监控日志在 backend/logs/（非 data 子目录），额外追加该目录
        "extra_dirs": [_BACKEND_DIR / "logs"],
    },
]


def _newest_mtime(dirs: list[Path], glob: str, exclude: str | None) -> tuple[str | None, float | None]:
    """返回多个目录下匹配 glob 的最新文件 mtime（ISO 字符串 + epoch 秒）。"""
    candidates: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        candidates += [p for p in d.glob(glob) if p.is_file()]
    if exclude:
        candidates = [p for p in candidates if p.name != exclude]
    if not candidates:
        return None, None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    mtime = newest.stat().st_mtime
    return datetime.fromtimestamp(mtime).isoformat(), mtime


def _stale_days(mtime: float | None) -> int | None:
    if mtime is None:
        return None
    return (datetime.now() - datetime.fromtimestamp(mtime)).days


def collect_freshness() -> list[dict[str, Any]]:
    """汇总各巡检产物的新鲜度（同时扫描权威 + 历史两处 data 目录）。"""
    rows: list[dict[str, Any]] = []
    for chk in FRESHNESS_CHECKS:
        dirs = _candidate_dirs(chk["sub"])
        dirs += chk.get("extra_dirs", [])
        iso, mtime = _newest_mtime(dirs, chk["glob"], chk["exclude"])
        stale = _stale_days(mtime)
        ok = (stale is not None) and (stale <= chk["max_stale_days"])
        rows.append({
            "name": chk["name"],
            "last_updated": iso,
            "stale_days": stale,
            "max_stale_days": chk["max_stale_days"],
            "ok": ok,
        })
    return rows


def collect_disk() -> dict[str, Any]:
    """磁盘占用（根分区）。"""
    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024 ** 3)
    return {
        "total_gb": round(total / (1024 ** 3), 1),
        "used_gb": round(used / (1024 ** 3), 1),
        "free_gb": round(free_gb, 1),
        "ok": free_gb > DISK_WARN_GB,
    }


def collect_llm_balance() -> dict[str, Any]:
    """抓取 LLM 余额监控日志里的关键信号（余额 + 欠费）。"""
    result: dict[str, Any] = {
        "checked": False,
        "balances": {},   # {provider: 余额字符串}
        "arrears": [],    # 欠费的 provider 列表
    }
    # 余额监控日志在 backend/logs/（相对脚本），需同时扫描两处 data/logs
    candidates = _candidate_dirs("logs") + [_BACKEND_DIR / "logs"]
    log_files = [d / "llm_balance_monitor.log" for d in candidates if (d / "llm_balance_monitor.log").exists()]
    if not log_files:
        return result
    result["checked"] = True
    # 取最新一份日志
    log_file = max(log_files, key=lambda p: p.stat().st_mtime)
    text = log_file.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        if "当前余额" in line:
            # 例：[INFO] [deepseek] 当前余额: ¥31.15（阈值 ¥10.00）
            # provider 出现在最后一个 [xxx] 块（紧邻「当前余额」前）
            try:
                prefix = line.split("当前余额:")[0]
                # 取最后一个方括号里的 token
                provider = prefix.rstrip().rsplit("[", 1)[-1].rstrip("]").strip()
                balance = line.split("当前余额:")[1].split("（")[0].strip()
                result["balances"][provider] = balance
            except Exception:
                pass
        if "Arrearage" in line or "overdue-payment" in line:
            # 例：[WARNING] [qwen] 可用性探测返回 400: ... Arrearage ...
            # 欠费 provider 是「Arrearage/探测」所在行的 provider 名（qwen），
            # 从 [provider] 中抓：取所有 [xxx] 块，跳过日志级别关键字。
            try:
                import re
                blocks = re.findall(r"\[([^\]]+)\]", line)
                for b in blocks:
                    if b in ("INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"):
                        continue
                    if b not in result["arrears"]:
                        result["arrears"].append(b)
            except Exception:
                pass
    return result


def collect_error_logs() -> dict[str, Any]:
    """扫描核心 cron 日志里 24h 内的错误/异常关键字。"""
    log_dirs = [
        Path("/var/log/moneybag"),
        DATA_DIR / "logs",
        _LEGACY_DATA_DIR / "logs",
        DATA_DIR / "night_worker",
        _LEGACY_DATA_DIR / "night_worker",
    ]
    keywords = ("Traceback", "ERROR", "❌", "Exception", "failed", "Failed")
    cutoff = datetime.now() - timedelta(hours=24)
    findings: list[dict[str, Any]] = []
    for log_dir in log_dirs:
        if not log_dir.exists():
            continue
        for f in log_dir.rglob("*.log"):
            try:
                if f.stat().st_mtime < cutoff.timestamp():
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
                for kw in keywords:
                    if kw in text:
                        findings.append({"file": str(f), "keyword": kw})
                        break
            except Exception:
                continue
    return {"count_24h": len(findings), "files": findings[:20]}


def build_snapshot() -> dict[str, Any]:
    """组装完整快照。"""
    freshness = collect_freshness()
    all_ok = all(r["ok"] for r in freshness)
    return {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "freshness": freshness,
        "summary": {
            "checks": len(freshness),
            "stale_count": sum(1 for r in freshness if not r["ok"]),
            "overall_ok": all_ok,
        },
        "disk": collect_disk(),
        "llm_balance": collect_llm_balance(),
        "error_logs_24h": collect_error_logs(),
    }


def main() -> int:
    snapshot = build_snapshot()

    # 打印可读摘要
    print(f"🔍 运行态势快照 {snapshot['date']}")
    for r in snapshot["freshness"]:
        mark = "✅" if r["ok"] else "❌"
        stale = r["stale_days"] if r["stale_days"] is not None else "未知"
        print(f"  {mark} {r['name']}: 距今 {stale} 天（阈值 {r['max_stale_days']} 天）")
    print(f"  💾 磁盘剩余 {snapshot['disk']['free_gb']}GB"
          f"{'' if snapshot['disk']['ok'] else ' ⚠️ 低于阈值'}")
    if snapshot["llm_balance"]["arrears"]:
        print(f"  🚨 欠费 provider: {snapshot['llm_balance']['arrears']}")
    print(f"  📝 24h 错误日志文件数: {snapshot['error_logs_24h']['count_24h']}")

    # 落盘（原子写，铁律 M4）
    out_file = OPS_DIR / f"snapshot_{snapshot['date']}.json"
    if atomic_write_json is not None:
        atomic_write_json(out_file, snapshot)
    else:  # 兜底
        out_file.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"  📝 快照已写入: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
