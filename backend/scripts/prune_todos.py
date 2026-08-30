#!/usr/bin/env python3
"""
钱袋子 — todos 历史数据收敛脚本（一次性/可重复运行）
====================================================
用途：把生产环境已经堆积的 todos 收敛到合理规模。

背景（2026-08-30 排查）：
  services/todo_manager.py 的 create_todo() 原来是"裸 append"，既无幂等也无上限，
  而 services/cfo_dashboard.py 在**构建仪表板（读操作）时**就调它，配合 55 秒一次
  的后台预热线程，从 2026-05-16 起累积出 153,093 条待办（33.9MB / 单用户 JSON 49MB）。
  代码侧的护栏已在 todo_manager.py + cfo_dashboard.py 修好（幂等窗口 + 硬上限 +
  读写解耦），但**历史存量**需要本脚本清理。

收敛策略（按 rule_triggered 分组）：
  - 每个规则只保留 **最新 1 条 status == "open"** 的待办（同规则多条 open 是纯垃圾）
  - 每个规则保留 **最近 N 条已关闭历史**（completed / skipped，N 由 --keep-history 控制，默认 50）
  - 最后再套一层全局上限（--max-entries，默认 500），与 todo_manager.TODO_MAX_ENTRIES 一致

安全设计：
  - **默认 dry-run**：不加 --apply 只统计并打印，绝不写任何文件
  - --apply 时先把原文件复制一份 `<name>.json.bak-prune-<时间戳>` 再写
  - 写入统一走 services/persistence.py 的 atomic_write_json()（tmp + fsync + rename）
  - 写入受 services/persistence.py 的 user_write_lock() 保护（跨进程 flock）：
    本脚本也是"独立进程写用户 JSON"的一方，必须和 todo_manager 一起参与加锁，
    否则运行期间若有 uvicorn/cron 在写会互相覆盖。锁内会重新读一次文件以
    基于最新快照重算，不复用锁外的计算结果。
  - 处理 49MB 大 JSON 时不做 deepcopy，保留的是原 dict 的引用，只重建 list

使用：
  # 1) 先看会删多少（默认 dry-run，安全）
  cd backend && python3 -m scripts.prune_todos

  # 2) 确认没问题再真的写入
  cd backend && python3 -m scripts.prune_todos --apply

  # 3) 只处理某个文件 / 调整保留量
  cd backend && python3 -m scripts.prune_todos --file data/users/ab12cd34.json --keep-history 20
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 让脚本能 import config / services（与 scripts/ 下其他脚本一致）
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from config import USERS_DIR                          # noqa: E402
from services.persistence import (                     # noqa: E402
    atomic_write_json,
    user_write_lock,
)

# ---- 默认参数（与 todo_manager 的护栏对齐）----
DEFAULT_KEEP_HISTORY = 50   # 每个规则保留的已关闭历史条数
DEFAULT_MAX_ENTRIES = 500   # 全局硬上限，对齐 todo_manager.TODO_MAX_ENTRIES
OPEN_STATUS = "open"


def _fmt_size(num_bytes: int) -> str:
    """人类可读的体积字符串。"""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _created_key(todo: dict) -> str:
    """排序键：created_at 缺失时用空串（排最前 → 最先被淘汰）。"""
    value = todo.get("created_at")
    return value if isinstance(value, str) else ""


def plan_prune(
    todos: list,
    keep_history: int = DEFAULT_KEEP_HISTORY,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> tuple[list, dict]:
    """
    计算收敛后的 todos 列表（纯函数，不写盘、不修改入参）。

    Args:
        todos: 原始待办列表
        keep_history: 每个 rule_triggered 保留的已关闭历史条数
        max_entries: 全局硬上限

    Returns:
        (保留的待办列表, 统计信息 dict)
    """
    stats: dict = {
        "total_before": len(todos),
        "open_before": 0,
        "closed_before": 0,
        "malformed": 0,
        "rules": {},          # rule -> {"before": n, "after": n, "open_before": n}
        "dropped_by_cap": 0,
    }

    # ── 1. 按 rule_triggered 分组 ──
    groups: dict[str, dict[str, list]] = {}
    for todo in todos:
        if not isinstance(todo, dict):
            stats["malformed"] += 1
            continue
        rule = todo.get("rule_triggered") or "__unknown__"
        bucket = groups.setdefault(rule, {"open": [], "closed": []})
        if todo.get("status") == OPEN_STATUS:
            bucket["open"].append(todo)
            stats["open_before"] += 1
        else:
            bucket["closed"].append(todo)
            stats["closed_before"] += 1

    # ── 2. 组内收敛：最新 1 条 open + 最近 keep_history 条已关闭 ──
    kept: list = []
    for rule, bucket in groups.items():
        opens = sorted(bucket["open"], key=_created_key, reverse=True)
        closed = sorted(bucket["closed"], key=_created_key, reverse=True)

        keep_open = opens[:1]                    # 同规则只留最新的一条未完成
        keep_closed = closed[:keep_history]      # 已完成/已跳过留最近 N 条

        kept.extend(keep_open)
        kept.extend(keep_closed)

        stats["rules"][rule] = {
            "before": len(opens) + len(closed),
            "open_before": len(opens),
            "after": len(keep_open) + len(keep_closed),
        }

    # ── 3. 全局上限兜底（按 created_at 倒序，保留最新的）──
    kept.sort(key=_created_key, reverse=True)
    if max_entries > 0 and len(kept) > max_entries:
        stats["dropped_by_cap"] = len(kept) - max_entries
        kept = kept[:max_entries]

    stats["total_after"] = len(kept)
    stats["removed"] = stats["total_before"] - stats["total_after"]
    return kept, stats


def process_file(
    filepath: Path,
    keep_history: int,
    max_entries: int,
    apply_changes: bool,
    top_rules: int = 8,
) -> dict:
    """
    处理单个用户 JSON 文件。

    Args:
        filepath: 用户数据文件路径
        keep_history: 每规则保留的已关闭历史条数
        max_entries: 全局上限
        apply_changes: False = dry-run（只打印，不写盘）
        top_rules: 打印前 N 个规则的明细

    Returns:
        本文件的处理结果摘要
    """
    result: dict = {
        "file": str(filepath),
        "ok": False,
        "removed": 0,
        "total_before": 0,
        "total_after": 0,
        "size_before": 0,
        "size_after": 0,
        "written": False,
        "error": None,
    }

    size_before = filepath.stat().st_size
    result["size_before"] = size_before
    print(f"\n{'=' * 68}")
    print(f"📄 {filepath.name}  （{_fmt_size(size_before)}）")
    print(f"{'=' * 68}")

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        result["error"] = f"读取失败: {e}"
        print(f"  ❌ 读取失败，跳过: {e}")
        return result

    if not isinstance(data, dict):
        result["error"] = "顶层不是 JSON 对象"
        print("  ❌ 顶层不是 JSON 对象，跳过")
        return result

    todos = data.get("todos")
    if not isinstance(todos, list):
        result["ok"] = True
        print("  ✅ 无 todos 字段（或不是列表），无需处理")
        return result

    kept, stats = plan_prune(todos, keep_history, max_entries)
    result.update(
        total_before=stats["total_before"],
        total_after=stats["total_after"],
        removed=stats["removed"],
    )

    print(f"  用户: {data.get('userId', '<unknown>')}")
    print(f"  todos 总数: {stats['total_before']:,} "
          f"（open {stats['open_before']:,} / 已关闭 {stats['closed_before']:,}）")
    print(f"  规则种类: {len(stats['rules'])}")
    if stats["malformed"]:
        print(f"  ⚠️ 非法条目（非 dict）: {stats['malformed']}（将被丢弃）")

    # 打印膨胀最严重的规则
    ranked = sorted(
        stats["rules"].items(), key=lambda kv: kv[1]["before"], reverse=True
    )
    if ranked:
        print(f"  ── 规则明细（前 {min(top_rules, len(ranked))} 个）──")
        for rule, info in ranked[:top_rules]:
            print(f"     {rule:34s} {info['before']:>7,} → {info['after']:>4} "
                  f"（其中 open {info['open_before']:,}）")

    if stats["dropped_by_cap"]:
        print(f"  全局上限 {max_entries} 又裁掉: {stats['dropped_by_cap']:,} 条")

    print(f"  ── 结果 ──")
    print(f"     保留: {stats['total_after']:,} 条")
    print(f"     删除: {stats['removed']:,} 条")

    if stats["removed"] <= 0:
        result["ok"] = True
        print("  ✅ 已在合理规模，无需写入")
        return result

    if not apply_changes:
        result["ok"] = True
        print("  🔍 DRY-RUN：未写入任何文件（加 --apply 才真正执行）")
        return result

    # ── 真正写入：抢锁 → 锁内重读 → 备份 → 原子写 ──
    # FIX 2026-08-30（自查补漏）：本脚本自己也是一个"独立进程写用户 JSON"的
    # 调用方，必须和 services/todo_manager.py 一样participate in user_write_lock，
    # 否则运行期间若有 uvicorn/cron 在写，双方会互相覆盖（正是本次要根治的
    # lost update）。虽然运维流程是"停服务再跑"，但脚本不该依赖流程的正确性。
    #
    # 注意这里刻意在锁内**重新读一次**文件：上面那次读是无锁的（为了拿到
    # userId 才能确定锁的粒度），期间数据可能已被别的进程改动，因此锁内必须
    # 基于最新快照重算，不能复用锁外算出的 kept。49MB 文件多读一次的代价
    # （几秒）对一次性维护脚本完全可接受。
    user_id = data.get("userId")
    if not user_id:
        result["error"] = "缺少 userId 字段，无法加锁，跳过写入"
        print("  ❌ 缺少 userId 字段，无法安全加锁 → 跳过写入")
        return result

    # 释放锁外那份大对象，避免锁内重读时内存里存在两份 49MB 数据
    del data

    try:
        with user_write_lock(user_id) as acquired:
            if not acquired:
                result["error"] = "抢锁超时，跳过写入"
                print("  ❌ 抢锁超时（有其他进程正在写该用户）→ 跳过写入")
                return result

            # 锁内重读 + 重算
            fresh = json.loads(filepath.read_text(encoding="utf-8"))
            fresh_todos = fresh.get("todos")
            if not isinstance(fresh_todos, list):
                result["ok"] = True
                print("  ✅ 锁内重读后无 todos 字段，无需处理")
                return result

            fresh_kept, fresh_stats = plan_prune(fresh_todos, keep_history, max_entries)
            if fresh_stats["removed"] <= 0:
                result["ok"] = True
                print("  ✅ 锁内重读后已在合理规模，无需写入")
                return result
            if fresh_stats["removed"] != stats["removed"]:
                print(f"  ℹ️ 锁内重读后数据有变化："
                      f"删除数 {stats['removed']:,} → {fresh_stats['removed']:,}"
                      f"（以锁内的为准）")

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = filepath.with_name(f"{filepath.name}.bak-prune-{stamp}")
            shutil.copy2(filepath, backup)
            print(f"  📦 已备份: {backup.name}")

            # 原地替换（不 deepcopy：fresh_kept 里是原 todo dict 的引用）
            fresh["todos"] = fresh_kept
            atomic_write_json(filepath, fresh)

            result["written"] = True
            result["size_after"] = filepath.stat().st_size
            result["total_after"] = fresh_stats["total_after"]
            result["removed"] = fresh_stats["removed"]
            result["ok"] = True
            print(f"  ✅ 已写入: {_fmt_size(size_before)} → "
                  f"{_fmt_size(result['size_after'])} "
                  f"（省 {_fmt_size(size_before - result['size_after'])}）")
    except Exception as e:  # 单个文件失败不影响其他文件
        result["error"] = f"写入失败: {e}"
        print(f"  ❌ 写入失败（原文件未受影响）: {e}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="钱袋子 todos 历史数据收敛脚本（默认 dry-run，加 --apply 才写入）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python3 -m scripts.prune_todos                 # 只看，不改（默认）\n"
            "  python3 -m scripts.prune_todos --apply         # 真正执行\n"
            "  python3 -m scripts.prune_todos --keep-history 20 --max-entries 300\n"
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="真正写入。不加此参数为 dry-run（默认），只打印不改动任何文件",
    )
    parser.add_argument(
        "--keep-history", type=int, default=DEFAULT_KEEP_HISTORY,
        help=f"每个 rule_triggered 保留的已关闭历史条数（默认 {DEFAULT_KEEP_HISTORY}）",
    )
    parser.add_argument(
        "--max-entries", type=int, default=DEFAULT_MAX_ENTRIES,
        help=f"todos 全局硬上限（默认 {DEFAULT_MAX_ENTRIES}，0 = 不限）",
    )
    parser.add_argument(
        "--file", type=str, default=None,
        help="只处理指定文件（默认处理 data/users/ 下所有 *.json）",
    )
    parser.add_argument(
        "--users-dir", type=str, default=None,
        help=f"用户数据目录（默认 {USERS_DIR}）",
    )
    args = parser.parse_args()

    mode = "APPLY（会写盘）" if args.apply else "DRY-RUN（只读，不写盘）"
    print(f"===== todos 收敛脚本启动 @ {datetime.now().isoformat(timespec='seconds')} =====")
    print(f"模式: {mode}")
    print(f"策略: 每规则保留 最新 1 条 open + 最近 {args.keep_history} 条已关闭；"
          f"全局上限 {args.max_entries or '不限'}")

    # ── 收集待处理文件 ──
    if args.file:
        target = Path(args.file).expanduser()
        if not target.is_absolute():
            target = (_BACKEND.parent / target).resolve()
        if not target.exists():
            print(f"❌ 文件不存在: {target}")
            return 1
        files = [target]
    else:
        users_dir = Path(args.users_dir).expanduser() if args.users_dir else USERS_DIR
        if not users_dir.exists():
            print(f"✅ 用户目录不存在，无需处理: {users_dir}")
            return 0
        # 只处理主数据文件，跳过 .bak / .bak-prune-* / .tmp
        files = sorted(
            f for f in users_dir.glob("*.json")
            if ".bak" not in f.name and not f.name.endswith(".tmp")
        )
        print(f"目录: {users_dir}")

    if not files:
        print("✅ 未找到用户数据文件")
        return 0

    print(f"待检查文件: {len(files)} 个")

    results = [
        process_file(fp, args.keep_history, args.max_entries, args.apply)
        for fp in files
    ]

    # ── 汇总 ──
    total_before = sum(r["total_before"] for r in results)
    total_after = sum(r["total_after"] for r in results)
    removed = sum(r["removed"] for r in results)
    size_before = sum(r["size_before"] for r in results)
    size_after = sum(
        r["size_after"] if r["written"] else r["size_before"] for r in results
    )
    failed = [r for r in results if r["error"]]

    print(f"\n{'=' * 68}")
    print("📊 汇总")
    print(f"{'=' * 68}")
    print(f"  文件数:     {len(results)}（失败 {len(failed)}）")
    print(f"  todos 条数: {total_before:,} → {total_after:,}（删除 {removed:,}）")
    print(f"  磁盘占用:   {_fmt_size(size_before)} → {_fmt_size(size_after)}")
    for r in failed:
        print(f"  ❌ {Path(r['file']).name}: {r['error']}")

    if not args.apply and removed > 0:
        print("\n  🔍 以上为 DRY-RUN 预演，未改动任何文件。")
        print("     确认无误后执行: python3 -m scripts.prune_todos --apply")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
