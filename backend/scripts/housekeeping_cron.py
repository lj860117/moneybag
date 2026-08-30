#!/usr/bin/env python3
"""
钱袋子 — 运维清理 cron（housekeeping）
=====================================
用途：自动清理服务器上会反复长出来的垃圾文件，防止磁盘被慢慢吃满。
     默认 dry-run，只有加 --apply 才真正删除。

背景（2026-08-30 部署漂移 / 磁盘排查）：
  1) data/users/*.tmp 孤儿临时文件 10 个共 33MB（最老 2026-05-26，最新 2026-07-05）
     根因链条：services/todo_manager.py 无上限地往 todos 追加 → 用户 JSON 涨到 49MB
     → atomic_write_json() 写入变慢 → 进程被 **SIGKILL** → except 分支根本不执行
     → tempfile.mkstemp() 建的 .tmp 永远留在磁盘上。
     persistence.py 的代码本身是对的（except 里确实 os.unlink），但 SIGKILL 杀不住。
     todos 的根因已在 todo_manager.py / cfo_dashboard.py 修复，本脚本负责兜住
     "万一还是被 KILL" 的残留 —— 属于防复发的第二道防线。
  2) __pycache__ / .mypy_cache 等可再生缓存，删了会自动重建，零风险。
  3) data/night_worker/YYYY-MM-DD.log 这类**按日期归档**的历史日志。

⚠️ 与 logrotate 的分工（别搞混，两者不冲突）：
  - **logrotate 管"活跃日志"**：data/night_worker/night_worker.log、data/monitor/cron.log
    这类文件名里没有日期、正在被进程写入的 .log。轮转/压缩/删除由 logrotate 配置负责，
    本脚本**绝不碰**（判据：文件名里必须含 YYYY-MM-DD 或 YYYYMMDD 才会被本脚本处理）。
  - **本脚本管"按日期命名的历史归档"**：night_worker 自己按天新建的 2026-05-16.log，
    logrotate 不认识这种命名（它只跟踪固定路径），所以只能由本脚本按 mtime 清。

安全设计：
  - 默认 dry-run；--apply 才执行
  - .tmp 只删 mtime 超过 --tmp-age-days（默认 1 天）的，绝不误删正在写入的临时文件
  - 每一类清理独立 try/except，一类失败不影响其他类
  - 输出清理前后的磁盘占用对比

建议 crontab（每天凌晨 04:30，日志追加）：
  30 4 * * * cd /opt/moneybag/backend && python3 -m scripts.housekeeping_cron --apply >> /var/log/moneybag/housekeeping.log 2>&1

手动跑：
  cd backend && python3 -m scripts.housekeeping_cron              # 只看（默认）
  cd backend && python3 -m scripts.housekeeping_cron --apply      # 真删
"""
import argparse
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# 让脚本能 import config（与 scripts/ 下其他脚本一致）
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from config import DATA_DIR  # noqa: E402

#: 仓库根目录（backend 的上一层），用于扫 __pycache__
PROJECT_ROOT = _BACKEND.parent

# ---- 默认阈值 ----
DEFAULT_TMP_AGE_DAYS = 1     # .tmp 至少 1 天没动过才删（绝不误删正在写入的）
DEFAULT_LOG_AGE_DAYS = 90    # 按日期归档的历史日志保留 90 天
DEFAULT_ARCHIVE_AGE_DAYS = 90

#: 可再生缓存目录名（删了会自动重建）
REGENERABLE_CACHE_DIRS = ("__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache")

#: 扫描时直接跳过的目录（虚拟环境/版本库/前端依赖，不属于本脚本职责）
SKIP_DIR_NAMES = (".git", ".venv", "venv", "node_modules", ".idea", ".vscode")

#: "文件名里带日期" 的判据 —— 只有命中才认为是历史归档，
#: 从而与 logrotate 管的活跃 .log（文件名无日期）严格区分开。
DATED_NAME_RE = re.compile(r"(?:19|20)\d{2}[-_]?\d{2}[-_]?\d{2}")

#: 被视为"历史归档日志"的后缀
ARCHIVED_LOG_SUFFIXES = (".log", ".log.gz", ".jsonl", ".jsonl.gz", ".txt")


def _fmt_size(num_bytes: int) -> str:
    """人类可读的体积字符串。"""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _safe_size(path: Path) -> int:
    """取文件体积，失败返回 0（文件可能刚好被别的进程删了）。"""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    """递归统计目录体积，跳过无权限/软链接异常。"""
    total = 0
    if not path.exists():
        return 0
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        for name in files:
            fp = Path(root) / name
            if fp.is_symlink():
                continue
            total += _safe_size(fp)
    return total


