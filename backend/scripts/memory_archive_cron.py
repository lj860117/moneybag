"""
记忆分层归档 cron — 每月 1 号 04:00 跑

职责：
  1. 扫所有用户（data/*/memory/ 目录）
  2. 把 decisions.json 里 30 天前的条目移到 decisions_archive.json
  3. 对"上个月"没摘要过的数据调 LLM 生成月度摘要 → archive_summary.json

为什么选每月 1 号：
  - 固定周期，LLM 调用量可预估
  - 前一天（月末）的决策都能归档
  - 04:00 低流量，不影响用户

建议 crontab：
  0 4 1 * * cd /opt/moneybag/backend && python -m scripts.memory_archive_cron >> /var/log/moneybag/memory_archive.log 2>&1

手动跑：
  cd backend && python -m scripts.memory_archive_cron
  cd backend && python -m scripts.memory_archive_cron --user LeiJiang --month 2026-03
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# 让脚本能 import services
_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

from config import DATA_DIR
from services.agent_memory import (
    archive_old_decisions,
    summarize_archive_month,
    get_archive_summaries,
)


def discover_users() -> list:
    """扫出所有有记忆目录的用户"""
    users = []
    if not DATA_DIR.exists():
        return users
    for d in DATA_DIR.iterdir():
        if not d.is_dir():
            continue
        if (d / "memory" / "decisions.json").exists() or (d / "memory").exists():
            users.append(d.name)
    return users


def last_month() -> str:
    """返回"上一个月" YYYY-MM 字符串"""
    today = datetime.now().date()
    first_of_this_month = today.replace(day=1)
    last_day_prev = first_of_this_month - timedelta(days=1)
    return last_day_prev.strftime("%Y-%m")


def run_for_user(user_id: str, month: str = None, verbose: bool = True) -> dict:
    """为单个用户跑归档+摘要"""
    result = {"user": user_id}

    # Step 1: 移冷数据
    arch = archive_old_decisions(user_id)
    result["archive"] = arch
    if verbose:
        print(f"[{user_id}] 归档：移走 {arch['moved']} 条，热区剩 {arch['hot_remaining']}，冷区累计 {arch['cold_total']}")

    # Step 2: 对目标月份做摘要（默认上个月）
    target_month = month or last_month()
    summed = summarize_archive_month(user_id, target_month)
    result["summary"] = summed
    if verbose:
        if summed.get("skipped"):
            print(f"[{user_id}] {target_month} 已有摘要，跳过")
        elif summed.get("ok"):
            print(f"[{user_id}] {target_month} 摘要完成：{summed.get('total')} 条，"
                  f"胜率 {summed.get('win_rate')}，{summed.get('summary', '')[:50]}...")
        else:
            print(f"[{user_id}] {target_month} 摘要跳过：{summed.get('reason')}")

    return result


def main():
    parser = argparse.ArgumentParser(description="钱袋子记忆分层归档 cron")
    parser.add_argument("--user", type=str, help="只跑指定用户（默认全用户）")
    parser.add_argument("--month", type=str, help="指定月份 YYYY-MM（默认上个月）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    print(f"===== 记忆归档 cron 启动 @ {datetime.now().isoformat()} =====")
    target_month = args.month or last_month()
    print(f"目标月份：{target_month}")

    if args.user:
        users = [args.user]
    else:
        users = discover_users()
        print(f"发现 {len(users)} 个用户：{users}")

    total_moved = 0
    total_summed = 0
    for uid in users:
        try:
            r = run_for_user(uid, month=target_month, verbose=not args.quiet)
            total_moved += r["archive"].get("moved", 0)
            if r["summary"].get("ok") and not r["summary"].get("skipped"):
                total_summed += 1
        except Exception as e:
            print(f"[ERROR] {uid}: {e}")

    print(f"===== 完成：移动 {total_moved} 条到冷区，生成 {total_summed} 份月度摘要 =====")


if __name__ == "__main__":
    main()
