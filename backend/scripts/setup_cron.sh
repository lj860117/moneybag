#!/usr/bin/env bash
# setup_cron.sh — 安装全部 MoneyBag cron 任务
#
# ⛔⛔⛔ 本脚本已与线上排班漂移，并且是**破坏性**的，默认拒绝执行。⛔⛔⛔
#
# 2026-08-31 排查发现，在服务器上执行本脚本会：
#   1. 先跑 `crontab -l | grep -v '<14 个关键字>' | crontab -` 清理旧条目 ——
#      该过滤器会命中线上**全部**条目（实测 35 行），即先删光现有排班；
#   2. 再装入本文件里的 23 条，而它们全部使用裸 `python` ——
#      服务器上 `python` **不存在**（实测 `which python: NOT FOUND`），
#      装上必然 `command not found`；
#   3. 即便改成 `python3`，那也是 `/usr/bin/python3` 而非
#      `/opt/moneybag/venv/bin/python3`，项目依赖全部缺失；
#   4. 本文件的条目不加载 `.env`，即使解释器对了也会因缺 API Key 失败。
#
# 净效果：一次 `bash setup_cron.sh` + 回答 y = 删光 29 条正常排班、
# 换成 23 条必然失败的条目，整条 AI 排班**静默死亡**。
#
# 排班的**权威来源**是服务器上的 `crontab -l`，本仓库的可恢复快照在：
#     docs/ops/crontab.production.txt
# （含完整漂移明细：本文件缺 7 类条目、7 处频率不一致）
#
# 若你确实要强制运行（例如要在全新机器上从零搭排班），请先读完上面的
# 漂移清单、修正本文件，再显式解锁：
#     SETUP_CRON_I_UNDERSTAND=yes bash backend/scripts/setup_cron.sh

if [ "${SETUP_CRON_I_UNDERSTAND:-}" != "yes" ]; then
    echo "⛔ 拒绝执行：setup_cron.sh 已过期且会删光线上排班（详见文件头注释）。"
    echo ""
    echo "   排班的权威来源：服务器 crontab -l"
    echo "   可恢复快照：    docs/ops/crontab.production.txt"
    echo ""
    echo "   确认要强制运行请先修正本文件，再用："
    echo "     SETUP_CRON_I_UNDERSTAND=yes bash backend/scripts/setup_cron.sh"
    exit 1
fi

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$BACKEND_DIR")"

echo "=== MoneyBag Cron Job Setup ==="
echo "Backend path: $BACKEND_DIR"
echo "Repo root: $REPO_ROOT"
echo ""

# Create logs directory
mkdir -p "$BACKEND_DIR/logs"
echo "✅ Created logs directory"