def _age_days(path: Path, now: float) -> float:
    """文件的 mtime 距今多少天，取不到时返回 -1（视为"太新"，不删）。"""
    try:
        return (now - path.stat().st_mtime) / 86400.0
    except OSError:
        return -1.0


def _delete_file(path: Path) -> None:
    """删除单个文件（软链接也按文件删）。"""
    path.unlink()


# ============================================================
# 清理任务 1：孤儿 .tmp（SIGKILL 残留）
# ============================================================

def collect_orphan_tmp(data_dir: Path, min_age_days: float, now: float) -> list:
    """
    收集 data/** 下的孤儿临时文件。

    只认 mtime 超过 min_age_days 的 *.tmp —— atomic_write_json() 正常写入
    在毫秒级完成，超过 1 天还在的一定是进程被 KILL 留下的死文件。

    ⚠️⚠️ 下面那行**必须**用 `pathlib.Path.rglob`，不可换成 `glob.glob()`
        或 shell 的 `find`/`ls *.tmp`：

            pathlib rglob("*.tmp")             → 匹配点开头文件 ✅
            glob.glob("**/*.tmp", recursive=1) → **不**匹配点开头文件 ❌
            shell   ls *.tmp                   → **不**匹配点开头文件 ❌

        这不是风格偏好，是功能正确性问题：`scripts/cache_warmer.py` 用
        `tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp")` 落盘，它产生的
        孤儿文件名 **100% 都是点开头**（如 `.market_context.a1b2c3.tmp`）。
        一旦换成 `glob` 或 shell 写法，缓存孤儿的收集率会从 100% 直接掉到 0%，
        而且是**静默失效** —— 脚本照样跑、照样打印"命中 0 个孤儿"、看起来一切健康。

        守门：`backend/tests/test_housekeeping_orphan_tmp.py` 有一条断言专门
        锁定这个行为（构造点开头 + 嵌套子目录 + 超龄的孤儿并断言被命中）。
        改这行前请先看那个测试。

    Returns:
        [(路径, 体积, 年龄天数), ...]
    """
    targets = []
    if not data_dir.exists():
        return targets
    # 必须是 pathlib.rglob —— 见上方 docstring，glob/shell 不匹配点开头文件
    for fp in data_dir.rglob("*.tmp"):
        if not fp.is_file():
            continue
        age = _age_days(fp, now)
        if age < min_age_days:
            continue  # 太新 → 可能正在写入，绝不碰
        targets.append((fp, _safe_size(fp), age))
    targets.sort(key=lambda item: item[2], reverse=True)
    return targets


# ============================================================
# 清理任务 2：可再生缓存目录（__pycache__ 等）
# ============================================================

def collect_cache_dirs(project_root: Path) -> list:
    """
    收集仓库内的可再生缓存目录（__pycache__ / .mypy_cache / ...）。

    Returns:
        [(路径, 体积), ...]
    """
    targets = []
    if not project_root.exists():
        return targets
    for root, dirs, _files in os.walk(project_root, onerror=lambda e: None):
        # 先剪掉不该进的目录，避免走进 .venv / node_modules
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        hits = [d for d in dirs if d in REGENERABLE_CACHE_DIRS]
        for name in hits:
            dp = Path(root) / name
            targets.append((dp, _dir_size(dp)))
        # 命中的目录整体删除，不需要再往里递归
        dirs[:] = [d for d in dirs if d not in REGENERABLE_CACHE_DIRS]
    targets.sort(key=lambda item: item[1], reverse=True)
    return targets


# ============================================================
# 清理任务 3：按日期命名的历史归档日志
# ============================================================

