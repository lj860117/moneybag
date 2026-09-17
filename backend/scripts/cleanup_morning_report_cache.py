#!/usr/bin/env python3
"""
晨报缓存清理脚本
==================

用途：清理 ``DATA_DIR/briefings/`` 下超过保留期的晨报缓存 JSON。

用法::

    # cron / 自动化（必须显式 --yes；非交互环境不给 --yes 会被拒绝执行）
    python3 scripts/cleanup_morning_report_cache.py --yes

    # 先看将要删什么：不删，也不需要 --yes
    python3 scripts/cleanup_morning_report_cache.py --dry-run

    # 手动跑，删前确认（有人在旁边时多一道确认是好的）
    python3 scripts/cleanup_morning_report_cache.py

    # 自定义保留期（默认 180 天）
    python3 scripts/cleanup_morning_report_cache.py --days 7 --yes

设计要点（2026-09-17 重写，原因见下）
------------------------------------

1. **保留期可配置，默认 180 天**
   优先级：``--days N`` > 环境变量 ``BRIEF_CACHE_RETENTION_DAYS`` > 默认 180。
   以前写死 ``timedelta(days=7)``，用户一改保留策略就得改代码 —— 典型的
   「以后还会踩坑」的硬编码，所以这里把三层都留出来。

2. **支持非交互（cron 必需）**
   ``--yes`` 或 ``BRIEF_CACHE_CLEANUP_YES=1`` 跳过确认直接删。
   但**反过来不成立**：非交互环境（stdin 不是 tty）且没给 ``--yes`` 时，
   脚本**拒绝执行并返回 2**，而不是去调 ``input()`` 然后被 EOFError 崩掉。
   宁可 cron 每周报错被看见，也不要「静默不删」或「静默删错」。

3. **支持 ``--dry-run``**
   只打印将要删什么，不删，也不需要 ``--yes``。这是挂 cron 前在服务器上
   验收的手段。

4. **扫描范围只有 ``brief_dir/*.json``，不递归子目录**
   服务器上 ``data/briefings/_legacy_uppercase_backup/`` 里放着 26 个待观察
   备份，必须不被扫到、不被删。除了依赖 glob 的非递归语义，本脚本还加了两道
   保险，把「没扫到」从**推断**变成**可核对的输出**：

   - 显式跳过非普通文件：**``glob("*.json")`` 是会匹配到*目录*的**，
     只靠后缀过滤并不足以证明安全；
   - 把被跳过的子目录连同其文件数打印出来，服务器 dry-run 一眼可验。

退出码
------
======  ======================================================
  0     正常结束（含「无需清理」）
  1     用户在交互确认时取消
  2     用法/环境错误（保留期非法、非交互却没给 ``--yes`` 等）
======  ======================================================
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

# 必须在 `import config` 之前：以 `python3 cleanup_morning_report_cache.py` 方式调用时
# sys.path[0] 是 scripts/ 而不是 backend/，先 import config 会抛
# ModuleNotFoundError: No module named 'config'（cron 走的就是这条路径）。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import config  # noqa: E402

# ---------------------------------------------------------------------------
# 可配置项
# ---------------------------------------------------------------------------
DEFAULT_RETENTION_DAYS = 180
RETENTION_DAYS_ENV = "BRIEF_CACHE_RETENTION_DAYS"
ASSUME_YES_ENV = "BRIEF_CACHE_CLEANUP_YES"
DRY_RUN_ENV = "BRIEF_CACHE_CLEANUP_DRY_RUN"

# 退出码
EXIT_OK = 0
EXIT_CANCELLED = 1
EXIT_USAGE = 2

# 分类标签
CATEGORY_VALID = "valid"
CATEGORY_EXPIRED = "expired"
CATEGORY_FUTURE = "future"
CATEGORY_INVALID = "invalid"

# 由 decide_action() 返回的动作
ACTION_DELETE = "delete"
ACTION_DRY_RUN = "dry_run"
ACTION_PROMPT = "prompt"
ACTION_REFUSE = "refuse"

# 「确认为删除」的可接受输入
_CONFIRM_WORDS = ("yes", "y")

_TRUTHY_ENV_VALUES = ("1", "true", "yes", "y", "on")


class CacheEntry(NamedTuple):
    """一条缓存文件记录。

    Attributes:
        path: 文件绝对路径。
        date_str: 文件名里解析出的 8 位日期；无法解析时为 ``""``。
        category: :data:`CATEGORY_VALID` / ``EXPIRED`` / ``FUTURE`` / ``INVALID``
            之一。
    """

    path: Path
    date_str: str
    category: str


def _env_flag(name: str, env: Optional[dict] = None) -> bool:
    """判断环境变量是否被设成「真」。

    Args:
        name: 环境变量名。
        env: 用于测试的替身字典，默认用 ``os.environ``。

    Returns:
        值（去空白、转小写）属于 ``1/true/yes/y/on`` 时为真。
    """
    source = os.environ if env is None else env
    return (source.get(name) or "").strip().lower() in _TRUTHY_ENV_VALUES


def resolve_retention_days(
    cli_days: Optional[int] = None,
    env: Optional[dict] = None,
) -> int:
    """解析保留期天数：``--days`` > 环境变量 > :data:`DEFAULT_RETENTION_DAYS`。

    Args:
        cli_days: ``--days`` 传入的值，没传则为 ``None``。
        env: 用于测试的替身字典，默认用 ``os.environ``。

    Returns:
        保留期天数（正整数）。

    Raises:
        ValueError: 值不是整数或 <= 0。
    """
    if cli_days is not None:
        source, raw = "--days", cli_days
    else:
        source = os.environ if env is None else env
        raw = (source.get(RETENTION_DAYS_ENV) or "").strip()
        if not raw:
            return DEFAULT_RETENTION_DAYS
        source = RETENTION_DAYS_ENV

    try:
        days = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"保留期必须是整数天数（{source}={raw!r}）") from exc
    if days <= 0:
        raise ValueError(f"保留期必须 > 0 天（{source}={days}）")
    return days


def classify_cache_files(
    brief_dir: Path,
    today_str: str,
    cutoff_str: str,
) -> Dict[str, List[CacheEntry]]:
    """把缓存目录里的 ``*.json`` 分成四类。

    ⚠️ 刻意用 ``glob`` 而不是 ``rglob``：**不递归子目录**。
    ``data/briefings/_legacy_uppercase_backup/`` 下有待观察备份，递归会误删。

    Args:
        brief_dir: 缓存目录。
        today_str: 今天的 ``YYYYMMDD``。
        cutoff_str: 保留期起点的 ``YYYYMMDD``，早于它的算过期。

    Returns:
        ``{category: [CacheEntry, ...]}``，四个 key 恒存在（可能为空列表）。
    """
    grouped: Dict[str, List[CacheEntry]] = {
        CATEGORY_VALID: [],
        CATEGORY_EXPIRED: [],
        CATEGORY_FUTURE: [],
        CATEGORY_INVALID: [],
    }

    for fp in sorted(brief_dir.glob("*.json")):
        # 保险一：glob("*.json") 同样会匹配到**目录**，只靠后缀不足以证明安全。
        if not fp.is_file():
            continue

        stem = fp.stem  # e.g. "LeiJiang_20250714"
        date_str = stem.rsplit("_", 1)[-1] if "_" in stem else ""
        if len(date_str) != 8 or not date_str.isdigit():
            grouped[CATEGORY_INVALID].append(
                CacheEntry(fp, "", CATEGORY_INVALID))
            continue

        if date_str > today_str:
            category = CATEGORY_FUTURE
        elif date_str < cutoff_str:
            category = CATEGORY_EXPIRED
        else:
            category = CATEGORY_VALID
        grouped[category].append(CacheEntry(fp, date_str, category))

    return grouped


def skipped_subdirectories(brief_dir: Path) -> List[Tuple[Path, int]]:
    """列出**被 glob 跳过**的子目录及其文件数。

    存在的意义是把「没扫到子目录」从一句推断变成一行可核对的输出：
    服务器 dry-run 时可以直接看到 ``_legacy_uppercase_backup/ (26 个文件)``
    被列出但未被清理。

    Args:
        brief_dir: 缓存目录。

    Returns:
        ``[(子目录, 文件数), ...]``，按名称排序；无法统计时文件数为 ``-1``。
    """
    out: List[Tuple[Path, int]] = []
    if not brief_dir.is_dir():
        return out
    for child in sorted(brief_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            count = sum(1 for p in child.rglob("*") if p.is_file())
        except OSError:
            count = -1
        out.append((child, count))
    return out


def decide_action(assume_yes: bool, dry_run: bool, stdin_isatty: bool) -> str:
    """决定这次运行要做什么动作（纯函数，便于测试）。

    Args:
        assume_yes: 是否给了 ``--yes``（或对应的环境变量）。
        dry_run: 是否给了 ``--dry-run``。
        stdin_isatty: ``sys.stdin.isatty()``，即是否有人在旁边。

    Returns:
        :data:`ACTION_DRY_RUN` / ``DELETE`` / ``PROMPT`` / ``REFUSE`` 之一。

    优先级说明：``--dry-run`` 最优先（它不删东西，因此不需要确认）；
    其次是 ``--yes``；都没有时，非交互环境一律 **REFUSE** —— 不静默删。
    """
    if dry_run:
        return ACTION_DRY_RUN
    if assume_yes:
        return ACTION_DELETE
    if not stdin_isatty:
        return ACTION_REFUSE
    return ACTION_PROMPT


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="cleanup_morning_report_cache.py",
        description=(f"清理过期的晨报缓存文件（默认保留 "
                     f"{DEFAULT_RETENTION_DAYS} 天）"),
    )
    parser.add_argument(
        "--days", "-d", type=int, default=None, metavar="N",
        help=(f"保留期天数，默认 {DEFAULT_RETENTION_DAYS}；"
              f"也可用环境变量 {RETENTION_DAYS_ENV} 设置"),
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help=(f"非交互：跳过确认直接删除（cron 必加；"
              f"也可用 {ASSUME_YES_ENV}=1）"),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(f"只打印将要删除的清单，不删除；"
              f"也可用 {DRY_RUN_ENV}=1"),
    )
    return parser


def _print_entries(title: str, entries: List[CacheEntry]) -> None:
    """打印一组文件的清单（按日期倒序）。"""
    if not entries:
        return
    print(f"\n{title}:")
    for entry in sorted(entries, key=lambda e: e.date_str, reverse=True):
        size_kb = entry.path.stat().st_size / 1024
        suffix = f" ({entry.date_str})" if entry.date_str else ""
        print(f"  {entry.path.name:40s}{suffix} {size_kb:6.1f} KB")


def main(argv: Optional[List[str]] = None) -> int:
    """入口。

    Args:
        argv: 命令行参数（不含程序名），默认取 ``sys.argv[1:]``。

    Returns:
        进程退出码，见模块 docstring 的「退出码」表。
    """
    args = build_parser().parse_args(argv)

    try:
        days = resolve_retention_days(args.days)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_USAGE

    assume_yes = bool(args.yes) or _env_flag(ASSUME_YES_ENV)
    dry_run = bool(args.dry_run) or _env_flag(DRY_RUN_ENV)

    data_dir = Path(config.DATA_DIR)
    brief_dir = data_dir / "briefings"

    if not brief_dir.exists():
        print(f"✅ 缓存目录不存在，无需清理: {brief_dir}")
        return EXIT_OK

    now = datetime.now()
    today_str = now.strftime("%Y%m%d")
    cutoff_str = (now - timedelta(days=days)).strftime("%Y%m%d")

    print(f"🔍 扫描缓存目录: {brief_dir}")
    print(f"   扫描范围: {brief_dir.name}/*.json（glob，不递归子目录）")
    print(f"📅 当前日期: {now.strftime('%Y-%m-%d')}")
    print(f"🗓️  保留期: {days} 天（早于 {cutoff_str} 的文件算过期）")
    print("-" * 60)

    grouped = classify_cache_files(brief_dir, today_str, cutoff_str)
    valid_files = grouped[CATEGORY_VALID]
    expired_files = grouped[CATEGORY_EXPIRED]
    future_files = grouped[CATEGORY_FUTURE]
    invalid_files = grouped[CATEGORY_INVALID]

    if not any((valid_files, expired_files, future_files, invalid_files)):
        print("✅ 未找到缓存文件")
        return EXIT_OK

    print("📊 统计:")
    print(f"  有效缓存（保留期内）: {len(valid_files)}")
    print(f"  过期缓存（>{days}天前）: {len(expired_files)}")
    print(f"  未来缓存（未来日期）: {len(future_files)}")
    print(f"  无效缓存（格式错误）: {len(invalid_files)}")

    # 保险二：把「没扫到子目录」打印出来，让服务器 dry-run 可以直接核对
    skipped = skipped_subdirectories(brief_dir)
    if skipped:
        print(f"  已跳过子目录（glob 不递归，内容不会被扫到/不会被删）: "
              f"{len(skipped)} 个")
        for child, count in skipped:
            print(f"    {child.name}/  ({count} 个文件)")

    print("-" * 60)

    _print_entries("✅ 有效缓存", valid_files)
    _print_entries("⏰ 过期缓存（待删除）", expired_files)
    _print_entries("⚠️  未来缓存（待删除）", future_files)
    _print_entries("❓ 无效缓存（待删除）", invalid_files)
    print("-" * 60)

    to_delete: List[CacheEntry] = expired_files + future_files + invalid_files
    if not to_delete:
        print("✅ 无需清理")
        return EXIT_OK

    total_size = sum(entry.path.stat().st_size for entry in to_delete) / 1024
    print(f"\n🗑️  待删除: {len(to_delete)} 个文件，共 {total_size:.1f} KB")

    action = decide_action(assume_yes, dry_run, sys.stdin.isatty())
    if action == ACTION_REFUSE:
        print(
            f"❌ 拒绝执行：非交互环境未给 --yes。"
            f"cron 请显式加 --yes（或 {ASSUME_YES_ENV}=1）；"
            f"只想看清单请加 --dry-run。",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if action == ACTION_DRY_RUN:
        print(f"🧪 dry-run：以下 {len(to_delete)} 个文件将被删除"
              f"（本次**未删除**）")
        for entry in to_delete:
            print(f"  [dry-run] 将删除: {entry.path.name}")
        print("-" * 60)
        print(f"✅ dry-run 完成: 0 个文件被删除")
        return EXIT_OK

    if action == ACTION_PROMPT:
        response = input("确认删除? (yes/no): ").strip().lower()
        if response not in _CONFIRM_WORDS:
            print("❌ 已取消")
            return EXIT_CANCELLED

    deleted_count = 0
    deleted_bytes = 0
    for entry in to_delete:
        try:
            size = entry.path.stat().st_size
            entry.path.unlink()
        except OSError as exc:
            print(f"❌ 删除失败 {entry.path.name}: {exc}")
            continue
        deleted_count += 1
        deleted_bytes += size
        print(f"🗑️  已删除: {entry.path.name}")

    print("-" * 60)
    print(f"✅ 完成: 删除了 {deleted_count} 个过期缓存文件"
          f"（共 {deleted_bytes / 1024:.1f} KB）")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