# Define all cron jobs
declare -a CRON_JOBS=(
    "0 1 * * * cd $BACKEND_DIR && python scripts/night_worker.py >> logs/night.log 2>&1"
    "0 2 * * * cd $BACKEND_DIR && python scripts/auto_extract_cron.py >> logs/auto_extract.log 2>&1"
    "10 8 * * * cd $BACKEND_DIR && python scripts/daily_reflection_cron.py >> logs/daily_reflection.log 2>&1"
    "30 8 * * 1-5 cd $BACKEND_DIR && python scripts/night_worker.py --push-only >> logs/night.log 2>&1"
    "*/10 9,10,11,13,14 * * 1-5 cd $BACKEND_DIR && python scripts/stock_monitor_cron.py >> logs/stock_monitor.log 2>&1"
    "30 15 * * 1-5 cd $BACKEND_DIR && python scripts/stock_monitor_cron.py --close >> logs/stock_monitor.log 2>&1"
    "30 15 * * 5 cd $BACKEND_DIR && python scripts/weekly_review_cron.py >> logs/weekly_review.log 2>&1"
    "0 21 * * 0 cd $BACKEND_DIR && python scripts/weekly_plan_cron.py >> logs/weekly_plan.log 2>&1"
    "0 4 1 * * cd $BACKEND_DIR && python scripts/memory_archive_cron.py >> logs/memory_archive.log 2>&1"
    # E6 v9.5.44: 晨报幻觉自检（02:35，night_worker 01:00 跑完约 1-2h 后），有问题发企微告警
    "35 2 * * * cd $BACKEND_DIR && python scripts/briefing_hallucination_check.py --rounds 2 --interval 1 --alert >> logs/hallucination_check.log 2>&1"
    # v9.7.0: 补齐遗漏的 cron 任务
    "30 9 * * 6 cd $BACKEND_DIR && python scripts/weekend_push.py >> logs/weekend_push.log 2>&1"
    "0 20 * * 0 cd $BACKEND_DIR && python scripts/broker_rating_cron.py >> logs/broker_rating.log 2>&1"
    "0 1 1 * * cd $BACKEND_DIR && python scripts/monthly_report.py >> logs/monthly_report.log 2>&1"
    "0 10 25 * * cd $BACKEND_DIR && python scripts/dca_scheduler.py --dca >> logs/dca.log 2>&1"
    "0 8 * * * cd $BACKEND_DIR && python scripts/dca_scheduler.py --discipline >> logs/dca_discipline.log 2>&1"
    "0 20 * * 0 cd $BACKEND_DIR && python scripts/dca_scheduler.py --weekly >> logs/dca_weekly.log 2>&1"
    # cache_warmer 五段调度
    "10 18 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --after-close >> logs/cache_warmer.log 2>&1"
    "0 16 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --harvest >> logs/cache_warmer.log 2>&1"
    "45 8 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --morning >> logs/cache_warmer.log 2>&1"
    "5 13 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --midday >> logs/cache_warmer.log 2>&1"
    "0 10 * * 6 cd $BACKEND_DIR && python scripts/cache_warmer.py --weekend >> logs/cache_warmer.log 2>&1"
    "30 20 * * 1-5 cd $BACKEND_DIR && python scripts/cache_warmer.py --nav-confirmed >> logs/cache_warmer.log 2>&1"
)

# Check if any cron jobs already exist
EXISTING=$(crontab -l 2>/dev/null | grep -c 'moneybag\|night_worker\|stock_monitor\|weekly_review\|weekly_plan\|memory_archive\|daily_reflection\|auto_extract\|hallucination_check\|weekend_push\|broker_rating\|monthly_report\|dca_scheduler\|cache_warmer' || echo 0)

if [ "$EXISTING" -gt 0 ]; then
    echo "⚠️  Found $EXISTING existing MoneyBag cron jobs"
    read -p "Do you want to replace them? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborting."
        exit 0
    fi
    # Remove old cron jobs
    (crontab -l 2>/dev/null | grep -v 'moneybag\|night_worker\|stock_monitor\|weekly_review\|weekly_plan\|memory_archive\|daily_reflection\|auto_extract\|hallucination_check\|weekend_push\|broker_rating\|monthly_report\|dca_scheduler\|cache_warmer') | crontab - 2>/dev/null || true
    echo "✅ Removed old cron jobs"
fi

# Add all new cron jobs
{
    crontab -l 2>/dev/null || true
    for job in "${CRON_JOBS[@]}"; do
        echo "$job"
    done
} | crontab -

echo "✅ Installed ${#CRON_JOBS[@]} cron jobs"
echo ""
echo "=== Installed Cron Jobs ==="
crontab -l | grep -E 'night_worker|stock_monitor|weekly_review|weekly_plan|memory_archive|daily_reflection|auto_extract|hallucination_check|weekend_push|broker_rating|monthly_report|dca_scheduler|cache_warmer' || echo "(none found)"
echo ""
echo "=== Verification ==="
echo "Run these commands to test:"
echo "  python3 backend/scripts/weekly_review_cron.py --dry-run"
echo "  python3 backend/scripts/night_worker.py --push-only"
echo "  python3 backend/scripts/stock_monitor_cron.py"