def collect_dated_archives(data_dir: Path, max_age_days: float, now: float) -> list:
    """
    收集 data/** 下"文件名含日期"且超龄的归档文件。

    ⚠️ 判据是**文件名里必须含日期**（如 2026-05-16.log / night_worker_20260516.log）。
    文件名无日期的活跃日志（night_worker.log、cron.log）由 logrotate 负责，
    本函数一律不返回，两套机制互不干扰。

    Returns:
        [(路径, 体积, 年龄天数), ...]
    """
    targets = []
    if not data_dir.exists():
        return targets
    for root, dirs, files in os.walk(data_dir, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        for name in files:
            lower = name.lower()
            if not lower.endswith(ARCHIVED_LOG_SUFFIXES):
                continue
            if not DATED_NAME_RE.search(name):
                continue  # 无日期 → 活跃日志，交给 logrotate
            fp = Path(root) / name
            if not fp.is_file():
                continue
            age = _age_days(fp, now)
            if age < max_age_days:
                continue
            targets.append((fp, _safe_size(fp), age))
    targets.sort(key=lambda item: item[2], reverse=True)
    return targets


# ============================================================
# 执行器
# ============================================================

def _run_category(
    label: str,
    targets: list,
    apply_changes: bool,
    is_dir: bool = False,
    show_limit: int = 12,
) -> dict:
    """
    统一处理一类清理目标（打印 + 可选删除）。

    Args:
        label: 分类名称（打印用）
        targets: collect_* 返回的目标列表，元素首项为路径、次项为体积
        apply_changes: False = dry-run
        is_dir: 目标是目录（用 shutil.rmtree）还是文件（用 unlink）
        show_limit: 最多打印几条明细

    Returns:
        {"count", "bytes", "deleted", "failed"}
    """
    total_bytes = sum(item[1] for item in targets)
    summary = {"count": len(targets), "bytes": total_bytes, "deleted": 0, "failed": 0}

    print(f"\n── {label} ──")
    if not targets:
        print("   ✅ 没有需要清理的对象")
        return summary

    print(f"   命中 {len(targets)} 个，共 {_fmt_size(total_bytes)}")
    for item in targets[:show_limit]:
        path, size = item[0], item[1]
        age_txt = f"  {item[2]:.1f} 天前" if len(item) > 2 else ""
        try:
            shown = path.relative_to(PROJECT_ROOT)
        except ValueError:
            shown = path
        print(f"     {str(shown):58s} {_fmt_size(size):>10s}{age_txt}")
    if len(targets) > show_limit:
        print(f"     ... 另有 {len(targets) - show_limit} 个未列出")

    if not apply_changes:
        print("   🔍 DRY-RUN：未删除（加 --apply 才执行）")
        return summary

    for item in targets:
        path = item[0]
        try:
            if is_dir:
                shutil.rmtree(path)
            else:
                _delete_file(path)
            summary["deleted"] += 1
        except OSError as e:
            summary["failed"] += 1
            print(f"   ❌ 删除失败 {path}: {e}")

    print(f"   ✅ 已删除 {summary['deleted']} 个"
          f"（失败 {summary['failed']}），释放约 {_fmt_size(total_bytes)}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="钱袋子运维清理 cron（默认 dry-run，加 --apply 才真正删除）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "清理范围:\n"
            "  1. data/**/*.tmp        —— mtime 超过 --tmp-age-days 的孤儿临时文件\n"
            "  2. __pycache__ 等       —— 可再生缓存目录，删了自动重建\n"
            "  3. 按日期命名的归档日志 —— 文件名含 YYYY-MM-DD 且超过 --log-age-days\n"
            "     （文件名无日期的活跃 .log 由 logrotate 负责，本脚本不碰）\n\n"
            "示例:\n"
            "  python3 -m scripts.housekeeping_cron              # 只看，不删（默认）\n"
            "  python3 -m scripts.housekeeping_cron --apply      # 真正清理\n"
            "  python3 -m scripts.housekeeping_cron --skip-cache --apply\n"
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="真正删除。不加此参数为 dry-run（默认），只打印不删任何东西",
    )
    parser.add_argument(
        "--tmp-age-days", type=float, default=DEFAULT_TMP_AGE_DAYS,
        help=f".tmp 文件至少多少天未修改才删（默认 {DEFAULT_TMP_AGE_DAYS}，"
             f"确保不误删正在写入的临时文件）",
    )
    parser.add_argument(
        "--log-age-days", type=float, default=DEFAULT_LOG_AGE_DAYS,
        help=f"按日期命名的归档日志保留天数（默认 {DEFAULT_LOG_AGE_DAYS}）",
    )
    parser.add_argument("--skip-tmp", action="store_true", help="跳过 .tmp 清理")
    parser.add_argument("--skip-cache", action="store_true", help="跳过 __pycache__ 清理")
    parser.add_argument("--skip-logs", action="store_true", help="跳过历史归档日志清理")
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help=f"数据目录（默认 {DATA_DIR}）",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else DATA_DIR
    now = time.time()
    mode = "APPLY（会真删）" if args.apply else "DRY-RUN（只读，不删）"

    print(f"===== 运维清理 cron 启动 @ {datetime.now().isoformat(timespec='seconds')} =====")
    print(f"模式:     {mode}")
    print(f"仓库根:   {PROJECT_ROOT}")
    print(f"数据目录: {data_dir}")
    print(f"阈值:     .tmp > {args.tmp_age_days} 天 / 归档日志 > {args.log_age_days} 天")

    # ── 清理前占用 ──
    size_before_data = _dir_size(data_dir)
    print(f"\n📊 清理前: data/ 占用 {_fmt_size(size_before_data)}")

    results: dict = {}

    # ── 任务 1：孤儿 .tmp（独立 try/except）──
    if args.skip_tmp:
        print("\n── 孤儿临时文件 data/**/*.tmp ──\n   ⏭️ 已按 --skip-tmp 跳过")
    else:
        try:
            targets = collect_orphan_tmp(data_dir, args.tmp_age_days, now)
            results["tmp"] = _run_category(
                f"孤儿临时文件 data/**/*.tmp（mtime > {args.tmp_age_days} 天）",
                targets, args.apply, is_dir=False,
            )
        except Exception as e:
            print(f"\n── 孤儿临时文件 ──\n   ❌ 本类清理异常（不影响其他类）: {e}")
            results["tmp"] = {"count": 0, "bytes": 0, "deleted": 0, "failed": 1}

    # ── 任务 2：可再生缓存目录（独立 try/except）──
    if args.skip_cache:
        print("\n── 可再生缓存目录 ──\n   ⏭️ 已按 --skip-cache 跳过")
    else:
        try:
            targets = collect_cache_dirs(PROJECT_ROOT)
            results["cache"] = _run_category(
                f"可再生缓存目录 {'/'.join(REGENERABLE_CACHE_DIRS)}",
                targets, args.apply, is_dir=True,
            )
        except Exception as e:
            print(f"\n── 可再生缓存目录 ──\n   ❌ 本类清理异常（不影响其他类）: {e}")
            results["cache"] = {"count": 0, "bytes": 0, "deleted": 0, "failed": 1}

    # ── 任务 3：按日期命名的历史归档日志（独立 try/except）──
    if args.skip_logs:
        print("\n── 历史归档日志 ──\n   ⏭️ 已按 --skip-logs 跳过")
    else:
        try:
            targets = collect_dated_archives(data_dir, args.log_age_days, now)
            results["logs"] = _run_category(
                f"按日期命名的历史归档（> {args.log_age_days} 天；活跃 .log 由 logrotate 管）",
                targets, args.apply, is_dir=False,
            )
        except Exception as e:
            print(f"\n── 历史归档日志 ──\n   ❌ 本类清理异常（不影响其他类）: {e}")
            results["logs"] = {"count": 0, "bytes": 0, "deleted": 0, "failed": 1}

    # ── 汇总 ──
    total_hit = sum(r["count"] for r in results.values())
    total_bytes = sum(r["bytes"] for r in results.values())
    total_failed = sum(r["failed"] for r in results.values())

    print(f"\n{'=' * 68}")
    print("📊 汇总")
    print(f"{'=' * 68}")
    for key, label in (("tmp", "孤儿 .tmp"), ("cache", "可再生缓存"), ("logs", "历史归档日志")):
        r = results.get(key)
        if not r:
            continue
        print(f"  {label:14s} 命中 {r['count']:>4} 个 / {_fmt_size(r['bytes']):>10s}"
              f"  已删 {r['deleted']}")
    print(f"  {'合计':14s} 命中 {total_hit:>4} 个 / {_fmt_size(total_bytes):>10s}")

    if args.apply:
        size_after_data = _dir_size(data_dir)
        print(f"\n  data/ 占用: {_fmt_size(size_before_data)} → {_fmt_size(size_after_data)}"
              f"（释放 {_fmt_size(size_before_data - size_after_data)}）")
    else:
        projected = max(0, size_before_data - results.get("tmp", {}).get("bytes", 0)
                        - results.get("logs", {}).get("bytes", 0))
        print(f"\n  data/ 占用: {_fmt_size(size_before_data)} → 预计 {_fmt_size(projected)}")
        print("  🔍 以上为 DRY-RUN 预演，未删除任何文件。")
        print("     确认无误后执行: python3 -m scripts.housekeeping_cron --apply")

    print(f"===== 完成 @ {datetime.now().isoformat(timespec='seconds')} =====")
    return 1 if total_failed else 0


if __name__ == "__main__":
    sys.exit(main())
