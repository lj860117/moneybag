"""
auto_extract 凌晨批量处理 cron — 每天 02:00 跑

V7.6 新增：把白天对话收集的原始对话片段集中在凌晨统一提炼，降低白天 API 压力。

职责：
  1. 扫所有用户（data/*/memory/extract_queue.json）
  2. 逐条调 LLM 提炼出用户的新习惯/偏好/原则
  3. 入 pending_insights 待审队列（同时走家庭主账号路由）
  4. 清空原队列

建议 crontab：
  0 2 * * * cd /opt/moneybag/backend && python -m scripts.auto_extract_cron >> /var/log/moneybag/auto_extract.log 2>&1

手动跑：
  cd backend && python -m scripts.auto_extract_cron
  cd backend && python -m scripts.auto_extract_cron --user LeiJiang
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime

_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

from config import DATA_DIR
from services.agent_memory import (
    get_extract_queue,
    batch_extract_for_user,
)


def discover_users_with_queue() -> list:
    """扫出所有 extract_queue 非空的用户"""
    users = []
    if not DATA_DIR.exists():
        return users
    for d in DATA_DIR.iterdir():
        if not d.is_dir():
            continue
        q = d / "memory" / "extract_queue.json"
        if q.exists():
            try:
                import json
                data = json.loads(q.read_text(encoding="utf-8"))
                if data:  # 非空队列
                    users.append(d.name)
            except Exception:
                continue
    return users


def main():
    parser = argparse.ArgumentParser(description="钱袋子 auto_extract 凌晨批量 cron")
    parser.add_argument("--user", type=str, help="只跑指定用户（默认全用户）")
    parser.add_argument("--max-items", type=int, default=10,
                        help="每用户单次最多提炼多少条对话（默认 10）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印要处理的队列，不调 LLM")
    args = parser.parse_args()

    print(f"===== auto_extract cron 启动 @ {datetime.now().isoformat()} =====")

    if args.user:
        users = [args.user]
    else:
        users = discover_users_with_queue()
        print(f"队列非空用户：{len(users)} 个 → {users}")

    if args.dry_run:
        for uid in users:
            q = get_extract_queue(uid)
            print(f"[DRY] {uid}: {len(q)} 条待处理")
            for i, item in enumerate(q[-args.max_items:], 1):
                print(f"  {i}. {item.get('time','')[:19]} user: {item.get('user_msg','')[:60]}...")
        return

    total_processed = 0
    total_extracted = 0
    for uid in users:
        try:
            r = batch_extract_for_user(uid, max_items=args.max_items)
            total_processed += r["processed"]
            total_extracted += r["extracted"]
            print(f"[{uid}] 处理 {r['processed']}/{r['queue_len_before']} 条，"
                  f"提炼到 {r['extracted']} 条新洞察，"
                  f"丢弃 {r.get('discarded', 0)} 条老对话")
        except Exception as e:
            print(f"[ERROR] {uid}: {e}")

    print(f"===== 完成：共处理 {total_processed} 段对话，提炼 {total_extracted} 条新洞察 =====")


if __name__ == "__main__":
    main()
